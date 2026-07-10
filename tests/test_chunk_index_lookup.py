import json
from pathlib import Path

from rag_knowledge.services.chunk_index_lookup import ChunkIndexLookupService


def test_lookup_maps_chunk_ids_to_file_records(tmp_path):
    index = tmp_path / "file_index.json"
    index.write_text(
        json.dumps({"files": {"h1": {"chunk_ids": ["c1"], "kb_name": "文章附件"}}}),
        encoding="utf-8",
    )
    lookup = ChunkIndexLookupService(index)
    assert lookup.by_chunk_id("c1")["kb_name"] == "文章附件"
    assert lookup.by_chunk_id("missing") == {}


def test_lookup_returns_empty_map_when_index_missing(tmp_path):
    lookup = ChunkIndexLookupService(tmp_path / "missing.json")
    assert lookup.all() == {}
    assert lookup.by_chunk_id("c1") == {}


def test_lookup_returns_empty_map_on_invalid_json(tmp_path):
    index = tmp_path / "file_index.json"
    index.write_text("{", encoding="utf-8")
    lookup = ChunkIndexLookupService(index)
    assert lookup.all() == {}
