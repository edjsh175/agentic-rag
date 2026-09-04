"""Integration tests for live Ollama model micro-chains.

Tests individual nodes with real Ollama models to verify reasoning stream
and structured JSON parsing without full end-to-end multi-step timeout risk.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.services.answer_finalizer import AnswerFinalizer, FinalizedAnswer
from rag_knowledge.services.agent_candidate_pipeline import CandidateProvenance, CandidateResult
from rag_knowledge.services.agent_orchestration.models import (
    AgentBudget,
    AgentDecision,
    AnswerGenerationContext,
    ConversationContext,
    EvidencePool,
    AgentTurnResult,
)
from rag_knowledge.services.agent_orchestration.runtime import (
    AgentLoop,
    build_agent_registry,
)
from rag_knowledge.services.agent_orchestration.graph_working_set import (
    GraphEntityState,
    GraphRelationCandidate,
    GraphWorkingSet,
)
from rag_knowledge.services.dialogue_understanding import SemanticTaskContext
from rag_knowledge.services.rag import RagChain
from rag_knowledge.services.text_evidence_admission import TextEvidenceAdmissionService


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
            "evidence_class": "TARGET_DIRECT",
            "support_scope": "TARGET_SPECIFIC",
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


def _assert_reasoning_hides_runtime_control(events: list[dict]) -> None:
    text = _reasoning_text(events)
    for forbidden in (
        "retrieve_attempts", "remaining_retrieve_attempts", "allowed_tools",
        "clarification_callback", "steps_used", "max_steps",
        "还剩一次预算", "还剩 1 次预算", "第 2 次检索", "第二次检索协议",
    ):
        assert forbidden not in text, f"runtime control leaked into reasoning: {forbidden!r} in {text[:600]!r}"


def _live_cfg() -> Config:
    os.environ["ALLOW_LIVE_STORAGE_IN_TESTS"] = "1"
    # Respect an explicitly selected integration-test config (for example
    # config-mix.ini); keep config-local.ini as the default for normal runs.
    os.environ.setdefault("RAG_CONFIG", "config-local.ini")
    Config._instance = None
    return Config()


@pytest.mark.integration
def test_real_controller_micro_chain():
    """Verify a live Controller returns a valid decision and lifecycle protocol."""
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
    assert end_data["num_predict"] > 0

    controller_endpoint = cfg.endpoint_for("llm")
    if controller_endpoint.normalized_provider() == "ollama" and "qwen3" in controller_endpoint.model.lower():
        assert end_data["content_chars"] > 0
        reasoning_text = _reasoning_text(reasoning_events)
        if len(reasoning_text) >= 20:
            _assert_reasoning_is_chinese(reasoning_events)
            _assert_reasoning_hides_runtime_control(reasoning_events)
    else:
        # Compatible providers may advertise or omit a native reasoning
        # channel independently of a particular response's token stream.
        assert isinstance(end_data["reasoning_available"], bool)
        assert end_data["content_chars"] >= 0


@pytest.mark.integration
def test_real_reviewer_micro_chain():
    """Verify the live Reviewer returns a valid review under the active provider contract."""
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
    assert result.error is None
    assert 1 <= len(result.protocol_attempts) <= 2
    assert result.protocol_attempts[-1]["error"] is None
    # Reviewer reasoning is not a product-visible contract. Some providers emit
    # native reasoning events and some do not; when present, only validate the
    # lifecycle shape rather than requiring them.
    if reasoning_events:
        assert reasoning_events[0]["type"] == "llm_reasoning_start"
        assert reasoning_events[-1]["type"] == "llm_reasoning_end"


@pytest.mark.integration
def test_real_reviewer_rejects_context_only_target_attribution():
    """Live Reviewer must not upgrade CONTEXT_ONLY evidence into a target attribute claim."""
    cfg = _live_cfg()
    chain = RagChain()
    reviewer = chain._helper_grounding_reviewer()
    assert reviewer is not None

    context_doc = {
        "content": "管线系统支持碰撞分析和智能排管。",
        "metadata": {
            "chunk_id": "scope-context-1",
            "citation_id": 1,
            "document_entity": "管线系统",
            "file_name": "pipe.md",
            "page_label": "无页码",
            "source_type": "knowledge_base",
            "support_scope": "CONTEXT_ONLY",
            "evidence_class": "RELATED_CONTEXT",
        },
    }
    result = reviewer.review(
        question="三维管线管理支持智能排管吗？",
        context_docs=[context_doc],
        candidate="三维管线管理支持智能排管功能 [1]。",
    )

    assert result.verdict == "REVISE"
    assert result.coverage in {"FULL", "PARTIAL"}
    assert any(
        claim.status == "unsupported" and 1 in claim.evidence_ids
        for claim in result.claim_reviews
    )
    unsupported_ids = {claim.claim_id for claim in result.claim_reviews if claim.status == "unsupported"}
    assert any(
        action.claim_id in unsupported_ids
        and action.action in {"rewrite_to_supported_scope_or_remove", "add_limitation_statement", "correct_to_evidence", "remove_claim"}
        for action in result.rewrite_actions
    )


@pytest.mark.integration
def test_real_answer_generator_micro_chain():
    """Verify answer generation emits reasoning before any candidate can be published."""
    cfg = _live_cfg()
    conversation = ConversationContext.from_request("StampServer 的端口是多少？", [])
    evidence = EvidencePool(question_id="real-answer-q")
    evidence.add_retrieve([_doc()], query="StampServer 端口")
    snapshot = evidence.create_snapshot(
        verdict={"allow_knowledge_answer": True, "coverage": "FULL"},
    )
    answer_context = AnswerGenerationContext.from_snapshot(
        original_question="StampServer 的端口是多少？",
        resolved_question="StampServer 的端口是多少？",
        conversation_context="当前主体身份：StampServer",
        snapshot=snapshot,
        answer_contract={
            "answer_type": "knowledge",
            "evidence_required": True,
            "answer_mode": "full",
        },
        answer_policy={"allow_general_knowledge": False},
        execution_summary="真实 Answer Generator 微链",
    )
    result = AgentTurnResult(
        conversation=conversation,
        evidence=evidence,
        route="retrieve",
        answer_gate={"allow_knowledge_answer": True, "coverage": "FULL"},
        evidence_snapshot=snapshot,
        answer_context=answer_context,
        answer_contract={
            "answer_type": "knowledge",
            "evidence_required": True,
            "answer_mode": "full",
        },
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
    answer_endpoint = cfg.endpoint_for("llm")
    chain._apply_vram_guard = lambda model: (model or answer_endpoint.model, False)
    chain._filter_cited_sources = lambda _answer, docs: docs

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
                llm_model=answer_endpoint.model,
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
    end_data = reasoning[-1]["data"]
    assert end_data["content_chars"] > 0
    assert end_data["num_predict"] == 8192
    reasoning_text = "".join(
        str(event.get("data", {}).get("delta") or "")
        for event in reasoning
        if event["type"] == "llm_reasoning_delta"
    )
    if end_data["reasoning_available"]:
        assert any(event["type"] == "llm_reasoning_delta" for event in reasoning)
        assert end_data["reasoning_chars"] > 0
        # When a provider exposes native reasoning, the production Answer prompt
        # requires it to be Chinese from the first reasoning token.
        assert any("\u4e00" <= ch <= "\u9fff" for ch in reasoning_text)
        assert "Thinking Process" not in reasoning_text
        assert "Analyze the Request" not in reasoning_text
        _assert_reasoning_hides_runtime_control(reasoning)
    else:
        # Some external providers/models do not expose a separate reasoning
        # channel; content generation must still complete normally.
        assert not any(event["type"] == "llm_reasoning_delta" for event in reasoning)
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
    reasoning_starts = [e for e in reasoning_events if e.get("type") == "llm_reasoning_start"]
    reasoning_ends = [e for e in reasoning_events if e.get("type") == "llm_reasoning_end"]
    assert reasoning_starts
    assert reasoning_ends
    end_data = reasoning_ends[-1]["data"]
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
    _assert_reasoning_hides_runtime_control(reasoning_events)


@pytest.mark.integration
def test_real_citation_binding_rewrite_second_review_micro_chain():
    """A supported fact with the wrong citation must be repaired, not passed or marked unsupported."""
    _live_cfg()
    chain = RagChain()
    reviewer = chain._helper_grounding_reviewer()
    assert reviewer is not None

    doc1 = _doc("port", "StampServer 的默认端口是 8080。")
    doc2 = _doc("auth", "StampServer 提供授权服务。")
    doc2["metadata"]["citation_id"] = 2
    docs = [doc1, doc2]
    events: list[dict] = []

    finalized = AnswerFinalizer().finalize(
        "StampServer 的默认端口是 8080 [2]。",
        "StampServer 的默认端口是多少？",
        docs,
        helper_reviewer=reviewer,
        retry_candidate=lambda review: chain._retry_grounded_candidate(
            None,
            "StampServer 的默认端口是多少？",
            "StampServer 的默认端口是 8080 [2]。",
            docs,
            review,
        ),
        on_lifecycle_event=events.append,
    )

    review_events = [event for event in events if event.get("type") == "review_status"]
    assert len(review_events) == 2
    first = review_events[0]["data"]
    second = review_events[1]["data"]
    assert first["verdict"] == "REVISE"
    assert first["repair_mode"] == "REWRITE"
    assert first["unsupported_count"] == 0
    assert first["contradicted_count"] == 0
    assert any(action.get("action") == "fix_citations" for action in first["rewrite_actions"])
    assert second["verdict"] == "PASS"
    assert finalized.grounding.get("review_verdict") == "PASS"
    assert "[1]" in finalized.answer
    assert "[2]" not in finalized.answer


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
    # Reviewer 的用户可见解释是结构化 Claim Finding；不把其自由思考
    # 冒充 Main reasoning。此配置的 helper 模型没有 native reasoning，
    # 因此闭环必须至少保留 Main Rewrite 的原生 reasoning。
    call_ids = [event["data"]["call_id"] for event in events if event["type"] == "llm_reasoning_start"]
    assert call_ids == ["grounded_retry_v2"]
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

    # Reviewer #1 / #2 通过 review_status 暴露结构化裁决；只有 Main Rewrite
    # 的 native reasoning 应作为用户可见 reasoning block。
    reasoning_starts = [e for e in lifecycle_events if e.get("type") == "llm_reasoning_start"]
    assert [event["data"]["stage"] for event in reasoning_starts] == ["grounded_retry"]
    assert reasoning_starts[0]["data"]["call_id"] == "grounded_retry_v2"

    assert finalized.grounding.get("final_mode") in {"grounded_rewrite", "grounded_partial"}
    assert finalized.grounding.get("review_verdict") == "PASS"
    assert finalized.answer.strip()
    assert "8080" in finalized.answer


def _pipe_management_live_context_doc() -> tuple[dict, object]:
    candidate = CandidateResult(
        document=Document(
            page_content="管线系统支持碰撞分析和智能排管。",
            metadata={
                "chunk_id": "pipe-context-live",
                "document_entity": "管线系统",
                "review_status": "approved",
            },
        ),
        target_entity="三维管线管理",
    )
    candidate.provenance.append(CandidateProvenance("bm25", 1, exact_lexical=True))
    task = SemanticTaskContext(
        resolved_question="三维管线管理的相关信息",
        primary_entity="三维管线管理",
        mentioned_entities=("三维管线管理",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )
    qualification = TextEvidenceAdmissionService(cfg=_live_cfg()).qualify(
        candidate,
        semantic_task=task,
        target_entity="三维管线管理",
    )
    assert qualification.verdict == "PASS"
    assert qualification.evidence_class == "RELATED_CONTEXT"
    assert qualification.support_scope == "CONTEXT_ONLY"
    return {
        "content": candidate.document.page_content,
        "metadata": {
            "chunk_id": "pipe-context-live",
            "citation_id": 1,
            "document_entity": "管线系统",
            "file_name": "pipe.md",
            "source_type": "knowledge_base",
            "support_scope": qualification.support_scope,
            "evidence_class": qualification.evidence_class,
        },
    }, qualification


@pytest.mark.integration
def test_real_prd_pipe_management_scope_rejects_target_attribution():
    """One live Reviewer call must preserve CONTEXT_ONLY instead of upgrading target attribution."""
    _live_cfg()
    reviewer = RagChain()._helper_grounding_reviewer()
    assert reviewer is not None
    doc, _qualification = _pipe_management_live_context_doc()

    result = reviewer.review(
        question="三维管线管理支持智能排管吗？",
        context_docs=[doc],
        candidate="三维管线管理支持智能排管功能 [1]。",
    )
    assert result.verdict == "REVISE"
    assert any(claim.status == "unsupported" for claim in result.claim_reviews)


@pytest.mark.integration
def test_real_prd_pipe_management_contextual_publication():
    """A scope-safe contextual answer must reach grounded_partial publication with the live Reviewer."""
    _live_cfg()
    reviewer = RagChain()._helper_grounding_reviewer()
    assert reviewer is not None
    doc, _qualification = _pipe_management_live_context_doc()
    lifecycle: list[dict] = []

    finalized = AnswerFinalizer().finalize(
        "相关管线系统资料涉及碰撞分析和智能排管 [1]；现有证据未确认这些能力直接属于三维管线管理。",
        "三维管线管理的相关信息",
        [doc],
        helper_reviewer=reviewer,
        allow_general_knowledge=False,
        on_lifecycle_event=lifecycle.append,
    )
    assert finalized.grounding.get("review_verdict") == "PASS"
    assert finalized.grounding.get("coverage") == "PARTIAL"
    assert finalized.grounding.get("final_mode") == "grounded_partial"
    assert any(event.get("type") == "publication" for event in lifecycle)


@pytest.mark.integration
def test_prd_pipeline_webgl_conflict_guard_is_stable_without_model_override():
    """Explicit different_from conflicts are deterministic and never delegated to an LLM."""
    ws = GraphWorkingSet()
    ws.add_root("PipelineWebGL", entity_id="webgl")
    ws.add_entity(GraphEntityState("builder", "PipelineBuilder", depth_from_root=1, origin_root="PipelineWebGL"))
    ws.add_relation(GraphRelationCandidate("rel-diff-live", "PipelineWebGL", "PipelineBuilder", "different_from"))
    task = SemanticTaskContext(
        resolved_question="PipelineWebGL 的主要功能是什么？",
        primary_entity="PipelineWebGL",
        mentioned_entities=("PipelineWebGL",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="function",
        requested_facets=("function",),
    )
    service = TextEvidenceAdmissionService()

    for run in range(3):
        candidate = CandidateResult(
            document=Document(
                page_content="PipelineBuilder 用于自动化构建与发布。",
                metadata={
                    "chunk_id": f"builder-conflict-{run}",
                    "document_entity": "PipelineBuilder",
                    "review_status": "approved",
                },
            ),
            target_entity="PipelineWebGL",
        )
        candidate.provenance.append(
            CandidateProvenance(
                "graph_expansion",
                1,
                graph_path=("PipelineWebGL -> PipelineBuilder",),
            )
        )
        qualification = service.qualify(
            candidate,
            semantic_task=task,
            target_entity="PipelineWebGL",
            graph_working_set=ws,
        )
        assert qualification.verdict == "REJECT"
        assert qualification.evidence_class == "CONFLICT"
        assert qualification.support_scope == "NONE"
        assert service.admitted_documents([candidate], {candidate.chunk_id: qualification}) == []


def _pipeline_webrtc_live_cross_document_doc() -> tuple[dict, object]:
    candidate = CandidateResult(
        document=Document(
            page_content="在 StampServer 部署章节中，PipelineWebRTC 用于建立低延迟实时音视频通信通道，提供实时流推流功能。",
            metadata={
                "chunk_id": "webrtc-cross-live",
                "document_entity": "StampServer",
                "review_status": "approved",
            },
        ),
        target_entity="PipelineWebRTC",
    )
    candidate.provenance.append(CandidateProvenance("bm25", 1, exact_lexical=True))
    task = SemanticTaskContext(
        resolved_question="PipelineWebRTC 的主要功能是什么？",
        primary_entity="PipelineWebRTC",
        mentioned_entities=("PipelineWebRTC",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="function",
        requested_facets=("function",),
    )
    qualification = TextEvidenceAdmissionService(cfg=_live_cfg()).qualify(
        candidate,
        semantic_task=task,
        target_entity="PipelineWebRTC",
    )
    assert qualification.verdict == "PASS"
    assert qualification.evidence_class == "TARGET_DIRECT"
    assert qualification.support_scope == "TARGET_SPECIFIC"
    return {
        "content": candidate.document.page_content,
        "metadata": {
            "chunk_id": "webrtc-cross-live",
            "citation_id": 1,
            "document_entity": "StampServer",
            "file_name": "stampserver.md",
            "source_type": "knowledge_base",
            "support_scope": qualification.support_scope,
            "evidence_class": qualification.evidence_class,
        },
    }, qualification


@pytest.mark.integration
def test_real_prd_pipeline_webrtc_cross_document_review_passes():
    """One live Reviewer call must accept cross-document TARGET_SPECIFIC evidence."""
    _live_cfg()
    reviewer = RagChain()._helper_grounding_reviewer()
    assert reviewer is not None
    doc, _qualification = _pipeline_webrtc_live_cross_document_doc()

    review = reviewer.review(
        question="PipelineWebRTC 的主要功能是什么？",
        context_docs=[doc],
        candidate="PipelineWebRTC 用于建立低延迟实时音视频通信通道 [1]。",
    )
    assert review.verdict == "PASS"
    assert all(claim.status == "supported" for claim in review.claim_reviews)


@pytest.mark.integration
def test_real_prd_pipeline_webrtc_cross_document_publication():
    """Cross-document TARGET_SPECIFIC evidence must reach grounded publication with the live Reviewer."""
    _live_cfg()
    reviewer = RagChain()._helper_grounding_reviewer()
    assert reviewer is not None
    doc, _qualification = _pipeline_webrtc_live_cross_document_doc()
    lifecycle: list[dict] = []

    finalized = AnswerFinalizer().finalize(
        "PipelineWebRTC 用于建立低延迟实时音视频通信通道 [1]。",
        "PipelineWebRTC 的主要功能是什么？",
        [doc],
        helper_reviewer=reviewer,
        allow_general_knowledge=False,
        on_lifecycle_event=lifecycle.append,
    )
    assert finalized.grounding.get("review_verdict") == "PASS"
    assert finalized.grounding.get("final_mode") in {"generated", "grounded_partial"}
    assert any(event.get("type") == "publication" for event in lifecycle)


@pytest.mark.integration
def test_real_ambiguous_pipeline_clarification_and_callback_micro_chain():
    """Verify ambiguous 'pipeline' triggers clarification snapshot and stable callback binding."""
    from rag_knowledge.services.entity_candidate_resolver import EntityCandidateResolver
    resolver = EntityCandidateResolver()

    # 1. Ambiguous query
    resolution = resolver.resolve_identity("pipeline 怎么部署？")
    assert resolution.status == "ambiguous"
    assert len(resolution.candidates) >= 2
    canonical_names = {c.canonical_name for c in resolution.candidates}
    assert "PipelineWebGL" in canonical_names
    assert "PipelineWebRTC" in canonical_names

    # 2. Snapshot creation & validation
    snapshot = resolver.create_clarification_snapshot(resolution)
    assert snapshot.clarification_id

    # 3. Valid callback selection
    cb_result = resolver.validate_callback_selection(
        selected_id_or_option="PipelineWebGL",
        snapshot_id=snapshot.clarification_id,
    )
    assert cb_result is not None
    assert cb_result.canonical_name == "PipelineWebGL"


@pytest.mark.integration
def test_real_generic_resolvers_server_builder_stamp_micro_chain():
    """Verify generic resolvers correctly handle non-pipeline keywords (server, builder, stamp)."""
    from rag_knowledge.services.entity_candidate_resolver import EntityCandidateResolver
    resolver = EntityCandidateResolver()

    server_res = resolver.resolve_identity("server 的默认配置是什么？")
    assert server_res.status == "ambiguous"
    assert any("Server" in c.canonical_name for c in server_res.candidates)

    builder_res = resolver.resolve_identity("builder 支持哪些操作系统？")
    assert builder_res.status in {"confirmed", "ambiguous"}
    assert any("Builder" in c.canonical_name for c in builder_res.candidates)

    stamp_res = resolver.resolve_identity("stamp 工具集包括哪些？")
    assert stamp_res.status in {"confirmed", "ambiguous"}
    assert any("Stamp" in c.canonical_name for c in stamp_res.candidates)


@pytest.mark.integration
def test_real_non_entity_topic_prevents_historical_drift_micro_chain():
    """Verify non-entity topic with entity_binding_required=False does not inherit previous entity."""
    from rag_knowledge.services.dialogue_understanding import SemanticTaskContext
    from rag_knowledge.services.identity_scope import IdentityScopeResolver

    task = SemanticTaskContext(
        resolved_question="今天天气怎么样？",
        primary_entity=None,
        mentioned_entities=(),
        confidence=0.0,
        entity_binding_required=False,
        task_type="general_chat",
    )
    scope = IdentityScopeResolver.resolve(
        task,
        question="今天天气怎么样？",
        previous_confirmed_entity="StampServer",
    )
    assert scope.identity_status == "not_required"
    assert scope.primary_entity is None
    assert scope.is_identity_locked is False
