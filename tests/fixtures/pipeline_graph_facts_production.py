# -*- coding: utf-8 -*-
"""Production-shaped graph facts: scoped Fields, source::section_path Sections."""
from __future__ import annotations

import json

from rag_knowledge.models.graph_schema import make_field_entity_name, make_section_entity_name
from rag_knowledge.repository.relational_db import RelationalDB

DOC_SOURCE = "StampTools用户手册.docx"

POINT_CANONICAL_PATH = "PipelineBuilder > 数据规范 > 管线点表"
LINE_CANONICAL_PATH = "PipelineBuilder > 数据规范 > 管线线表"
FACE_CANONICAL_PATH = "PipelineBuilder > 数据规范 > 管线面表"


def _section(db: RelationalDB, section_path: str) -> str:
    name = make_section_entity_name(DOC_SOURCE, section_path)
    entity = db.get_entity_by_name(name)
    if entity:
        return entity["id"]
    return db.create_entity(
        name,
        "Section",
        properties_json=json.dumps({"section_path": section_path}, ensure_ascii=False),
        review_status="approved",
    )


def _field(db: RelationalDB, table_name: str, leaf: str) -> str:
    name = make_field_entity_name(table_name, leaf)
    entity = db.get_entity_by_name(name)
    if entity:
        return entity["id"]
    return db.create_entity(name, "Field", review_status="approved")


def seed_production_pipeline_graph(db: RelationalDB) -> None:
    """Full production-shaped pipeline graph facts."""
    pipeline = db.create_entity("PipelineBuilder", "Tool", doc_category="StampTools", review_status="approved")
    stamp_tools = db.create_entity("StampTools", "Product", doc_category="StampTools", review_status="approved")
    dom_builder = db.create_entity("DOMBuilder", "Tool", doc_category="StampTools", review_status="approved")

    point = db.create_entity("管线点表", "DataTable", doc_category="StampTools", review_status="approved")
    line = db.create_entity("管线线表", "DataTable", doc_category="StampTools", review_status="approved")
    face = db.create_entity("管线面表", "DataTable", doc_category="StampTools", review_status="approved")

    db.create_alias(point, "点数据结构", review_status="approved")
    db.create_alias(line, "线表数据结构", review_status="approved")
    db.create_alias(face, "面表数据结构", review_status="approved")

    point_field_a = _field(db, "管线点表", "管点编号")
    point_field_b = _field(db, "管线点表", "地面高程")
    line_field = _field(db, "管线线表", "管线编号")
    face_field = _field(db, "管线面表", "管面编号")

    point_section = _section(db, POINT_CANONICAL_PATH)
    line_section = _section(db, LINE_CANONICAL_PATH)
    face_section = _section(db, FACE_CANONICAL_PATH)

    for table_id, section_id, tool_id in (
        (point, point_section, pipeline),
        (line, line_section, pipeline),
        (face, face_section, pipeline),
    ):
        db.create_relation(tool_id, table_id, "has_table", review_status="approved")
        db.create_relation(table_id, tool_id, "belongs_to", review_status="approved")
        db.create_relation(table_id, section_id, "defined_in", review_status="approved")

    db.create_relation(point, point_field_a, "has_field", review_status="approved")
    db.create_relation(point, point_field_b, "has_field", review_status="approved")
    db.create_relation(line, line_field, "has_field", review_status="approved")
    db.create_relation(face, face_field, "has_field", review_status="approved")

    for left, right in ((point, line), (point, face), (line, face)):
        db.create_relation(left, right, "different_from", review_status="approved")

    db.create_relation(dom_builder, stamp_tools, "belongs_to", review_status="approved")


def seed_partial_pipeline_graph(db: RelationalDB) -> None:
    """Phase B partial facts: point/line tables only, no face/alias/sibling."""
    pipeline = db.create_entity("PipelineBuilder", "Tool", doc_category="StampTools", review_status="approved")

    point = db.create_entity("管线点表", "DataTable", doc_category="StampTools", review_status="approved")
    line = db.create_entity("管线线表", "DataTable", doc_category="StampTools", review_status="approved")

    point_field_a = _field(db, "管线点表", "管点编号")
    point_field_b = _field(db, "管线点表", "地面高程")
    line_field = _field(db, "管线线表", "管线编号")

    point_section = _section(db, POINT_CANONICAL_PATH)
    line_section = _section(db, LINE_CANONICAL_PATH)

    for table_id, section_id in ((point, point_section), (line, line_section)):
        db.create_relation(pipeline, table_id, "has_table", review_status="approved")
        db.create_relation(table_id, pipeline, "belongs_to", review_status="approved")
        db.create_relation(table_id, section_id, "defined_in", review_status="approved")

    db.create_relation(point, point_field_a, "has_field", review_status="approved")
    db.create_relation(point, point_field_b, "has_field", review_status="approved")
    db.create_relation(line, line_field, "has_field", review_status="approved")
