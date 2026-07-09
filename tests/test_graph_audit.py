# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_audit import GraphAuditService


def test_graph_audit_with_isolated_db(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    db = RelationalDB()

    # 1. Populate some dummy entities in database
    # Manual creators: 'admin', 'manual', 'seed', 'rule:special', 'rule:special_relations'
    doc_id = db.create_entity("Doc1.docx", "Document", created_by="admin")
    sec_id = db.create_entity("Doc1::Section1", "Section", created_by="rule:section_path")
    tool_id = db.create_entity("PipelineBuilder", "Tool", created_by="seed")
    service_id = db.create_entity("管线发布服务", "Service", created_by="rule:special")
    env_id = db.create_entity("PostgreSQL", "EnvironmentComponent", created_by="llm:schema_extractor")

    # Duplicate names (case-insensitive or whitespace differences) to test duplicate check
    db.create_entity("postgresQL", "EnvironmentComponent", created_by="manual")

    # Type conflicts (name matches case-insensitively but different types)
    db.create_entity("pipelinebuilder", "Service", created_by="manual")

    # 2. Populate some relations
    db.create_relation(doc_id, sec_id, "has_section", created_by="admin")
    db.create_relation(service_id, env_id, "depends_on", created_by="llm:schema_extractor")

    # 3. Create some evidence chunk links
    # Link 1: valid chunk 'chunk-123'
    db.create_link(tool_id, "chunk-123", source="Doc1.docx")
    # Link 2: stale chunk 'chunk-stale' (which won't be mocked in Chroma)
    db.create_link(service_id, "chunk-stale", source="Doc1.docx")

    # Mock VectorStore to return only 'chunk-123'
    mock_collection = MagicMock()
    mock_collection.get.return_value = {"ids": ["chunk-123"]}
    mock_store = MagicMock()
    mock_store._collection = mock_collection

    with patch("rag_knowledge.repository.vector_store.VectorStore._get_store", return_value=mock_store):
        service = GraphAuditService(db)
        report = service.audit()

        assert report["total_entities"] == 7
        assert report["total_relations"] == 2
        assert report["total_entity_chunk_links"] == 2
        assert report["entity_counts"]["EnvironmentComponent"] == 2
        assert report["entity_counts"]["Section"] == 1
        assert report["stale_link_count"] == 1
        assert len(report["stale_link_samples"]) == 1
        assert report["stale_link_samples"][0]["chunk_id"] == "chunk-stale"

        assert report["manual_fact_count"] == 4  # entities: Doc1.docx (admin), postgresQL (manual), pipelinebuilder (manual) + relations: has_section (admin)
        assert report["seed_fact_count"] == 1    # PipelineBuilder (seed)
        assert report["rule_fact_count"] == 2    # Doc1::Section1 (rule:section_path), 管线发布服务 (rule:special)
        assert report["llm_fact_count"] == 2     # PostgreSQL (llm:*), depends_on relation (llm:*)

        assert report["duplicate_canonical_name_count"] == 2  # postgresql and pipelinebuilder
        assert report["type_conflict_count"] == 1  # pipelinebuilder (Tool vs Service)

        # Check coverage
        coverage = report["document_entity_coverage"]
        assert len(coverage) == 1
        assert coverage[0]["source"] == "Doc1.docx"
        assert coverage[0]["section_count"] == 0  # Only links for Tool and Service exist in links table
        assert coverage[0]["business_entity_count"] == 2

        # Check report generation
        json_report_path = data_dir / "audit.json"
        md_report_path = data_dir / "audit.md"
        service.generate_reports(str(json_report_path), str(md_report_path))

        assert json_report_path.exists()
        assert md_report_path.exists()
        
        md_content = md_report_path.read_text(encoding="utf-8")
        assert "Total Entities**: 7" in md_content
        assert "Stale Links Count**: 1" in md_content


def test_graph_audit_chroma_not_accessible(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    db = RelationalDB()

    # Create dummy entity and link
    doc_id = db.create_entity("Doc1.docx", "Document", created_by="admin")
    db.create_link(doc_id, "chunk-123", source="Doc1.docx")

    # Mock VectorStore to throw exception when accessed
    with patch("rag_knowledge.repository.vector_store.VectorStore._get_store", side_effect=ConnectionError("Chroma offline")):
        service = GraphAuditService(db)
        report = service.audit()

        assert report["chroma_accessible"] is False
        assert report["stale_link_count"] == -1
        assert report["stale_link_samples"] == []

        md_report_path = data_dir / "audit.md"
        json_report_path = data_dir / "audit.json"
        service.generate_reports(str(json_report_path), str(md_report_path))

        md_content = md_report_path.read_text(encoding="utf-8")
        assert "Chroma DB is not accessible" in md_content
