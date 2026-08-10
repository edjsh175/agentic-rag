# -*- coding: utf-8 -*-
from __future__ import annotations

from rag_knowledge.services.graph_extraction.chapter_leaf_extractor import (
    ChapterLeafExtractor,
    resolve_path_owner,
)


def test_resolve_path_owner_prefers_most_specific_tool():
    assert resolve_path_owner("TerrainBuilder > DOMBuilder > 工程设置") == "DOMBuilder"
    assert resolve_path_owner("点云数据处理工具 > 数据规范") == "点云数据处理工具"


def test_chapter_leaf_extractor_procedures_not_config_labels():
    chunk = {
        "chunk_id": "c1",
        "content": (
            "新建工程\n"
            "新建一个倾斜模型数据的编译处理工程。\n"
            "纹理格式：设置成果数据的纹理格式。\n"
            "工程路径：设置工程文件存储路径。\n"
        ),
        "metadata": {
            "source": "StampTools用户手册.docx",
            "doc_category": "StampTools",
            "section_path": "ObliqueModelBuilder > 工程设置",
        },
    }
    result = ChapterLeafExtractor().extract(chunk)
    assert result.entity("新建工程") is not None
    assert result.entity("新建工程").entity_type == "Procedure"
    assert result.has_relation("ObliqueModelBuilder", "has_procedure", "新建工程")
    # GUI labels stay in evidence chunks — not ConfigItem nodes.
    assert result.entity("纹理格式") is None
    assert result.entity("工程路径") is None
    assert not any(e.entity_type == "ConfigItem" for e in result.entities)


def test_chapter_leaf_extractor_formats_from_data_spec_table():
    chunk = {
        "chunk_id": "c2",
        "content": (
            "| 数据类型 | 数据格式 | 约束与限制 |\n"
            "| --- | --- | --- |\n"
            "| 点云数据 | Las（*.las） | 激光点云文件 |\n"
            "| 高斯泼溅数据 | Ply（*.ply） | 只支持标准ply |\n"
        ),
        "metadata": {
            "source": "StampTools用户手册.docx",
            "doc_category": "StampTools",
            "section_path": "点云数据处理工具 > 数据规范",
        },
    }
    result = ChapterLeafExtractor().extract(chunk)
    assert result.entity("Las") is not None
    assert result.entity("Las").entity_type == "Format"
    assert result.has_relation("点云数据处理工具", "supports_format", "Las")
    assert result.has_relation("点云数据处理工具", "supports_format", "Ply")
