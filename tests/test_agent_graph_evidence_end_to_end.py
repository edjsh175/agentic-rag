"""End-to-end integration tests for GraphEvidence first-class citation and claim alignment (PRD 2026-08-26)."""
from __future__ import annotations

import pytest
from rag_knowledge.services.agent_orchestration.evidence_gate import evaluate_rules
from rag_knowledge.services.agent_orchestration.models import (
    ConversationContext,
    EvidencePool,
)


def test_graph_relation_first_class_evidence_and_citation():
    evidence = EvidencePool(question_id="q-100")

    # 1. Add normal text chunk
    evidence.add_retrieve(
        [
            {
                "content": "StampServer 是核心服务端组件，负责元数据管理与分布式协调。",
                "metadata": {
                    "chunk_id": "chunk-server-001",
                    "file_name": "StampServer说明.md",
                    "document_entity": "StampServer",
                },
            }
        ],
        query="StampServer 介绍",
        target_entity="StampServer",
        grant_id="grant-1",
    )

    # 2. Add admitted GraphRelationEvidence
    evidence.add_relation(
        relation_key="StampServer -[depends_on]-> StampDB",
        target_entity="StampServer",
        source_name="StampServer",
        target_name="StampDB",
        relation_type="depends_on",
        origin_root="StampServer",
        depth_from_root=1,
        discovery_source="bootstrap",
        admission_verdict="PASS",
        admission_reason="intent_and_entity_direct_match",
    )

    citable = evidence.citable_docs_renumbered()
    assert len(citable) == 2
    assert citable[0]["metadata"]["citation_id"] == 1
    assert citable[1]["metadata"]["citation_id"] == 2
    assert citable[1]["metadata"]["source_type"] == "graph_relation"
    assert citable[1]["metadata"]["relation_key"] == "StampServer -[depends_on]-> StampDB"


def test_graph_relation_passes_structural_evidence_gate_after_admission():
    conv = ConversationContext.from_request("StampServer 依赖什么？", history=[])
    evidence = EvidencePool(question_id="q-101")
    evidence.add_relation(
        relation_key="StampServer -[depends_on]-> StampDB",
        target_entity="StampServer",
        source_name="StampServer",
        target_name="StampDB",
        relation_type="depends_on",
        origin_root="StampServer",
        depth_from_root=1,
        discovery_source="bootstrap",
        admission_verdict="PASS",
    )

    verdict = evaluate_rules(conv, evidence)
    assert verdict["allow_knowledge_answer"] is True
