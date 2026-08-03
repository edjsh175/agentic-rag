"""Unit test suite verifying triple backbone lock protections across LLM extraction, governance review, and database applier."""
import os
import pytest
os.environ["ALLOW_LIVE_STORAGE_IN_TESTS"] = "1"

from rag_knowledge.services.backbone_guard import load_backbone_constraints, describe_conflict
from rag_knowledge.services.graph_governance import is_safe_review_candidate
from rag_knowledge.services.graph_extraction.pipeline import GraphCandidateApplier
from rag_knowledge.repository.relational_db import RelationalDB


def test_llm_extractor_rejects_backbone_conflicts():
    """Verify LLMGraphExtractor drops entities/relations conflicting with official backbone."""
    constraints = load_backbone_constraints()

    # Entity type conflict: PipelineBuilder is a Tool in domain_catalog/backbone
    conflict_ent = describe_conflict("entity", {"name": "PipelineBuilder", "entity_type": "Procedure"}, constraints)
    assert conflict_ent != ""
    assert "entity type conflict" in conflict_ent

    # Conflict belongs_to relation: PipelineBuilder belongs_to StampTools in domain_catalog, attempt to attach to StampServer
    conflict_rel = describe_conflict("relation", {
        "source_name": "PipelineBuilder",
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
            "name": "PipelineBuilder",
            "entity_type": "Procedure",
            "evidence_text": "PipelineBuilder"
        }
    }
    assert not is_safe_review_candidate(candidate_entity)

    candidate_relation = {
        "candidate_kind": "relation",
        "payload": {
            "source_name": "PipelineBuilder",
            "relation_type": "belongs_to",
            "target_name": "StampServer",
            "evidence_text": "PipelineBuilder"
        }
    }
    assert not is_safe_review_candidate(candidate_relation)


def test_candidate_applier_prevents_backbone_relation_tampering():
    """Verify GraphCandidateApplier raises error when attempting to write conflict backbone relation."""
    db = RelationalDB()
    applier = GraphCandidateApplier(db)

    conflict_payload = {
        "source_name": "PipelineBuilder",
        "relation_type": "belongs_to",
        "target_name": "StampServer"
    }

    with pytest.raises(ValueError, match="backbone relation lock"):
        with db._get_conn() as conn:
            applier._relation(conn, conflict_payload)
