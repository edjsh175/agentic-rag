# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_manual_export import GraphManualFactExporter


def test_graph_manual_export(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    db = RelationalDB()

    # 1. Populate mix of manual and rule-based data
    # Manual/Seed entities (should be exported)
    ent_admin = db.create_entity("Doc1", "Document", created_by="admin")
    ent_seed = db.create_entity("PipelineBuilder", "Tool", created_by="seed")
    ent_special = db.create_entity("管线发布服务", "Service", created_by="rule:special")
    
    # Non-manual entity (should NOT be exported)
    ent_rule = db.create_entity("AutoModule", "Module", created_by="rule:section_path")

    # Manual relation (should be exported)
    db.create_relation(ent_seed, ent_special, "requires", created_by="seed")
    # Non-manual relation (should NOT be exported)
    db.create_relation(ent_seed, ent_rule, "belongs_to", created_by="rule:section_path")

    # Special alias (should be exported)
    db.create_alias(ent_seed, "PipelinePublishTool", evidence_text="special_rule:alias")

    # Links (one valid, one stale)
    db.create_link(ent_seed, "chunk-valid", source="Doc1")
    db.create_link(ent_seed, "chunk-stale", source="Doc1")

    # Mock Chroma VectorStore to return only 'chunk-valid'
    mock_collection = MagicMock()
    mock_collection.get.return_value = {"ids": ["chunk-valid"]}
    mock_store = MagicMock()
    mock_store._collection = mock_collection

    with patch("rag_knowledge.repository.vector_store.VectorStore._get_store", return_value=mock_store):
        exporter = GraphManualFactExporter(db)
        export_file = data_dir / "manual_facts.json"
        
        summary = exporter.export_manual(str(export_file))

        assert export_file.exists()
        exported_data = json.loads(export_file.read_text(encoding="utf-8"))

        # Check summary in return and file
        assert summary["entities"] == 3
        assert summary["relations"] == 1
        assert summary["aliases"] == 1
        assert summary["entity_chunk_links"] == 1
        assert summary["skipped_stale_links"] == 1

        assert exported_data["summary"] == summary

        # Assert exported entity names match
        exported_entity_names = {e["name"] for e in exported_data["entities"]}
        assert "Doc1" in exported_entity_names
        assert "PipelineBuilder" in exported_entity_names
        assert "管线发布服务" in exported_entity_names
        assert "AutoModule" not in exported_entity_names

        # Assert exported relation maps end-points by name, not SQLite ID
        exported_relations = exported_data["relations"]
        assert len(exported_relations) == 1
        assert exported_relations[0]["source_canonical_name"] == "PipelineBuilder"
        assert exported_relations[0]["target_canonical_name"] == "管线发布服务"
        assert exported_relations[0]["relation_type"] == "requires"

        # Assert links has valid link only
        exported_links = exported_data["entity_chunk_links"]
        assert len(exported_links) == 1
        assert exported_links[0]["chunk_id"] == "chunk-valid"
        assert exported_links[0]["entity_canonical_name"] == "PipelineBuilder"
