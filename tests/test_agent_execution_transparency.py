"""Acceptance tests for the Agent execution-transparency event contract.

All tests use in-memory controller decisions, tool handlers, reviewers, and
stream stubs.  They must never open the configured vector or relational stores.
"""

from __future__ import annotations

import asyncio
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
from rag_knowledge.services.rag import RagChain


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


def test_normal_and_forced_finalization_both_emit_finalization_check():
    _, normal_events = asyncio.run(
        _run_loop(
            [AgentDecision(action="finish", source="test")],
            max_steps=1,
            terminal_finalization_v2=False,
        ),
    )
    normal_check = normal_events[_event_index(normal_events, "finalization_check")]
    assert normal_check["data"].get("forced", False) is False

    forced_pool = EvidencePool(question_id="forced-finalization")

    async def retrieve(arguments: dict) -> ToolObservation:
        forced_pool.add_retrieve([_doc()], query=arguments["query"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="found evidence")

    _, forced_events = asyncio.run(
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
            evidence=forced_pool,
        ),
    )
    forced_index = _event_index(
        forced_events,
        "finalization_check",
        lambda data: data.get("forced") is True,
    )
    assert forced_events[forced_index]["data"]["forced"] is True


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
    assert lifecycle[publication_index]["data"]["final_mode"] == "grounded_rewrite"
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
    chain._resolve_llm_endpoint = lambda _model: object()
    chain._helper_grounding_reviewer = lambda: object()
    chain._retry_grounded_candidate = lambda *_args, **_kwargs: reviewed_answer
    chain._safe_set_grounding = lambda *_args, **_kwargs: None
    chain._filter_cited_sources = lambda _answer, docs: docs
    chain._commit_qa_trace = lambda *_args, **_kwargs: "fixture-trace"

    async def fake_stream(*_args, **_kwargs):
        yield raw_candidate

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
        patch("rag_knowledge.llm_http.achat_stream", fake_stream),
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
