# -*- coding: utf-8 -*-
"""Tests for graph staging/review/apply governance gates."""
from __future__ import annotations

import pytest

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_extraction import GraphCandidateApplier, GraphQualityService
from rag_knowledge.services.graph_governance import (
    approve_all_allowed,
    assert_staging_review_status,
    assert_write_confirmation,
    is_production_relational_db,
)


def test_profile_sync_approve_all_forbidden():
    batch = {"mode": "profile_sync"}
    pending = [
        {
            "id": "c1",
            "candidate_kind": "entity",
            "payload": {"name": "管线点表", "entity_type": "DataTable", "evidence_text": "profile:x"},
        }
    ]
    allowed, reason = approve_all_allowed(batch, pending)
    assert not allowed
    assert "profile_sync" in reason


def test_llm_batch_approve_all_forbidden():
    batch = {"mode": "incremental", "filters": {"include_llm": True}}
    pending = [
        {
            "id": "c1",
            "candidate_kind": "entity",
            "payload": {
                "name": "EntityA",
                "entity_type": "Tool",
                "created_by": "llm:schema_extractor",
                "evidence_text": "chunk text",
                "source_chunk_id": "chunk-1",
            },
        }
    ]
    allowed, reason = approve_all_allowed(batch, pending)
    assert not allowed
    assert "LLM" in reason


def test_rule_batch_approve_all_allowed_when_safe(isolated_storage):
    isolated_storage(
        db_name="gov-approve-all.db",
        data_dir_name="gov-approve-all-data",
        chroma_name="gov-approve-all-chroma",
    )
    db = RelationalDB()
    batch_id = db.create_extraction_batch("incremental", {}, "snapshot-safe")
    db.add_extraction_candidate(
        batch_id,
        "entity",
        "fp-entity",
        {
            "name": "SafeEntity",
            "entity_type": "Tool",
            "created_by": "rule:phase_b",
            "source_chunk_id": "chunk-1",
            "evidence_text": "evidence",
        },
        evidence_text="evidence",
        source_chunk_id="chunk-1",
    )
    batch = db.get_extraction_batch(batch_id)
    pending = db.list_extraction_candidates(batch_id, "pending")
    allowed, reason = approve_all_allowed(batch, pending)
    assert allowed, reason


def test_production_staging_rejects_preapproved(monkeypatch):
    monkeypatch.setattr(
        "rag_knowledge.services.graph_governance.is_production_relational_db",
        lambda db_path=None: True,
    )
    with pytest.raises(ValueError, match="review_status=pending"):
        assert_staging_review_status("approved")


def test_production_apply_requires_confirmation(monkeypatch):
    monkeypatch.setattr(
        "rag_knowledge.services.graph_governance.is_production_relational_db",
        lambda db_path=None: True,
    )
    db_path = "/tmp/rag_relational.db"
    with pytest.raises(ValueError, match="--confirm-db-path"):
        assert_write_confirmation(db_path=db_path, confirm_db_path=None, batch_id="batch-1", require_backup=True)


def test_apply_fails_when_batch_quality_invalid(isolated_storage):
    isolated_storage(
        db_name="gov-rollback.db",
        data_dir_name="gov-rollback-data",
        chroma_name="gov-rollback-chroma",
    )
    db = RelationalDB()
    batch_id = db.create_extraction_batch("incremental", {}, "snapshot-rollback")
    db.add_extraction_candidate(
        batch_id,
        "entity",
        "fp-good",
        {
            "name": "GoodEntity",
            "entity_type": "Tool",
            "created_by": "rule:phase_b",
            "source_chunk_id": "chunk-1",
            "evidence_text": "evidence",
        },
        evidence_text="evidence",
        source_chunk_id="chunk-1",
    )
    db.add_extraction_candidate(
        batch_id,
        "relation",
        "fp-bad",
        {
            "source_name": "MissingSource",
            "target_name": "GoodEntity",
            "relation_type": "belongs_to",
            "evidence_text": "evidence",
        },
        evidence_text="evidence",
    )
    db.review_extraction_candidates(
        batch_id,
        [item["id"] for item in db.list_extraction_candidates(batch_id)],
        "approved",
    )
    db.set_extraction_batch_status(batch_id, "approved")
    with pytest.raises(ValueError):
        GraphCandidateApplier(db).apply(batch_id)
    assert db.get_extraction_batch(batch_id)["status"] == "failed"
    assert db.get_entity_by_name("GoodEntity") is None


def test_duplicate_apply_rejected(isolated_storage):
    isolated_storage(
        db_name="gov-idempotent.db",
        data_dir_name="gov-idempotent-data",
        chroma_name="gov-idempotent-chroma",
    )
    db = RelationalDB()
    batch_id = db.create_extraction_batch("incremental", {}, "snapshot-idempotent")
    db.add_extraction_candidate(
        batch_id,
        "entity",
        "fp-entity",
        {
            "name": "AppliedEntity",
            "entity_type": "Tool",
            "created_by": "rule:phase_b",
            "source_chunk_id": "chunk-1",
            "evidence_text": "evidence",
        },
        evidence_text="evidence",
        source_chunk_id="chunk-1",
    )
    db.review_extraction_candidates(batch_id, [db.list_extraction_candidates(batch_id)[0]["id"]], "approved")
    db.set_extraction_batch_status(batch_id, "approved")
    GraphCandidateApplier(db).apply(batch_id)
    with pytest.raises(ValueError, match="already applied"):
        GraphCandidateApplier(db).apply(batch_id)


def test_profile_sync_review_cli_rejects_approve_all(isolated_storage):
    isolated_storage(
        db_name="gov-review-cli.db",
        data_dir_name="gov-review-cli-data",
        chroma_name="gov-review-cli-chroma",
    )
    db = RelationalDB()
    batch_id = db.create_extraction_batch("profile_sync", {"profile_id": "p1"}, "snapshot")
    db.add_extraction_candidate(
        batch_id,
        "entity",
        "fp",
        {
            "name": "管线点表",
            "entity_type": "DataTable",
            "created_by": "rule:profile_sync",
            "evidence_text": "profile:p1",
        },
        evidence_text="profile:p1",
    )
    import run_graph_build

    with pytest.raises(ValueError, match="approve-all is forbidden"):
        run_graph_build.main(["review", "--batch", batch_id, "--approve-all"], db=db)


def test_is_production_false_under_pytest():
    assert not is_production_relational_db()
