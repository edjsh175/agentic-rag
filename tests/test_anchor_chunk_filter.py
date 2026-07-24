"""Unit tests for anchor-constrained chunk filter."""

from __future__ import annotations

from langchain_core.documents import Document

from rag_knowledge.services.anchor_chunk_filter import (
    filter_docs_by_backbone_anchor,
    resolve_product_line,
)
from rag_knowledge.services.backbone_guard import load_backbone_constraints


def _doc(*, source: str, section_path: str, doc_category: str = "", text: str = "x") -> Document:
    return Document(
        page_content=text,
        metadata={
            "source": source,
            "section_path": section_path,
            "doc_category": doc_category,
            "file_name": source.split("/")[-1],
        },
    )


def test_resolve_product_line_pipeline_builder():
    constraints = load_backbone_constraints()
    assert resolve_product_line("PipelineBuilder", constraints) == "StampGIS Tools"


def test_filter_keeps_pipeline_drops_server_interference():
    constraints = load_backbone_constraints()
    keep = _doc(
        source="watch_directory/upload/StampTools用户手册.docx",
        section_path="工具概述 > PipelineBuilder > 工程设置",
        doc_category="StampTools",
        text="PipelineBuilder 用法",
    )
    drop_server = _doc(
        source="watch_directory/upload/StampServer用户手册_Rocky9 .docx",
        section_path="运维管理配置 > 数据配置 > 工程配置",
        doc_category="StampServer",
        text="三个文件由管线工具配置产生",
    )
    drop_update = _doc(
        source="watch_directory/upload/2ca727efa70847b49f0f67528544d210.pdf",
        section_path="服务说明 > 管线更新服务",
        doc_category="StampServer",
        text="管线工具相关更新",
    )
    out = filter_docs_by_backbone_anchor(
        [drop_server, keep, drop_update],
        ["PipelineBuilder"],
        enabled=True,
        constraints=constraints,
    )
    assert out == [keep]


def test_filter_disabled_or_empty_canonical_passthrough():
    docs = [
        _doc(
            source="a.pdf",
            section_path="管线更新服务",
            doc_category="StampServer",
        )
    ]
    assert filter_docs_by_backbone_anchor(docs, ["PipelineBuilder"], enabled=False) is docs
    assert filter_docs_by_backbone_anchor(docs, [], enabled=True) is docs


def test_filter_empty_preferred_falls_back_when_all_foreign():
    constraints = load_backbone_constraints()
    only_foreign = [
        _doc(
            source="StampServer用户手册_Rocky9 .docx",
            section_path="运维管理配置 > 数据配置 > 工程配置",
            doc_category="StampServer",
            text="管线工具",
        )
    ]
    out = filter_docs_by_backbone_anchor(
        only_foreign,
        ["PipelineBuilder"],
        enabled=True,
        constraints=constraints,
    )
    # No preferred left and cleaned also empty → fallback to original.
    assert out == only_foreign
