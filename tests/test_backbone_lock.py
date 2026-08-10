"""Unit test suite verifying triple backbone lock protections across LLM extraction, governance review, and database applier."""
import os
import pytest
os.environ["ALLOW_LIVE_STORAGE_IN_TESTS"] = "1"

from rag_knowledge.services.backbone_guard import load_backbone_constraints, describe_conflict
from rag_knowledge.services.graph_governance import is_safe_review_candidate
from rag_knowledge.services.graph_extraction.pipeline import GraphCandidateApplier
from rag_knowledge.repository.relational_db import RelationalDB


@pytest.fixture(autouse=True)
def mock_backbone_constraints(monkeypatch):
    mock_data = {
        "belongs_to": {"ActiveX": {"StampGIS Client"}},
        "different_from": set(),
        "requires": set(),
        "relations": [{"source": "ActiveX", "relation_type": "belongs_to", "target": "StampGIS Client"}],
        "canonical_by_alias": {"ActiveX": "ActiveX"},
        "entity_type_by_name": {"ActiveX": "Module"},
        "doc_category_by_name": {},
        "doc_categories": set(),
    }
    monkeypatch.setattr(
        "rag_knowledge.services.backbone_guard.load_backbone_constraints",
        lambda *args, **kwargs: mock_data
    )
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.load_backbone_constraints",
        lambda *args, **kwargs: mock_data
    )


def test_llm_extractor_rejects_backbone_conflicts():
    """Verify LLMGraphExtractor drops entities/relations conflicting with official backbone."""
    import rag_knowledge.services.backbone_guard as bb_guard
    constraints = bb_guard.load_backbone_constraints()

    # Entity type conflict: ActiveX is a Module in official product_relation_backbone.json
    conflict_ent = describe_conflict("entity", {"name": "ActiveX", "entity_type": "Procedure"}, constraints)
    assert conflict_ent != ""
    assert "entity type conflict" in conflict_ent

    # Conflict belongs_to relation: ActiveX belongs_to StampGIS Client in official backbone, attempt to attach to StampServer
    conflict_rel = describe_conflict("relation", {
        "source_name": "ActiveX",
        "relation_type": "belongs_to",
        "target_name": "StampServer"
    }, constraints)
    assert conflict_rel != ""
    assert "belongs_to conflict" in conflict_rel


def test_governance_review_blocks_backbone_conflicts():
    """Verify is_safe_review_candidate rejects candidates conflicting with backbone."""
    candidate_entity = {
        "candidate_kind": "entity",
        "payload": {
            "name": "ActiveX",
            "entity_type": "Procedure",
            "evidence_text": "ActiveX"
        }
    }
    assert not is_safe_review_candidate(candidate_entity)

    candidate_relation = {
        "candidate_kind": "relation",
        "payload": {
            "source_name": "ActiveX",
            "relation_type": "belongs_to",
            "target_name": "StampServer",
            "evidence_text": "ActiveX"
        }
    }
    assert not is_safe_review_candidate(candidate_relation)


def test_candidate_applier_prevents_backbone_relation_tampering():
    """Verify GraphCandidateApplier raises error when attempting to write conflict backbone relation."""
    db = RelationalDB()
    applier = GraphCandidateApplier(db)

    conflict_payload = {
        "source_name": "ActiveX",
        "relation_type": "belongs_to",
        "target_name": "StampServer"
    }

    with pytest.raises(ValueError, match="backbone relation lock"):
        with db._get_conn() as conn:
            applier._relation(conn, conflict_payload)
