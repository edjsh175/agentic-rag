"""Approved graph facts mirroring retrieval_intent_profiles_v1 for intent scoring tests."""
from __future__ import annotations

import json

from rag_knowledge.repository.relational_db import RelationalDB


def _section(db: RelationalDB, section_path: str) -> str:
    name = section_path.replace(" > ", "_")
    entity = db.get_entity_by_name(name)
    if entity:
        return entity["id"]
    return db.create_entity(
        name,
        "Section",
        properties_json=json.dumps({"section_path": section_path}, ensure_ascii=False),
        review_status="approved",
    )


def seed_pipeline_table_graph(db: RelationalDB) -> None:
    """Seed graph facts equivalent to legacy pipeline table profiles."""
    pipeline = db.create_entity("PipelineBuilder", "Tool", doc_category="StampTools", review_status="approved")
    stamp_tools = db.create_entity("StampTools", "Product", doc_category="StampTools", review_status="approved")
    dom_builder = db.create_entity("DOMBuilder", "Tool", doc_category="StampTools", review_status="approved")

    point = db.create_entity("管线点表", "DataTable", doc_category="StampTools", review_status="approved")
    line = db.create_entity("管线线表", "DataTable", doc_category="StampTools", review_status="approved")
    face = db.create_entity("管线面表", "DataTable", doc_category="StampTools", review_status="approved")

    db.create_alias(point, "点数据结构", review_status="approved")
    db.create_alias(line, "线表数据结构", review_status="approved")
    db.create_alias(face, "面表数据结构", review_status="approved")

    point_field_a = db.create_entity("管点编号", "Field", review_status="approved")
    point_field_b = db.create_entity("地面高程", "Field", review_status="approved")
    line_field = db.create_entity("管线编号", "Field", review_status="approved")
    face_field = db.create_entity("管面编号", "Field", review_status="approved")

    point_section = _section(db, "PipelineBuilder > 数据规范 > 管线点表")
    point_section_alias = _section(db, "PipelineBuilder > 数据规范 > 点数据结构")
    line_section = _section(db, "PipelineBuilder > 数据规范 > 管线线表")
    line_section_alias = _section(db, "PipelineBuilder > 数据规范 > 线表数据结构")
    face_section = _section(db, "PipelineBuilder > 数据规范 > 管线面表")
    face_section_alias = _section(db, "PipelineBuilder > 数据规范 > 面表数据结构")

    for table_id, sections in (
        (point, (point_section, point_section_alias)),
        (line, (line_section, line_section_alias)),
        (face, (face_section, face_section_alias)),
    ):
        db.create_relation(pipeline, table_id, "has_table", review_status="approved")
        db.create_relation(table_id, pipeline, "belongs_to", review_status="approved")
        for section_id in sections:
            db.create_relation(table_id, section_id, "defined_in", review_status="approved")

    db.create_relation(point, point_field_a, "has_field", review_status="approved")
    db.create_relation(point, point_field_b, "has_field", review_status="approved")
    db.create_relation(line, line_field, "has_field", review_status="approved")
    db.create_relation(face, face_field, "has_field", review_status="approved")

    for left, right in (
        (point, line),
        (point, face),
        (line, face),
    ):
        db.create_relation(left, right, "different_from", review_status="approved")

    db.create_relation(dom_builder, stamp_tools, "belongs_to", review_status="approved")
