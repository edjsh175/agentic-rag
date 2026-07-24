"""Tests for live → official product_relation_backbone.json sync."""

from __future__ import annotations

import json
from pathlib import Path

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.knowledge_graph import KnowledgeGraphService
from rag_knowledge.services.product_backbone_live_sync import ProductBackboneLiveSyncService
from rag_knowledge.models.api import RelationTypeEnum


def _seed_backbone(db: RelationalDB, path: Path) -> None:
    product = db.create_entity("StampManager", "Product", created_by="seed:product_backbone")
    module = db.create_entity("运维管理层", "Module", created_by="seed:product_backbone")
    db.create_relation(
        product,
        module,
        "belongs_to",
        created_by="seed:product_backbone",
        review_status="approved",
    )
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_ref": "test",
                "entities": [
                    {"name": "StampManager", "entity_type": "Product", "aliases": [], "doc_category": ""},
                    {"name": "运维管理层", "entity_type": "Module", "aliases": [], "doc_category": ""},
                ],
                "relations": [
                    {"source": "StampManager", "relation_type": "belongs_to", "target": "运维管理层"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_export_includes_admin_edge_between_backbone_entities(isolated_storage, tmp_path):
    isolated_storage(db_name="backbone-live-sync.db")
    db = RelationalDB()
    path = tmp_path / "product_relation_backbone.json"
    _seed_backbone(db, path)

    # Invert via admin edit on live (formal page style).
    with db._get_conn() as conn:
        conn.execute("DELETE FROM relations")
    product = db.get_entity_by_name("StampManager")
    module = db.get_entity_by_name("运维管理层")
    assert product and module
    db.create_relation(
        module["id"],
        product["id"],
        "belongs_to",
        created_by="seed:product_backbone",
        review_status="approved",
    )
    db.create_relation(
        product["id"],
        module["id"],
        "belongs_to",
        created_by="admin",
        review_status="approved",
    )

    summary = ProductBackboneLiveSyncService(db=db, path=path).export_from_live()
    data = json.loads(path.read_text(encoding="utf-8"))
    rels = {(r["source"], r["relation_type"], r["target"]) for r in data["relations"]}
    assert ("运维管理层", "belongs_to", "StampManager") in rels
    assert ("StampManager", "belongs_to", "运维管理层") in rels
    assert summary["relations"] == 2
    assert data.get("synced_from_live_at")


def test_knowledge_graph_create_relation_writeback(isolated_storage, tmp_path, monkeypatch):
    isolated_storage(db_name="backbone-live-sync-kg.db")
    db = RelationalDB()
    path = tmp_path / "product_relation_backbone.json"
    _seed_backbone(db, path)

    service = KnowledgeGraphService()
    service._backbone_live_sync = ProductBackboneLiveSyncService(db=db, path=path)

    product = db.get_entity_by_name("StampManager")
    module = db.get_entity_by_name("运维管理层")
    assert product and module
    # Remove seed edge, add only admin reverse to mimic formal-page edit.
    with db._get_conn() as conn:
        conn.execute("DELETE FROM relations")
    service.create_relation(product["id"], module["id"], RelationTypeEnum.belongs_to)

    data = json.loads(path.read_text(encoding="utf-8"))
    rels = {(r["source"], r["relation_type"], r["target"]) for r in data["relations"]}
    assert ("StampManager", "belongs_to", "运维管理层") in rels
