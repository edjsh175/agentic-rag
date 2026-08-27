"""Unit tests for GraphRelationAdmissionService (PRD 2026-08-26)."""
from __future__ import annotations

import pytest
from rag_knowledge.services.agent_orchestration.graph_admission import (
    GraphRelationAdmissionResult,
    GraphRelationAdmissionService,
)
from rag_knowledge.services.agent_orchestration.graph_working_set import GraphRelationCandidate


class MockGraphDB:
    def __init__(self, entities: list[dict] | None = None, relations: list[dict] | None = None):
        self._entities = entities or []
        self._relations = relations or []

    def list_entities(self, review_status: str | None = None):
        if review_status:
            return [e for e in self._entities if e.get("review_status") == review_status]
        return self._entities

    def list_relations(self, review_status: str | None = None, entity_id: str | None = None):
        res = self._relations
        if review_status:
            res = [r for r in res if r.get("review_status") == review_status]
        if entity_id:
            res = [r for r in res if r.get("source_entity_id") == entity_id or r.get("target_entity_id") == entity_id]
        return res


def test_admission_hard_validation_unapproved():
    service = GraphRelationAdmissionService()
    candidate = GraphRelationCandidate(
        relation_id="rel-1",
        source_name="StampServer",
        target_name="StampDB",
        relation_type="depends_on",
        review_status="pending",
    )
    result = service.admit_relation(candidate, question="StampServer 依赖什么？")
    assert result.verdict == "REJECT"
    assert "unapproved_review_status" in result.reason


def test_admission_hard_validation_invalid_type():
    service = GraphRelationAdmissionService()
    candidate = GraphRelationCandidate(
        relation_id="rel-1",
        source_name="StampServer",
        target_name="StampDB",
        relation_type="non_existent_relation_type",
        review_status="approved",
    )
    result = service.admit_relation(candidate, question="StampServer 依赖什么？")
    assert result.verdict == "REJECT"
    assert "unregistered_relation_type" in result.reason


def test_admission_deterministic_depends_on():
    service = GraphRelationAdmissionService()
    candidate = GraphRelationCandidate(
        relation_id="rel-1",
        source_name="StampServer",
        target_name="StampDB",
        relation_type="depends_on",
        review_status="approved",
    )
    # Question asking for dependency
    result = service.admit_relation(candidate, question="StampServer 启动需要依赖哪些组件？")
    assert result.verdict == "PASS"
    assert result.intent_relevance == "HIGH"


def test_admission_deterministic_belongs_to():
    service = GraphRelationAdmissionService()
    candidate = GraphRelationCandidate(
        relation_id="rel-2",
        source_name="StampServer",
        target_name="StampPlatform",
        relation_type="belongs_to",
        review_status="approved",
    )
    # Overview / attribution question -> PASS
    res1 = service.admit_relation(candidate, question="StampServer 是什么产品？")
    assert res1.verdict == "PASS"

    # Exact parameter question -> REJECT
    res2 = service.admit_relation(candidate, question="StampServer 的默认端口是多少？")
    assert res2.verdict == "REJECT"
    assert res2.reason == "relation_type_not_answer_evidence:belongs_to"


def test_admission_uses_relation_policy_intent_as_a_hard_authority():
    candidate = GraphRelationCandidate(
        relation_id="rel-3",
        source_name="StampServer",
        target_name="StampPlatform",
        relation_type="belongs_to",
        review_status="approved",
    )

    result = GraphRelationAdmissionService().admit_relation(
        candidate,
        question="StampServer 默认端口是多少？",
        task_type="config",
    )

    assert result.verdict == "REJECT"
    assert result.reason == "relation_type_not_answer_evidence:belongs_to"
