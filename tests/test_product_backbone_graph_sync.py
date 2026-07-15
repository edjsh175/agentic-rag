"""Tests for product_relation_backbone seed staging."""
from __future__ import annotations

import json

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_extraction import GraphCandidateApplier
from rag_knowledge.services.product_backbone_graph_sync import (
    BATCH_MODE,
    SEED_CREATED_BY,
    ProductBackboneGraphSyncService,
)
import sync_product_backbone_to_graph


def _backbone_path(tmp_path):
    path = tmp_path / "product_relation_backbone.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_ref": "product-relation-backbone/source/demo.pptx",
                "entities": [
                    {"name": "StampGIS三维产品", "entity_type": "Product", "aliases": ["Stamp三维"], "doc_category": ""},
                    {"name": "数据处理层", "entity_type": "Module", "aliases": [], "doc_category": ""},
                    {"name": "地形影像切片", "entity_type": "Tool", "aliases": ["Terrain Builder"], "doc_category": "StampTools"},
                ],
                "relations": [
                    {
                        "source": "数据处理层",
                        "relation_type": "belongs_to",
                        "target": "StampGIS三维产品",
                        "note": "demo",
                    },
                    {
                        "source": "地形影像切片",
                        "relation_type": "belongs_to",
                        "target": "数据处理层",
                        "note": "demo tool",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_product_backbone_preview_and_validation(tmp_path):
    service = ProductBackboneGraphSyncService(path=_backbone_path(tmp_path))
    preview = service.preview()
    assert len(preview["entities"]) == 3
    assert len(preview["aliases"]) == 2
    assert len(preview["relations"]) == 2
    assert preview["diagnostics"] == []


def test_product_backbone_rejects_illegal_relation(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_ref": "x",
                "entities": [
                    {"name": "A", "entity_type": "Tool", "aliases": []},
                    {"name": "B", "entity_type": "Tool", "aliases": []},
                ],
                "relations": [{"source": "A", "relation_type": "belongs_to", "target": "B", "note": "bad"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    preview = ProductBackboneGraphSyncService(path=path).preview()
    assert any(item["code"] == "illegal_relation" for item in preview["diagnostics"])


def test_product_backbone_stage_and_apply(isolated_storage, tmp_path):
    isolated_storage(db_name="backbone.db", data_dir_name="backbone-data", chroma_name="backbone-chroma")
    db = RelationalDB()
    service = ProductBackboneGraphSyncService(db=db, path=_backbone_path(tmp_path))
    result = service.build_batch(review_status="pending")
    assert result.stats["entity"] == 3
    assert result.stats["relation"] == 2
    batch = db.get_extraction_batch(result.batch_id)
    assert batch["mode"] == BATCH_MODE
    candidates = db.list_extraction_candidates(result.batch_id)
    assert all(item["payload"].get("created_by") == SEED_CREATED_BY for item in candidates)
    ids = [item["id"] for item in candidates]
    db.review_extraction_candidates(result.batch_id, ids, "approved")
    db.set_extraction_batch_status(result.batch_id, "approved")
    GraphCandidateApplier(db).apply(result.batch_id)
    assert db.get_entity_by_name("数据处理层")["entity_type"] == "Module"
    assert db.get_entity_by_name("地形影像切片")["created_by"] == SEED_CREATED_BY
    relations = db.list_relations(entity_id=db.get_entity_by_name("数据处理层")["id"])
    assert any(item["relation_type"] == "belongs_to" for item in relations)


def test_sync_product_backbone_cli_dry_run(tmp_path):
    path = _backbone_path(tmp_path)
    code = sync_product_backbone_to_graph.main(["--dry-run", "--path", str(path), "--json"])
    assert code == 0
