import json
from pathlib import Path

import run_graph_build
from rag_knowledge.services.safe_rebuild import (
    SafeRebuildService,
    _candidate_confidence,
    classify_sources,
    is_preserved_creator,
    is_replaceable_creator,
    relation_conflicts_with_backbone,
)
from tests.test_graph_extraction import chunk, make_db


def test_candidate_confidence_defaults_missing_to_one():
    assert _candidate_confidence({}) == 1.0
    assert _candidate_confidence({"confidence": None}) == 1.0
    assert _candidate_confidence({"properties": {"confidence": 0.91}}) == 0.91
    assert _candidate_confidence({"confidence": 0.5}) == 0.5


def test_creator_classification():
    assert is_preserved_creator("seed:product_backbone")
    assert is_preserved_creator("rule:profile_sync")
    assert is_preserved_creator("rule:special_relations")
    assert is_replaceable_creator("rule:phase_b")
    assert is_replaceable_creator("llm:schema_extractor")
    assert not is_replaceable_creator("rule:profile_sync")
    preserved, superseded = classify_sources(
        {"seed:product_backbone": 36, "rule:phase_b": 100, "rule:profile_sync": 4},
        {"seed:product_backbone": 40, "rule:phase_b": 200, "llm:schema_extractor": 14},
    )
    assert preserved["seed:product_backbone"] == 76
    assert preserved["rule:profile_sync"] == 4
    assert superseded["rule:phase_b"] == 300
    assert superseded["llm:schema_extractor"] == 14
    assert "rule:profile_sync" not in superseded


def test_backbone_conflict_detection():
    constraints = {"belongs_to": {"PipelineBuilder": {"StampTools"}}}
    assert relation_conflicts_with_backbone(
        {"source_name": "PipelineBuilder", "relation_type": "belongs_to", "target_name": "StampServer"},
        constraints,
    )
    assert not relation_conflicts_with_backbone(
        {"source_name": "PipelineBuilder", "relation_type": "belongs_to", "target_name": "StampTools"},
        constraints,
    )


def test_rebuild_safe_dry_run_writes_report_without_changing_database(isolated_storage, tmp_path):
    db = make_db(isolated_storage, name="safe-rebuild.db", data_dir_name="safe-rebuild-data", chroma_name="safe-rebuild-chroma")
    entity_id = db.create_entity("Manual fact", "Tool", created_by="manual", review_status="approved")
    db.create_entity("Backbone Tool", "Tool", created_by="seed:product_backbone", review_status="approved")
    db.create_entity("Profile Tool", "Tool", created_by="rule:profile_sync", review_status="approved")
    db.create_entity("Auto Tool", "Tool", created_by="rule:phase_b", review_status="approved")
    before = db.get_entity(entity_id)
    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"

    assert run_graph_build.main(
        ["rebuild-safe", "--dry-run", "--output-json", str(json_path), "--output-md", str(md_path)],
        db=db,
    ) == 0
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["manual_fact_preserved"] is True
    assert payload["preserved_by_source"]["manual"] >= 1
    assert payload["preserved_by_source"]["seed:product_backbone"] >= 1
    assert payload["preserved_by_source"]["rule:profile_sync"] >= 1
    assert payload["superseded_by_source"]["rule:phase_b"] >= 1
    assert "rule:profile_sync" not in payload["superseded_by_source"]
    assert md_path.exists()
    assert db.get_entity(entity_id) == before


def test_rebuild_safe_execute_preserves_seed_and_replaces_auto(isolated_storage, tmp_path, monkeypatch):
    db = make_db(
        isolated_storage,
        name="safe-rebuild-exec.db",
        data_dir_name="safe-rebuild-exec-data",
        chroma_name="safe-rebuild-exec-chroma",
    )
    product_id = db.create_entity("StampTools", "Product", created_by="seed:product_backbone", review_status="approved")
    tool_id = db.create_entity("PipelineBuilder", "Tool", created_by="seed:product_backbone", review_status="approved")
    db.create_relation(tool_id, product_id, "belongs_to", created_by="seed:product_backbone", evidence_text="product_backbone:test")
    profile_id = db.create_entity("ProfileSvc", "Service", created_by="rule:profile_sync", review_status="approved")
    auto_id = db.create_entity("OldSection", "Section", created_by="rule:phase_b", review_status="approved")
    db.create_relation(auto_id, product_id, "belongs_to", created_by="rule:phase_b", evidence_text="old")

    backbone = {
        "schema_version": 1,
        "source_ref": "test",
        "entities": [
            {"name": "StampTools", "entity_type": "Product", "aliases": [], "doc_category": "StampTools"},
            {"name": "PipelineBuilder", "entity_type": "Tool", "aliases": [], "doc_category": "StampTools"},
        ],
        "relations": [
            {"source": "PipelineBuilder", "relation_type": "belongs_to", "target": "StampTools", "note": "test"},
        ],
    }
    backbone_path = tmp_path / "product_relation_backbone.json"
    backbone_path.write_text(json.dumps(backbone, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        "rag_knowledge.services.safe_rebuild.load_backbone_constraints",
        lambda path=None: {
            "belongs_to": {"PipelineBuilder": {"StampTools"}},
            "different_from": set(),
            "requires": set(),
            "relations": backbone["relations"],
        },
    )

    chunks = [
        chunk(
            "c1",
            "PipelineBuilder 属于 StampTools。",
            source="StampTools用户手册.docx",
            doc_category="StampTools",
            section_path="PipelineBuilder > 概述",
            content_type="text",
        )
    ]
    out_json = tmp_path / "exec.json"
    out_md = tmp_path / "exec.md"
    backup_dir = tmp_path / "backups"
    manual_path = tmp_path / "manual.json"

    report = SafeRebuildService(db=db, chunk_source=lambda: chunks).run(
        output_json=str(out_json),
        output_md=str(out_md),
        include_llm=False,
        backup_dir=str(backup_dir),
        manual_export_path=str(manual_path),
    )

    assert report["dry_run"] is False
    assert Path(report["backup_path"]).is_file()
    assert db.get_entity(product_id) is not None
    assert db.get_entity(tool_id) is not None
    assert db.get_entity(profile_id) is not None
    assert db.get_entity(auto_id) is None
    assert db.get_relation_by_details(tool_id, product_id, "belongs_to") is not None
    assert report["backbone_after"]["complete"] is True
    assert (report.get("review") or {}).get("approved", 0) >= 1
    assert out_json.exists() and out_md.exists()
