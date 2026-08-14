"""Tests for GraphResyncService."""
import json
import sqlite3
import pytest
from unittest.mock import MagicMock

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_resync import GraphResyncService


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test_graph_resync.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entity_chunk_links ("
        "  id TEXT PRIMARY KEY, entity_id TEXT, chunk_id TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS relations ("
        "  id TEXT PRIMARY KEY, source_chunk_id TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS extraction_candidates ("
        "  id TEXT PRIMARY KEY, source_chunk_id TEXT"
        ")"
    )
    conn.close()

    mock_db = MagicMock(spec=RelationalDB)
    mock_db._get_conn.side_effect = lambda: sqlite3.connect(db_path)
    return mock_db, db_path


def test_graph_resync_exact_match(tmp_path, tmp_db):
    mock_db, db_path = tmp_db

    # Insert old chunk references in relational DB
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO entity_chunk_links VALUES ('link-1', 'e-1', 'old-chunk-1')")
    conn.execute("INSERT INTO relations VALUES ('rel-1', 'old-chunk-1')")
    conn.commit()
    conn.close()

    # Old index backup data
    backup_file = tmp_path / "file_index.before.json"
    backup_data = {
        "files": {
            "docs/manual.pdf": {
                "chunks": [
                    {
                        "chunk_id": "old-chunk-1",
                        "source_file": "docs/manual.pdf",
                        "section_title": "Section 1",
                        "text": "Hello world graph resync text",
                    }
                ]
            }
        }
    }
    backup_file.write_text(json.dumps(backup_data, ensure_ascii=False), encoding="utf-8")

    # Mock VectorStore returning new chunk ID for the same content
    mock_store = MagicMock()
    mock_store.get_chunk_stats_source.return_value = {
        "ids": ["new-chunk-100"],
        "documents": ["Hello world graph resync text"],
        "metadatas": [{"source_file": "docs/manual.pdf", "section_title": "Section 1"}],
    }

    service = GraphResyncService(db=mock_db, store=mock_store)
    res = service.resync(index_backup_path=backup_file)

    assert res["remapped_exact"] == 1
    assert res["orphaned"] == 0

    # Verify updated chunk_id in DB
    conn = sqlite3.connect(db_path)
    row_link = conn.execute("SELECT chunk_id FROM entity_chunk_links WHERE id = 'link-1'").fetchone()
    row_rel = conn.execute("SELECT source_chunk_id FROM relations WHERE id = 'rel-1'").fetchone()
    conn.close()

    assert row_link[0] == "new-chunk-100"
    assert row_rel[0] == "new-chunk-100"


def test_graph_resync_similar_match(tmp_path, tmp_db):
    mock_db, db_path = tmp_db

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO entity_chunk_links VALUES ('link-2', 'e-2', 'old-chunk-2')")
    conn.commit()
    conn.close()

    backup_file = tmp_path / "file_index.before.json"
    backup_data = {
        "files": {
            "docs/guide.pdf": {
                "chunks": [
                    {
                        "chunk_id": "old-chunk-2",
                        "source_file": "docs/guide.pdf",
                        "section_title": "Section 2",
                        "text": "This is a detailed configuration guide for StampServer module",
                    }
                ]
            }
        }
    }
    backup_file.write_text(json.dumps(backup_data, ensure_ascii=False), encoding="utf-8")

    # Slightly altered document text (Jaccard > 0.8)
    mock_store = MagicMock()
    mock_store.get_chunk_stats_source.return_value = {
        "ids": ["new-chunk-200"],
        "documents": ["This is a detailed configuration guide for StampServer module v2"],
        "metadatas": [{"source_file": "docs/guide.pdf", "section_title": "Section 2"}],
    }

    service = GraphResyncService(db=mock_db, store=mock_store)
    res = service.resync(index_backup_path=backup_file)

    assert res["remapped_similar"] == 1

    conn = sqlite3.connect(db_path)
    row_link = conn.execute("SELECT chunk_id FROM entity_chunk_links WHERE id = 'link-2'").fetchone()
    conn.close()

    assert row_link[0] == "new-chunk-200"


def test_graph_resync_chunk_ids_backup_reads_backup_collection(tmp_path, tmp_db):
    """Scanner/rebuild backups only have chunk_ids; text must come from backup collection."""
    mock_db, db_path = tmp_db

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO entity_chunk_links VALUES ('link-3', 'e-3', 'old-chunk-3')")
    conn.commit()
    conn.close()

    backup_file = tmp_path / "file_index.before.json"
    backup_file.write_text(
        json.dumps(
            {
                "files": {
                    "hash-old": {
                        "file_path": "docs/manual.pdf",
                        "chunk_ids": ["old-chunk-3"],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    backup_store = MagicMock()
    backup_store.get_chunk_stats_source.return_value = {
        "ids": ["old-chunk-3"],
        "documents": ["Hello world graph resync text"],
        "metadatas": [{"source": "docs/manual.pdf", "section_title": "Section 1"}],
    }

    live_store = MagicMock()
    live_store.fork.return_value = backup_store
    live_store.get_chunk_stats_source.return_value = {
        "ids": ["new-chunk-300"],
        "documents": ["Hello world graph resync text"],
        "metadatas": [{"source": "docs/manual.pdf", "section_title": "Section 1"}],
    }

    service = GraphResyncService(db=mock_db, store=live_store)
    res = service.resync(
        index_backup_path=backup_file,
        backup_collection="rag_knowledge__backup__op1",
    )

    assert res["remapped_exact"] == 1
    assert res["orphaned"] == 0
    live_store.fork.assert_called_once_with("rag_knowledge__backup__op1")

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT chunk_id FROM entity_chunk_links WHERE id = 'link-3'").fetchone()
    conn.close()
    assert row[0] == "new-chunk-300"
