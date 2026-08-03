import json

import pytest

from rag_knowledge.services.domain_catalog import DomainCatalogLoader
from rag_knowledge.services.graph_extraction import SectionPathExtractor


def test_catalog_resolves_alias_and_section_extractor_uses_external_catalog(tmp_path):
    catalog_path = tmp_path / "domain_catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "products": [{"name": "DemoProduct", "aliases": ["Demo 产品"], "doc_categories": ["Demo"]}],
                "tools": [{"name": "DemoTool", "aliases": ["演示工具"], "belongs_to": "DemoProduct"}],
                "services": [],
                "environment_components": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    catalog = DomainCatalogLoader(catalog_path)
    assert catalog.resolve("演示工具") == ("DemoTool", "Tool")

    result = SectionPathExtractor(catalog=catalog).extract(
        {
            "chunk_id": "c1",
            "content": "正文",
            "metadata": {"source": "demo.md", "doc_category": "Demo", "section_path": "演示工具 > 使用说明"},
        }
    )
    assert result.entity("DemoTool").entity_type == "Tool"
    assert result.entity("演示工具") is None


def test_catalog_missing_file_fails_fast(tmp_path):
    with pytest.raises(FileNotFoundError, match="domain_catalog.json"):
        DomainCatalogLoader(tmp_path / "domain_catalog.json")


def test_catalog_invalid_json_reports_path(tmp_path):
    path = tmp_path / "domain_catalog.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="domain_catalog.json"):
        DomainCatalogLoader(path)


def test_related_entities_for_scoring_and_reasons(tmp_path):
    catalog_path = tmp_path / "domain_catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "products": [],
                "tools": [
                    {"name": "PipelineBuilder", "aliases": ["pipeline"], "belongs_to": "StampTools", "different_from": ["PipelineWebGL"]},
                    {"name": "PipelineWebGL", "aliases": ["pipelinewebgl"], "belongs_to": "StampTools"},
                    {"name": "OtherTool", "aliases": [], "belongs_to": "StampTools"},
                ],
                "services": [{"name": "管线发布服务", "aliases": [], "belongs_to": "StampServer"}],
                "environment_components": [{"name": "Apache", "aliases": []}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    catalog = DomainCatalogLoader(catalog_path)
    res = catalog.related_entities_for("PipelineBuilder")
    assert len(res) > 0
    top = res[0]
    assert top["name"] == "PipelineWebGL"
    assert top["score"] >= 1.0
    assert "explicit_different_from" in top["reasons"]
