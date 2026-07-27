# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_extraction import (
    EntityCandidate,
    ExtractionResult,
    GraphBuilder,
)


def _chunk(chunk_id: str, content: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "content": content,
        "metadata": {
            "doc_category": "StampTools",
            "section_path": "StampTools > 安装",
            "content_type": "text",
        },
    }


def _llm_result(chunk_id: str, name: str) -> ExtractionResult:
    result = ExtractionResult()
    result.entities.append(
        EntityCandidate(
            name=name,
            entity_type="Command",
            doc_category="StampTools",
            properties={"created_by": "llm:schema_extractor", "confidence": 0.91},
            source_chunk_id=chunk_id,
            evidence_text=name,
        )
    )
    return result


def test_extract_checkpoint_and_resume_skips_processed_chunks(isolated_storage, monkeypatch):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    db = RelationalDB()
    chunks = [
        _chunk("chunk-a", "运行 systemctl restart stamp-tools"),
        _chunk("chunk-b", "运行 yum install stamp-tools"),
    ]

    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.load_backbone_constraints",
        lambda path=None: {
            "belongs_to": {},
            "different_from": set(),
            "requires": set(),
            "relations": [],
            "canonical_by_alias": {},
            "entity_type_by_name": {},
            "doc_categories": {"StampTools"},
        },
    )
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.assert_ollama_reachable",
        lambda **kwargs: "http://test",
    )
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.chunk_in_backbone_neighborhood",
        lambda chunk, constraints: True,
    )
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.rule_result_hits_backbone",
        lambda result, constraints: False,
    )

    call_log: list[str] = []
    interrupted = {"raised": False}

    def _extract(self, chunk):
        chunk_id = str(chunk.get("chunk_id") or "")
        call_log.append(chunk_id)
        if chunk_id == "chunk-b" and not interrupted["raised"]:
            interrupted["raised"] = True
            raise RuntimeError("simulated llm interrupt")
        return _llm_result(chunk_id, "systemctl restart" if chunk_id == "chunk-a" else "yum install")

    builder = GraphBuilder(db=db, chunk_source=lambda: chunks)
    with patch(
        "rag_knowledge.services.graph_extraction.llm_extractor.LLMGraphExtractor.extract",
        _extract,
    ):
        with pytest.raises(RuntimeError, match="simulated llm interrupt"):
            builder.build_full(force_rebuild=True, include_llm=True)

        batches = db.list_extraction_batches()
        assert len(batches) == 1
        batch_id = batches[0]["id"]
        mid_stats = json.loads(batches[0]["stats_json"] or "{}")
        assert mid_stats.get("extract_progress") == "running"
        assert mid_stats.get("processed_chunk_ids") == ["chunk-a"]
        assert mid_stats.get("llm_candidates", 0) >= 1

        before_ids = {c["id"] for c in db.list_extraction_candidates(batch_id)}
        before_count = len(before_ids)

        resumed = builder.resume_batch(batch_id)
        assert resumed.batch_id == batch_id
        assert resumed.stats.get("extract_progress") == "completed"
        assert resumed.stats.get("processed_chunk_ids") == ["chunk-a", "chunk-b"]
        assert call_log.count("chunk-a") == 1  # not re-extracted
        assert call_log.count("chunk-b") == 2  # fail once, succeed on resume

        after = db.list_extraction_candidates(batch_id)
        assert len(after) > before_count
        # First-pass candidates remain; fingerprint collision must not duplicate rows.
        assert before_ids.issubset({c["id"] for c in after})

        llm_names = {
            c["payload"].get("name")
            for c in after
            if c["candidate_kind"] == "entity" and str(c["payload"].get("created_by") or "").startswith("llm:")
        }
        assert "systemctl restart" in llm_names
        assert "yum install" in llm_names


def test_resume_rejects_applied_batch(isolated_storage, monkeypatch):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    db = RelationalDB()
    chunks = [_chunk("chunk-a", "运行 systemctl restart stamp-tools")]

    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.load_backbone_constraints",
        lambda path=None: {
            "belongs_to": {},
            "different_from": set(),
            "requires": set(),
            "relations": [],
            "canonical_by_alias": {},
            "entity_type_by_name": {},
            "doc_categories": set(),
        },
    )
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.assert_ollama_reachable",
        lambda **kwargs: "http://test",
    )
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.chunk_in_backbone_neighborhood",
        lambda chunk, constraints: True,
    )

    with patch(
        "rag_knowledge.services.graph_extraction.llm_extractor.LLMGraphExtractor.extract",
        lambda self, chunk: _llm_result("chunk-a", "systemctl restart"),
    ):
        result = GraphBuilder(db=db, chunk_source=lambda: chunks).build_full(
            force_rebuild=True, include_llm=True
        )
    db.set_extraction_batch_status(result.batch_id, "applied")
    with pytest.raises(ValueError, match="cannot resume batch in status=applied"):
        GraphBuilder(db=db, chunk_source=lambda: chunks).resume_batch(result.batch_id)


def test_incomplete_batch_blocks_duplicate_extract(isolated_storage, monkeypatch):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    db = RelationalDB()
    chunks = [
        _chunk("chunk-a", "运行 systemctl restart stamp-tools"),
        _chunk("chunk-b", "运行 yum install stamp-tools"),
    ]

    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.load_backbone_constraints",
        lambda path=None: {
            "belongs_to": {},
            "different_from": set(),
            "requires": set(),
            "relations": [],
            "canonical_by_alias": {},
            "entity_type_by_name": {},
            "doc_categories": set(),
        },
    )
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.assert_ollama_reachable",
        lambda **kwargs: "http://test",
    )
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.chunk_in_backbone_neighborhood",
        lambda chunk, constraints: True,
    )

    calls = {"n": 0}

    def _extract(self, chunk):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("stop")
        return _llm_result(str(chunk.get("chunk_id")), "systemctl restart")

    builder = GraphBuilder(db=db, chunk_source=lambda: chunks)
    with patch(
        "rag_knowledge.services.graph_extraction.llm_extractor.LLMGraphExtractor.extract",
        _extract,
    ):
        with pytest.raises(RuntimeError, match="stop"):
            builder.build_full(force_rebuild=False, include_llm=True)
        with pytest.raises(ValueError, match="incomplete batch .* still in progress"):
            builder.build_full(force_rebuild=False, include_llm=True)
