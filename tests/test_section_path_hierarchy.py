"""Round-1: section_path hierarchy and DataSpec table→structure relations."""
from rag_knowledge.models.graph_schema import make_section_entity_name, validate_relation
from rag_knowledge.services.graph_extraction import (
    DataSpecTableRelationExtractor,
    SectionPathExtractor,
)


def chunk(chunk_id="c1", content="正文", **metadata):
    return {"chunk_id": chunk_id, "content": content, "metadata": metadata}


SOURCE = "StampTools用户手册.docx"


def test_section_hierarchy_creates_prefix_nodes_and_parent_edges():
    result = SectionPathExtractor().extract(
        chunk(
            source=SOURCE,
            doc_category="StampTools",
            section_path="PipelineBuilder > 数据规范 > 管线点表",
        )
    )

    root = make_section_entity_name(SOURCE, "PipelineBuilder")
    mid = make_section_entity_name(SOURCE, "PipelineBuilder > 数据规范")
    leaf = make_section_entity_name(SOURCE, "PipelineBuilder > 数据规范 > 管线点表")

    assert result.entity(root).entity_type == "Section"
    assert result.entity(mid).entity_type == "Section"
    assert result.entity(leaf).entity_type == "Section"
    assert result.entity(root).properties["section_path"] == "PipelineBuilder"
    assert result.entity(mid).properties["section_path"] == "PipelineBuilder > 数据规范"

    assert result.has_relation(SOURCE, "has_section", root)
    assert result.has_relation(root, "has_section", mid)
    assert result.has_relation(mid, "has_section", leaf)
    assert not result.has_relation(SOURCE, "has_section", leaf)
    assert not result.has_relation(SOURCE, "has_section", mid)

    assert result.has_relation("PipelineBuilder", "defined_in", leaf)
    assert not result.has_relation("PipelineBuilder", "defined_in", mid)

    linked = {link.entity_name for link in result.links if link.chunk_id == "c1"}
    assert {root, mid, leaf, SOURCE, "PipelineBuilder", "StampTools"} <= linked


def test_section_hierarchy_two_level_path():
    result = SectionPathExtractor().extract(
        chunk(source=SOURCE, doc_category="StampTools", section_path="PipelineBuilder > 发布流程")
    )
    root = make_section_entity_name(SOURCE, "PipelineBuilder")
    leaf = make_section_entity_name(SOURCE, "PipelineBuilder > 发布流程")
    assert result.has_relation(SOURCE, "has_section", root)
    assert result.has_relation(root, "has_section", leaf)
    assert len([e for e in result.entities if e.entity_type == "Section"]) == 2


def test_section_hierarchy_single_level_only_document_edge():
    result = SectionPathExtractor().extract(
        chunk(source=SOURCE, doc_category="StampTools", section_path="总览")
    )
    section = make_section_entity_name(SOURCE, "总览")
    assert result.entity(section).entity_type == "Section"
    assert result.has_relation(SOURCE, "has_section", section)
    assert not any(
        rel.relation_type == "has_section" and rel.source_name != SOURCE
        for rel in result.relations
    )


def test_validate_relation_allows_section_and_datatable_has_section():
    assert validate_relation("Section", "has_section", "Section")[0]
    assert validate_relation("DataTable", "has_section", "Section")[0]
    assert validate_relation("Document", "has_section", "Section")[0]
    assert not validate_relation("Tool", "has_section", "Section")[0]


def test_dataspec_links_table_to_nested_structure_section():
    path = "PipelineBuilder > 数据规范 > 管线点表 > 点数据结构"
    context = SectionPathExtractor().extract(
        chunk(
            content="| 字段名 | 说明 |\n|---|---|\n| 管点编号 | 唯一编号 |",
            source=SOURCE,
            doc_category="StampTools",
            section_path=path,
            content_type="table",
        )
    )
    result = DataSpecTableRelationExtractor().extract(
        chunk(
            content="点数据结构说明",
            source=SOURCE,
            doc_category="StampTools",
            section_path=path,
            content_type="table",
        ),
        context,
    )
    structure = make_section_entity_name(SOURCE, path)
    assert result.has_relation("管线点表", "has_section", structure)


def test_dataspec_matches_face_table_and_structure_kind():
    path = "PipelineBuilder > 数据规范 > 管线面表 > 面表数据结构"
    context = SectionPathExtractor().extract(
        chunk(source=SOURCE, doc_category="StampTools", section_path=path, content_type="table")
    )
    result = DataSpecTableRelationExtractor().extract(
        chunk(source=SOURCE, doc_category="StampTools", section_path=path),
        context,
    )
    structure = make_section_entity_name(SOURCE, path)
    assert result.has_relation("管线面表", "has_section", structure)


def test_dataspec_skips_sibling_tables_without_nested_structure():
    """Sibling 管线点表 / 面表数据结构 under 数据规范: hierarchy shares parent; no table→sibling edge."""
    path = "PipelineBuilder > 数据规范 > 面表数据结构"
    context = SectionPathExtractor().extract(
        chunk(source=SOURCE, doc_category="StampTools", section_path=path)
    )
    result = DataSpecTableRelationExtractor().extract(
        chunk(source=SOURCE, doc_category="StampTools", section_path=path),
        context,
    )
    assert result.relations == []
    # Common parent is created by SectionPathExtractor hierarchy.
    mid = make_section_entity_name(SOURCE, "PipelineBuilder > 数据规范")
    leaf = make_section_entity_name(SOURCE, path)
    assert context.has_relation(mid, "has_section", leaf)


def test_section_entity_name_normalizes_fullwidth_parens():
    from rag_knowledge.models.graph_schema import make_section_entity_name

    name = make_section_entity_name("StampTools用户手册.docx", "2）不支持自定义曲线 > UEModelBuilder")
    assert name == "StampTools用户手册::2)不支持自定义曲线 > UEModelBuilder"


def test_section_hierarchy_fullwidth_parens_align_entity_and_relation():
    """Entity normalizer and relation endpoints must share the same Section name."""
    result = SectionPathExtractor().extract(
        chunk(
            source=SOURCE,
            doc_category="StampTools",
            section_path="2）不支持自定义曲线 > UEModelBuilder",
        )
    )
    root = make_section_entity_name(SOURCE, "2）不支持自定义曲线")
    leaf = make_section_entity_name(SOURCE, "2）不支持自定义曲线 > UEModelBuilder")
    assert ")" in root and "）" not in root
    assert result.entity(root) is not None
    assert result.has_relation(SOURCE, "has_section", root)
    assert result.has_relation(root, "has_section", leaf)


def test_dataspec_ignores_paths_outside_data_spec_keywords():
    result = DataSpecTableRelationExtractor().extract(
        chunk(
            source=SOURCE,
            doc_category="StampTools",
            section_path="PipelineBuilder > 发布说明 > 管线点表 > 点数据结构",
        )
    )
    assert result.relations == []
