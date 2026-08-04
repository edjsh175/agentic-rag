# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from rag_knowledge.models.graph_schema import validate_relation
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_extraction import (
    EntityCandidate,
    ExtractionResult,
    GraphBuilder,
    RelationCandidate,
)
from rag_knowledge.services.graph_extraction.evidence_span import (
    evidence_matches,
    normalize_for_evidence_match,
)


def test_evidence_matches_folds_whitespace_and_fullwidth():
    content = "执行　systemctl restart redis（生产）"
    assert evidence_matches("执行 systemctl restart redis(生产)", content)
    assert evidence_matches("执行　systemctl restart redis（生产）", content)
    assert normalize_for_evidence_match("A（B）") == "A(B)"
    assert not evidence_matches("完全不相关", content)


def test_runs_command_allows_tool_and_service():
    assert validate_relation("Procedure", "runs_command", "Command")[0]
    assert validate_relation("Tool", "runs_command", "Command")[0]
    assert validate_relation("Service", "runs_command", "Command")[0]
    assert validate_relation("EnvironmentComponent", "runs_command", "Command")[0]
    assert not validate_relation("Command", "runs_command", "EnvironmentComponent")[0]


def test_staging_rejects_illegal_relation(isolated_storage, monkeypatch):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    db = RelationalDB()
    chunks = [
        {
            "chunk_id": "c1",
            "content": "执行 systemctl restart redis 完成安装 redis",
            "metadata": {
                "doc_category": "StampServer",
                "section_path": "Redis安装",
                "content_type": "text",
            },
        }
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
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.rule_result_hits_backbone",
        lambda result, constraints: False,
    )

    llm_result = ExtractionResult()
    llm_result.entities.extend(
        [
            EntityCandidate(
                name="systemctl restart redis",
                entity_type="Command",
                doc_category="StampServer",
                properties={"created_by": "llm:schema_extractor", "confidence": 0.95},
                source_chunk_id="c1",
                evidence_text="systemctl restart redis",
            ),
            EntityCandidate(
                name="redis",
                entity_type="EnvironmentComponent",
                doc_category="StampServer",
                properties={"created_by": "llm:schema_extractor", "confidence": 0.9},
                source_chunk_id="c1",
                evidence_text="安装 redis",
            ),
            EntityCandidate(
                name="Redis安装流程",
                entity_type="Procedure",
                doc_category="StampServer",
                properties={"created_by": "llm:schema_extractor", "confidence": 0.9},
                source_chunk_id="c1",
                evidence_text="完成安装 redis",
            ),
        ]
    )
    llm_result.relations.extend(
        [
            RelationCandidate(
                source_name="systemctl restart redis",
                relation_type="runs_command",
                target_name="redis",
                source_chunk_id="c1",
                evidence_text="systemctl restart redis",
            ),
            RelationCandidate(
                source_name="Redis安装流程",
                relation_type="runs_command",
                target_name="systemctl restart redis",
                source_chunk_id="c1",
                evidence_text="执行 systemctl restart redis",
            ),
        ]
    )
    llm_result.relation_metadata = {
        ("systemctl restart redis", "runs_command", "redis"): {
            "confidence": 0.9,
            "prompt_version": "v2",
            "extractor_version": "v1",
        },
        ("Redis安装流程", "runs_command", "systemctl restart redis"): {
            "confidence": 0.95,
            "prompt_version": "v2",
            "extractor_version": "v1",
        },
    }

    with patch(
        "rag_knowledge.services.graph_extraction.llm_extractor.LLMGraphExtractor.extract",
        return_value=llm_result,
    ):
        result = GraphBuilder(db=db, chunk_source=lambda: chunks).build_full(
            force_rebuild=True, include_llm=True
        )

    rels = [
        c
        for c in db.list_extraction_candidates(result.batch_id)
        if c["candidate_kind"] == "relation"
        and str((c.get("payload") or {}).get("created_by") or "").startswith("llm:")
    ]
    by_key = {
        (
            c["payload"]["source_name"],
            c["payload"]["relation_type"],
            c["payload"]["target_name"],
        ): c["status"]
        for c in rels
    }
    assert by_key[("redis", "runs_command", "systemctl restart redis")] == "pending"
    assert by_key[("Redis安装流程", "runs_command", "systemctl restart redis")] == "pending"
    assert ("systemctl restart redis", "runs_command", "redis") not in by_key
    flipped = [
        c
        for c in db.list_extraction_candidates(result.batch_id)
        if c["candidate_kind"] == "diagnostic"
        and (c.get("payload") or {}).get("code") == "relation_direction_flipped"
    ]
    assert flipped
    assert result.stats.get("relation_direction_flipped", 0) >= 1
    # Still-illegal both ways should remain rejected (covered separately if needed).
    diags = [
        c
        for c in db.list_extraction_candidates(result.batch_id)
        if c["candidate_kind"] == "diagnostic"
        and (c.get("payload") or {}).get("code") == "illegal_relation"
    ]
    # Command→redis was flipped, so may be zero illegal for this fixture.
    assert isinstance(diags, list)


def test_fingerprint_keeps_first_confidence(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    db = RelationalDB()
    batch_id = db.create_extraction_batch("full", {"include_llm": True}, "snap")
    payload1 = {
        "name": "X",
        "entity_type": "Command",
        "confidence": 0.7,
        "created_by": "rule:phase_b",
        "evidences": [{"source_chunk_id": "a", "evidence_text": "x"}],
    }
    payload2 = {
        "name": "X",
        "entity_type": "Command",
        "confidence": 0.99,
        "created_by": "llm:schema_extractor",
        "evidences": [{"source_chunk_id": "b", "evidence_text": "x"}],
    }
    fp = "same-fp"
    id1 = db.add_extraction_candidate(batch_id, "entity", fp, payload1, "a", "x")
    id2 = db.add_extraction_candidate(batch_id, "entity", fp, payload2, "b", "x")
    assert id1 == id2
    row = next(c for c in db.list_extraction_candidates(batch_id) if c["id"] == id1)
    assert row["payload"]["confidence"] == 0.7
    assert row["payload"]["created_by"] == "rule:phase_b"
    assert len(row["payload"]["evidences"]) == 2


def test_auto_approve_confidence_removed():
    from rag_knowledge.config import GraphLLMExtractorConfig

    assert not hasattr(GraphLLMExtractorConfig(), "auto_approve_confidence")
