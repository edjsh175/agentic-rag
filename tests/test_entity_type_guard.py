# -*- coding: utf-8 -*-
from __future__ import annotations

from rag_knowledge.models.graph_schema import validate_relation
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.entity_type_guard import coerce_entity_type, looks_like_utility_name
from rag_knowledge.services.graph_extraction.pipeline import GraphCandidateApplier


def test_looks_like_utility_name():
    assert looks_like_utility_name("Test_coord.exe")
    assert looks_like_utility_name("libfoo.so")
    assert not looks_like_utility_name("PipelineBuilder")
    assert not looks_like_utility_name("TerrainBuilder")


def test_coerce_tool_binary_to_utility():
    assert coerce_entity_type("Test_coord.exe", "Tool") == "Utility"
    assert coerce_entity_type("PipelineBuilder", "Tool") == "Tool"


def test_utility_cannot_belong_to_product():
    ok, _ = validate_relation("Utility", "belongs_to", "Product")
    assert not ok
    ok, _ = validate_relation("Utility", "belongs_to", "Tool")
    assert ok
    ok, _ = validate_relation("Utility", "belongs_to", "Procedure")
    assert ok


def test_apply_coerces_exe_tool_and_skips_product_parent(isolated_storage):
    isolated_storage(db_name="utility.db", data_dir_name="utility-data", chroma_name="utility-chroma")
    db = RelationalDB()
    db.create_entity("StampTools", "Product", review_status="approved", created_by="seed:product_backbone")
    batch_id = db.create_extraction_batch("incremental", {}, "snapshot")
    entity_id = db.add_extraction_candidate(
        batch_id,
        "entity",
        "fp-util-ent",
        {
            "name": "Test_coord.exe",
            "entity_type": "Tool",
            "doc_category": "StampTools",
            "created_by": "llm:schema_extractor",
            "confidence": 1.0,
            "source_chunk_id": "c1",
            "evidence_text": "投影参数",
        },
        "c1",
        "投影参数",
    )
    rel_id = db.add_extraction_candidate(
        batch_id,
        "relation",
        "fp-util-rel",
        {
            "source_name": "Test_coord.exe",
            "relation_type": "belongs_to",
            "target_name": "StampTools",
            "created_by": "llm:schema_extractor",
            "confidence": 0.8,
            "source_chunk_id": "c1",
            "evidence_text": "投影参数",
        },
        "c1",
        "投影参数",
    )
    db.review_extraction_candidates(batch_id, [entity_id, rel_id], "approved")
    db.set_extraction_batch_status(batch_id, "approved")

    GraphCandidateApplier(db).apply(batch_id)

    entity = db.get_entity_by_name("Test_coord.exe")
    assert entity is not None
    assert entity["entity_type"] == "Utility"
    with db._get_conn() as conn:
        rows = conn.execute(
            """
            SELECT t.name AS parent, t.entity_type
            FROM relations r
            JOIN entities s ON s.id = r.source_entity_id
            JOIN entities t ON t.id = r.target_entity_id
            WHERE s.name = ? AND r.relation_type = 'belongs_to'
            """,
            ("Test_coord.exe",),
        ).fetchall()
    assert list(rows) == []
