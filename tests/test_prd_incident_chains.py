"""PRD DoD Acceptance Tests: Three Core Business Incident Chains.

1. Incident Chain 1: 三维管线管理
   - Recalls RELATED_CONTEXT without direct mention.
   - Support scope is frozen at CONTEXT_ONLY (cannot upgrade to TARGET_SPECIFIC).
   - Facet / Function coverage is capped at PARTIAL.
   - Grounding Reviewer blocks direct target attribution and accepts context expressions.

2. Incident Chain 2: PipelineWebGL / PipelineBuilder
   - Sibling conflict under different_from relation.
   - Graph expansion provenance alone does not bypass conflict guard.
   - Final qualification is CONFLICT + REJECT + NONE (0 contamination in evidence snapshot).

3. Incident Chain 3: PipelineWebRTC
   - Cross-document entity candidate with exact local target mention in body text.
   - Mention/document metadata remain candidate signals; semantic qualification decides local attribution.
   - Document entity mismatch does not block admission -> TARGET_DIRECT + PASS + TARGET_SPECIFIC when Helper confirms direct attribution.
"""

from __future__ import annotations

import json
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
    ConversationContext,
    EvidencePool,
)
from rag_knowledge.services.agent_orchestration.runtime import FinalizationHandler
from rag_knowledge.services.dialogue_understanding import SemanticTaskContext
from rag_knowledge.services.helper_grounding_reviewer import HelperGroundingReviewer
from rag_knowledge.services.text_evidence_admission import (
    TextEvidenceAdmissionService,
    TextEvidenceQualification,
    valid_text_qualification_protocol,
)


def _candidate(chunk_id: str, doc_entity: str, content: str, **kwargs) -> CandidateResult:
    meta = {
        "chunk_id": chunk_id,
        "document_entity": doc_entity,
        "review_status": "approved",
        **kwargs,
    }
    doc = Document(page_content=content, metadata=meta)
    return CandidateResult(document=doc, target_entity=kwargs.get("target_entity", doc_entity))


def test_dod_incident_chain_1_pipe_management_related_context():
    """Chain 1: 三维管线管理 -> RELATED_CONTEXT -> CONTEXT_ONLY -> Coverage PARTIAL -> Grounded Partial."""
    # Step 1: Candidate has domain term overlap (管线系统) but no direct 三维管线管理 attribution
    candidate = _candidate(
        "pipe-chunk-1",
        "管线系统",
        "管线系统提供管网碰撞分析、覆土深度计算与智能排管设计等综合功能。",
        target_entity="三维管线管理",
    )
    candidate.provenance.append(CandidateProvenance("bm25", 1, exact_lexical=False))

    task = SemanticTaskContext(
        resolved_question="三维管线管理的相关信息",
        primary_entity="三维管线管理",
        mentioned_entities=("三维管线管理",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="general_qa",
        requested_facets=(),
    )

    service = TextEvidenceAdmissionService()
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
            reason="Candidate is semantically relevant context but does not directly attribute facts to the target.",
            signals=("semantic_context_match",),
            canonical_question=query,
            answer_intent="general_qa",
        ),
    )

    # Qualification assertions
    assert qual.verdict == "PASS"
    assert qual.evidence_class == "RELATED_CONTEXT"
    assert qual.support_scope == "CONTEXT_ONLY"
    assert valid_text_qualification_protocol(qual) is True

    # Step 2: Pool & Snapshot & Coverage Evaluation
    conv = ConversationContext.from_request("三维管线管理的相关信息", [], entity_name="三维管线管理")
    conv.semantic_task = task
    conv.resolved_question = task.resolved_question

    pool = EvidencePool(question_id="pipe-q1")
    admitted_doc = {
        "content": candidate.document.page_content,
        "metadata": {
            "chunk_id": "pipe-chunk-1",
            "citation_id": 1,
            "document_entity": "管线系统",
            "evidence_target_entity": "三维管线管理",
            "candidate_pipeline_v2": True,
            "admission_verdict": qual.verdict,
            "support_scope": qual.support_scope,
            "text_evidence_class": qual.evidence_class,
            "grant_id": "grant-pipe-1",
            "grant_admitted": True,
            "identity_scope_id": conv.scope.scope_id,
        },
    }
    pool.add_retrieve(
        [admitted_doc],
        query="三维管线管理 功能",
        target_entity="三维管线管理",
        grant_id="grant-pipe-1",
    )

    finalization = FinalizationHandler(conv, pool).evaluate(answer_mode="partial")
    assert finalization["status"] == "accepted"
    # Coverage MUST be PARTIAL (because only CONTEXT_ONLY evidence exists, no direct TARGET_SPECIFIC evidence)
    assert finalization["evidence_verdict"]["coverage"] == "PARTIAL"
    assert finalization["evidence_verdict"]["can_answer"] is True

    # Step 3: Grounding Reviewer Assertion
    # Case 3a: Candidate directly claims target entity has feature -> REVISE
    mock_revise_json = json.dumps({
        "coverage": "PARTIAL",
        "summary": "目标直接断言受 CONTEXT_ONLY 证据限制无法直接证明",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "三维管线管理支持智能排管设计与碰撞分析。",
                "claim_type": "knowledge_claim",
                "status": "unsupported",
                "evidence_ids": [1],
                "reason": "证据 1 (CONTEXT_ONLY) 仅为管线系统上下文资料，未证明三维管线管理自身直接具备该功能",
            }
        ],
        "rewrite_actions": [
            {
                "claim_id": "c1",
                "action": "rewrite_to_supported_scope_or_remove",
                "instruction": "改写为'相关管线系统资料涉及智能排管设计与碰撞分析'",
            }
        ],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_revise_json)
    rev_result = reviewer.review(
        "三维管线管理的主要功能是什么？",
        [admitted_doc],
        "三维管线管理支持智能排管设计与碰撞分析。[1]",
    )
    assert rev_result.verdict == "REVISE"
    assert len(rev_result.unsupported_claims) == 1

    # Case 3b: Candidate uses contextual claim -> PASS (grounded_partial)
    mock_pass_json = json.dumps({
        "coverage": "PARTIAL",
        "summary": "上下文断言受 CONTEXT_ONLY 证据支持",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "相关管线系统资料涉及管网碰撞分析与智能排管设计。",
                "claim_type": "knowledge_claim",
                "status": "supported",
                "evidence_ids": [1],
                "reason": "证据 1 直接支持管线系统的相关功能描述",
            }
        ],
        "rewrite_actions": [],
    })
    reviewer_pass = HelperGroundingReviewer(lambda _msgs: mock_pass_json)
    pass_result = reviewer_pass.review(
        "三维管线管理的主要功能是什么？",
        [admitted_doc],
        "相关管线系统资料涉及管网碰撞分析与智能排管设计。[1]",
    )
    assert pass_result.verdict == "PASS"
    assert pass_result.coverage == "PARTIAL"


def test_dod_incident_chain_2_pipeline_builder_conflict_zero_contamination():
    """Chain 2: PipelineWebGL vs PipelineBuilder (different_from) -> CONFLICT -> REJECT -> 0 contamination."""
    ws = GraphWorkingSet()
    ws.add_root("PipelineWebGL", entity_id="webgl")
    ws.add_entity(GraphEntityState("builder", "PipelineBuilder", depth_from_root=1, origin_root="PipelineWebGL"))
    ws.add_relation(GraphRelationCandidate("rel-diff", "PipelineWebGL", "PipelineBuilder", "different_from"))

    # PipelineBuilder candidate retrieved via graph expansion path
    candidate = _candidate(
        "builder-chunk-1",
        "PipelineBuilder",
        "PipelineBuilder 用于自动化构建 WebGL 场景与发布管线包。",
        target_entity="PipelineWebGL",
    )
    candidate.provenance.append(
        CandidateProvenance("graph_expansion", 1, graph_path=("PipelineWebGL -> PipelineBuilder",))
    )

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
    qual = service.qualify(
        candidate,
        semantic_task=task,
        target_entity="PipelineWebGL",
        graph_working_set=ws,
    )

    # Must be strictly CONFLICT + REJECT + NONE
    assert qual.verdict == "REJECT"
    assert qual.evidence_class == "CONFLICT"
    assert qual.support_scope == "NONE"
    assert valid_text_qualification_protocol(qual) is True

    # When converted to admitted documents, 0 documents pass
    admitted = TextEvidenceAdmissionService.admitted_documents([candidate], {candidate.chunk_id: qual})
    assert len(admitted) == 0


def test_dod_incident_chain_3_pipeline_webrtc_cross_doc_entity_direct_mention():
    """Chain 3: cross-document candidate -> semantic direct attribution -> TARGET_DIRECT."""
    # Document belongs to StampServer, but body text explicitly describes PipelineWebRTC
    candidate = _candidate(
        "webrtc-chunk-cross",
        "StampServer",
        "在 StampServer 部署章节中，PipelineWebRTC 用于建立低延迟实时音视频通信通道，提供实时流推流功能。",
        target_entity="PipelineWebRTC",
    )
    candidate.provenance.append(CandidateProvenance("bm25", 1, exact_lexical=True))

    task = SemanticTaskContext(
        resolved_question="PipelineWebRTC 的主要功能是什么？",
        primary_entity="PipelineWebRTC",
        mentioned_entities=("PipelineWebRTC",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="definition",
        requested_facets=("function",),
    )

    service = TextEvidenceAdmissionService()
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
            reason="The local proposition explicitly attributes the described function to PipelineWebRTC.",
            signals=("semantic_local_attribution",),
            canonical_question=query,
            answer_intent="definition",
        ),
    )

    # Mismatched document_entity (StampServer) must NOT block admission.
    # Mention/metadata are candidate signals; Helper confirms TARGET_DIRECT.
    assert qual.verdict == "PASS"
    assert qual.evidence_class == "TARGET_DIRECT"
    assert qual.support_scope == "TARGET_SPECIFIC"
    assert "semantic_local_attribution" in qual.signals
    assert valid_text_qualification_protocol(qual) is True

    admitted = TextEvidenceAdmissionService.admitted_documents([candidate], {candidate.chunk_id: qual})
    assert len(admitted) == 1
    assert admitted[0].metadata["support_scope"] == "TARGET_SPECIFIC"
    assert admitted[0].metadata["text_evidence_class"] == "TARGET_DIRECT"
