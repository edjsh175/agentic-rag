# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.domain_catalog import DomainCatalogLoader
from rag_knowledge.services.extraction_coverage import ExtractionCoverageService


def test_catalog_tools_for_stamptools_includes_subtools(tmp_path: Path):
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "products": [{"name": "StampTools", "aliases": [], "doc_categories": ["StampTools"]}],
                "tools": [
                    {"name": "TerrainBuilder", "aliases": [], "belongs_to": "StampTools"},
                    {"name": "DOMBuilder", "aliases": [], "belongs_to": "TerrainBuilder"},
                    {"name": "PipelineBuilder", "aliases": [], "belongs_to": "StampTools"},
                    {"name": "OtherTool", "aliases": [], "belongs_to": "StampServer"},
                ],
                "services": [],
                "environment_components": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    catalog = DomainCatalogLoader(catalog_path)
    service = ExtractionCoverageService(catalog=catalog)
    rows = service.catalog_tools_for_product("StampTools")
    names = {name for name, _, _ in rows}
    assert names == {"TerrainBuilder", "DOMBuilder", "PipelineBuilder"}
    top = {name: is_top for name, _, is_top in rows}
    assert top["TerrainBuilder"] is True
    assert top["DOMBuilder"] is False


def test_inspect_product_counts_function_area_table_field_chain(isolated_storage, tmp_path):
    isolated_storage(db_name="coverage.db", data_dir_name="coverage-data", chroma_name="coverage-chroma")
    db = RelationalDB()
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "products": [{"name": "StampTools", "aliases": [], "doc_categories": []}],
                "tools": [
                    {"name": "PipelineBuilder", "aliases": [], "belongs_to": "StampTools"},
                    {"name": "点云数据处理工具", "aliases": [], "belongs_to": "StampTools"},
                ],
                "services": [],
                "environment_components": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stamp = db.create_entity(
        "StampTools", "Product", review_status="approved", created_by="seed:product_backbone"
    )
    pipe = db.create_entity(
        "PipelineBuilder", "Tool", review_status="approved", created_by="seed:product_backbone"
    )
    cloud = db.create_entity(
        "点云数据处理工具", "Tool", review_status="approved", created_by="seed:product_backbone"
    )
    fa = db.create_entity(
        "PipelineBuilder::数据规范",
        "FunctionArea",
        review_status="approved",
        created_by="rule:phase_b",
    )
    table = db.create_entity(
        "管线点表", "DataTable", review_status="approved", created_by="rule:phase_b"
    )
    field = db.create_entity(
        "管线点表.管点编号", "Field", review_status="approved", created_by="rule:phase_b"
    )
    db.create_relation(pipe, stamp, "belongs_to", created_by="seed:product_backbone")
    db.create_relation(cloud, stamp, "belongs_to", created_by="seed:product_backbone")
    db.create_relation(fa, pipe, "belongs_to", created_by="rule:phase_b")
    db.create_relation(table, fa, "belongs_to", created_by="rule:phase_b")
    db.create_relation(table, field, "has_field", created_by="rule:phase_b")

    service = ExtractionCoverageService(db=db, catalog=DomainCatalogLoader(catalog_path))
    report = service.inspect_product("StampTools")
    by_name = {row.tool: row for row in report.tools}
    assert by_name["PipelineBuilder"].covered
    assert by_name["PipelineBuilder"].extraction_leaf_count >= 3
    assert by_name["点云数据处理工具"].top_level
    assert not by_name["点云数据处理工具"].covered
    assert "点云数据处理工具" in report.as_dict()["uncovered_tools"]
