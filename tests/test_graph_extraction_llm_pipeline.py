# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_extraction import (
    GraphBuilder,
    GraphCandidateApplier,
    EntityCandidate,
    RelationCandidate,
    ExtractionResult
)


def test_graph_extraction_llm_pipeline(isolated_storage):
    cfg, db_path, chroma_dir, data_dir = isolated_storage()
    db = RelationalDB()

    # 1. Setup sample input chunks
    chunks = [
        {
            "chunk_id": "chunk-100",
            "content": "管线发布服务配置说明. 管线发布服务依赖 PostgreSQL 16 数据库.",
            "metadata": {
                "doc_category": "StampServer",
                "section_path": "服务配置 > 管线发布服务配置",
                "content_type": "text"
            }
        }
    ]

    # 2. Mock LLMGraphExtractor to return mock entities & relations & aliases
    mock_llm_result = ExtractionResult()
    mock_llm_result.entities.append(
        EntityCandidate(
            name="PostgreSQL 16 数据库",
            entity_type="EnvironmentComponent",
            doc_category="StampServer",
            properties={
                "created_by": "llm:schema_extractor",
                "confidence": 0.95
            },
            source_chunk_id="chunk-100",
            evidence_text="PostgreSQL 16 数据库"
        )
    )
    mock_llm_result.entities.append(
        EntityCandidate(
            name="管线发布服务",
            entity_type="Service",
            doc_category="StampServer",
            properties={
                "created_by": "llm:schema_extractor",
                "confidence": 0.95
            },
            source_chunk_id="chunk-100",
            evidence_text="管线发布服务"
        )
    )
    mock_llm_result.relations.append(
        RelationCandidate(
            source_name="管线发布服务",
            relation_type="depends_on",
            target_name="PostgreSQL 16 数据库",
            source_chunk_id="chunk-100",
            evidence_text="管线发布服务依赖 PostgreSQL 16 数据库"
        )
    )
    mock_llm_result.relation_metadata = {
        ("管线发布服务", "depends_on", "PostgreSQL 16 数据库"): {
            "confidence": 0.88,
            "prompt_version": "v1.2",
            "extractor_version": "v2.1"
        }
    }
    # Add alias dynamically
    mock_llm_result.aliases = [
        {
            "entity_name": "PostgreSQL 16 数据库",
            "alias": "PostgreSQL 16",
            "confidence": 0.9,
            "evidence_text": "PostgreSQL 16 数据库",
            "source_chunk_id": "chunk-100",
            "created_by": "llm:schema_extractor"
        }
    ]

    with patch("rag_knowledge.services.graph_extraction.llm_extractor.LLMGraphExtractor.extract", return_value=mock_llm_result):
        # Instantiate builder passing the local chunk source mock
        builder = GraphBuilder(db=db, chunk_source=lambda: chunks)
        
        # Run extraction pipeline with include_llm = True
        res = builder.build_full(force_rebuild=True, include_llm=True)

        batch_id = res.batch_id
        stats = res.stats

        # 3. Check stats details
        assert stats["chunks"] == 1
        assert stats["entity"] > 0
        assert stats["relation"] > 0
        assert stats["alias"] == 1
        assert stats["rule_candidates"] > 0
        assert stats["llm_candidates"] == 4  # Entities + Relation + Alias from LLM
        
        # Check extraction candidates count in DB staging
        candidates = db.list_extraction_candidates(batch_id)
        assert len(candidates) > 0

        # Verify LLM candidate created_by payload exists
        llm_entity_cand = next(c for c in candidates if c["candidate_kind"] == "entity" and c["payload"].get("name") == "PostgreSQL 16 数据库")
        assert llm_entity_cand["payload"]["created_by"] == "llm:schema_extractor"

        # 4. Approve batch candidates
        db.review_extraction_candidates(batch_id, [c["id"] for c in candidates], "approved")
        db.set_extraction_batch_status(batch_id, "approved")

        # 5. Apply batch candidates to final graph DB
        applier = GraphCandidateApplier(db)
        applier.apply(batch_id)

        # 6. Verify entities & relations in DB
        ent = db.get_entity_by_name("PostgreSQL 16 数据库")
        assert ent is not None
        assert ent["entity_type"] == "EnvironmentComponent"
        assert ent["created_by"] == "llm:schema_extractor"
        assert ent["confidence"] == 0.95
        ent_props = json.loads(ent["properties_json"])
        assert ent_props.get("prompt_version") is not None

        relations = db.list_relations(relation_type="depends_on")
        assert len(relations) == 1
        assert relations[0]["source_name"] == "管线发布服务"
        assert relations[0]["target_name"] == "PostgreSQL 16 数据库"
        assert relations[0]["created_by"] == "llm:schema_extractor"
        assert relations[0]["confidence"] == 0.88
        rel_props = json.loads(relations[0]["properties_json"])
        assert rel_props.get("prompt_version") == "v1.2"
        assert rel_props.get("extractor_version") == "v2.1"
        assert rel_props.get("confidence") == 0.88

        aliases = db.list_aliases(ent["id"])
        assert len(aliases) == 1
        assert aliases[0]["alias"] == "PostgreSQL 16"
