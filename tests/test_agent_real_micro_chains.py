"""Integration tests for live Ollama model micro-chains.

Tests individual nodes with real Ollama models to verify reasoning stream
and structured JSON parsing without full end-to-end multi-step timeout risk.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from rag_knowledge.config import Config
from rag_knowledge.services.answer_finalizer import FinalizedAnswer
from rag_knowledge.services.agent_orchestration.models import (
    AgentBudget,
    AgentDecision,
    ConversationContext,
    EvidencePool,
    AgentTurnResult,
)
from rag_knowledge.services.agent_orchestration.runtime import (
    AgentLoop,
    build_agent_registry,
)
from rag_knowledge.services.rag import RagChain


def _doc(chunk_id: str = "c1", content: str = "StampServer 的端口默认是 8080。") -> dict:
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


def _reasoning_text(events: list[dict]) -> str:
    return "".join(
        str((event.get("data") or {}).get("delta") or "")
        for event in events
        if event.get("type") == "llm_reasoning_delta"
    )


def _assert_reasoning_is_chinese(events: list[dict]) -> None:
    """Live-model smoke check: native reasoning should materially use Chinese, not English-only prose."""
    text = _reasoning_text(events)
    cjk_count = sum("\u4e00" <= char <= "\u9fff" for char in text)
    assert cjk_count >= 20, f"reasoning is not materially Chinese: {text[:300]!r}"
    assert "Analyze the Request" not in text
    assert "Decision Criteria Check" not in text
    assert "Thinking Process" not in text


def _live_cfg() -> Config:
    os.environ["ALLOW_LIVE_STORAGE_IN_TESTS"] = "1"
    os.environ["RAG_CONFIG"] = "config-local.ini"
    Config._instance = None
    return Config()


@pytest.mark.integration
def test_real_controller_micro_chain():
    """Verify Controller with live qwen3.5:9b streams reasoning and outputs valid decision JSON."""
    cfg = _live_cfg()
    context = ConversationContext.from_request("StampServer 的主要端口是什么？", [])
    pool = EvidencePool(question_id="real-controller-q")

    loop = AgentLoop(
        conversation=context,
        evidence=pool,
        budget=AgentBudget(max_steps=3),
        registry=build_agent_registry(),
        handlers={},
        cfg=cfg,
    )

    reasoning_events = []

    async def on_event(evt: dict) -> None:
        if evt.get("type", "").startswith("llm_reasoning_"):
            reasoning_events.append(evt)

    decision = asyncio.run(loop._adecide_via_llm(on_event, step_index=1))

    assert isinstance(decision, AgentDecision)
    assert decision.action in {"tool_call", "finalize"}
    assert len(reasoning_events) >= 2
    assert reasoning_events[0]["type"] == "llm_reasoning_start"
    assert reasoning_events[-1]["type"] == "llm_reasoning_end"
    end_data = reasoning_events[-1]["data"]
    assert end_data["reasoning_available"] is True
    assert end_data["reasoning_chars"] > 0
    assert end_data["content_chars"] > 0
    assert end_data["num_predict"] == 8192
    _assert_reasoning_is_chinese(reasoning_events)


@pytest.mark.integration
def test_real_reviewer_micro_chain():
    """Verify Grounding Reviewer with live qwen3.5:4b streams reasoning and returns valid review JSON."""
    cfg = _live_cfg()
    chain = RagChain()

    reasoning_events = []

    def on_reasoning(evt: dict) -> None:
        reasoning_events.append(evt)

    reviewer = chain._helper_grounding_reviewer(on_reasoning_event=on_reasoning)
    assert reviewer is not None

    result = reviewer.review(
        question="StampServer 的端口是多少？",
        context_docs=[_doc("c1", "StampServer 的端口默认是 8080。")],
        candidate="StampServer 的默认端口是 8080 [1]。",
    )

    assert result.verdict in {"PASS", "REVISE"}
    assert result.coverage in {"FULL", "PARTIAL"}
    assert len(reasoning_events) >= 2
    assert reasoning_events[0]["type"] == "llm_reasoning_start"
    assert reasoning_events[-1]["type"] == "llm_reasoning_end"
    end_data = reasoning_events[-1]["data"]
    assert end_data["reasoning_available"] is True
    assert end_data["reasoning_chars"] > 0
    assert end_data["content_chars"] > 0
    assert end_data["num_predict"] == 12288


@pytest.mark.integration
def test_real_answer_generator_micro_chain():
    """Verify answer generation emits reasoning before any candidate can be published."""
    cfg = _live_cfg()
    conversation = ConversationContext.from_request("StampServer 的端口是多少？", [])
    evidence = EvidencePool(question_id="real-answer-q")
    evidence.add_retrieve([_doc()], query="StampServer 端口")
    result = AgentTurnResult(
        conversation=conversation,
        evidence=evidence,
        route="retrieve",
        answer_gate={"allow_knowledge_answer": True, "coverage": "FULL"},
        evidence_snapshot=evidence.create_snapshot(
            verdict={"allow_knowledge_answer": True, "coverage": "FULL"},
        ),
    )

    class _Trace:
        def set_understanding(self, _value):
            pass

        def set_plan(self, _value):
            pass

        def set_agent(self, _value):
            pass

        def set_pack(self, _value):
            pass

        def mark(self, _value):
            pass

    chain = object.__new__(RagChain)
    chain._cfg = cfg
    chain._allow_general_knowledge = False
    chain._ollama_base = cfg.ollama_base_url
    chain._record_execution_event = lambda *_args, **_kwargs: None
    chain._safe_set_retrieval = lambda *_args, **_kwargs: None
    chain._safe_set_grounding = lambda *_args, **_kwargs: None
    chain._commit_qa_trace = lambda *_args, **_kwargs: "real-answer-trace"
    chain._apply_vram_guard = lambda model: (model or "qwen3.5:9b", False)
    chain._filter_cited_sources = lambda _answer, docs: docs
    chain._build_messages = lambda *_args, **_kwargs: [{
        "role": "user",
        "content": "仅根据证据回答 StampServer 的端口，并标注 [1]。",
    }]

    async def fake_run_agent_turn(*_args, **_kwargs):
        return result

    chain._run_agent_turn = fake_run_agent_turn

    def finalize(candidate, *_args, **kwargs):
        assert candidate.strip()
        kwargs["on_lifecycle_event"]({
            "type": "publication",
            "data": {
                "final_mode": "generated",
                "review_verdict": "PASS",
                "coverage": "FULL",
            },
        })
        return FinalizedAnswer(
            answer=candidate,
            grounding={"final_mode": "generated", "review_verdict": "PASS"},
        )

    async def collect() -> list[dict]:
        return [
            event
            async for event in chain._stream_agent_query(
                "StampServer 的端口是多少？",
                None,
                llm_model="qwen3.5:9b",
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
                trace=_Trace(),
            )
        ]

    from unittest.mock import patch

    with patch("rag_knowledge.services.rag._ANSWER_FINALIZER.finalize", finalize):
        events = asyncio.run(collect())

    reasoning = [event for event in events if event["type"].startswith("llm_reasoning_")]
    assert reasoning[0]["type"] == "llm_reasoning_start"
    assert reasoning[-1]["type"] == "llm_reasoning_end"
    assert any(event["type"] == "llm_reasoning_delta" for event in reasoning)
    end_data = reasoning[-1]["data"]
    assert end_data["reasoning_available"] is True
    assert end_data["reasoning_chars"] > 0
    assert end_data["content_chars"] > 0
    assert end_data["num_predict"] == 8192
    reasoning_text = "".join(
        str(event.get("data", {}).get("delta") or "")
        for event in reasoning
        if event["type"] == "llm_reasoning_delta"
    )
    assert any("\u4e00" <= ch <= "\u9fff" for ch in reasoning_text)
    assert "Thinking Process" not in reasoning_text
    assert "Analyze the Request" not in reasoning_text
    assert not any(event["type"] == "token" for event in events)
    assert [event["type"] for event in events].index("llm_reasoning_end") < [
        event["type"] for event in events
    ].index("publication") < [event["type"] for event in events].index("final_answer")


@pytest.mark.integration
def test_real_rewrite_micro_chain():
    """Verify Grounded Candidate Retry (Rewrite) with live qwen3.5:9b emits reasoning."""
    cfg = _live_cfg()
    chain = RagChain()

    from rag_knowledge.services.helper_grounding_reviewer import (
        ClaimReview,
        HelperGroundingReviewResult,
        RewriteAction,
    )

    review_result = HelperGroundingReviewResult(
        verdict="REVISE",
        coverage="PARTIAL",
        summary="部分断言未被证据支持",
        claim_reviews=(
            ClaimReview(
                claim_id="c1",
                claim="StampServer 支持 9999 端口",
                claim_type="knowledge_claim",
                status="unsupported",
                evidence_ids=(1,),
                reason="证据中未提及 9999 端口",
            ),
        ),
        rewrite_actions=(
            RewriteAction(
                claim_id="c1",
                action="rewrite_to_supported_scope_or_remove",
                instruction="删除不支持的 9999 端口表述，仅保留 8080 端口",
            ),
        ),
    )

    reasoning_events = []

    def on_reasoning(evt: dict) -> None:
        reasoning_events.append(evt)

    retry_candidate = chain._retry_grounded_candidate(
        "qwen3.5:9b",
        "StampServer 的端口是多少？",
        "StampServer 支持 8080 和 9999 端口 [1]。",
        [_doc("c1", "StampServer 的端口默认是 8080。")],
        review_result,
        on_reasoning_event=on_reasoning,
    )

    assert isinstance(retry_candidate, str) and retry_candidate.strip()
    assert len(reasoning_events) >= 2
    assert reasoning_events[0]["type"] == "llm_reasoning_start"
    assert reasoning_events[-1]["type"] == "llm_reasoning_end"
    end_data = reasoning_events[-1]["data"]
    assert end_data["reasoning_available"] is True
    assert end_data["reasoning_chars"] > 0
    assert end_data["num_predict"] == 8192
    reasoning_text = "".join(
        str(event.get("data", {}).get("delta") or "")
        for event in reasoning_events
        if event["type"] == "llm_reasoning_delta"
    )
    assert any("\u4e00" <= ch <= "\u9fff" for ch in reasoning_text)
    assert "Thinking Process" not in reasoning_text
    assert "Analyze the Request" not in reasoning_text


@pytest.mark.integration
def test_real_revise_rewrite_second_review_micro_chain():
    """Verify the live Reviewer → Rewrite → Reviewer loop reaches a publishable candidate."""
    _live_cfg()
    chain = RagChain()
    events: list[dict] = []
    reviewer = chain._helper_grounding_reviewer(on_reasoning_event=events.append)
    assert reviewer is not None
    docs = [_doc("c1", "StampServer 的端口默认是 8080。")]

    first_review = reviewer.review(
        question="StampServer 的端口是多少？",
        context_docs=docs,
        candidate="StampServer 的默认端口是 8080，也支持未在证据中出现的 9999 端口 [1]。",
    )

    assert first_review.verdict == "REVISE"
    assert first_review.rewrite_actions
    candidate_v2 = chain._retry_grounded_candidate(
        "qwen3.5:9b",
        "StampServer 的端口是多少？",
        "StampServer 的默认端口是 8080，也支持未在证据中出现的 9999 端口 [1]。",
        docs,
        first_review,
        on_reasoning_event=events.append,
    )
    assert candidate_v2.strip()

    second_review = reviewer.review(
        question="StampServer 的端口是多少？",
        context_docs=docs,
        candidate=candidate_v2,
    )

    assert second_review.verdict == "PASS"
    call_ids = [event["data"]["call_id"] for event in events if event["type"] == "llm_reasoning_start"]
    assert call_ids == ["grounding_reviewer_1", "grounded_retry_v2", "grounding_reviewer_2"]
    assert all(
        event["data"].get("reasoning_chars", 0) > 0
        for event in events
        if event["type"] == "llm_reasoning_end"
    )


@pytest.mark.integration
def test_real_revise_rewrite_review2_closed_loop():
    """Verify full real REVISE -> Rewrite -> Review #2 cycle with live models and reasoning."""
    cfg = _live_cfg()
    chain = RagChain()

    lifecycle_events: list[dict] = []
    def on_lifecycle(evt: dict) -> None:
        lifecycle_events.append(evt)

    # Candidate with unsupported claim that will trigger REVISE from real reviewer
    unsupported_candidate = "StampServer 端口是 8080，并且支持 99999 端口与量子加密传输。"
    doc = _doc("c1", "StampServer 运行在 Windows 上，默认端口是 8080。")

    from rag_knowledge.services.answer_finalizer import AnswerFinalizer

    finalizer = AnswerFinalizer()
    reviewer = chain._helper_grounding_reviewer(on_reasoning_event=on_lifecycle)

    def retry_candidate_fn(review_result):
        return chain._retry_grounded_candidate(
            "qwen3.5:9b",
            "StampServer 的端口是多少？",
            unsupported_candidate,
            [doc],
            review_result,
            on_reasoning_event=on_lifecycle,
        )

    finalized = finalizer.finalize(
        unsupported_candidate,
        "StampServer 的端口是多少？",
        [doc],
        allow_general_knowledge=False,
        is_direct_chat=False,
        retry_candidate=retry_candidate_fn,
        helper_reviewer=reviewer,
        on_lifecycle_event=on_lifecycle,
    )

    event_types = [e.get("type") for e in lifecycle_events]
    assert "review_status" in event_types
    assert "publication" in event_types

    # Find the review verdicts
    review_statuses = [e for e in lifecycle_events if e.get("type") == "review_status"]
    assert len(review_statuses) >= 2
    assert review_statuses[0]["data"]["verdict"] == "REVISE"
    assert review_statuses[1]["data"]["verdict"] == "PASS"

    # Check reasoning blocks for Reviewer #1, Main Rewrite, Reviewer #2
    reasoning_starts = [e for e in lifecycle_events if e.get("type") == "llm_reasoning_start"]
    assert len(reasoning_starts) >= 3
    assert reasoning_starts[0]["data"]["stage"] == "grounding_reviewer"
    assert reasoning_starts[1]["data"]["stage"] == "grounded_retry"
    assert reasoning_starts[2]["data"]["stage"] == "grounding_reviewer"

    assert finalized.grounding.get("final_mode") == "grounded_rewrite"
    assert finalized.grounding.get("review_verdict") == "PASS"
    assert finalized.answer.strip()
    assert "8080" in finalized.answer
