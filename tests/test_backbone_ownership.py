# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from rag_knowledge.models.graph_schema import validate_relation
from rag_knowledge.services.backbone_ownership import (
    catalog_ownership_expectations,
    find_ownership_gaps,
    is_architecture_layer_name,
    repair_backbone_payload,
)
from rag_knowledge.services.domain_catalog import DomainCatalogLoader


def test_architecture_layer_names():
    assert is_architecture_layer_name("工具与数据处理层")
    assert not is_architecture_layer_name("TerrainBuilder")


def test_schema_rejects_tool_belongs_to_module():
    ok, _ = validate_relation("Tool", "belongs_to", "Module")
    assert ok  # Module allowed for non-layer groupings
    ok, _ = validate_relation("Service", "belongs_to", "Module")
    assert ok
    ok, _ = validate_relation("Tool", "belongs_to", "Product")
    assert ok
    ok, _ = validate_relation("Tool", "belongs_to", "Tool")
    assert ok
    assert is_architecture_layer_name("工具与数据处理层")


def test_repair_drops_layer_and_ensures_catalog_owner(tmp_path: Path):
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "products": [{"name": "StampTools", "aliases": [], "doc_categories": ["StampTools"]}],
                "tools": [
                    {"name": "TerrainBuilder", "aliases": [], "belongs_to": "StampTools"},
                    {"name": "DOMBuilder", "aliases": [], "belongs_to": "TerrainBuilder"},
                ],
                "services": [],
                "environment_components": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    catalog = DomainCatalogLoader(catalog_path)
    payload = {
        "schema_version": 1,
        "entities": [
            {"name": "StampTools", "entity_type": "Product"},
            {"name": "TerrainBuilder", "entity_type": "Tool"},
            {"name": "DOMBuilder", "entity_type": "Tool"},
            {"name": "工具与数据处理层", "entity_type": "Module"},
        ],
        "relations": [
            {
                "source": "TerrainBuilder",
                "relation_type": "belongs_to",
                "target": "工具与数据处理层",
            },
            {
                "source": "DOMBuilder",
                "relation_type": "belongs_to",
                "target": "TerrainBuilder",
            },
            {
                "source": "StampTools",
                "relation_type": "belongs_to",
                "target": "工具与数据处理层",
            },
        ],
    }
    repaired, report = repair_backbone_payload(payload, catalog=catalog)
    edges = {
        (r["source"], r["relation_type"], r["target"])
        for r in repaired["relations"]
    }
    assert ("TerrainBuilder", "belongs_to", "工具与数据处理层") not in edges
    assert ("StampTools", "belongs_to", "工具与数据处理层") not in edges
    assert ("TerrainBuilder", "belongs_to", "StampTools") in edges
    assert ("DOMBuilder", "belongs_to", "TerrainBuilder") in edges
    assert report.dropped_count == 2
    assert ("TerrainBuilder", "StampTools") in report.ensured_owner_edges


def test_find_ownership_gaps_reports_missing_and_layer():
    catalog_path = Path(__file__).resolve().parents[1] / "data" / "domain_catalog.json"
    catalog = DomainCatalogLoader(catalog_path)
    expectations = catalog_ownership_expectations(catalog)
    assert expectations["TerrainBuilder"] == "StampTools"
    assert expectations["DOMBuilder"] == "TerrainBuilder"

    gaps = find_ownership_gaps(
        entity_types={
            "TerrainBuilder": "Tool",
            "StampTools": "Product",
            "DOMBuilder": "Tool",
        },
        belongs_to_parents={
            "TerrainBuilder": ["工具与数据处理层"],
            "DOMBuilder": ["TerrainBuilder"],
        },
        catalog=catalog,
    )
    codes = {g.reason for g in gaps if g.child == "TerrainBuilder"}
    assert "architecture_layer_parent" in codes
    assert "missing_owner_edge" in codes
