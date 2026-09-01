from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document

from rag_knowledge.services.agent_candidate_pipeline import (
    CandidateProvenance,
    CandidateResult,
)
from rag_knowledge.services.agent_orchestration.graph_working_set import (
    GraphEntityState,
    GraphRelationCandidate,
    GraphWorkingSet,
)
from rag_knowledge.services.agent_orchestration.models import (
    EvidencePool,
    EvidenceSnapshot,
)
from rag_knowledge.services.dialogue_understanding import SemanticTaskContext
from rag_knowledge.services.text_evidence_admission import (
    TextEvidenceAdmissionService,
    TextEvidenceQualification,
    is_citable_text_qualification,
    resolve_entity_conflict,
    text_evidence_observation,
    valid_text_qualification_protocol,
)


def _doc(chunk_id: str, entity: str, content: str, **kwargs) -> CandidateResult:
    meta = {
        "chunk_id": chunk_id,
        "document_entity": entity,
        "review_status": "approved",
        **kwargs,
    }
    document = Document(page_content=content, metadata=meta)
    return CandidateResult(document=document, target_entity=entity)


def _working_set_with_conflict() -> GraphWorkingSet:
    ws = GraphWorkingSet()
    ws.add_root("PipelineWebGL", entity_id="webgl")
    ws.add_entity(GraphEntityState("builder", "PipelineBuilder", depth_from_root=1, origin_root="PipelineWebGL"))
    ws.add_relation(GraphRelationCandidate("rel-diff", "PipelineWebGL", "PipelineBuilder", "different_from"))
    return ws


def test_protocol_valid_combinations():
    # TARGET_DIRECT + PASS + TARGET_SPECIFIC
    q1 = TextEvidenceQualification(
        verdict="PASS",
        evidence_class="TARGET_DIRECT",
        support_scope="TARGET_SPECIFIC",
        intent_relevance="HIGH",
        reason_code="direct_mention",
        reason="Direct target mention",
    )
    assert valid_text_qualification_protocol(q1) is True

    # RELATED_CONTEXT + PASS + CONTEXT_ONLY
    q2 = TextEvidenceQualification(
        verdict="PASS",
        evidence_class="RELATED_CONTEXT",
        support_scope="CONTEXT_ONLY",
        intent_relevance="HIGH",
        reason_code="domain_related",
        reason="Related domain context",
    )
    assert valid_text_qualification_protocol(q2) is True

    # CONFLICT + REJECT + NONE
    q3 = TextEvidenceQualification(
        verdict="REJECT",
        evidence_class="CONFLICT",
        support_scope="NONE",
        intent_relevance="NONE",
        reason_code="explicit_conflict",
        reason="Conflicting entity",
    )
    assert valid_text_qualification_protocol(q3) is True

    # IRRELEVANT + REJECT + NONE
    q4 = TextEvidenceQualification(
        verdict="REJECT",
        evidence_class="IRRELEVANT",
        support_scope="NONE",
        intent_relevance="LOW",
        reason_code="no_relevance",
        reason="No relation",
    )
    assert valid_text_qualification_protocol(q4) is True


def test_working_observation_keeps_conflict_attribution_but_marks_it_not_citable():
    candidate = _doc(
        "builder-conflict",
        "PipelineBuilder",
        "PipelineBuilder 用于编译和发布。",
    )
    qualification = TextEvidenceQualification(
        verdict="REJECT",
        evidence_class="CONFLICT",
        support_scope="NONE",
        intent_relevance="HIGH",
        reason_code="explicit_entity_conflict",
        reason="PipelineBuilder is different_from PipelineWebGL.",
    )

    observation = text_evidence_observation(
        candidate,
        qualification,
        target_entity="PipelineWebGL",
    )

    assert observation["document_entity"] == "PipelineBuilder"
    assert observation["relation_to_subject"] == "DIFFERENT_ENTITY"
    assert observation["evidence_class"] == "CONFLICT"
    assert observation["support_scope"] == "NONE"
    assert observation["relevance"] == "HIGH"
    assert observation["citable"] is False
    assert is_citable_text_qualification(qualification) is False


@pytest.mark.parametrize(
    ("qualification", "expected_citable"),
    [
        (
            TextEvidenceQualification(
                verdict="REJECT", evidence_class="CONFLICT", support_scope="NONE",
                intent_relevance="HIGH", reason_code="conflict", reason="different entity",
            ),
            False,
        ),
        (
            TextEvidenceQualification(
                verdict="PASS", evidence_class="RELATED_CONTEXT", support_scope="CONTEXT_ONLY",
                intent_relevance="HIGH", reason_code="context", reason="related context",
            ),
            True,
        ),
        (
            TextEvidenceQualification(
                verdict="PASS", evidence_class="TARGET_DIRECT", support_scope="TARGET_SPECIFIC",
                intent_relevance="HIGH", reason_code="direct", reason="direct support",
            ),
            True,
        ),
    ],
)
def test_two_layer_routing_gold(qualification, expected_citable):
    candidate = _doc("two-layer", "PipelineBuilder", "candidate content")
    docs = TextEvidenceAdmissionService.qualified_documents(
        [candidate], {candidate.chunk_id: qualification}
    )
    pool = EvidencePool(question_id="two-layer")
    pool.add_retrieve(
        [{"content": doc.page_content, "metadata": dict(doc.metadata)} for doc in docs],
        query="question",
    )

    assert len(pool.working_docs()) == 1
    assert bool(pool.citable_docs()) is expected_citable
    assert pool.to_trace()[0]["citable_chunk_ids"] == (
        [candidate.chunk_id] if expected_citable else []
    )
    snapshot = pool.create_snapshot(verdict={"coverage": "PARTIAL"})
    assert bool(snapshot.documents()) is expected_citable


def test_protocol_invalid_combinations_rejected():
    # CONFLICT + PASS
    bad1 = TextEvidenceQualification(
        verdict="PASS",
        evidence_class="CONFLICT",
        support_scope="NONE",
        intent_relevance="HIGH",
        reason_code="err",
        reason="err",
    )
    assert valid_text_qualification_protocol(bad1) is False

    # IRRELEVANT + PASS
    bad2 = TextEvidenceQualification(
        verdict="PASS",
        evidence_class="IRRELEVANT",
        support_scope="NONE",
        intent_relevance="HIGH",
        reason_code="err",
        reason="err",
    )
    assert valid_text_qualification_protocol(bad2) is False

    # RELATED_CONTEXT + TARGET_SPECIFIC
    bad3 = TextEvidenceQualification(
        verdict="PASS",
        evidence_class="RELATED_CONTEXT",
        support_scope="TARGET_SPECIFIC",
        intent_relevance="HIGH",
        reason_code="err",
        reason="err",
    )
    assert valid_text_qualification_protocol(bad3) is False

    # TARGET_DIRECT + NONE
    bad4 = TextEvidenceQualification(
        verdict="PASS",
        evidence_class="TARGET_DIRECT",
        support_scope="NONE",
        intent_relevance="HIGH",
        reason_code="err",
        reason="err",
    )
    assert valid_text_qualification_protocol(bad4) is False


def test_no_entity_link_does_not_mean_conflict():
    candidate = _doc("chunk-1", "三维管线", "管线系统支持碰撞分析和智能排管。")
    ws = GraphWorkingSet()
    ws.add_root("三维管线管理", entity_id="pipe_mgmt")

    status, signals = resolve_entity_conflict(candidate, target_entity="三维管线管理", graph_working_set=ws)
    assert status == "NO_CONFLICT"


def test_document_entity_mismatch_is_not_hard_reject():
    candidate = _doc("chunk-2", "StampServer", "PipelineWebRTC 支持通过配置连接实时流。")
    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="PipelineWebRTC 的主要功能是什么？",
        primary_entity="PipelineWebRTC",
        mentioned_entities=("PipelineWebRTC",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )
    qual = service.qualify(
        candidate,
        semantic_task=task,
        target_entity="PipelineWebRTC",
        semantic_admitter=lambda query, _candidate, pending: TextEvidenceQualification(
            verdict="PASS",
            evidence_class="TARGET_DIRECT",
            support_scope="TARGET_SPECIFIC",
            intent_relevance="HIGH",
            reason_code="semantic_direct_attribution",
            reason="Candidate text directly attributes the fact to PipelineWebRTC.",
            signals=pending.signals,
            canonical_question=query,
        ),
    )
    assert qual.verdict == "PASS"
    assert qual.evidence_class == "TARGET_DIRECT"
    assert qual.support_scope == "TARGET_SPECIFIC"


def test_different_from_is_explicit_conflict():
    candidate = _doc("chunk-3", "PipelineBuilder", "PipelineBuilder 的构建流程说明。")
    candidate.provenance.append(
        CandidateProvenance("graph_expansion", 1, graph_path=("PipelineWebGL -> PipelineBuilder",))
    )
    ws = _working_set_with_conflict()
    status, signals = resolve_entity_conflict(candidate, target_entity="PipelineWebGL", graph_working_set=ws)
    assert status == "EXPLICIT_CONFLICT"

    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="PipelineWebGL 的主要功能是什么？",
        primary_entity="PipelineWebGL",
        mentioned_entities=("PipelineWebGL",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )
    qual = service.qualify(candidate, semantic_task=task, target_entity="PipelineWebGL", graph_working_set=ws)
    assert qual.verdict == "REJECT"
    assert qual.evidence_class == "CONFLICT"
    assert qual.support_scope == "NONE"
    assert valid_text_qualification_protocol(qual) is True


def test_conflict_candidate_cannot_pass_protocol():
    candidate = _doc("chunk-3-conflict", "PipelineBuilder", "PipelineBuilder 构建流程。")
    candidate.provenance.append(
        CandidateProvenance("graph_expansion", 1, graph_path=("PipelineWebGL -> PipelineBuilder",))
    )
    ws = _working_set_with_conflict()
    service = TextEvidenceAdmissionService()

    def rogue_admitter(query, cand, deterministic):
        return TextEvidenceQualification(
            verdict="PASS",
            evidence_class="TARGET_DIRECT",
            support_scope="TARGET_SPECIFIC",
            intent_relevance="HIGH",
            reason_code="rogue_pass",
            reason="I want to pass",
            canonical_question=query,
        )

    task = SemanticTaskContext(
        resolved_question="PipelineWebGL 的主要功能是什么？",
        primary_entity="PipelineWebGL",
        mentioned_entities=("PipelineWebGL",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )
    # Even if rogue semantic admitter is passed, Step 1 Conflict Guard rejects before semantic admission!
    qual = service.qualify(
        candidate,
        semantic_task=task,
        target_entity="PipelineWebGL",
        graph_working_set=ws,
        semantic_admitter=rogue_admitter,
    )
    assert qual.verdict == "REJECT"
    assert qual.evidence_class == "CONFLICT"
    assert qual.support_scope == "NONE"
    assert valid_text_qualification_protocol(qual) is True


def test_direct_target_mention_low_intent_rejection_satisfies_protocol():
    candidate = _doc("chunk-dep", "PipelineWebRTC", "PipelineWebRTC 安装包上传至 /data/html 目录。")
    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="PipelineWebRTC 的主要功能是什么？",
        primary_entity="PipelineWebRTC",
        mentioned_entities=("PipelineWebRTC",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="definition",
    )
    qual = service.qualify(candidate, semantic_task=task, target_entity="PipelineWebRTC")
    assert qual.verdict == "REJECT"
    assert qual.evidence_class == "IRRELEVANT"
    assert qual.support_scope == "NONE"
    assert valid_text_qualification_protocol(qual) is True


def test_target_direct_requires_semantic_attribution_even_with_exact_local_mention():
    candidate = _doc("chunk-4", "StampServer", "在 StampServer 部署章节中，PipelineWebRTC 用于建立实时通道。")
    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="PipelineWebRTC 的主要功能是什么？",
        primary_entity="PipelineWebRTC",
        mentioned_entities=("PipelineWebRTC",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )
    qual = service.qualify(
        candidate,
        semantic_task=task,
        target_entity="PipelineWebRTC",
        semantic_admitter=lambda query, _candidate, pending: TextEvidenceQualification(
            verdict="PASS",
            evidence_class="TARGET_DIRECT",
            support_scope="TARGET_SPECIFIC",
            intent_relevance="HIGH",
            reason_code="semantic_direct_attribution",
            reason="Candidate text directly attributes the fact to PipelineWebRTC.",
            signals=pending.signals,
            canonical_question=query,
        ),
    )
    assert qual.verdict == "PASS"
    assert qual.evidence_class == "TARGET_DIRECT"
    assert qual.support_scope == "TARGET_SPECIFIC"
    assert "target_text_mention" in qual.signals
    assert valid_text_qualification_protocol(qual) is True


def test_helper_direct_without_attribution_candidate_is_downgraded(monkeypatch):
    candidate = _doc("chunk-no-attribution", "管线系统", "管线系统支持碰撞分析和智能排管。")
    task = SemanticTaskContext(
        resolved_question="三维管线管理的相关信息",
        primary_entity="三维管线管理",
        mentioned_entities=("三维管线管理",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )
    captured: dict[str, object] = {}

    def fake_chat_role(_cfg, _role, messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        captured["schema"] = kwargs["json_schema"]
        return json.dumps({
            "verdict": "PASS",
            "evidence_class": "TARGET_DIRECT",
            "support_scope": "TARGET_SPECIFIC",
            "intent_relevance": "HIGH",
            "reason_code": "incorrect_direct_attribution",
            "reason": "Incorrectly inferred target ownership from domain similarity.",
            "signals": [],
        })

    monkeypatch.setattr("rag_knowledge.llm_http.chat_role", fake_chat_role)

    qual = TextEvidenceAdmissionService(cfg=SimpleNamespace()).qualify(
        candidate,
        semantic_task=task,
        target_entity="三维管线管理",
    )

    assert qual.verdict == "PASS"
    assert qual.evidence_class == "RELATED_CONTEXT"
    assert qual.support_scope == "CONTEXT_ONLY"
    assert qual.reason_code == "direct_scope_downgraded_without_attribution_candidate"
    assert "target_direct_scope_downgraded" in qual.signals
    assert '"target_direct_eligibility": false' in str(captured["prompt"])
    assert "RELATED_CONTEXT 不要求候选写出目标的完整名称" in str(captured["prompt"])
    assert "TARGET_DIRECT" not in captured["schema"]["properties"]["evidence_class"]["enum"]
    assert "TARGET_SPECIFIC" not in captured["schema"]["properties"]["support_scope"]["enum"]


def test_semantic_admitter_cannot_bypass_direct_attribution_ceiling():
    candidate = _doc("chunk-admitter-ceiling", "管线系统", "管线系统支持碰撞分析和智能排管。")
    task = SemanticTaskContext(
        resolved_question="三维管线管理的相关信息",
        primary_entity="三维管线管理",
        mentioned_entities=("三维管线管理",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )

    qual = TextEvidenceAdmissionService().qualify(
        candidate,
        semantic_task=task,
        target_entity="三维管线管理",
        semantic_admitter=lambda query, _candidate, _pending: TextEvidenceQualification(
            verdict="PASS",
            evidence_class="TARGET_DIRECT",
            support_scope="TARGET_SPECIFIC",
            intent_relevance="HIGH",
            reason_code="rogue_direct_attribution",
            reason="A provider attempted to over-grant target-specific scope.",
            canonical_question=query,
        ),
    )

    assert qual.verdict == "PASS"
    assert qual.evidence_class == "RELATED_CONTEXT"
    assert qual.support_scope == "CONTEXT_ONLY"
    assert qual.reason_code == "direct_scope_downgraded_without_attribution_candidate"


def test_target_direct_cross_document_entity():
    candidate = _doc("chunk-5", "OtherApp", "PipelineWebRTC 可在 OtherApp 环境下运行。")
    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="PipelineWebRTC 的相关信息",
        primary_entity="PipelineWebRTC",
        mentioned_entities=("PipelineWebRTC",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )
    qual = service.qualify(
        candidate,
        semantic_task=task,
        target_entity="PipelineWebRTC",
        semantic_admitter=lambda query, _candidate, _pending: TextEvidenceQualification(
            verdict="PASS",
            evidence_class="TARGET_DIRECT",
            support_scope="TARGET_SPECIFIC",
            intent_relevance="HIGH",
            reason_code="semantic_direct_attribution",
            reason="Candidate text directly attributes the fact to PipelineWebRTC.",
            canonical_question=query,
        ),
    )
    assert qual.verdict == "PASS"
    assert qual.evidence_class == "TARGET_DIRECT"
    assert qual.support_scope == "TARGET_SPECIFIC"
    assert valid_text_qualification_protocol(qual) is True


def test_related_context_without_direct_attribution():
    candidate = _doc("chunk-6", "管线系统", "三维管线系统支持碰撞分析、覆土分析和智能排管。")
    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="三维管线管理的相关信息",
        primary_entity="三维管线管理",
        mentioned_entities=("三维管线管理",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )
    qual = service.qualify(
        candidate,
        semantic_task=task,
        target_entity="三维管线管理",
        semantic_admitter=lambda query, _candidate, _pending: TextEvidenceQualification(
            verdict="PASS",
            evidence_class="RELATED_CONTEXT",
            support_scope="CONTEXT_ONLY",
            intent_relevance="HIGH",
            reason_code="semantic_related_context",
            reason="Candidate is relevant context only.",
            canonical_question=query,
        ),
    )
    assert qual.verdict == "PASS"
    assert qual.evidence_class == "RELATED_CONTEXT"
    assert qual.support_scope == "CONTEXT_ONLY"
    assert valid_text_qualification_protocol(qual) is True


def test_related_context_scope_is_decided_by_semantic_admission():
    candidate = _doc("chunk-7", "管线系统", "三维管线系统支持碰撞分析。")
    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="三维管线管理的相关信息",
        primary_entity="三维管线管理",
        mentioned_entities=("三维管线管理",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )

    def mock_admitter(query, cand, deterministic):
        return TextEvidenceQualification(
            verdict="PASS",
            evidence_class="RELATED_CONTEXT",
            support_scope="CONTEXT_ONLY",
            intent_relevance="HIGH",
            reason_code="semantic_related_context",
            reason="The candidate is contextual, not a target-attributed proposition.",
            canonical_question=query,
        )

    qual = service.qualify(
        candidate,
        semantic_task=task,
        target_entity="三维管线管理",
        semantic_admitter=mock_admitter,
    )
    assert qual.evidence_class == "RELATED_CONTEXT"
    assert qual.support_scope == "CONTEXT_ONLY"
    assert qual.verdict == "PASS"
    assert valid_text_qualification_protocol(qual) is True


def test_irrelevant_candidate_rejected():
    candidate = _doc("chunk-8", "WebRTC", "WebRTC 外部端口配置为 3478。")
    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="三维管线管理的相关信息",
        primary_entity="三维管线管理",
        mentioned_entities=("三维管线管理",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )
    qual = service.qualify(candidate, semantic_task=task, target_entity="三维管线管理")
    assert qual.verdict == "REJECT"
    assert qual.evidence_class == "IRRELEVANT"
    assert qual.support_scope == "NONE"
    assert valid_text_qualification_protocol(qual) is True


def test_general_qa_accepts_related_context_after_semantic_admission():
    candidate = _doc("chunk-9", "管线模型", "管线模型包括重点管线、管理范围和保护区监测。")
    candidate.provenance.append(CandidateProvenance("bm25", 1, exact_lexical=True))
    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="三维管线管理有哪些功能？",
        primary_entity="三维管线管理",
        mentioned_entities=("三维管线管理",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )
    qual = service.qualify(
        candidate,
        semantic_task=task,
        target_entity="三维管线管理",
        semantic_admitter=lambda query, _candidate, _pending: TextEvidenceQualification(
            verdict="PASS",
            evidence_class="RELATED_CONTEXT",
            support_scope="CONTEXT_ONLY",
            intent_relevance="HIGH",
            reason_code="semantic_related_context",
            reason="Candidate is relevant context only.",
            canonical_question=query,
        ),
    )
    assert qual.verdict == "PASS"
    assert qual.evidence_class == "RELATED_CONTEXT"
    assert qual.support_scope == "CONTEXT_ONLY"
    assert valid_text_qualification_protocol(qual) is True


def test_parent_fact_does_not_transfer_to_child():
    candidate = _doc("chunk-10", "PipelineWebGL", "PipelineWebGL 具备高并发渲染能力。")
    # Candidate came from neighbor chunk link: entity_link=True, linked_entity="PipelineWebGL"
    candidate.provenance.append(
        CandidateProvenance("graph_entity_chunk_link", 1, entity_link=True, linked_entity="PipelineWebGL")
    )
    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="三维管线管理的主要功能是什么？",
        primary_entity="三维管线管理",
        mentioned_entities=("三维管线管理",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )
    qual = service.qualify(candidate, semantic_task=task, target_entity="三维管线管理")
    # PipelineWebGL parent facts without child mention cannot become child's TARGET_DIRECT
    assert qual.evidence_class != "TARGET_DIRECT"
    assert qual.support_scope != "TARGET_SPECIFIC"
    assert valid_text_qualification_protocol(qual) is True


def test_general_qa_graph_path_alone_does_not_authorize_text_evidence():
    candidate = _doc("chunk-graph-only", "WebRTC", "WebRTC 外网 IP 配置说明。")
    candidate.provenance.append(
        CandidateProvenance("graph_expansion", 1, graph_path=("三维管线管理 -> WebRTC",))
    )
    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="三维管线管理的相关信息",
        primary_entity="三维管线管理",
        mentioned_entities=("三维管线管理",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )

    qual = service.qualify(candidate, semantic_task=task, target_entity="三维管线管理")

    assert qual.verdict == "REJECT"
    assert qual.evidence_class == "IRRELEVANT"
    assert qual.support_scope == "NONE"
    assert "graph_provenance" in qual.signals
    assert valid_text_qualification_protocol(qual) is True


def test_general_qa_graph_only_semantic_failure_fails_closed():
    candidate = _doc("chunk-graph-helper-fail", "WebRTC", "WebRTC 外网 IP 配置说明。")
    candidate.provenance.append(
        CandidateProvenance("graph_expansion", 1, graph_path=("三维管线管理 -> WebRTC",))
    )
    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="三维管线管理的相关信息",
        primary_entity="三维管线管理",
        mentioned_entities=("三维管线管理",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
    )

    qual = service.qualify(
        candidate,
        semantic_task=task,
        target_entity="三维管线管理",
        semantic_admitter=lambda *_args: None,
    )

    assert qual.verdict == "REJECT"
    assert qual.evidence_class == "IRRELEVANT"
    assert qual.reason_code == "semantic_admission_required"


def test_text_qualification_structured_output_schema_matches_protocol():
    from rag_knowledge.services.text_evidence_admission import text_qualification_response_json_schema

    schema = text_qualification_response_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "verdict", "evidence_class", "support_scope", "intent_relevance",
        "reason_code", "reason", "signals",
    }
    assert schema["properties"]["evidence_class"]["enum"] == [
        "TARGET_DIRECT", "RELATED_CONTEXT", "CONFLICT", "IRRELEVANT",
    ]
    assert schema["properties"]["support_scope"]["enum"] == [
        "TARGET_SPECIFIC", "CONTEXT_ONLY", "NONE",
    ]
    context_only_schema = text_qualification_response_json_schema(direct_attribution_eligible=False)
    assert "TARGET_DIRECT" not in context_only_schema["properties"]["evidence_class"]["enum"]
    assert "TARGET_SPECIFIC" not in context_only_schema["properties"]["support_scope"]["enum"]


def test_graph_path_does_not_authorize_text_evidence():
    # Irrelevant content even with graph_path must be rejected
    candidate = _doc("chunk-11", "WebRTC", "不相关的测试说明。")
    candidate.provenance.append(CandidateProvenance("graph_expansion", 1, graph_path=("三维管线管理 -> WebRTC",)))
    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="三维管线管理的端口是什么？",
        primary_entity="三维管线管理",
        mentioned_entities=("三维管线管理",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="facet_query",
        requested_facets=("端口",),
    )
    qual = service.qualify(candidate, semantic_task=task, target_entity="三维管线管理")
    assert qual.verdict == "REJECT"
    assert qual.evidence_class == "IRRELEVANT"
    assert valid_text_qualification_protocol(qual) is True


def test_reused_evidence_is_requalified_per_query():
    # If a document was TARGET_DIRECT for previous query (PipelineWebRTC),
    # but the new query is about PipelineBuilder, it must be requalified.
    candidate = _doc("chunk-12", "PipelineWebRTC", "PipelineWebRTC 用于实时通信。")
    ws = GraphWorkingSet()
    ws.add_root("PipelineBuilder", entity_id="bld")
    ws.add_entity(GraphEntityState("webrtc", "PipelineWebRTC", depth_from_root=1, origin_root="PipelineBuilder"))
    ws.add_relation(GraphRelationCandidate("rel-1", "PipelineBuilder", "PipelineWebRTC", "different_from"))

    service = TextEvidenceAdmissionService()
    task = SemanticTaskContext(
        resolved_question="PipelineBuilder 的部署方式",
        primary_entity="PipelineBuilder",
        mentioned_entities=("PipelineBuilder",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="deployment",
    )
    qual = service.qualify(candidate, semantic_task=task, target_entity="PipelineBuilder", graph_working_set=ws)
    assert qual.verdict == "REJECT"
    assert qual.evidence_class == "CONFLICT"
    assert valid_text_qualification_protocol(qual) is True


def test_target_mention_does_not_override_explicit_sibling_conflict():
    candidate = _doc(
        "chunk-mixed-conflict",
        "PipelineBuilder",
        "PipelineBuilder 与 PipelineWebGL 不同；PipelineBuilder 用于构建项目。",
    )
    ws = _working_set_with_conflict()

    status, signals = resolve_entity_conflict(
        candidate,
        target_entity="PipelineWebGL",
        graph_working_set=ws,
    )

    assert status == "EXPLICIT_CONFLICT"
    assert signals == ["explicit_sibling_conflict:PipelineBuilder"]


def test_working_only_text_without_support_scope_is_excluded_from_snapshot():
    pool = EvidencePool(question_id="q-missing-scope")
    pool.add_retrieve(
        [{
            "content": "PipelineWebRTC 用于建立实时通道。",
            "metadata": {
                "chunk_id": "c-missing-scope",
                "evidence_class": "TARGET_DIRECT",
            },
        }],
        query="PipelineWebRTC 的主要功能是什么？",
        target_entity="PipelineWebRTC",
    )

    assert len(pool.working_docs()) == 1
    assert pool.citable_docs() == []
    assert pool.create_snapshot(verdict={"coverage": "PARTIAL"}).documents() == []


def test_unbound_working_evidence_cannot_enter_frozen_snapshot():
    pool = EvidencePool(question_id="q-unbound")
    pool.add_retrieve(
        [{
            "content": "PipelineWebRTC 用于建立实时通道。",
            "metadata": {
                "chunk_id": "c-unbound",
                "evidence_class": "TARGET_DIRECT",
                "support_scope": "TARGET_SPECIFIC",
                "citable": True,
                "scope_binding_strength": "unbound",
            },
        }],
        query="pipeline 是什么？",
    )

    assert len(pool.working_docs()) == 1
    assert pool.citable_docs() == []
    assert pool.create_snapshot(verdict={"coverage": "PARTIAL"}).documents() == []


def test_snapshot_freezes_support_scope():
    pool = EvidencePool(question_id="q-test")
    doc1 = {
        "content": "三维管线系统支持碰撞分析。",
        "metadata": {
            "chunk_id": "c1",
            "citation_id": 1,
            "document_entity": "管线系统",
            "evidence_class": "RELATED_CONTEXT",
            "support_scope": "CONTEXT_ONLY",
        },
    }
    pool.add_retrieve([doc1], query="三维管线管理", target_entity="三维管线管理")
    snapshot = pool.create_snapshot(verdict={"coverage": "PARTIAL"})

    docs = snapshot.documents()
    assert len(docs) == 1
    assert docs[0]["metadata"]["support_scope"] == "CONTEXT_ONLY"
    assert docs[0]["metadata"]["evidence_class"] == "RELATED_CONTEXT"
