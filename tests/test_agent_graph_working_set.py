"""Unit tests for GraphWorkingSet and GraphBudget (PRD 2026-08-26)."""
from __future__ import annotations

import pytest
from rag_knowledge.services.agent_orchestration.graph_working_set import (
    GraphBudget,
    GraphEntityState,
    GraphPathCandidate,
    GraphRelationCandidate,
    GraphWorkingSet,
)


def test_graph_budget_limits():
    budget = GraphBudget(
        max_hops_per_expansion=2,
        max_total_depth=3,
        max_expansion_calls=2,
        max_entities_total=5,
        max_relations_total=10,
    )
    assert budget.can_expand(hops=1, target_depth=1) is True
    assert budget.can_expand(hops=3, target_depth=1) is False  # exceeds max_hops_per_expansion
    assert budget.can_expand(hops=1, target_depth=4) is False  # exceeds max_total_depth

    assert budget.consume_expansion(hops=1, entities_discovered=3, relations_discovered=4) is True
    assert budget.expansion_calls_used == 1
    assert budget.entities_discovered == 3
    assert budget.relations_discovered == 4

    assert budget.can_expand(hops=1, target_depth=2) is True
    assert budget.consume_expansion(hops=1, entities_discovered=3, relations_discovered=4) is True
    assert budget.expansion_calls_used == 2
    # Calls limit reached
    assert budget.can_expand(hops=1, target_depth=2) is False


def test_graph_working_set_multi_root():
    ws = GraphWorkingSet()
    ws.add_root("StampServer")
    ws.add_root("StampTools")

    assert ws.exploration_roots == ("StampServer", "StampTools")
    assert "stampserver" in ws.entities
    assert "stamptools" in ws.entities

    # Add 1-hop neighbor for StampServer
    e1 = GraphEntityState(
        entity_id="e1",
        canonical_name="StampCore",
        origin_root="StampServer",
        depth_from_root=1,
        source="bootstrap",
    )
    ws.add_entity(e1)

    # Add 1-hop neighbor for StampTools
    e2 = GraphEntityState(
        entity_id="e2",
        canonical_name="StampViewer",
        origin_root="StampTools",
        depth_from_root=1,
        source="bootstrap",
    )
    ws.add_entity(e2)

    # Frontier should contain the unexpanded 1-hop entities
    frontier = ws.recalculate_frontier()
    assert "StampCore" in frontier or "stampcore" in [f.casefold() for f in frontier]
    assert "StampViewer" in frontier or "stampviewer" in [f.casefold() for f in frontier]


def test_graph_working_set_signature_and_dedup():
    ws = GraphWorkingSet()
    ws.add_root("StampServer")

    sig1 = ws.compute_expansion_signature(["StampServer"], ["depends_on"], "out", 1)
    sig2 = ws.compute_expansion_signature(["StampServer"], ["depends_on"], "out", 1)
    assert sig1 == sig2

    assert ws.is_signature_attempted(sig1) is False
    ws.record_attempted_signature(sig1)
    assert ws.is_signature_attempted(sig1) is True


def test_graph_working_set_controller_state():
    ws = GraphWorkingSet()
    ws.add_root("StampServer")
    r1 = GraphRelationCandidate(
        relation_id="rel-1",
        source_name="StampServer",
        target_name="StampDB",
        relation_type="depends_on",
        origin_root="StampServer",
        depth_from_root=1,
        evidence_status="PASS",
    )
    ws.add_relation(r1)
    ws.mark_relation_admitted("rel-1")

    state = ws.to_controller_state()
    assert state["roots"] == ["StampServer"]
    assert "frontier_entities" in state
    for forbidden in (
        "bootstrap_status",
        "last_graph_status",
        "entity_count",
        "relation_count",
        "admitted_relation_evidence_count",
        "remaining_expansion_calls",
        "max_total_depth",
        "expansion_allowed",
        "max_depth_reached",
    ):
        assert forbidden not in state


def test_relation_candidate_carries_graph_revision_to_provenance():
    candidate = GraphRelationCandidate(
        relation_id="rel-revision",
        source_name="StampServer",
        target_name="StampDB",
        relation_type="depends_on",
        graph_revision="rev-42",
    )

    assert candidate.to_dict()["graph_revision"] == "rev-42"


def test_relation_candidate_admission_is_pending_until_recorded_and_traced():
    ws = GraphWorkingSet()
    candidate = GraphRelationCandidate(
        relation_id="rel-admission",
        source_name="StampServer",
        target_name="StampDB",
        relation_type="depends_on",
    )
    ws.add_relation(candidate)

    assert candidate.evidence_status == "PENDING"
    ws.record_relation_evidence(candidate.relation_id, "REJECT", "wrong_intent")

    assert candidate.to_dict()["evidence_status"] == "REJECT"
    assert candidate.to_dict()["evidence_reason"] == "wrong_intent"
    assert candidate.relation_id not in ws.admitted_relation_ids
