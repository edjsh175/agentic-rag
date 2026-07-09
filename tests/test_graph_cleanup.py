from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_cleanup import GraphCleanupService


def test_graph_cleanup_stale_links(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    db = RelationalDB()

    # Create dummy entities
    ent_a = db.create_entity("ComponentA", "Tool", created_by="admin")
    ent_b = db.create_entity("ComponentB", "Service", created_by="manual")

    # Create chunk links
    # link_valid: chunk-1
    link_valid_id = db.create_link(ent_a, "chunk-1", source="DocA.docx")
    # link_stale: chunk-2
    link_stale_id = db.create_link(ent_b, "chunk-2", source="DocB.docx")

    # Mock Chroma VectorStore to return only 'chunk-1'
    mock_collection = MagicMock()
    mock_collection.get.return_value = {"ids": ["chunk-1"]}
    mock_store = MagicMock()
    mock_store._collection = mock_collection

    with patch("rag_knowledge.repository.vector_store.VectorStore._get_store", return_value=mock_store):
        service = GraphCleanupService(db)

        # 1. Dry Run Cleanup
        res_dry = service.cleanup_stale_links(dry_run=True)
        assert res_dry["stale_links_before"] == 1
        assert res_dry["stale_links_deleted"] == 0
        assert res_dry["stale_links_after"] == 1
        assert len(res_dry["samples"]) == 1
        assert res_dry["samples"][0]["chunk_id"] == "chunk-2"

        # Verify DB still has both links
        assert len(db.list_links()) == 2

        # 2. Actual Cleanup
        res_act = service.cleanup_stale_links(dry_run=False)
        assert res_act["stale_links_before"] == 1
        assert res_act["stale_links_deleted"] == 1
        assert res_act["stale_links_after"] == 0

        # Verify DB only has valid link left
        links = db.list_links()
        assert len(links) == 1
        assert links[0]["chunk_id"] == "chunk-1"

        # Verify entities are untouched
        entities = db.list_entities()
        assert len(entities) == 2
