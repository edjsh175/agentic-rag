"""Unit tests for GraphExplorer: Bootstrap & Scope Expansion (PRD 2026-08-26)."""
from __future__ import annotations

import pytest
from rag_knowledge.services.agent_orchestration.graph_admission import GraphRelationAdmissionService
from rag_knowledge.services.agent_orchestration.graph_explorer import GraphExplorer
from rag_knowledge.services.agent_orchestration.graph_working_set import GraphWorkingSet


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


@pytest.fixture
def sample_graph_db():
    entities = [
        {"id": "e-server", "name": "StampServer", "canonical_name": "StampServer", "review_status": "approved"},
        {"id": "e-db", "name": "StampDB", "canonical_name": "StampDB", "review_status": "approved"},
        {"id": "e-tools", "name": "StampTools", "canonical_name": "StampTools", "review_status": "approved"},
        {"id": "e-core", "name": "StampCore", "canonical_name": "StampCore", "review_status": "approved"},
    ]
    relations = [
        {
            "id": "r-1",
            "source_entity_id": "e-server",
            "source_name": "StampServer",
            "target_entity_id": "e-db",
            "target_name": "StampDB",
            "relation_type": "depends_on",
            "review_status": "approved",
        },
        {
            "id": "r-2",
            "source_entity_id": "e-tools",
            "source_name": "StampTools",
            "target_entity_id": "e-core",
            "target_name": "StampCore",
            "relation_type": "requires",
            "review_status": "approved",
        },
    ]
    return MockGraphDB(entities=entities, relations=relations)


def test_bootstrap_single_and_multi_root(sample_graph_db):
    explorer = GraphExplorer(graph_db=sample_graph_db)

    # Multi-root bootstrap
    ws, admitted, admissions = explorer.bootstrap_anchor_graph(
        confirmed_roots=["StampServer", "StampTools"],
        question="StampServer 和 StampTools 依赖什么？",
    )

    assert ws.exploration_roots == ("StampServer", "StampTools")
    assert "stampserver" in ws.entities
    assert "stamptools" in ws.entities
    assert "stampdb" in ws.entities
    assert "stampcore" in ws.entities

    # Check that both relations were discovered
    assert len(ws.relations) == 2
    # Check admitted relations
    assert len(admitted) >= 1
    assert any("StampServer -[depends_on]-> StampDB" in r.relation_key for r in admitted)


def test_scope_expansion_depth_and_authorization(sample_graph_db):
    explorer = GraphExplorer(graph_db=sample_graph_db)
    ws, _, _ = explorer.bootstrap_anchor_graph(["StampServer"], question="StampServer 依赖什么？")

    # Unauthorized start_entities should fail authorization gate
    obs2 = explorer.expand_graph_scope(
        working_set=ws,
        start_entities=["RandomFakeProduct"],
        question="StampServer 依赖什么？",
    )
    assert obs2.status == "DENIED"

    # Authorized start_entities from existing working set
    obs3 = explorer.expand_graph_scope(
        working_set=ws,
        start_entities=["StampDB"],
        question="StampDB 依赖什么？",
    )
    # StampDB has no further relations in mock DB -> NO_PROGRESS
    assert obs3.status == "NO_PROGRESS"


def test_scope_expansion_root_expansion(sample_graph_db):
    explorer = GraphExplorer(graph_db=sample_graph_db)
    ws, _, _ = explorer.bootstrap_anchor_graph(["StampServer"], question="StampServer 和 StampTools？")

    # Root expansion with user mention StampTools
    obs2 = explorer.expand_graph_scope(
        working_set=ws,
        start_entities=["StampTools"],
        question="StampTools 需要什么？",
        user_mentions=["StampTools"],
    )

    assert obs2.status == "PROGRESS"
    assert "StampTools" in ws.exploration_roots
    assert "stampcore" in ws.entities
    assert ws.entities["stampcore"].depth_from_root == 1
    assert obs2.data.get("new_relations") >= 1
