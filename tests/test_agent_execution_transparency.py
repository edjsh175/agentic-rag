"""Acceptance tests for the Agent execution-transparency event contract.

All tests use in-memory controller decisions, tool handlers, reviewers, and
stream stubs.  They must never open the configured vector or relational stores.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from rag_knowledge.services.agent_orchestration.models import (
    AgentBudget,
    AgentDecision,
    AgentTurnResult,
    ConversationContext,
    EvidencePool,
    ToolObservation,
    ToolProgressStatus,
)
from rag_knowledge.services.agent_orchestration.runtime import (
    AgentLoop,
    build_agent_registry,
)
from rag_knowledge.llm_http import LLMStreamPart, ModelEndpoint
from rag_knowledge.services.answer_finalizer import (
    AnswerFinalizer,
    FinalizedAnswer,
    REVIEW_BLOCKED_ANSWER,
)
from rag_knowledge.services.helper_grounding_reviewer import (
    ClaimReview,
    HelperGroundingReviewResult,
    RewriteAction,
)
from rag_knowledge.services.rag import CONTROLLER_ERROR_ANSWER, NO_KNOWLEDGE_ANSWER, RagChain


def _doc(chunk_id: str = "c1", content: str = "StampServer 的端口是 8080。") -> dict:
    return {
        "content": content,
        "metadata": {
            "chunk_id": chunk_id,
            "citation_id": 1,
            "document_entity": "StampServer",
            "file_name": f"{chunk_id}.md",
            "page_label": "无页码",
            "category": "text",
            "source_type": "knowledge_base",
        },
    }


def _agent_cfg(*, terminal_finalization_v2: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        agent_orchestration=SimpleNamespace(
            terminal_finalization_v2=terminal_finalization_v2,
        ),
    )


async def _run_loop(
    decisions: list[AgentDecision],
    *,
    handlers: dict | None = None,
    max_steps: int | None = None,
    terminal_finalization_v2: bool = True,
    conversation: ConversationContext | None = None,
    evidence: EvidencePool | None = None,
):
    conv = conversation or ConversationContext.from_request(
        "StampServer 的端口是多少？",
        [],
    )
    pool = evidence or EvidencePool(question_id="acceptance-q")
    decision_iter = iter(decisions)
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    result = await AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=max_steps or len(decisions)),
        registry=build_agent_registry(),
        handlers=handlers or {},
        cfg=_agent_cfg(terminal_finalization_v2=terminal_finalization_v2),
        decide_fn=lambda *_: next(decision_iter),
        tool_timeout=0,
    ).run(on_event=on_event)
    return result, events


def _event_index(events: list[dict], event_type: str, predicate=None) -> int:
    for index, event in enumerate(events):
        if event.get("type") != event_type:
            continue
        if predicate is None or predicate(event.get("data") or {}):
            return index
    raise AssertionError(f"missing event: {event_type}; got {[e.get('type') for e in events]}")


def _claim(
    claim_id: str,
    *,
    evidence_ids: tuple[int, ...],
    status: str,
) -> ClaimReview:
    return ClaimReview(
        claim_id=claim_id,
        claim=f"claim-{claim_id}",
        claim_type="fact",
        status=status,
        evidence_ids=evidence_ids,
        reason="acceptance fixture",
    )


def test_tool_lifecycle_has_canonical_order_and_no_legacy_events():
    pool = EvidencePool(question_id="ordered-events")

    async def retrieve(arguments: dict) -> ToolObservation:
        pool.add_retrieve([_doc()], query=arguments["query"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="found one chunk")

    _, events = asyncio.run(
        _run_loop(
            [
                AgentDecision(
                    action="tool_call",
                    tool="retrieve_kb",
                    arguments={"query": "StampServer 端口"},
                    thought="需要检索端口证据",
                    source="test",
                ),
            ],
            handlers={"retrieve_kb": retrieve},
            max_steps=1,
            evidence=pool,
        ),
    )

    ordered = [
        _event_index(events, "decision"),
        _event_index(events, "guard", lambda data: data.get("allowed") is True),
        _event_index(events, "tool_start"),
        _event_index(events, "tool_result"),
        _event_index(events, "evidence_update"),
    ]
    assert ordered == sorted(ordered)
    assert len(set(ordered)) == len(ordered)
    assert not {"thinking", "tool_end"}.intersection(
        event.get("type") for event in events
    )


def test_stage1_reasoning_completes_before_understanding_and_controller_reasoning():
    from rag_knowledge.services.conversation_context import UnderstandingResult

    chain = object.__new__(RagChain)
    endpoint = ModelEndpoint(
        role="llm",
        provider="ollama",
        model="qwen3.5:9b",
        base_url="http://unused.test",
    )
    chain._cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(
            max_steps=1,
            max_retrieve_attempts=1,
            hard_retrieve_cap=1,
            tool_timeout=0,
            terminal_finalization_v2=False,
        ),
        context_budget=SimpleNamespace(context_window=32768),
        ollama_base_url="http://unused.test",
        model_routing=None,
        endpoint_for=lambda _role: endpoint,
    )
    events: list[dict] = []

    def fake_understand(question, _history=None, **kwargs):
        emit = kwargs.get("on_reasoning_event")
        assert emit is not None
        for event in (
            {
                "type": "llm_reasoning_start",
                "data": {
                    "call_id": "common_stage1_contextualize",
                    "role": "helper",
                    "stage": "common_stage1",
                    "model": "qwen3.5:4b",
                    "provider": "ollama",
                },
            },
            {
                "type": "llm_reasoning_delta",
                "data": {
                    "call_id": "common_stage1_contextualize",
                    "role": "helper",
                    "stage": "common_stage1",
                    "delta": "先结合上一轮上下文解析当前追问。",
                },
            },
            {
                "type": "llm_reasoning_end",
                "data": {
                    "call_id": "common_stage1_contextualize",
                    "role": "helper",
                    "stage": "common_stage1",
                    "model": "qwen3.5:4b",
                    "provider": "ollama",
                    "reasoning_available": True,
                },
            },
        ):
            emit(event)
        return UnderstandingResult(
            mode="retrieve",
            user_utterance=question,
            resolved_question="StampServer 的端口是多少？",
            retrieval_queries=[
                {"text": "StampServer 的端口是多少？", "kind": "standalone", "weight": 0.8},
            ],
            rationale="contextualize",
        )

    chain._understand_for_retrieval = fake_understand

    async def fake_controller_stream(*_args, **_kwargs):
        yield LLMStreamPart("reasoning", "Stage-1 已解析主体，现在决定是否结束。")
        yield LLMStreamPart("content", '{"action":"finalize","reason":"结束本轮"}')

    async def on_event(event: dict) -> None:
        events.append(event)

    async def run_turn():
        return await chain._run_agent_turn(
            "那它的端口呢？",
            history=[
                {"role": "user", "content": "StampServer 是什么？"},
                {"role": "assistant", "content": "上一轮回答"},
            ],
            kb_name=None,
            doc_category=None,
            entity_name=None,
            web_search=False,
            pinned_chunk_ids=None,
            excluded_chunk_ids=None,
            clarification_question=None,
            clarification_selected=None,
            on_event=on_event,
            trace=None,
        )

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_controller_stream):
        asyncio.run(run_turn())

    stage1_start = _event_index(
        events,
        "llm_reasoning_start",
        lambda data: data.get("stage") == "common_stage1",
    )
    stage1_delta = _event_index(
        events,
        "llm_reasoning_delta",
        lambda data: data.get("stage") == "common_stage1",
    )
    stage1_end = _event_index(
        events,
        "llm_reasoning_end",
        lambda data: data.get("stage") == "common_stage1",
    )
    understanding = _event_index(events, "understanding")
    controller_start = _event_index(
        events,
        "llm_reasoning_start",
        lambda data: data.get("stage") == "agent_controller",
    )
    decision = _event_index(events, "decision")
    assert stage1_start < stage1_delta < stage1_end < understanding < controller_start < decision


def test_controller_raw_reasoning_streams_before_structured_decision():
    conversation = ConversationContext.from_request("StampServer 的端口是多少？", [])
    evidence = EvidencePool(question_id="controller-reasoning")
    endpoint = ModelEndpoint(
        role="llm",
        provider="ollama",
        model="qwen3.5:9b",
        base_url="http://unused.test",
    )
    cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(terminal_finalization_v2=False),
        context_budget=SimpleNamespace(context_window=32768),
        ollama_base_url="http://unused.test",
        model_routing=None,
        endpoint_for=lambda _role: endpoint,
    )
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    async def fake_stream_parts(*_args, **_kwargs):
        yield LLMStreamPart("reasoning", "当前还没有足够证据，先判断是否需要检索。")
        yield LLMStreamPart("content", '{"action":"finalize","reason":"结束本轮"}')

    loop = AgentLoop(
        conversation=conversation,
        evidence=evidence,
        budget=AgentBudget(max_steps=1),
        registry=build_agent_registry(),
        handlers={},
        cfg=cfg,
        tool_timeout=0,
    )

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream_parts):
        asyncio.run(loop.run(on_event=on_event))

    start_index = _event_index(events, "llm_reasoning_start")
    delta_index = _event_index(events, "llm_reasoning_delta")
    end_index = _event_index(events, "llm_reasoning_end")
    decision_index = _event_index(events, "decision")
    assert start_index < delta_index < end_index < decision_index
    assert events[delta_index]["data"]["delta"] == "当前还没有足够证据，先判断是否需要检索。"
    assert events[end_index]["data"]["reasoning_available"] is True
    assert events[decision_index]["data"]["reason"] == "结束本轮"


def test_helper_reviewer_streams_raw_reasoning_without_bypassing_protocol_validation():
    chain = object.__new__(RagChain)
    endpoint = ModelEndpoint(
        role="helper_llm",
        provider="ollama",
        model="qwen3.5:4b",
        base_url="http://unused.test",
    )
    chain._cfg = SimpleNamespace(
        grounding_reviewer_enabled=True,
        grounding_reviewer_timeout=30.0,
        context_budget=SimpleNamespace(context_window=32768),
        ollama_base_url="http://unused.test",
        model_routing=None,
        endpoint_for=lambda _role: endpoint,
    )
    events: list[dict] = []
    response = json.dumps({
        "coverage": "PARTIAL",
        "summary": "证据只覆盖部分问题",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "StampServer 的端口是 8080",
            "claim_type": "knowledge_claim",
            "evidence_ids": [1],
            "status": "supported",
            "reason": "证据直接支持",
        }],
        "rewrite_actions": [],
    }, ensure_ascii=False)

    async def fake_stream_parts(*_args, **_kwargs):
        yield LLMStreamPart("reasoning", "逐条核对候选事实与 Evidence。")
        yield LLMStreamPart("content", response)

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream_parts):
        reviewer = chain._helper_grounding_reviewer(on_reasoning_event=events.append)
        result = reviewer.review("StampServer 的端口是多少？", [_doc()], "StampServer 的端口是 8080。[1]")

    assert result.verdict == "PASS"
    assert result.coverage == "PARTIAL"
    assert [event["type"] for event in events] == [
        "llm_reasoning_start",
        "llm_reasoning_delta",
        "llm_reasoning_end",
    ]
    assert events[1]["data"]["role"] == "helper"
    assert events[1]["data"]["stage"] == "grounding_reviewer"
    assert events[1]["data"]["delta"] == "逐条核对候选事实与 Evidence。"
    assert events[2]["data"]["reasoning_available"] is True


def test_grounded_rewrite_streams_raw_reasoning_while_buffering_candidate_v2():
    chain = object.__new__(RagChain)
    endpoint = ModelEndpoint(
        role="llm",
        provider="ollama",
        model="qwen3.5:9b",
        base_url="http://unused.test",
    )
    chain._cfg = SimpleNamespace(context_budget=SimpleNamespace(context_window=32768))
    chain._ollama_base = "http://unused.test"
    chain._resolve_llm_endpoint = lambda _model: endpoint
    chain._need_ollama_thinking = lambda _model: True
    events: list[dict] = []
    review = HelperGroundingReviewResult(
        verdict="REVISE",
        coverage="PARTIAL",
        summary="remove unsupported claim",
        claim_reviews=[
            _claim("c1", evidence_ids=(1,), status="supported"),
            _claim("c2", evidence_ids=(), status="unsupported"),
        ],
        rewrite_actions=[
            RewriteAction("c2", "rewrite_to_supported_scope_or_remove", "删除未支持断言"),
        ],
    )

    async def fake_stream_parts(*_args, **_kwargs):
        yield LLMStreamPart("reasoning", "只保留 c1，删除 c2。")
        yield LLMStreamPart("content", "StampServer 的端口是 8080。[1]")

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream_parts):
        candidate_v2 = chain._retry_grounded_candidate(
            "qwen3.5:9b",
            "StampServer 的端口是多少？",
            "StampServer 的端口是 8080。[1] 另有未支持断言。",
            [_doc()],
            review,
            on_reasoning_event=events.append,
        )

    assert candidate_v2 == "StampServer 的端口是 8080。[1]"
    assert [event["type"] for event in events] == [
        "llm_reasoning_start",
        "llm_reasoning_delta",
        "llm_reasoning_end",
    ]
    assert events[1]["data"]["role"] == "main"
    assert events[1]["data"]["stage"] == "grounded_retry"
    assert events[1]["data"]["delta"] == "只保留 c1，删除 c2。"
    assert events[-1]["data"]["num_predict"] == 8192


def test_understanding_is_the_first_agent_lifecycle_event():
    _, events = asyncio.run(
        _run_loop(
            [AgentDecision(action="finish", source="test")],
            max_steps=1,
            terminal_finalization_v2=False,
        ),
    )

    assert events
    assert events[0]["type"] == "understanding"
    assert isinstance(events[0]["data"], dict)


def test_guard_deny_is_visible_without_fabricated_tool_lifecycle():
    _, events = asyncio.run(
        _run_loop(
            [
                AgentDecision(
                    action="tool_call",
                    tool="not_a_registered_tool",
                    arguments={},
                    source="test",
                ),
            ],
            max_steps=1,
        ),
    )

    decision_index = _event_index(events, "decision")
    guard_index = _event_index(
        events,
        "guard",
        lambda data: data.get("allowed") is False,
    )
    assert decision_index < guard_index
    guard = events[guard_index]["data"]
    denied_step = guard["step"]
    assert guard["reason"]
    assert not any(
        event.get("type") in {"tool_start", "tool_result"}
        and (event.get("data") or {}).get("step") == denied_step
        for event in events
    )


def test_no_progress_emits_evidence_update_then_explicit_evidence_gap():
    pool = EvidencePool(question_id="no-progress")

    async def retrieve(arguments: dict) -> ToolObservation:
        pool.add_retrieve([], query=arguments["query"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="empty retrieval")

    _, events = asyncio.run(
        _run_loop(
            [
                AgentDecision(
                    action="tool_call",
                    tool="retrieve_kb",
                    arguments={"query": "unknown StampServer fact"},
                    gap="缺少关键事实",
                    expected_gain="找到可引用事实",
                    source="test",
                ),
            ],
            handlers={"retrieve_kb": retrieve},
            max_steps=1,
            evidence=pool,
        ),
    )

    result_index = _event_index(
        events,
        "tool_result",
        lambda data: data.get("progress") == ToolProgressStatus.NO_PROGRESS,
    )
    update_index = _event_index(events, "evidence_update")
    gap_index = _event_index(events, "evidence_gap")
    assert result_index < update_index < gap_index
    gap = events[gap_index]["data"]
    assert gap.get("coverage")
    assert isinstance(gap.get("missing_facts"), list)


def test_budget_exhaustion_does_not_emit_forced_finalization_check():
    _, normal_events = asyncio.run(
        _run_loop(
            [AgentDecision(action="finish", source="test")],
            max_steps=1,
            terminal_finalization_v2=False,
        ),
    )
    normal_check = normal_events[_event_index(normal_events, "finalization_check")]
    assert normal_check["data"].get("forced", False) is False

    exhausted_pool = EvidencePool(question_id="budget-exhausted")

    async def retrieve(arguments: dict) -> ToolObservation:
        exhausted_pool.add_retrieve([_doc()], query=arguments["query"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="found evidence")

    result, exhausted_events = asyncio.run(
        _run_loop(
            [
                AgentDecision(
                    action="tool_call",
                    tool="retrieve_kb",
                    arguments={"query": "StampServer 端口"},
                    source="test",
                ),
            ],
            handlers={"retrieve_kb": retrieve},
            max_steps=1,
            evidence=exhausted_pool,
        ),
    )
    assert result.terminal_action == "step_budget_exhausted"
    assert result.evidence_snapshot is None
    assert not any(
        event.get("type") == "finalization_check"
        and (event.get("data") or {}).get("forced") is True
        for event in exhausted_events
    )


def test_agent_trace_contains_the_same_lifecycle_events_as_the_live_projection():
    result, events = asyncio.run(
        _run_loop(
            [AgentDecision(action="finish", source="test")],
            max_steps=1,
            terminal_finalization_v2=False,
        ),
    )

    trace_events = result.to_trace().get("lifecycle_events")
    assert isinstance(trace_events, list)
    assert trace_events == events


def test_revise_rewrite_second_pass_and_publication_are_fully_structured():
    review1 = HelperGroundingReviewResult(
        verdict="REVISE",
        coverage="PARTIAL",
        summary="remove unsupported claim",
        claim_reviews=[
            _claim("c1", evidence_ids=(1,), status="supported"),
            _claim("c2", evidence_ids=(), status="unsupported"),
        ],
        rewrite_actions=[
            RewriteAction("c1", "preserve", "保留已支持断言"),
            RewriteAction("c2", "remove", "删除未支持断言"),
        ],
    )
    review2 = HelperGroundingReviewResult(
        verdict="PASS",
        coverage="FULL",
        summary="rewritten candidate passed",
        claim_reviews=[_claim("c1", evidence_ids=(1,), status="supported")],
        rewrite_actions=[],
    )
    reviews = iter([review1, review2])
    lifecycle: list[dict] = []

    finalized = AnswerFinalizer().finalize(
        "支持在线发布 [1]，也支持未被证据支持的离线发布。",
        "StampServer 支持哪些发布模式？",
        [_doc()],
        helper_reviewer=lambda *_: next(reviews),
        retry_candidate=lambda review: "StampServer 支持在线发布。[1]",
        on_lifecycle_event=lifecycle.append,
    )

    review1_index = _event_index(
        lifecycle,
        "review_status",
        lambda data: data.get("review_count") == 1 and data.get("verdict") == "REVISE",
    )
    rewrite_started = _event_index(
        lifecycle,
        "rewrite_status",
        lambda data: data.get("status") == "started",
    )
    rewrite_completed = _event_index(
        lifecycle,
        "rewrite_status",
        lambda data: data.get("status") == "completed",
    )
    review2_index = _event_index(
        lifecycle,
        "review_status",
        lambda data: data.get("review_count") == 2 and data.get("verdict") == "PASS",
    )
    publication_index = _event_index(lifecycle, "publication")
    assert (
        review1_index
        < rewrite_started
        < rewrite_completed
        < review2_index
        < publication_index
    )

    first_review = lifecycle[review1_index]["data"]
    assert [claim["claim_id"] for claim in first_review["claim_reviews"]] == ["c1", "c2"]
    assert [claim["evidence_ids"] for claim in first_review["claim_reviews"]] == [[1], []]
    assert [action["claim_id"] for action in first_review["rewrite_actions"]] == ["c1", "c2"]
    assert all(action.get("action") for action in first_review["rewrite_actions"])
    assert lifecycle[publication_index]["data"]["final_mode"] == "grounded_partial"
    assert finalized.answer == "StampServer 支持在线发布。[1]"


@pytest.mark.parametrize(
    ("review", "expected_mode"),
    [
        (
            HelperGroundingReviewResult(
                verdict="ERROR",
                coverage="NONE",
                summary="review service timeout",
                error="reviewer_timeout",
            ),
            "reviewer_error",
        ),
        (
            HelperGroundingReviewResult(
                verdict="NO_SAFE_ANSWER",
                coverage="NONE",
                summary="no safe answer",
            ),
            "review_blocked",
        ),
    ],
)
def test_reviewer_error_and_review_blocked_emit_structured_states(review, expected_mode):
    lifecycle: list[dict] = []
    AnswerFinalizer().finalize(
        "candidate [1]",
        "question",
        [_doc()],
        helper_reviewer=lambda *_: review,
        on_lifecycle_event=lifecycle.append,
    )

    review_event = lifecycle[
        _event_index(lifecycle, "review_status", lambda data: data.get("verdict") == review.verdict)
    ]
    publication = lifecycle[
        _event_index(
            lifecycle,
            "publication",
            lambda data: data.get("final_mode") == expected_mode,
        )
    ]
    assert isinstance(review_event["data"], dict)
    assert isinstance(publication["data"], dict)
    assert publication["data"]["review_verdict"] == review.verdict
    assert publication["data"]["message"]
    if expected_mode == "reviewer_error":
        assert review_event["data"]["error"] == "reviewer_timeout"


def test_rewrite_failure_emits_failed_status_before_blocked_publication():
    review = HelperGroundingReviewResult(
        verdict="REVISE",
        coverage="PARTIAL",
        summary="rewrite required",
        claim_reviews=[_claim("c2", evidence_ids=(), status="unsupported")],
        rewrite_actions=[RewriteAction("c2", "remove", "删除未支持断言")],
    )
    lifecycle: list[dict] = []

    def fail_rewrite(_review):
        raise RuntimeError("rewrite backend unavailable")

    finalized = AnswerFinalizer().finalize(
        "unsupported candidate",
        "question",
        [_doc()],
        helper_reviewer=lambda *_: review,
        retry_candidate=fail_rewrite,
        on_lifecycle_event=lifecycle.append,
    )

    failure_index = _event_index(
        lifecycle,
        "rewrite_status",
        lambda data: data.get("status") == "failed",
    )
    publication_index = _event_index(
        lifecycle,
        "publication",
        lambda data: data.get("final_mode") == "review_blocked",
    )
    failure = lifecycle[failure_index]["data"]
    assert failure_index < publication_index
    assert failure.get("error")
    assert failure.get("message")
    assert finalized.answer == REVIEW_BLOCKED_ANSWER


class _TraceStub:
    def __init__(self) -> None:
        self.stages_ms = {}
        self.agent = None

    def set_understanding(self, _value) -> None:
        pass

    def set_clarify(self, _value) -> None:
        pass

    def set_plan(self, _value) -> None:
        pass

    def set_agent(self, value) -> None:
        self.agent = value

    def set_pack(self, _value) -> None:
        pass

    def mark(self, _stage: str) -> None:
        pass


@pytest.mark.parametrize("agent_enabled", [False, True])
def test_public_stream_query_keeps_heartbeat_in_both_modes(agent_enabled):
    chain = object.__new__(RagChain)
    chain._cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(
            heartbeat_initial_delay=0.05,
            heartbeat_interval=0.2,
        ),
    )

    async def delayed_events(*_args, **kwargs):
        assert kwargs["agent_orchestration_enabled"] is agent_enabled
        await asyncio.sleep(0.08)
        yield {"type": "final_answer", "data": "ok"}
        yield {"type": "done"}

    chain._stream_query_events = delayed_events

    async def collect() -> list[dict]:
        return [
            event
            async for event in chain.stream_query(
                "你好",
                pipeline_events=True,
                agent_orchestration_enabled=agent_enabled,
            )
        ]

    events = asyncio.run(collect())
    assert events[0] == {"type": "heartbeat", "phase": "thinking"}
    assert events[-2] == {"type": "final_answer", "data": "ok"}
    assert events[-1] == {"type": "done"}


def test_backend_agent_and_linear_modes_isolate_pipeline_events():
    def make_chain() -> RagChain:
        chain = object.__new__(RagChain)
        chain._cfg = SimpleNamespace()
        chain._llm_model = "fixture-model"
        chain._agent_orchestration_enabled = lambda override: bool(override)
        chain._new_qa_trace = lambda *_args, **_kwargs: _TraceStub()
        chain._record_execution_event = lambda *_args, **_kwargs: None
        chain._commit_qa_trace = lambda *_args, **_kwargs: None
        return chain

    async def collect(agent_enabled: bool) -> list[dict]:
        chain = make_chain()
        return [
            event
            async for event in chain._stream_query_events(
                "你好",
                pipeline_events=True,
                agent_orchestration_enabled=agent_enabled,
            )
        ]

    with patch("rag_knowledge.services.rag.runtime_fingerprint", return_value={}):
        agent_events = asyncio.run(collect(True))
        linear_events = asyncio.run(collect(False))
    agent_types = [event["type"] for event in agent_events]
    linear_types = [event["type"] for event in linear_events]

    assert "status" not in agent_types
    assert "pipeline" not in agent_types
    assert "status" in linear_types
    assert "pipeline" in linear_types
    assert agent_types[-3:] == ["final_answer", "sources", "done"]
    assert linear_types[-3:] == ["final_answer", "sources", "done"]


def test_controller_error_with_evidence_is_not_published_as_no_knowledge():
    conversation = ConversationContext.from_request("StampServer 的主要用途是什么？", [])
    evidence = EvidencePool(question_id="controller-error-stream")
    evidence.add_retrieve([_doc(content="StampServer 的部署目录为 /data/stampserver。")], query="StampServer 用途")
    result = AgentTurnResult(
        conversation=conversation,
        evidence=evidence,
        route="retrieve",
        answer_gate={
            "allow_knowledge_answer": False,
            "coverage": "PARTIAL",
            "evidence_count": 1,
            "reason": "controller_decision_error",
        },
        terminal_action="controller_error",
    )

    chain = object.__new__(RagChain)
    chain._allow_general_knowledge = False

    async def fake_run_agent_turn(*_args, **_kwargs):
        return result

    chain._run_agent_turn = fake_run_agent_turn
    chain._record_execution_event = lambda *_args, **_kwargs: None
    chain._safe_set_retrieval = lambda *_args, **_kwargs: None
    chain._freeze_generation_source_docs = lambda docs: list(docs)
    chain._commit_qa_trace = lambda *_args, **_kwargs: "controller-error-trace"

    async def collect() -> list[dict]:
        return [
            event
            async for event in chain._stream_agent_query(
                "StampServer 的主要用途是什么？",
                None,
                llm_model=None,
                kb_name=None,
                doc_category=None,
                entity_name=None,
                thinking=False,
                web_search=False,
                allow_general_knowledge=False,
                agent_prompt=None,
                pipeline_events=False,
                pinned_chunk_ids=None,
                excluded_chunk_ids=None,
                path=None,
                clarification_question=None,
                clarification_selected=None,
                trace=_TraceStub(),
            )
        ]

    events = asyncio.run(collect())
    publication = next(event for event in events if event.get("type") == "publication")
    final_answer = next(event for event in events if event.get("type") == "final_answer")

    assert publication["data"]["final_mode"] == "controller_error"
    assert publication["data"]["coverage"] == "PARTIAL"
    assert publication["data"]["evidence_count"] == 1
    assert final_answer["data"] == CONTROLLER_ERROR_ANSWER
    assert final_answer["data"] != NO_KNOWLEDGE_ANSWER


def test_controller_error_sync_result_is_not_no_knowledge():
    conversation = ConversationContext.from_request("StampServer 的主要用途是什么？", [])
    evidence = EvidencePool(question_id="controller-error-sync")
    evidence.add_retrieve([_doc(content="StampServer 的部署目录为 /data/stampserver。")], query="StampServer 用途")
    result = AgentTurnResult(
        conversation=conversation,
        evidence=evidence,
        route="retrieve",
        answer_gate={
            "allow_knowledge_answer": False,
            "coverage": "PARTIAL",
            "evidence_count": 1,
            "reason": "controller_decision_error",
        },
        terminal_action="controller_error",
    )

    chain = object.__new__(RagChain)
    chain._allow_general_knowledge = False
    chain._last_understanding = None

    async def fake_run_agent_turn(*_args, **_kwargs):
        return result

    chain._run_agent_turn = fake_run_agent_turn
    chain._safe_set_scope = lambda *_args, **_kwargs: None
    chain._safe_set_retrieval = lambda *_args, **_kwargs: None
    chain._freeze_generation_source_docs = lambda docs: list(docs)
    chain._commit_qa_trace = lambda *_args, **_kwargs: "controller-error-sync-trace"

    out = asyncio.run(
        chain._aquery_agent(
            "StampServer 的主要用途是什么？",
            None,
            llm_model=None,
            kb_name=None,
            doc_category=None,
            entity_name=None,
            thinking=False,
            web_search=False,
            allow_general_knowledge=False,
            agent_prompt=None,
            include_evidence=False,
            clarification_question=None,
            clarification_selected=None,
            trace=_TraceStub(),
        )
    )

    assert out["final_mode"] == "controller_error"
    assert out["coverage"] == "PARTIAL"
    assert out["evidence_count"] == 1
    assert out["answer"] == CONTROLLER_ERROR_ANSWER
    assert out["answer"] != NO_KNOWLEDGE_ANSWER


def test_strict_agent_stream_publishes_before_final_answer_without_token_alias():
    raw_candidate = "UNREVIEWED candidate [1]"
    reviewed_answer = "REVIEWED final answer [1]"
    conversation = ConversationContext.from_request("StampServer 的端口是多少？", [])
    evidence = EvidencePool(question_id="strict-stream")
    evidence.add_retrieve([_doc()], query="StampServer 端口")
    snapshot = evidence.create_snapshot(
        verdict={"allow_knowledge_answer": True, "coverage": "FULL"},
    )
    result = AgentTurnResult(
        conversation=conversation,
        evidence=evidence,
        route="retrieve",
        answer_gate={"allow_knowledge_answer": True},
        evidence_snapshot=snapshot,
    )

    chain = object.__new__(RagChain)
    chain._allow_general_knowledge = False
    chain._cfg = SimpleNamespace(context_budget=SimpleNamespace(context_window=2048))
    chain._ollama_base = "http://unused.test"

    async def fake_run_agent_turn(*_args, **kwargs):
        on_event = kwargs.get("on_event")
        if on_event is not None:
            await on_event({"type": "understanding", "data": {"summary": "fixture"}})
        return result

    chain._run_agent_turn = fake_run_agent_turn
    chain._safe_set_retrieval = lambda *_args, **_kwargs: None
    chain._format_context = lambda _docs: "evidence context"
    chain._pack_for_generation = lambda docs, context, history, *_args, **_kwargs: SimpleNamespace(
        source_docs=docs,
        context=context,
        history=history or [],
        history_summary=None,
        decision={},
    )
    chain._build_messages = lambda *_args, **_kwargs: [{"role": "user", "content": "q"}]
    chain._apply_vram_guard = lambda model: (model or "fixture-model", False)
    chain._need_ollama_thinking = lambda _model: False
    chain._resolve_llm_endpoint = lambda _model: ModelEndpoint(
        role="llm",
        provider="ollama",
        model="fixture-model",
        base_url="http://unused.test",
    )
    chain._helper_grounding_reviewer = lambda **_kwargs: object()
    chain._retry_grounded_candidate = lambda *_args, **_kwargs: reviewed_answer
    chain._safe_set_grounding = lambda *_args, **_kwargs: None
    chain._filter_cited_sources = lambda _answer, docs: docs
    chain._commit_qa_trace = lambda *_args, **_kwargs: "fixture-trace"

    async def fake_stream_parts(*_args, **_kwargs):
        yield LLMStreamPart("content", raw_candidate)

    def fake_finalize(candidate, *_args, **kwargs):
        assert candidate == raw_candidate
        kwargs["on_lifecycle_event"](
            {
                "type": "publication",
                "data": {
                    "final_mode": "generated",
                    "review_verdict": "PASS",
                    "coverage": "FULL",
                    "message": "review passed",
                },
            },
        )
        return FinalizedAnswer(
            answer=reviewed_answer,
            grounding={"final_mode": "generated", "review_verdict": "PASS"},
        )

    async def collect() -> list[dict]:
        return [
            event
            async for event in chain._stream_agent_query(
                "StampServer 的端口是多少？",
                None,
                llm_model=None,
                kb_name=None,
                doc_category=None,
                entity_name=None,
                thinking=False,
                web_search=False,
                allow_general_knowledge=False,
                agent_prompt=None,
                pipeline_events=False,
                pinned_chunk_ids=None,
                excluded_chunk_ids=None,
                path=None,
                clarification_question=None,
                clarification_selected=None,
                trace=_TraceStub(),
            )
        ]

    with (
        patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream_parts),
        patch("rag_knowledge.services.rag._ANSWER_FINALIZER.finalize", fake_finalize),
    ):
        events = asyncio.run(collect())

    publication_index = _event_index(events, "publication")
    final_index = _event_index(events, "final_answer")
    assert publication_index < final_index
    assert events[final_index]["data"] == reviewed_answer
    assert raw_candidate not in [
        event.get("data") for event in events if event.get("type") == "final_answer"
    ]
    assert reviewed_answer not in [
        event.get("data") for event in events if event.get("type") == "token"
    ]


def test_linear_stream_emits_no_llm_reasoning_events():
    """Linear / standard pipeline stream must NEVER emit llm_reasoning_* events."""
    chain = object.__new__(RagChain)
    chain._cfg = SimpleNamespace(
        grounding_strict_mode=True,
        context_budget=SimpleNamespace(context_window=4096),
        ollama_base_url="http://localhost:11434",
        retrieval_strategy="hybrid",
        llm_model="qwen3.5:9b",
        helper_llm_model="qwen3.5:4b",
        endpoint_for=lambda role: ModelEndpoint(role=role, provider="ollama", model="qwen3.5:9b", base_url="http://localhost:11434"),
    )
    chain._llm_model = "qwen3.5:9b"
    chain._allow_general_knowledge = False
    chain._agent_orchestration_enabled = lambda override: False
    chain._new_qa_trace = lambda *_args, **_kwargs: _TraceStub()
    chain._record_execution_event = lambda *_args, **_kwargs: None
    chain._commit_qa_trace = lambda *_args, **_kwargs: "test-trace"
    chain._com_phase0_reject_if_needed = lambda *_args, **_kwargs: None
    chain._j3_clarify_reject_if_needed = lambda *_args, **_kwargs: None
    chain._safe_linear_identity_binding = lambda q, **kwargs: (q, None, None)
    chain._build_retrieval_query_specs = lambda q, h: [{"text": q, "kind": "original", "weight": 1.0}]
    chain._resolve_canonical_scope = lambda *_args, **_kwargs: SimpleNamespace(canonical_entity=None, explicit_selection=False, scope_id="")
    chain._safe_set_scope = lambda *_args, **_kwargs: None
    chain._prepare_graph_plan = lambda *_args, **_kwargs: (SimpleNamespace(queries=[{"text": "StampServer", "weight": 1.0}], enable_rerank=False, top_k=1, candidate_k=1, expand_neighbors=False, linked_entities=(), job=""), None, [])
    chain._build_trace_clarify = lambda *_args, **_kwargs: {}
    chain._effective_backbone_from_scope = lambda *_args, **_kwargs: None
    chain._anchor_protect_names = lambda *_args, **_kwargs: set()
    chain._apply_pinned_excluded = lambda docs, **kwargs: docs
    chain._admit_source_docs_by_scope = lambda docs, scope: docs
    chain._record_chunk_hit_query = lambda docs: None
    chain._format_context = lambda docs: "StampServer 端口 8080"
    chain._safe_set_retrieval = lambda *_args, **_kwargs: None
    chain._safe_set_grounding = lambda *_args, **_kwargs: None
    chain._pack_for_generation = lambda docs, ctx, hist, q, **kw: SimpleNamespace(source_docs=docs, history=[], history_summary="", decision="pack")
    chain._freeze_generation_source_docs = lambda docs: docs
    chain._build_messages = lambda *args, **kwargs: [{"role": "user", "content": "StampServer 端口？"}]
    chain._apply_vram_guard = lambda model: (model or "qwen3.5:9b", False)
    chain._need_ollama_thinking = lambda model: False
    chain._resolve_llm_endpoint = lambda model: ModelEndpoint(role="llm", provider="ollama", model="qwen3.5:9b", base_url="http://localhost:11434")
    chain._filter_cited_sources = lambda ans, docs: docs
    chain._extract_inline_citations = lambda ans: []
    chain._get_understanding_service = lambda: SimpleNamespace(
        analyze=lambda *args, **kwargs: SimpleNamespace(
            mode="retrieve",
            user_utterance="StampServer 端口？",
            resolved_question="StampServer 端口？",
            retrieval_queries=[{"text": "StampServer 端口？", "kind": "original", "weight": 1.0}],
            filters={},
            clarification=None,
            search_intent="factual",
            route_reason="test",
            entity_name=None,
            doc_category=None,
            kb_name=None,
        )
    )

    async def fake_chat_stream(*args, **kwargs):
        yield "StampServer 的端口是 8080。"

    async def collect() -> list[dict]:
        return [
            event
            async for event in chain._stream_query_events(
                "StampServer 端口？",
                history=[],
                agent_orchestration_enabled=False,
                pipeline_events=False,
            )
        ]

    with (
        patch("rag_knowledge.services.rag.runtime_fingerprint", return_value={}),
        patch.object(chain, "_understand_for_retrieval", return_value=SimpleNamespace(
            mode="retrieve",
            user_utterance="StampServer 端口？",
            resolved_question="StampServer 端口？",
            retrieval_queries=[{"text": "StampServer 端口？", "kind": "original", "weight": 1.0}],
            filters={},
            clarification=None,
            search_intent="factual",
            route_reason="test",
            entity_name=None,
            doc_category=None,
            kb_name=None,
        )),
        patch.object(chain, "_retrieve_multi", return_value=([_doc("c1", "StampServer 端口 8080。")], "StampServer 端口 8080")),
        patch("rag_knowledge.llm_http.achat_stream", fake_chat_stream),
        patch("rag_knowledge.services.rag._ANSWER_FINALIZER.finalize", return_value=FinalizedAnswer(
            answer="StampServer 的端口是 8080。",
            grounding={"final_mode": "generated", "review_verdict": "PASS"},
        )),
    ):
        events = asyncio.run(collect())

    event_types = [event.get("type") for event in events]
    assert "final_answer" in event_types
    assert "sources" in event_types
    assert "llm_reasoning_start" not in event_types
    assert "llm_reasoning_delta" not in event_types
    assert "llm_reasoning_end" not in event_types


def test_main_agent_reasoning_prompts_require_simplified_chinese_and_preserve_protocol_terms():
    """Only Main Agent reasoning is user-visible, so Main prompts must request Chinese."""
    from rag_knowledge.services.agent_orchestration.runtime import _AGENT_SYSTEM_PROMPT, _DECISION_PROMPT

    for prompt in (_DECISION_PROMPT, _AGENT_SYSTEM_PROMPT):
        assert "reasoning/thinking channel" in prompt
        assert "简体中文" in prompt

    assert "JSON 字段名" in _DECISION_PROMPT
    assert "工具名" in _DECISION_PROMPT


def test_controller_prompt_does_not_reclarify_an_already_confirmed_entity():
    """A short/ambiguous raw word must not override an explicit entity binding."""
    from rag_knowledge.services.agent_orchestration.runtime import _DECISION_PROMPT

    assert "不得仅因用户原始词较短、泛化、存在拼写近似" in _DECISION_PROMPT
    assert "EvidencePool 为空时优先围绕已确认实体做首次 retrieve_kb" in _DECISION_PROMPT
    assert "ControllerState 是权威状态" in _DECISION_PROMPT
    assert "allowed_tools" in _DECISION_PROMPT
    assert "用户问题：pipeline" in _DECISION_PROMPT
    assert "当前主体身份为 PipelineWebGL" in _DECISION_PROMPT
    assert '"tool":"retrieve_kb"' in _DECISION_PROMPT
    assert '"target_entity":"PipelineWebGL"' in _DECISION_PROMPT


def test_controller_state_removes_reclarify_after_entity_confirmation():
    conversation = ConversationContext.from_request("pipeline", [])
    conversation.identity_status = "confirmed_entity"
    conversation.confirmed_entity = "PipelineWebGL"
    conversation.head_entity = "PipelineWebGL"
    if getattr(conversation, "scope", None):
        import dataclasses
        conversation.scope = dataclasses.replace(
            conversation.scope,
            confirmed_entity="PipelineWebGL",
            identity_status="confirmed_entity"
        )
    evidence = EvidencePool(question_id="controller-state-confirmed")
    loop = AgentLoop(
        conversation=conversation,
        evidence=evidence,
        budget=AgentBudget(max_steps=3, max_retrieve_attempts=2),
        registry=build_agent_registry(),
        handlers={},
    )

    state = json.loads(loop._controller_state_for_prompt())

    assert state["identity_status"] == "confirmed_entity"
    assert state["confirmed_entity"] == "PipelineWebGL"
    assert "clarify" not in state["allowed_tools"]
    assert "retrieve_kb" in state["allowed_tools"]
    assert state["retrieval_allowed"] is True


def test_controller_state_blocks_graph_link_for_unconfirmed_identity():
    conversation = ConversationContext.from_request("pipeline", [])
    evidence = EvidencePool(question_id="controller-state-unresolved")
    loop = AgentLoop(
        conversation=conversation,
        evidence=evidence,
        budget=AgentBudget(max_steps=3, max_retrieve_attempts=2),
        registry=build_agent_registry(),
        handlers={},
    )

    state = json.loads(loop._controller_state_for_prompt())

    assert state["identity_status"] == "unresolved"
    assert "link_entities" not in state["allowed_tools"]
    assert "clarify" in state["allowed_tools"]


def test_controller_empty_content_after_reasoning_triggers_repair_or_fallback():
    """When controller outputs reasoning but zero content (e.g. token limit), it must report diagnosis and attempt repair."""
    context = ConversationContext.from_request("StampServer 端口？", [])
    pool = EvidencePool(question_id="test-empty-reasoning")

    async def fake_stream_parts(*args, **kwargs):
        # Model emits reasoning but exhausts tokens before emitting JSON content
        yield LLMStreamPart("reasoning", "Thinking about StampServer port...")

    def fake_repair_chat(*args, **kwargs):
        return json.dumps({
            "action": "finalize",
            "thought": "Repaired after empty response",
            "answer_mode": "full",
        })

    cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(
            terminal_finalization_v2=True,
        ),
        ollama_base_url="http://localhost:11434",
        context_budget=SimpleNamespace(context_window=4096),
        endpoint_for=lambda role: ModelEndpoint(role=role, provider="ollama", model="qwen3.5:9b", base_url="http://localhost:11434"),
    )

    loop = AgentLoop(
        conversation=context,
        evidence=pool,
        budget=AgentBudget(max_steps=3),
        registry=build_agent_registry(),
        handlers={},
        cfg=cfg,
    )

    reasoning_events = []
    async def on_event(evt):
        if evt.get("type", "").startswith("llm_reasoning_"):
            reasoning_events.append(evt)

    with (
        patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream_parts),
        patch("rag_knowledge.llm_http.chat_role", fake_repair_chat),
    ):
        decision = asyncio.run(loop._adecide_via_llm(on_event, step_index=1))

    assert decision.action == "finalize"
    assert len(reasoning_events) >= 2
    assert reasoning_events[0]["type"] == "llm_reasoning_start"
    assert reasoning_events[-1]["type"] == "llm_reasoning_end"
    end_data = reasoning_events[-1]["data"]
    assert end_data["reasoning_available"] is True
    assert end_data["content_chars"] == 0
    assert end_data["num_predict"] == 8192

    # Verify protocol attempts record the empty content diagnosis
    assert len(loop._controller_protocol_attempts) >= 1
    assert "controller_output_empty_after_reasoning" in str(loop._controller_protocol_attempts[0].get("error"))


def test_reviewer_empty_content_after_reasoning_fails_closed():
    """When reviewer outputs reasoning but zero content, it must fail-closed with error."""
    chain = object.__new__(RagChain)
    chain._cfg = SimpleNamespace(
        grounding_reviewer_timeout=30.0,
        ollama_base_url="http://localhost:11434",
        context_budget=SimpleNamespace(context_window=4096),
        endpoint_for=lambda role: ModelEndpoint(role=role, provider="ollama", model="qwen3.5:4b", base_url="http://localhost:11434"),
    )

    async def fake_stream_parts(*args, **kwargs):
        yield LLMStreamPart("reasoning", "Reviewing claim against evidence...")

    reasoning_events = []
    def on_reasoning(evt):
        reasoning_events.append(evt)

    with (
        patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream_parts),
        patch("rag_knowledge.llm_http.chat_role", lambda *args, **kwargs: ""),
    ):
        reviewer = chain._helper_grounding_reviewer(on_reasoning_event=on_reasoning)
        assert reviewer is not None
        result = reviewer.review(
            question="StampServer 端口？",
            context_docs=[_doc("c1", "StampServer 端口 8080。")],
            candidate="StampServer 端口是 8080。",
        )

    assert result.verdict == "ERROR"
    assert result.coverage == "NONE"
    assert len(reasoning_events) >= 2
    end_data = reasoning_events[-1]["data"]
    assert end_data["reasoning_available"] is True
    assert end_data["content_chars"] == 0
    assert end_data["num_predict"] == 12288
