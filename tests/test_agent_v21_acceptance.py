"""Targeted acceptance test suite for Dialogue Agent V2.1 orchestration invariants.

Covers:
1. Gap exhaustion: strictly consecutive failures (failure -> success -> failure does not trigger exhaustion).
2. Working evidence across Reviewer Resume: CONFLICT / IRRELEVANT retained with resume_working_only, never auto-promoted to citable.
3. GraphWorkingSet cross-resume: full state preservation via from_trace().
4. search_focus_text / focus_entity_id protocol: free search hypotheses vs verified entity reference.
5. Evidence epoch evolution: entity changes trigger epoch bump, old epoch excluded from citable.
6. Publication taxonomy: accounting-backed classification (retrieval_blocked, retrieved_no_hits, retrieved_no_support, no_safe_answer).
7. Working Evidence Compaction: priority sorting (TARGET_DIRECT > CONFLICT > RELATED_CONTEXT > IRRELEVANT).
8. Single source of truth for Trace citable derivation.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from rag_knowledge.services.agent_orchestration.graph_working_set import (
    GraphEntityState,
    GraphRelationCandidate,
    GraphWorkingSet,
)
from rag_knowledge.services.agent_orchestration.models import (
    AgentBudget,
    AgentDecision,
    AttemptedGapRegistry,
    ConversationContext,
    EvidenceGroup,
    EvidencePool,
    ToolObservation,
    ToolProgressStatus,
)
from rag_knowledge.services.agent_orchestration.runtime import (
    AgentLoop,
    build_agent_registry,
    build_phase1_registry,
)


def _text_doc(
    chunk_id: str,
    content: str = "test content",
    *,
    evidence_class: str = "TARGET_DIRECT",
    support_scope: str = "TARGET_SPECIFIC",
    entity: str = "EntityA",
    citation_id: int = 1,
    epoch: int = 1,
) -> dict[str, Any]:
    return {
        "content": content,
        "metadata": {
            "chunk_id": chunk_id,
            "document_entity": entity,
            "evidence_target_entity": entity,
            "evidence_class": evidence_class,
            "support_scope": support_scope,
            "citation_id": citation_id,
            "evidence_epoch": epoch,
        },
    }


# ==============================================================================
# 1. Gap Exhaustion: Consecutive Failure Invariant
# ==============================================================================

def test_gap_exhaustion_requires_consecutive_failures():
    """Verify that failure -> success -> failure does NOT trigger exhaustion, only consecutive failures do."""
    registry = AttemptedGapRegistry()

    # 1. First attempt fails
    registry.record(
        gap="端口配置",
        target_scope="StampServer",
        status=ToolProgressStatus.NO_PROGRESS,
        tool="retrieve_kb",
        gap_support_delta=0,
    )
    assert registry.is_exhausted("端口配置", "StampServer") is False

    # 2. Second attempt succeeds (finds partial evidence)
    registry.record(
        gap="端口配置",
        target_scope="StampServer",
        status=ToolProgressStatus.PROGRESS,
        tool="retrieve_kb",
        gap_support_delta=1,
    )
    assert registry.is_exhausted("端口配置", "StampServer") is False

    # 3. Third attempt fails again (1 failure after 1 success -> not 2 consecutive failures)
    registry.record(
        gap="端口配置",
        target_scope="StampServer",
        status=ToolProgressStatus.NO_PROGRESS,
        tool="retrieve_kb",
        gap_support_delta=0,
    )
    # The last 2 entries are [delta=1, delta=0] -> NOT 2 consecutive failures
    assert registry.is_exhausted("端口配置", "StampServer") is False

    # 4. Fourth attempt fails consecutively (last 2 entries are [delta=0, delta=0])
    registry.record(
        gap="端口配置",
        target_scope="StampServer",
        status=ToolProgressStatus.NO_PROGRESS,
        tool="retrieve_kb",
        gap_support_delta=0,
    )
    assert registry.is_exhausted("端口配置", "StampServer") is True


# ==============================================================================
# 2. Working Evidence across Reviewer Resume: Non-promotion Invariant
# ==============================================================================

def test_resume_working_evidence_retained_but_never_promoted_to_citable():
    """Working-only evidence (e.g. CONFLICT) carries across resume but stays non-citable."""
    pool = EvidencePool(question_id="q-resume-work", evidence_epoch=2)

    conflict_doc = {
        "content": "PipelineBuilder 材质参数",
        "metadata": {
            "chunk_id": "chk-conflict-1",
            "document_entity": "PipelineBuilder",
            "evidence_class": "CONFLICT",
            "support_scope": "TARGET_SPECIFIC",
            "citation_id": 2,
        },
    }

    # Seed working-only docs into pool
    group = pool.seed_resume_working([conflict_doc], head_entity="PipelineWebGL")
    assert group is not None
    assert group.status == "ACTIVE"

    # Must be visible in working_docs
    working = pool.working_docs()
    assert len(working) == 1
    assert working[0]["metadata"]["chunk_id"] == "chk-conflict-1"
    assert working[0]["metadata"]["resume_working_only"] is True

    # Must NOT be in citable_docs despite carrying TARGET_SPECIFIC
    assert pool.citable_docs() == []

    # Trace citable chunk IDs must be empty
    trace = pool.to_trace()
    assert trace[0]["citable_chunk_ids"] == []


# ==============================================================================
# 3. GraphWorkingSet Cross-Resume State Preservation
# ==============================================================================

def test_graph_working_set_from_trace_full_restoration():
    """Verify GraphWorkingSet.from_trace restores all frontier, budget, and candidate states."""
    original = GraphWorkingSet(
        graph_scope_id="gws_test_123",
        question_id="q_graph",
        graph_revision="rev_v2",
        exploration_roots=("StampServer",),
        anchor_entities=("StampServer",),
        frontier_entity_ids=("StampDB",),
        visited_entity_ids={"StampServer", "StampDB"},
        visited_relation_ids={"rel_1"},
        admitted_relation_ids={"rel_1"},
        max_depth_reached=2,
        expansion_calls=1,
        bootstrap_status="BOOTSTRAPPED",
        last_graph_status="PROGRESS",
    )
    original.entities["StampDB"] = GraphEntityState(
        entity_id="ent_db",
        canonical_name="StampDB",
        entity_type="Database",
        depth_from_root=1,
        origin_root="StampServer",
        is_root=False,
        is_frontier=True,
    )
    original.relations["rel_1"] = GraphRelationCandidate(
        relation_id="rel_1",
        source_name="StampServer",
        target_name="StampDB",
        relation_type="depends_on",
        depth_from_root=1,
        origin_root="StampServer",
    )

    trace = original.to_trace()
    restored = GraphWorkingSet.from_trace(trace)

    assert restored.graph_scope_id == "gws_test_123"
    assert restored.exploration_roots == ("StampServer",)
    assert "StampDB" in restored.entities
    assert restored.entities["StampDB"].entity_type == "Database"
    assert "rel_1" in restored.relations
    assert restored.admitted_relation_ids == {"rel_1"}
    assert restored.max_depth_reached == 2
    assert restored.bootstrap_status == "BOOTSTRAPPED"


# ==============================================================================
# 4. search_focus_text / focus_entity_id Protocol
# ==============================================================================

def test_retrieve_kb_schema_and_validation_for_search_focus():
    """Verify retrieve_kb accepts search_focus_text without requiring legacy query."""
    registry = build_agent_registry()
    retrieve_spec = registry.get("retrieve_kb")
    assert retrieve_spec is not None

    schema = retrieve_spec.input_schema
    assert "search_focus_text" in schema["properties"]
    assert "focus_entity_id" in schema["properties"]

    # Valid: search_focus_text provided alone
    assert registry.validate_call("retrieve_kb", {"search_focus_text": "StampServer 默认端口"}) is None
    # Legacy query is no longer part of the Agent retrieve contract.
    assert registry.validate_call("retrieve_kb", {"query": "StampServer 默认端口"}) == "tool_missing_arg:search_focus_text"
    # Valid: both provided
    assert registry.validate_call("retrieve_kb", {
        "search_focus_text": "StampServer 默认端口",
        "query": "StampServer 默认端口",
    }) is None
    # Invalid: neither provided
    assert registry.validate_call("retrieve_kb", {}) in {"tool_missing_arg:search_focus_text", "tool_missing_arg:query"}


# ==============================================================================
# 5. Evidence Epoch Evolution
# ==============================================================================

def test_evidence_epoch_rejects_stale_documents():
    """Documents stamped with an older epoch are excluded from citable_docs in the new epoch."""
    pool = EvidencePool(question_id="q-epoch", evidence_epoch=2)

    # Document from epoch 1 (historical seed, not carried active)
    doc_epoch1 = _text_doc("chk-old-1", "旧实体事实", epoch=1)
    pool.seed_previous_cited([doc_epoch1], carry_active=False)

    # Document from current epoch 2 (retrieved in current epoch)
    doc_epoch2 = _text_doc("chk-cur-2", "当前实体事实", epoch=2)
    pool.add_retrieve([doc_epoch2])

    # Only epoch 2 document is citable
    citable = pool.citable_docs()
    assert len(citable) == 1
    assert citable[0]["metadata"]["chunk_id"] == "chk-cur-2"


# ==============================================================================
# 6. Publication Taxonomy: Accounting-Backed Classification
# ==============================================================================

def test_publication_taxonomy_classification():
    """Verify RagChain._classify_empty_agent_publication classifies by retrieval execution facts."""
    from rag_knowledge.services.rag import RagChain

    # 1. Retrieval requested / blocked by guard -> retrieval_blocked
    res1 = SimpleNamespace(
        budget={"retrieval_accounting": {"requested": 1, "guard_rejected": 1, "executed": 0, "returned": 0}},
        evidence=None,
    )
    mode1, ans1, _ = RagChain._classify_empty_agent_publication(res1)
    assert mode1 == "retrieval_blocked"
    assert "未能实际执行" in ans1

    # 2. Retrieval executed but zero hits returned -> retrieved_no_hits
    res2 = SimpleNamespace(
        budget={"retrieval_accounting": {"requested": 1, "guard_rejected": 0, "executed": 1, "returned": 0}},
        evidence=None,
    )
    mode2, _, _ = RagChain._classify_empty_agent_publication(res2)
    assert mode2 == "retrieved_no_hits"

    # 3. Retrieval executed, returned candidates, but no citable support -> retrieved_no_support
    res3 = SimpleNamespace(
        budget={"retrieval_accounting": {"requested": 1, "guard_rejected": 0, "executed": 1, "returned": 5}},
        evidence=SimpleNamespace(working_docs=lambda: [{}] * 5),
    )
    mode3, ans3, _ = RagChain._classify_empty_agent_publication(res3)
    assert mode3 == "retrieved_no_support"
    assert "没有可支撑该问题" in ans3

    # 4. No retrieval attempted -> no_safe_answer
    res4 = SimpleNamespace(
        budget={"retrieval_accounting": {"requested": 0, "guard_rejected": 0, "executed": 0, "returned": 0}},
        evidence=None,
    )
    mode4, ans4, _ = RagChain._classify_empty_agent_publication(res4)
    assert mode4 == "no_safe_answer"
    assert "没有足够的可验证证据" in ans4


# ==============================================================================
# 7. Working Evidence Compaction Priority
# ==============================================================================

def test_decision_digest_prioritizes_conflict_over_irrelevant():
    """decision_digest places TARGET_DIRECT and CONFLICT ahead of IRRELEVANT."""
    pool = EvidencePool(question_id="q-digest")

    irrelevant = _text_doc("chk-irr-1", "无关内容", evidence_class="IRRELEVANT", support_scope="NONE")
    conflict = _text_doc("chk-conf-2", "PipelineBuilder 碰撞分析", evidence_class="CONFLICT", support_scope="TARGET_SPECIFIC")
    direct = _text_doc("chk-dir-3", "PipelineWebGL 渲染参数", evidence_class="TARGET_DIRECT", support_scope="TARGET_SPECIFIC")

    # Add in order: irrelevant, conflict, direct
    pool.add_retrieve([irrelevant, conflict, direct])

    digest = pool.decision_digest(max_items=3)

    # In sorted output, Evidence #1 should be TARGET_DIRECT, #2 CONFLICT, #3 IRRELEVANT
    lines = digest.strip().split("\n")
    assert "class=TARGET_DIRECT" in lines[0]
    assert "class=CONFLICT" in lines[1]
    assert "class=IRRELEVANT" in lines[2]
    assert "Working Summary:" in lines[3]


# ==============================================================================
# 8. Single Source of Truth for Trace Citable Derivation
# ==============================================================================

def test_trace_citable_derived_dynamically_from_is_citable_document():
    """EvidencePool.to_trace() dynamically computes citable_chunk_ids via _is_citable_document."""
    pool = EvidencePool(question_id="q-trace-auth", evidence_epoch=1)

    citable_doc = _text_doc("chk-cit-1", "合法事实", evidence_class="TARGET_DIRECT", support_scope="TARGET_SPECIFIC", epoch=1)
    non_citable_doc = _text_doc("chk-non-2", "未授权事实", evidence_class="TARGET_DIRECT", support_scope="TARGET_SPECIFIC", epoch=1)
    # Mark non_citable_doc with resume_working_only
    non_citable_doc["metadata"]["resume_working_only"] = True

    # Manually populate a group with both docs and a stale citable_docs cache
    group = EvidenceGroup(
        group_id="grp-1",
        question_id=pool.question_id,
        kind="retrieve",
        retrieve_index=1,
        chunk_ids=["chk-cit-1", "chk-non-2"],
        docs=[citable_doc, non_citable_doc],
        citable_docs=[citable_doc, non_citable_doc],  # Stale cache containing non-citable
        status="ACTIVE",
    )
    pool.groups.append(group)

    # to_trace must ignore the stale citable_docs cache and re-evaluate dynamically
    traces = pool.to_trace()
    assert len(traces) == 1
    assert traces[0]["citable_chunk_ids"] == ["chk-cit-1"]


# ==============================================================================
# 9. PipelineWebGL -> PipelineBuilder Zero Contamination Invariant
# ==============================================================================

def test_pipeline_builder_conflict_zero_contamination_invariant():
    """CONFLICT chunk from PipelineBuilder is visible in working but never admitted as citable for PipelineWebGL."""
    pool = EvidencePool(question_id="q-contam", evidence_epoch=1)

    builder_conflict = _text_doc(
        "chk-builder-1",
        "PipelineBuilder 材质参数",
        evidence_class="CONFLICT",
        support_scope="TARGET_SPECIFIC",
        entity="PipelineBuilder",
        epoch=1,
    )
    webgl_direct = _text_doc(
        "chk-webgl-2",
        "PipelineWebGL 渲染上下文",
        evidence_class="TARGET_DIRECT",
        support_scope="TARGET_SPECIFIC",
        entity="PipelineWebGL",
        epoch=1,
    )

    pool.add_retrieve([builder_conflict, webgl_direct])

    # Both in working
    assert len(pool.working_docs()) == 2

    # Only WebGL direct is citable; Builder CONFLICT has 0 contamination in citable
    citable = pool.citable_docs()
    assert len(citable) == 1
    assert citable[0]["metadata"]["chunk_id"] == "chk-webgl-2"
    assert all(doc["metadata"]["evidence_class"] != "CONFLICT" for doc in citable)


# ==============================================================================
# 10. REVISE/REWRITE and REVISE/RETRIEVE Mode Separation
# ==============================================================================

def test_revise_rewrite_and_retrieval_gap_separation():
    """Verify Reviewer output cleanly separates text rewrite instructions from retrieval feedback."""
    from rag_knowledge.services.helper_grounding_reviewer import HelperGroundingReviewResult, ClaimReview

    # Case A: Pure rewrite (unsupported claim can be removed/rewritten from existing facts)
    res_rewrite = HelperGroundingReviewResult(
        verdict="REVISE",
        coverage="PARTIAL",
        summary="删除未支持的次要声明",
        claim_reviews=[
            ClaimReview("c1", "核心支持内容", "knowledge_claim", "TARGET_ATTRIBUTION", [1], "supported", "证据支持"),
            ClaimReview("c2", "多余的推测内容", "knowledge_claim", "TARGET_ATTRIBUTION", [], "unsupported", "无依据"),
        ],
        rewrite_actions=[{"claim_id": "c2", "action": "rewrite_to_supported_scope_or_remove", "instruction": "删除 c2"}],
        repair_mode="REWRITE",
    )
    assert res_rewrite.repair_mode == "REWRITE"
    assert res_rewrite.retrieval_feedback is None

    # Case B: Retrieval gap (evidence missing for key fact)
    res_retrieve = HelperGroundingReviewResult(
        verdict="REVISE",
        coverage="PARTIAL",
        summary="缺少关键配置证据",
        claim_reviews=[
            ClaimReview("c1", "缺少配置端口", "knowledge_claim", "TARGET_ATTRIBUTION", [], "unsupported", "缺失事实"),
        ],
        rewrite_actions=[],
        repair_mode="RETRIEVE",
        retrieval_feedback={"gap_id": "gap-1", "affected_claim_ids": ["c1"]},
    )
    assert res_retrieve.repair_mode == "RETRIEVE"
    assert res_retrieve.retrieval_feedback is not None
