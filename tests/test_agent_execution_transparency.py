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
    AnswerGenerationContext,
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


def _doc(
    chunk_id: str = "c1",
    content: str = "StampServer 的端口是 8080。",
    evidence_class: str = "TARGET_DIRECT",
    support_scope: str = "TARGET_SPECIFIC",
) -> dict:
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
            "evidence_class": evidence_class,
            "support_scope": support_scope,
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
        pool.add_retrieve([_doc()], query=arguments["search_focus_text"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="found one chunk")

    _, events = asyncio.run(
        _run_loop(
            [
                AgentDecision(
                    action="tool_call",
                    tool="retrieve_kb",
                    arguments={"search_focus_text": "StampServer 端口"},
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
        _event_index(events, "public_explanation"),
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
        yield LLMStreamPart("content", '{"action":"tool_call","tool":"compose_answer","arguments":{},"reason":"结束本轮"}')

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
        yield LLMStreamPart("content", '{"action":"tool_call","tool":"compose_answer","arguments":{},"reason":"结束本轮"}')

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
    assert events[start_index]["data"]["reasoning_requested"] is True
    assert events[end_index]["data"]["reasoning_requested"] is True
    assert events[end_index]["data"]["reasoning_available"] is True
    assert events[decision_index]["data"]["reason"] == "结束本轮"
    # Sub-PRD 01: When native reasoning is present, do NOT emit public_explanation
    assert "public_explanation" not in [e["type"] for e in events]


def test_controller_fallback_public_explanation_when_model_unsupported():
    # Case B: Model does not support reasoning (reasoning_requested=False, reasoning_available=False)
    conversation = ConversationContext.from_request("StampServer 的端口是多少？", [])
    evidence = EvidencePool(question_id="controller-unsupported-reasoning")
    endpoint = ModelEndpoint(
        role="llm",
        provider="ollama",
        model="qwen2.5:7b",
        base_url="http://unused.test",
    )
    cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(terminal_finalization_v2=False, reasoning_stream_policy="token"),
        context_budget=SimpleNamespace(context_window=32768),
        ollama_base_url="http://unused.test",
        model_routing=None,
        endpoint_for=lambda _role: endpoint,
    )
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    async def fake_stream_parts(*_args, **_kwargs):
        yield LLMStreamPart("content", '{"action":"tool_call","tool":"compose_answer","arguments":{},"reason":"结束本轮"}')

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
    end_index = _event_index(events, "llm_reasoning_end")
    decision_index = _event_index(events, "decision")
    explanation_index = _event_index(events, "public_explanation")
    assert start_index < end_index < decision_index < explanation_index
    assert events[start_index]["data"]["reasoning_requested"] is False
    assert events[end_index]["data"]["reasoning_requested"] is False
    assert events[end_index]["data"]["reasoning_available"] is False
    assert events[explanation_index]["data"]["source"] == "system_fallback"
    assert events[explanation_index]["data"]["fallback_used"] is True
    assert "正在根据当前问题" in events[explanation_index]["data"]["text"]


def test_controller_fallback_public_explanation_when_reasoning_requested_but_no_output():
    # Case C: Model supports reasoning, requested=True, but 0 reasoning tokens produced
    conversation = ConversationContext.from_request("StampServer 的端口是多少？", [])
    evidence = EvidencePool(question_id="controller-case-c-reasoning")
    endpoint = ModelEndpoint(
        role="llm",
        provider="ollama",
        model="qwen3.5:9b",
        base_url="http://unused.test",
    )
    cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(terminal_finalization_v2=False, reasoning_stream_policy="token"),
        context_budget=SimpleNamespace(context_window=32768),
        ollama_base_url="http://unused.test",
        model_routing=None,
        endpoint_for=lambda _role: endpoint,
    )
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    async def fake_stream_parts(*_args, **_kwargs):
        yield LLMStreamPart("content", '{"action":"tool_call","tool":"compose_answer","arguments":{},"reason":"结束本轮"}')

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
    end_index = _event_index(events, "llm_reasoning_end")
    decision_index = _event_index(events, "decision")
    explanation_index = _event_index(events, "public_explanation")
    assert start_index < end_index < decision_index < explanation_index
    assert events[start_index]["data"]["reasoning_requested"] is True
    assert events[end_index]["data"]["reasoning_requested"] is True
    assert events[end_index]["data"]["reasoning_available"] is False
    assert events[explanation_index]["data"]["source"] == "system_fallback"
    assert events[explanation_index]["data"]["fallback_used"] is True
    assert "正在根据当前问题" in events[explanation_index]["data"]["text"]


def test_helper_reviewer_uses_structured_output_without_free_reasoning():
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
            "repair_mode": "NONE",
            "summary": "证据只覆盖部分问题",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "StampServer 的端口是 8080",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "evidence_ids": [1],
            "status": "supported",
            "reason": "证据直接支持",
        }],
        "rewrite_actions": [],
    }, ensure_ascii=False)

    calls = []

    def fake_chat_role(*_args, **kwargs):
        calls.append(kwargs)
        return response

    with patch("rag_knowledge.llm_http.chat_role", fake_chat_role):
        reviewer = chain._helper_grounding_reviewer(
            on_reasoning_event=events.append,
            reasoning_enabled=True,
        )
        result = reviewer.review("StampServer 的端口是多少？", [_doc()], "StampServer 的端口是 8080。[1]")

    assert result.verdict == "PASS"
    assert result.coverage == "PARTIAL"
    assert events == []
    assert calls[0]["think"] is False
    assert calls[0]["format_json"] is True
    assert calls[0]["json_schema"]


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
    # Sub-PRD 01: When native reasoning is present, no public_explanation
    assert [event["type"] for event in events] == [
        "llm_reasoning_start",
        "llm_reasoning_delta",
        "llm_reasoning_end",
    ]
    assert events[0]["data"]["stage"] == "grounded_retry"
    assert events[0]["data"]["reasoning_requested"] is True
    assert events[1]["data"]["role"] == "main"
    assert events[1]["data"]["stage"] == "grounded_retry"
    assert events[1]["data"]["delta"] == "只保留 c1，删除 c2。"
    assert events[-1]["data"]["reasoning_requested"] is True
    assert events[-1]["data"]["reasoning_available"] is True
    assert events[-1]["data"]["num_predict"] == 8192


def test_grounded_rewrite_fallback_public_explanation_when_no_native_reasoning():
    chain = object.__new__(RagChain)
    endpoint = ModelEndpoint(
        role="llm",
        provider="ollama",
        model="qwen2.5:7b",
        base_url="http://unused.test",
    )
    chain._cfg = SimpleNamespace(context_budget=SimpleNamespace(context_window=32768))
    chain._ollama_base = "http://unused.test"
    chain._resolve_llm_endpoint = lambda _model: endpoint
    chain._need_ollama_thinking = lambda _model: False
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
        yield LLMStreamPart("content", "StampServer 的端口是 8080。[1]")

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream_parts):
        candidate_v2 = chain._retry_grounded_candidate(
            "qwen2.5:7b",
            "StampServer 的端口是多少？",
            "StampServer 的端口是 8080。[1] 另有未支持断言。",
            [_doc()],
            review,
            on_reasoning_event=events.append,
        )

    assert candidate_v2 == "StampServer 的端口是 8080。[1]"
    assert [event["type"] for event in events] == [
        "llm_reasoning_start",
        "llm_reasoning_end",
        "public_explanation",
    ]
    assert events[-1]["data"]["source"] == "system_fallback"


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
        pool.add_retrieve([], query=arguments["search_focus_text"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="empty retrieval")

    _, events = asyncio.run(
        _run_loop(
            [
                AgentDecision(
                    action="tool_call",
                    tool="retrieve_kb",
                    arguments={"search_focus_text": "unknown StampServer fact"},
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
    exhausted_pool = EvidencePool(question_id="budget-exhausted")

    async def retrieve(arguments: dict) -> ToolObservation:
        exhausted_pool.add_retrieve([_doc()], query=arguments["search_focus_text"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="found evidence")

    result, exhausted_events = asyncio.run(
        _run_loop(
            [
                AgentDecision(
                    action="tool_call",
                    tool="retrieve_kb",
                    arguments={"search_focus_text": "StampServer 端口"},
                    source="test",
                ),
            ],
            handlers={"retrieve_kb": retrieve},
            max_steps=1,
            evidence=exhausted_pool,
        ),
    )
    assert result.terminal_action == "step_budget_exhausted"
    trace = result.to_trace()
    assert trace["execution_stop_reason"] == "step_budget_exhausted"
    assert trace["terminal_outcome"] == "NO_SAFE_ANSWER"
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
            repair_mode="REWRITE",
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
            repair_mode="REWRITE",
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


def _direct_state_result(
    candidate: str,
    *,
    docs: list[dict] | None = None,
    question_id: str,
    snapshot_version: int = 1,
) -> AgentTurnResult:
    conversation = ConversationContext.from_request("你刚才为什么反问我？", [])
    evidence = EvidencePool(question_id=question_id, snapshot_version=snapshot_version)
    if docs:
        evidence.add_retrieve(list(docs), query="reviewer resume evidence")
    snapshot = evidence.create_snapshot(
        verdict={"verdict": "PARTIAL" if docs else "NONE", "coverage": "PARTIAL" if docs else "NONE"},
    )
    return AgentTurnResult(
        conversation=conversation,
        evidence=evidence,
        terminal_action="controller_direct_candidate",
        evidence_snapshot=snapshot,
        direct_candidate=candidate,
        budget={
            "steps_used": 0,
            "max_steps": 4,
            "remaining_retrieve_attempts": 2,
        },
        retrieval_trace={"fixture": question_id},
    )


def _compose_state_result(
    *,
    docs: list[dict],
    question_id: str,
    snapshot_version: int = 2,
) -> AgentTurnResult:
    conversation = ConversationContext.from_request("你刚才为什么反问我？", [])
    evidence = EvidencePool(question_id=question_id, snapshot_version=snapshot_version)
    evidence.add_retrieve(list(docs), query="reviewer resume evidence")
    snapshot = evidence.create_snapshot(
        verdict={"verdict": "PARTIAL", "coverage": "PARTIAL"},
    )
    answer_context = AnswerGenerationContext.from_snapshot(
        original_question=conversation.user_question,
        resolved_question=conversation.resolved_question,
        conversation_context="reviewer resume",
        snapshot=snapshot,
        answer_contract={"answer_mode": "full"},
    )
    return AgentTurnResult(
        conversation=conversation,
        evidence=evidence,
        terminal_action="controller_compose_answer",
        evidence_snapshot=snapshot,
        answer_context=answer_context,
        budget={
            "steps_used": 1,
            "max_steps": 4,
            "remaining_retrieve_attempts": 1,
        },
        retrieval_trace={"fixture": question_id},
    )


def _retrieval_pending_finalized() -> FinalizedAnswer:
    return FinalizedAnswer(
        answer=REVIEW_BLOCKED_ANSWER,
        grounding={
            "review_verdict": "REVISE",
            "repair_mode": "RETRIEVE",
            "coverage": "PARTIAL",
            "final_mode": "retrieval_pending",
            "publication_state": "retrieval_pending",
            "details": {"claim_reviews": [], "rewrite_actions": []},
            "retrieval_feedback": {
                "gap_id": "missing-runtime-fact",
                "affected_claim_ids": ["c1"],
                "missing_fact": "本轮行为的可核验事实",
                "subject_entity_ids": [],
                "deficiency_type": "CONTEXTUAL_MISSING",
                "reason": "当前冻结快照缺少该事实",
            },
        },
    )


def _rewrite_pending_finalized() -> FinalizedAnswer:
    return FinalizedAnswer(
        answer=REVIEW_BLOCKED_ANSWER,
        grounding={
            "review_verdict": "REVISE",
            "repair_mode": "REWRITE",
            "coverage": "FULL",
            "final_mode": "review_blocked",
            "publication_state": "no_safe_answer",
            "details": {
                "claim_reviews": [{"claim_id": "c1", "status": "unsupported"}],
                "rewrite_actions": [{
                    "claim_id": "c1",
                    "action": "rewrite_to_supported_scope_or_remove",
                    "instruction": "删除未经当前上下文支持的归因。",
                }],
            },
        },
    )


def _pass_finalized(answer: str) -> FinalizedAnswer:
    return FinalizedAnswer(
        answer=answer,
        grounding={
            "review_verdict": "PASS",
            "repair_mode": "NONE",
            "coverage": "FULL",
            "final_mode": "generated",
            "publication_state": "published",
        },
    )


def _direct_state_chain() -> RagChain:
    chain = object.__new__(RagChain)
    chain._cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(
            reasoning_stream_policy="token",
            trace_reasoning_max_chars=2000,
        ),
        context_budget=SimpleNamespace(context_window=4096),
    )
    chain._allow_general_knowledge = False
    chain._last_understanding = None
    chain._ollama_base = "http://unused.test"
    chain._record_execution_event = lambda *_args, **_kwargs: None
    chain._safe_set_scope = lambda *_args, **_kwargs: None
    chain._safe_set_retrieval = lambda *_args, **_kwargs: None
    chain._safe_set_grounding = lambda *_args, **_kwargs: None
    chain._safe_add_trace_event = lambda *_args, **_kwargs: None
    chain._commit_qa_trace = lambda *_args, **_kwargs: None
    chain._filter_cited_sources = lambda _answer, docs: list(docs)
    chain._format_context = lambda docs: "\n".join(str(doc.get("content") or "") for doc in docs)
    chain._freeze_generation_source_docs = lambda docs: list(docs)
    chain._pack_agent_answer_context = lambda _result, docs, _context, hist, _q, **_kwargs: SimpleNamespace(
        source_docs=list(docs),
        history=list(hist or []),
        history_summary=None,
        decision={},
    )
    chain._helper_grounding_reviewer = lambda: object()
    chain._apply_vram_guard = lambda model: (model or "fixture-model", False)
    chain._resolve_llm_endpoint = lambda _model: ModelEndpoint(
        role="llm",
        provider="ollama",
        model="fixture-model",
        base_url="http://unused.test",
    )
    chain._should_enable_main_model_thinking = lambda *_args, **_kwargs: False
    return chain


def test_stream_direct_candidate_pass_publishes_after_single_review():
    chain = _direct_state_chain()
    initial = _direct_state_result(
        "我刚才反问，是因为当前表达存在歧义。",
        question_id="direct-pass-v1",
    )
    run_calls = 0

    async def fake_run_agent_turn(*_args, **_kwargs):
        nonlocal run_calls
        run_calls += 1
        return initial

    chain._run_agent_turn = fake_run_agent_turn
    finalize_calls: list[dict] = []

    def fake_finalize(candidate, _question, context_docs, **kwargs):
        finalize_calls.append({
            "candidate": candidate,
            "docs": list(context_docs),
            "candidate_version": kwargs.get("candidate_version", 1),
        })
        return _pass_finalized(candidate)

    async def collect() -> list[dict]:
        return [
            event
            async for event in chain._stream_agent_query(
                "你刚才为什么反问我？",
                [],
                llm_model="fixture-model",
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

    with patch("rag_knowledge.services.rag._ANSWER_FINALIZER.finalize", side_effect=fake_finalize):
        events = asyncio.run(collect())

    assert run_calls == 1
    assert [call["candidate_version"] for call in finalize_calls] == [1]
    assert [event for event in events if event["type"] == "final_answer"][-1]["data"] == initial.direct_candidate


def test_stream_direct_candidate_rewrite_returns_to_main_then_second_review_without_answer_generator():
    chain = _direct_state_chain()
    docs = [_doc(chunk_id="rewrite-context", content="本轮确实触发了澄清判断。")]
    initial = _direct_state_result(
        "我刚才已经向你弹出了澄清卡。",
        docs=docs,
        question_id="direct-rewrite-v1",
    )
    resumed = _direct_state_result(
        "我刚才尝试进入澄清流程，但这里不能据此断言卡片已经实际弹出。",
        docs=docs,
        question_id="direct-rewrite-v2",
        snapshot_version=2,
    )
    run_calls: list[dict] = []

    async def fake_run_agent_turn(*_args, **kwargs):
        run_calls.append(dict(kwargs))
        if len(run_calls) == 1:
            return initial
        assert kwargs.get("reviewer_finding") is not None
        assert kwargs.get("reviewer_feedback") is None
        return resumed

    chain._run_agent_turn = fake_run_agent_turn
    finalize_calls: list[dict] = []

    def fake_finalize(candidate, _question, context_docs, **kwargs):
        finalize_calls.append({
            "candidate": candidate,
            "docs": list(context_docs),
            "candidate_version": kwargs.get("candidate_version", 1),
        })
        return _rewrite_pending_finalized() if len(finalize_calls) == 1 else _pass_finalized(candidate)

    chain._resolve_llm_endpoint = lambda _model: (_ for _ in ()).throw(
        AssertionError("REWRITE direct resume must not enter Answer Generator")
    )

    async def collect() -> list[dict]:
        return [
            event
            async for event in chain._stream_agent_query(
                "你刚才为什么反问我？",
                [],
                llm_model="fixture-model",
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

    with patch("rag_knowledge.services.rag._ANSWER_FINALIZER.finalize", side_effect=fake_finalize):
        events = asyncio.run(collect())

    assert len(run_calls) == 2
    assert [call["candidate_version"] for call in finalize_calls] == [1, 2]
    assert [call["candidate"] for call in finalize_calls] == [initial.direct_candidate, resumed.direct_candidate]
    assert [event for event in events if event["type"] == "final_answer"][-1]["data"] == resumed.direct_candidate


def test_stream_direct_candidate_retrieve_forwards_resume_events_and_reviews_v2_snapshot():
    chain = _direct_state_chain()
    initial = _direct_state_result(
        "本轮已经完成了该事实确认。",
        question_id="direct-retrieve-v1",
    )
    v2_docs = [_doc(chunk_id="retrieved-v2", content="补检后确认：本轮只记录了澄清尝试。")]
    resumed = _direct_state_result(
        "补检记录只能确认本轮发生过澄清尝试。[1]",
        docs=v2_docs,
        question_id="direct-retrieve-v2",
        snapshot_version=2,
    )
    run_calls: list[dict] = []

    async def fake_run_agent_turn(*_args, **kwargs):
        run_calls.append(dict(kwargs))
        if len(run_calls) == 1:
            return initial
        assert kwargs.get("reviewer_feedback", {}).get("gap_id") == "missing-runtime-fact"
        assert kwargs.get("reviewer_finding") is None
        await kwargs["on_event"]({
            "type": "llm_reasoning_delta",
            "data": {
                "call_id": "reviewer_retrieve_resume_1_agent_controller_1",
                "role": "main",
                "stage": "agent_controller",
                "delta": "先根据 Reviewer 缺口补检当前事实。",
            },
        })
        return resumed

    chain._run_agent_turn = fake_run_agent_turn
    finalize_calls: list[dict] = []

    def fake_finalize(candidate, _question, context_docs, **kwargs):
        finalize_calls.append({
            "candidate": candidate,
            "docs": list(context_docs),
            "candidate_version": kwargs.get("candidate_version", 1),
        })
        return _retrieval_pending_finalized() if len(finalize_calls) == 1 else _pass_finalized(candidate)

    async def collect() -> list[dict]:
        return [
            event
            async for event in chain._stream_agent_query(
                "你刚才为什么反问我？",
                [],
                llm_model="fixture-model",
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

    with patch("rag_knowledge.services.rag._ANSWER_FINALIZER.finalize", side_effect=fake_finalize):
        events = asyncio.run(collect())

    assert len(run_calls) == 2
    assert [call["candidate_version"] for call in finalize_calls] == [1, 2]
    assert finalize_calls[0]["docs"] == []
    assert [doc["metadata"]["chunk_id"] for doc in finalize_calls[1]["docs"]] == ["retrieved-v2"]
    resume_reasoning = [
        event for event in events
        if isinstance(event.get("data"), dict)
        and event["data"].get("call_id") == "reviewer_retrieve_resume_1_agent_controller_1"
    ]
    assert [event["type"] for event in resume_reasoning] == ["llm_reasoning_delta"]
    assert [event for event in events if event["type"] == "final_answer"][-1]["data"] == resumed.direct_candidate


def test_stream_direct_candidate_retrieve_can_handoff_to_compose_answer_v2():
    chain = _direct_state_chain()
    initial = _direct_state_result(
        "这个事实已经可以直接确认。",
        question_id="direct-retrieve-compose-v1",
    )
    v2_docs = [_doc(chunk_id="compose-v2", content="补检后的正式证据。[1]")]
    resumed = _compose_state_result(
        docs=v2_docs,
        question_id="direct-retrieve-compose-v2",
        snapshot_version=2,
    )
    run_calls: list[dict] = []

    async def fake_run_agent_turn(*_args, **kwargs):
        run_calls.append(dict(kwargs))
        return initial if len(run_calls) == 1 else resumed

    chain._run_agent_turn = fake_run_agent_turn
    finalize_calls: list[dict] = []

    def fake_finalize(candidate, _question, context_docs, **kwargs):
        finalize_calls.append({
            "candidate": candidate,
            "docs": list(context_docs),
            "candidate_version": kwargs.get("candidate_version", 1),
        })
        return _retrieval_pending_finalized() if len(finalize_calls) == 1 else _pass_finalized(candidate)

    stream_call_ids: list[str] = []

    async def fake_arun(_self, options, *, on_event):
        stream_call_ids.append(options.call_id)
        await on_event({
            "type": "llm_reasoning_delta",
            "data": {
                "call_id": options.call_id,
                "role": "main",
                "stage": "answer_generation",
                "delta": "根据补检后的冻结证据组织正式回答。",
            },
        })
        return SimpleNamespace(
            content="补检后的正式回答。[1]",
            reasoning_available=True,
            raw_reasoning="",
        )

    async def collect() -> list[dict]:
        return [
            event
            async for event in chain._stream_agent_query(
                "你刚才为什么反问我？",
                [],
                llm_model="fixture-model",
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
        patch("rag_knowledge.services.rag._ANSWER_FINALIZER.finalize", side_effect=fake_finalize),
        patch("rag_knowledge.services.model_stream_runner.ModelStreamRunner.arun", new=fake_arun),
    ):
        events = asyncio.run(collect())

    assert len(run_calls) == 2
    assert run_calls[1].get("reviewer_feedback", {}).get("gap_id") == "missing-runtime-fact"
    assert stream_call_ids == ["answer_generator_v2"]
    assert [call["candidate_version"] for call in finalize_calls] == [1, 2]
    assert [doc["metadata"]["chunk_id"] for doc in finalize_calls[1]["docs"]] == ["compose-v2"]
    assert [event for event in events if event["type"] == "final_answer"][-1]["data"] == "补检后的正式回答。[1]"


def test_nonstream_direct_candidate_retrieve_resumes_to_direct_v2_with_new_snapshot():
    chain = _direct_state_chain()
    initial = _direct_state_result(
        "本轮事实已经确认。",
        question_id="nonstream-retrieve-v1",
    )
    v2_docs = [_doc(chunk_id="nonstream-v2", content="补检后确认的事实。[1]")]
    resumed = _direct_state_result(
        "补检后只能确认这一项事实。[1]",
        docs=v2_docs,
        question_id="nonstream-retrieve-v2",
        snapshot_version=2,
    )
    run_calls: list[dict] = []

    async def fake_run_agent_turn(*_args, **kwargs):
        run_calls.append(dict(kwargs))
        return initial if len(run_calls) == 1 else resumed

    chain._run_agent_turn = fake_run_agent_turn
    finalize_calls: list[dict] = []

    def fake_finalize(candidate, _question, context_docs, **kwargs):
        finalize_calls.append({
            "candidate": candidate,
            "docs": list(context_docs),
            "candidate_version": kwargs.get("candidate_version", 1),
        })
        return _retrieval_pending_finalized() if len(finalize_calls) == 1 else _pass_finalized(candidate)

    with patch("rag_knowledge.services.rag._ANSWER_FINALIZER.finalize", side_effect=fake_finalize):
        output = asyncio.run(chain._aquery_agent(
            "你刚才为什么反问我？",
            [],
            llm_model="fixture-model",
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
        ))

    assert len(run_calls) == 2
    assert run_calls[1].get("reviewer_feedback", {}).get("gap_id") == "missing-runtime-fact"
    assert [call["candidate_version"] for call in finalize_calls] == [1, 2]
    assert [doc["metadata"]["chunk_id"] for doc in finalize_calls[1]["docs"]] == ["nonstream-v2"]
    assert output["answer"] == resumed.direct_candidate


def test_nonstream_direct_candidate_retrieve_can_handoff_to_compose_answer_v2():
    chain = _direct_state_chain()
    initial = _direct_state_result(
        "这个事实已经可以直接确认。",
        question_id="nonstream-compose-v1",
    )
    v2_docs = [_doc(chunk_id="nonstream-compose-v2", content="补检后的正式证据。[1]")]
    resumed = _compose_state_result(
        docs=v2_docs,
        question_id="nonstream-compose-v2",
        snapshot_version=2,
    )
    run_calls: list[dict] = []

    async def fake_run_agent_turn(*_args, **kwargs):
        run_calls.append(dict(kwargs))
        return initial if len(run_calls) == 1 else resumed

    chain._run_agent_turn = fake_run_agent_turn
    invoke_count = 0

    def invoke(_messages):
        nonlocal invoke_count
        invoke_count += 1
        return SimpleNamespace(content="非流式正式回答。[1]")

    chain._build_llm = lambda _model: SimpleNamespace(invoke=invoke)
    finalize_calls: list[dict] = []

    def fake_finalize(candidate, _question, context_docs, **kwargs):
        finalize_calls.append({
            "candidate": candidate,
            "docs": list(context_docs),
            "candidate_version": kwargs.get("candidate_version", 1),
        })
        return _retrieval_pending_finalized() if len(finalize_calls) == 1 else _pass_finalized(candidate)

    with patch("rag_knowledge.services.rag._ANSWER_FINALIZER.finalize", side_effect=fake_finalize):
        output = asyncio.run(chain._aquery_agent(
            "你刚才为什么反问我？",
            [],
            llm_model="fixture-model",
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
        ))

    assert len(run_calls) == 2
    assert run_calls[1].get("reviewer_feedback", {}).get("gap_id") == "missing-runtime-fact"
    assert invoke_count == 1
    assert [call["candidate_version"] for call in finalize_calls] == [1, 2]
    assert [doc["metadata"]["chunk_id"] for doc in finalize_calls[1]["docs"]] == ["nonstream-compose-v2"]
    assert output["answer"] == "非流式正式回答。[1]"


def test_reviewer_resume_candidate_reasoning_streams_before_candidate_finishes():
    """Resume candidates keep the Main answer stage and forward Runner events live."""
    chain = object.__new__(RagChain)
    chain._cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(max_reviewer_resume_rounds=1),
    )
    source_docs = [_doc()]
    evidence = SimpleNamespace(working_only_docs=lambda: [])
    snapshot = SimpleNamespace(snapshot_id="snapshot-2", evidence_version=2)
    resumed = SimpleNamespace(
        budget={"steps_used": 0, "max_steps": 1, "remaining_retrieve_attempts": 1},
        graph_working_set=None,
        fallbacks=[],
        evidence=evidence,
        evidence_snapshot=snapshot,
        gap_registry=None,
        continuous_no_progress_count=0,
        exploration_fuse_open=False,
        retrieval_trace=None,
        retrieve_attempts=1,
        answer_context=None,
        to_trace=lambda: {},
    )
    call_prefixes: list[str] = []

    resume_controller_complete = False

    async def fake_run_agent_turn(*_args, **kwargs):
        call_prefixes.append(kwargs["model_call_id_prefix"])
        await kwargs["on_event"]({
            "type": "llm_reasoning_start",
            "data": {
                "call_id": "reviewer_resume_1_agent_controller_1",
                "role": "main",
                "stage": "agent_controller",
            },
        })
        await kwargs["on_event"]({
            "type": "llm_reasoning_delta",
            "data": {
                "call_id": "reviewer_resume_1_agent_controller_1",
                "role": "main",
                "stage": "agent_controller",
                "delta": "继续补检端口证据。",
            },
        })
        await asyncio.sleep(0)
        await kwargs["on_event"]({
            "type": "llm_reasoning_end",
            "data": {
                "call_id": "reviewer_resume_1_agent_controller_1",
                "role": "main",
                "stage": "agent_controller",
                "reasoning_available": True,
            },
        })
        nonlocal resume_controller_complete
        resume_controller_complete = True
        return resumed

    chain._run_agent_turn = fake_run_agent_turn
    chain._agent_answer_docs = lambda _result: (source_docs, source_docs)
    chain._format_context = lambda _docs: "evidence context"
    chain._safe_set_retrieval = lambda *_args, **_kwargs: None
    chain._safe_add_trace_event = lambda *_args, **_kwargs: None
    chain._pack_agent_answer_context = lambda *_args, **_kwargs: SimpleNamespace(
        source_docs=source_docs,
        history=[],
        history_summary=None,
        decision={},
    )
    chain._freeze_generation_source_docs = lambda docs: list(docs)
    chain._build_messages = lambda *_args, **_kwargs: [{"role": "user", "content": "q"}]
    chain._helper_grounding_reviewer = lambda: object()

    candidate_complete = False

    async def generate_candidate(_msgs, *, on_event, candidate_version):
        assert candidate_version == 2
        await on_event({
            "type": "llm_reasoning_start",
            "data": {
                "call_id": "answer_generator_resume_v2",
                "role": "main",
                "stage": "answer_generation",
            },
        })
        await on_event({
            "type": "llm_reasoning_delta",
            "data": {
                "call_id": "answer_generator_resume_v2",
                "role": "main",
                "stage": "answer_generation",
                "delta": "补检后的证据可以支持端口结论。",
            },
        })
        await asyncio.sleep(0)
        await on_event({
            "type": "llm_reasoning_end",
            "data": {
                "call_id": "answer_generator_resume_v2",
                "role": "main",
                "stage": "answer_generation",
                "reasoning_available": True,
            },
        })
        nonlocal candidate_complete
        candidate_complete = True
        return "StampServer 的端口是 8080。[1]"

    initial_finalized = SimpleNamespace(retrieval_feedback={"gap_id": "port"})
    final_finalized = SimpleNamespace(retrieval_feedback=None)
    emitted: list[dict] = []
    emitted_before_candidate_complete: list[dict] = []
    emitted_before_resume_controller_complete: list[dict] = []

    async def collect():
        async for event in chain._iter_reviewer_resume_loop(
            q="StampServer 的端口是多少？",
            history=[],
            kb_name=None,
            doc_category=None,
            entity_name=None,
            web_search=False,
            pinned_chunk_ids=None,
            excluded_chunk_ids=None,
            clarification_question=None,
            clarification_selected=None,
            clarification_option_id=None,
            clarification_snapshot_id=None,
            clarification_selected_candidate=None,
            clarification_options=None,
            clarification_selection_kind=None,
            clarification_free_text=None,
            result=resumed,
            source_docs=source_docs,
            retrieved_source_docs=source_docs,
            history_summary=None,
            answer_context=None,
            context="evidence context",
            finalized=initial_finalized,
            agent_prompt=None,
            allow_general=False,
            guarded_model="fixture-model",
            generate_candidate=generate_candidate,
            forward_retry_reasoning=True,
            emit_candidate_status=True,
            re_finalize_when_empty=True,
            state={},
            trace=_TraceStub(),
        ):
            emitted.append(event)
            if not candidate_complete:
                emitted_before_candidate_complete.append(event)
            if not resume_controller_complete:
                emitted_before_resume_controller_complete.append(event)

    with patch(
        "rag_knowledge.services.rag._ANSWER_FINALIZER.finalize",
        return_value=final_finalized,
    ):
        asyncio.run(collect())

    assert call_prefixes == ["reviewer_resume_1"]
    candidate_streamed = [
        event for event in emitted
        if event["data"].get("call_id") == "answer_generator_resume_v2"
    ]
    assert [event["type"] for event in candidate_streamed] == [
        "llm_reasoning_start",
        "llm_reasoning_delta",
        "llm_reasoning_end",
    ]
    assert all(event["data"]["stage"] == "answer_generation" for event in candidate_streamed)
    controller_streamed = [
        event for event in emitted
        if event["data"].get("call_id") == "reviewer_resume_1_agent_controller_1"
    ]
    assert [event["type"] for event in controller_streamed] == [
        "llm_reasoning_start",
        "llm_reasoning_delta",
        "llm_reasoning_end",
    ]
    assert any(event["type"] == "llm_reasoning_delta" for event in emitted_before_candidate_complete)
    assert any(event["type"] == "llm_reasoning_delta" for event in emitted_before_resume_controller_complete)


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


def test_backend_agent_mode_does_not_emit_legacy_pipeline_events():
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
    agent_types = [event["type"] for event in agent_events]

    assert "status" not in agent_types
    assert "pipeline" not in agent_types
    assert agent_types[-3:] == ["final_answer", "sources", "done"]


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
    evidence.groups[0].docs[0]["metadata"]["evidence_class"] = "TARGET_DIRECT"
    evidence.groups[0].docs[0]["metadata"]["support_scope"] = "TARGET_SPECIFIC"
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


def test_linear_stream_emits_provider_thinking_without_agent_reasoning_events():
    """Linear streams provider thinking separately from Agent reasoning blocks."""
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
        yield "<think>先核对证据。</think>"
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
    assert [event["data"] for event in events if event.get("type") == "thinking"] == ["先核对证据。"]
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
    assert "EvidencePool 为空时，若 `retrieve_kb` 当前可用，优先围绕已确认实体做首次检索" in _DECISION_PROMPT
    assert "ControllerState 是语义状态" in _DECISION_PROMPT
    assert "当前可以调用的工具" in _DECISION_PROMPT
    assert "不要根据历史记忆调用本步骤未提供的工具" in _DECISION_PROMPT
    assert "不要讨论澄清回调机制本身" in _DECISION_PROMPT


def test_controller_state_keeps_clarify_available_after_entity_confirmation():
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
    assert "allowed_tools" not in state
    assert "retrieval_allowed" not in state
    assert "budget" not in state
    visible_tools = loop._available_tool_names()
    assert "clarify" in visible_tools
    assert "retrieve_kb" in visible_tools


def test_controller_model_visible_contract_excludes_runtime_control_plane():
    conversation = ConversationContext.from_request("PipelineBuilder", [])
    evidence = EvidencePool(question_id="controller-contract")
    loop = AgentLoop(
        conversation=conversation,
        evidence=evidence,
        budget=AgentBudget(max_steps=8, max_retrieve_attempts=2),
        registry=build_agent_registry(),
        handlers={},
    )

    state = json.loads(loop._controller_state_for_prompt())
    forbidden = {
        "budget", "allowed_tools", "retrieval_allowed", "registered_entity_ids",
        "clarification_callback", "topic_shift", "entity_transition",
        "latest_denial_reason", "max_steps", "steps_used",
        "retrieve_attempts", "remaining_retrieve_attempts",
    }
    assert forbidden.isdisjoint(state)


def test_controller_full_prompt_excludes_conversation_runtime_provenance():
    conversation = ConversationContext.from_request("PipelineBuilder", [])
    conversation.topic_shift = True
    conversation.entity_transition = True
    conversation.clarification_callback = True
    conversation.evidence_epoch = 7
    evidence = EvidencePool(question_id="controller-full-prompt-contract")
    loop = AgentLoop(
        conversation=conversation,
        evidence=evidence,
        budget=AgentBudget(),
        registry=build_agent_registry(),
        handlers={},
    )

    prompt = loop._decision_prompt_for_model()
    for forbidden in (
        "当前证据 epoch", "topic_shift: true", "entity_transition: true",
        "本轮为澄清回调", "clarification_callback", "evidence_epoch",
    ):
        assert forbidden not in prompt


def test_controller_prompt_tool_surface_hides_exhausted_retriever():
    conversation = ConversationContext.from_request("PipelineBuilder", [])
    evidence = EvidencePool(question_id="controller-tool-surface")
    loop = AgentLoop(
        conversation=conversation,
        evidence=evidence,
        budget=AgentBudget(max_steps=8, max_retrieve_attempts=1, retrieve_attempts=1),
        registry=build_agent_registry(),
        handlers={},
    )

    prompt = loop._decision_prompt_for_model()
    tool_surface = prompt.split("你当前可以调用的工具（未列出的工具本步骤不可用）：", 1)[1].split("决策准则：", 1)[0]
    output_contract = prompt.split("输出严格 JSON 格式：", 1)[1]
    assert "retrieve_kb" not in tool_surface
    assert "retrieve_kb" not in output_contract
    assert "compose_answer" in tool_surface


def test_controller_tool_surface_hides_denied_and_confirmation_tools():
    registry = build_agent_registry()
    from rag_knowledge.services.agent_orchestration.models import ToolSpec
    registry.register(ToolSpec(
        name="denied_tool",
        description="denied",
        input_schema={"type": "object", "properties": {}},
        permission="deny",
    ))
    registry.register(ToolSpec(
        name="confirmation_tool",
        description="confirm",
        input_schema={"type": "object", "properties": {}},
        confirmation_required=True,
    ))
    loop = AgentLoop(
        conversation=ConversationContext.from_request("test", []),
        evidence=EvidencePool(question_id="tool-permission-surface"),
        budget=AgentBudget(),
        registry=registry,
        handlers={},
    )

    visible = loop._available_tool_names()
    prompt = loop._decision_prompt_for_model()
    assert "denied_tool" not in visible
    assert "confirmation_tool" not in visible
    assert "denied_tool" not in prompt
    assert "confirmation_tool" not in prompt


def test_controller_observation_projection_drops_runtime_accounting_and_provenance():
    conversation = ConversationContext.from_request("PipelineBuilder", [])
    evidence = EvidencePool(question_id="controller-observation-contract")
    loop = AgentLoop(
        conversation=conversation,
        evidence=evidence,
        budget=AgentBudget(),
        registry=build_agent_registry(),
        handlers={},
        initial_observations=[{
            "name": "retrieve_kb",
            "ok": True,
            "status": "PROGRESS",
            "summary": "找到 PipelineBuilder 功能说明",
            "evidence_delta": {"citable_delta": 4, "gap_support_delta": 2},
            "budget": {"retrieve_attempts": 1},
            "data": {
                "retrieval_executed": True,
                "n": 8,
                "clarification_snapshot_id": "secret-snapshot",
                "evidence_observations": [{
                    "chunk_id": "chunk-internal",
                    "document_entity": "PipelineBuilder",
                    "evidence_class": "TARGET_DIRECT",
                    "reason": "direct",
                }],
            },
        }],
    )

    history = loop._observation_history_for_prompt()
    assert "找到 PipelineBuilder 功能说明" in history
    assert "PipelineBuilder" in history
    for forbidden in (
        "citable_delta", "gap_support_delta", "retrieve_attempts", "retrieval_executed",
        "secret-snapshot", "chunk-internal", '"n":8',
    ):
        assert forbidden not in history


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

    assert state["identity_status"] in {"unresolved", "ambiguous_entity"}
    assert "allowed_tools" not in state
    visible_tools = loop._available_tool_names()
    assert "link_entities" not in visible_tools
    assert "clarify" in visible_tools


def test_unbound_topic_allows_main_to_choose_clarify_or_retrieval():
    conversation = ConversationContext.from_request("知识库里关于部署的注意事项有哪些？", [])
    evidence = EvidencePool(question_id="controller-state-unbound-topic")
    events: list[dict] = []

    def decide(*_args):
        return AgentDecision(
            action="tool_call",
            tool="clarify",
            arguments={
                "question": "您想了解哪个产品？",
            },
            reason="泛化问题，先澄清",
            source="llm",
        )

    async def on_event(event):
        events.append(event)

    async def clarify_handler(_args):
        return ToolObservation(tool="clarify", ok=True, summary="clarification requested")

    loop = AgentLoop(
        conversation=conversation,
        evidence=evidence,
        budget=AgentBudget(max_steps=1, max_retrieve_attempts=2),
        registry=build_agent_registry(),
        handlers={"clarify": clarify_handler},
        decide_fn=decide,
    )

    state = json.loads(loop._controller_state_for_prompt())
    assert "entity_binding_required" not in state
    assert "allowed_tools" not in state
    visible_tools = loop._available_tool_names()
    assert "clarify" in visible_tools
    assert "retrieve_kb" in visible_tools

    asyncio.run(loop.run(on_event=on_event))
    guards = [event for event in events if event.get("type") == "guard"]
    assert guards[-1]["data"]["allowed"] is True


def test_runtime_allows_main_to_reclarify_after_entity_confirmation():
    conversation = ConversationContext.from_request("pipeline", [])
    conversation.identity_status = "confirmed_entity"
    conversation.confirmed_entity = "PipelineWebGL"
    conversation.head_entity = "PipelineWebGL"
    conversation.scope = SimpleNamespace(
        identity_status="confirmed_entity",
        confirmed_entity="PipelineWebGL",
        primary_entity="PipelineWebGL",
    )
    evidence = EvidencePool(question_id="confirmed-reclarify-veto")
    clarify_calls = 0
    events: list[dict] = []

    async def clarify_handler(_args):
        nonlocal clarify_calls
        clarify_calls += 1
        return ToolObservation(tool="clarify", ok=True, summary="should not run")

    def decide(*_args):
        return AgentDecision(
            action="tool_call",
            tool="clarify",
            arguments={"question": "您指的是哪个 pipeline？"},
            reason="重新澄清",
            source="llm",
        )

    async def on_event(event):
        events.append(event)

    loop = AgentLoop(
        conversation=conversation,
        evidence=evidence,
        budget=AgentBudget(max_steps=1, max_retrieve_attempts=2),
        registry=build_agent_registry(),
        handlers={"clarify": clarify_handler},
        decide_fn=decide,
    )

    asyncio.run(loop.run(on_event=on_event))

    assert clarify_calls == 1
    guards = [event for event in events if event.get("type") == "guard"]
    assert guards
    assert guards[-1]["data"]["allowed"] is True


def test_controller_empty_content_after_reasoning_triggers_repair_or_fallback():
    """When controller outputs reasoning but zero content (e.g. token limit), it must report diagnosis and attempt repair."""
    context = ConversationContext.from_request("StampServer 端口？", [])
    pool = EvidencePool(question_id="test-empty-reasoning")
    routed_roles: list[str] = []

    def endpoint_for(role: str) -> ModelEndpoint:
        routed_roles.append(role)
        return ModelEndpoint(
            role=role,
            provider="ollama",
            model="qwen3.5:9b",
            base_url="http://localhost:11434",
        )

    async def fake_stream_parts(*args, **kwargs):
        # Model emits reasoning but exhausts tokens before emitting JSON content
        yield LLMStreamPart("reasoning", "Thinking about StampServer port...")

    def fake_repair_chat(*args, **kwargs):
        return json.dumps({
            "action": "tool_call",
            "tool": "compose_answer",
            "thought": "Repaired after empty response",
            "arguments": {"answer_mode": "full"},
        })

    cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(
            terminal_finalization_v2=True,
        ),
        ollama_base_url="http://localhost:11434",
        context_budget=SimpleNamespace(context_window=4096),
        endpoint_for=endpoint_for,
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

    assert decision.action == "tool_call"
    assert decision.tool == "compose_answer"
    assert len(reasoning_events) >= 2
    assert reasoning_events[0]["type"] == "llm_reasoning_start"
    assert reasoning_events[-1]["type"] == "llm_reasoning_end"
    assert routed_roles == ["llm"]
    assert {event["data"]["role"] for event in reasoning_events} == {"main"}
    assert {event["data"]["stage"] for event in reasoning_events} == {"agent_controller"}
    end_data = reasoning_events[-1]["data"]
    assert end_data["reasoning_available"] is True
    assert end_data["content_chars"] == 0
    assert end_data["num_predict"] == 8192

    # Verify protocol attempts record the empty content diagnosis
    assert len(loop._controller_protocol_attempts) >= 1
    assert "controller_output_empty_after_reasoning" in str(loop._controller_protocol_attempts[0].get("error"))


def test_reviewer_empty_structured_output_fails_closed():
    """Reviewer output remains fail-closed even though free reasoning is hidden."""
    chain = object.__new__(RagChain)
    chain._cfg = SimpleNamespace(
        grounding_reviewer_timeout=30.0,
        ollama_base_url="http://localhost:11434",
        context_budget=SimpleNamespace(context_window=4096),
        endpoint_for=lambda role: ModelEndpoint(role=role, provider="ollama", model="qwen3.5:4b", base_url="http://localhost:11434"),
    )

    reasoning_events = []
    def on_reasoning(evt):
        reasoning_events.append(evt)

    with patch("rag_knowledge.llm_http.chat_role", lambda *args, **kwargs: ""):
        reviewer = chain._helper_grounding_reviewer(on_reasoning_event=on_reasoning)
        assert reviewer is not None
        result = reviewer.review(
            question="StampServer 端口？",
            context_docs=[_doc("c1", "StampServer 端口 8080。")],
            candidate="StampServer 端口是 8080。",
        )

    assert result.verdict == "ERROR"
    assert result.coverage == "NONE"
    assert reasoning_events == []


def test_answer_generation_streams_native_reasoning_and_suppresses_public_explanation():
    chain = object.__new__(RagChain)
    endpoint = ModelEndpoint(
        role="llm",
        provider="ollama",
        model="qwen3.5:9b",
        base_url="http://unused.test",
    )
    chain._cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(
            reasoning_stream_policy="token",
            trace_reasoning_max_chars=4000,
        ),
        context_budget=SimpleNamespace(context_window=32768),
        ollama_base_url="http://unused.test",
        endpoint_for=lambda _role: endpoint,
        grounding_reviewer_enabled=False,
    )
    chain._resolve_llm_endpoint = lambda _model: endpoint
    chain._should_enable_main_model_thinking = lambda _ep, _th: True
    chain._apply_vram_guard = lambda model: (model, False)
    chain._downshift_fields = lambda *args: {}
    chain._record_execution_event = lambda *_args, **_kwargs: None
    chain._commit_qa_trace = lambda *_args, **_kwargs: "test-trace-id"
    chain._route_agent_query = lambda *args, **kwargs: (None, [], None)

    async def fake_run_agent_turn(*args, **kwargs):
        conv = ConversationContext.from_request("StampServer 端口？", [])
        pool = EvidencePool(question_id="test-q")
        pool.add_retrieve([_doc("c1", "StampServer 端口是 8080。")], query="StampServer 端口")
        snapshot = pool.create_snapshot(verdict={"coverage": "FULL", "admissibility": "VALID", "can_answer": True})
        return AgentTurnResult(
            conversation=conv,
            evidence=pool,
            route="retrieve",
            answer_gate={"coverage": "FULL", "admissibility": "VALID", "can_answer": True},
            answer_context=AnswerGenerationContext.from_snapshot(
                original_question="StampServer 端口？",
                resolved_question="StampServer 端口？",
                conversation_context="",
                snapshot=snapshot,
            ),
            terminal_action="controller_finalize",
        )

    chain._run_agent_turn = fake_run_agent_turn
    chain._safe_set_retrieval = lambda *_args, **_kwargs: None
    chain._freeze_generation_source_docs = lambda docs: list(docs)

    async def fake_stream_parts(*args, **kwargs):
        yield LLMStreamPart("reasoning", "正在基于证据 [1] 组织回答。")
        yield LLMStreamPart("content", "StampServer 的端口是 8080。[1]")

    with (
        patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream_parts),
        patch("rag_knowledge.services.rag._ANSWER_FINALIZER.finalize", lambda *args, **kwargs: FinalizedAnswer(
            answer="StampServer 的端口是 8080。[1]",
            grounding={"final_mode": "generated", "review_verdict": "PASS"},
        )),
    ):
        events = []
        async def collect():
            async for evt in chain._stream_agent_query(
                "StampServer 端口？",
                None,
                llm_model=None,
                kb_name=None,
                doc_category=None,
                entity_name=None,
                thinking=True,
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
            ):
                events.append(evt)
        asyncio.run(collect())

    event_types = [e["type"] for e in events]
    assert "llm_reasoning_start" in event_types
    assert "llm_reasoning_delta" in event_types
    assert "llm_reasoning_end" in event_types
    # Sub-PRD 01: When native reasoning is available, public_explanation must be suppressed
    assert "public_explanation" not in event_types
    start_event = next(e for e in events if e["type"] == "llm_reasoning_start")
    end_event = next(e for e in events if e["type"] == "llm_reasoning_end")
    assert start_event["data"]["reasoning_requested"] is True
    assert end_event["data"]["reasoning_requested"] is True
    assert end_event["data"]["reasoning_available"] is True
    delta_event = next(e for e in events if e["type"] == "llm_reasoning_delta")
    assert delta_event["data"]["delta"] == "正在基于证据 [1] 组织回答。"


def test_answer_generation_emits_fallback_public_explanation_when_no_native_reasoning():
    chain = object.__new__(RagChain)
    endpoint = ModelEndpoint(
        role="llm",
        provider="ollama",
        model="qwen2.5:7b",
        base_url="http://unused.test",
    )
    chain._cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(
            reasoning_stream_policy="token",
            trace_reasoning_max_chars=4000,
        ),
        context_budget=SimpleNamespace(context_window=32768),
        ollama_base_url="http://unused.test",
        endpoint_for=lambda _role: endpoint,
        grounding_reviewer_enabled=False,
    )
    chain._resolve_llm_endpoint = lambda _model: endpoint
    chain._should_enable_main_model_thinking = lambda _ep, _th: False
    chain._apply_vram_guard = lambda model: (model, False)
    chain._downshift_fields = lambda *args: {}
    chain._record_execution_event = lambda *_args, **_kwargs: None
    chain._commit_qa_trace = lambda *_args, **_kwargs: "test-trace-id"
    chain._route_agent_query = lambda *args, **kwargs: (None, [], None)

    async def fake_run_agent_turn(*args, **kwargs):
        conv = ConversationContext.from_request("StampServer 端口？", [])
        pool = EvidencePool(question_id="test-q-no-reasoning")
        pool.add_retrieve([_doc("c1", "StampServer 端口是 8080。")], query="StampServer 端口")
        snapshot = pool.create_snapshot(verdict={"coverage": "FULL", "admissibility": "VALID", "can_answer": True})
        return AgentTurnResult(
            conversation=conv,
            evidence=pool,
            route="retrieve",
            answer_gate={"coverage": "FULL", "admissibility": "VALID", "can_answer": True},
            answer_context=AnswerGenerationContext.from_snapshot(
                original_question="StampServer 端口？",
                resolved_question="StampServer 端口？",
                conversation_context="",
                snapshot=snapshot,
            ),
            terminal_action="controller_finalize",
        )

    chain._run_agent_turn = fake_run_agent_turn
    chain._safe_set_retrieval = lambda *_args, **_kwargs: None
    chain._freeze_generation_source_docs = lambda docs: list(docs)

    async def fake_stream_parts(*args, **kwargs):
        yield LLMStreamPart("content", "StampServer 的端口是 8080。[1]")

    with (
        patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream_parts),
        patch("rag_knowledge.services.rag._ANSWER_FINALIZER.finalize", lambda *args, **kwargs: FinalizedAnswer(
            answer="StampServer 的端口是 8080。[1]",
            grounding={"final_mode": "generated", "review_verdict": "PASS"},
        )),
    ):
        events = []
        async def collect():
            async for evt in chain._stream_agent_query(
                "StampServer 端口？",
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
            ):
                events.append(evt)
        asyncio.run(collect())

    event_types = [e["type"] for e in events]
    assert "llm_reasoning_start" in event_types
    assert "llm_reasoning_end" in event_types
    # Sub-PRD 01: When native reasoning is NOT available, fallback public_explanation must be emitted
    assert "public_explanation" in event_types
    start_event = next(e for e in events if e["type"] == "llm_reasoning_start")
    end_event = next(e for e in events if e["type"] == "llm_reasoning_end")
    assert start_event["data"]["reasoning_requested"] is False
    assert end_event["data"]["reasoning_requested"] is False
    assert end_event["data"]["reasoning_available"] is False
    exp_event = next(e for e in events if e["type"] == "public_explanation")
    assert exp_event["data"]["source"] == "system_fallback"
    assert exp_event["data"]["fallback_used"] is True
    assert exp_event["data"]["stage"] == "answer_generation"
