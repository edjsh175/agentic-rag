# -*- coding: utf-8 -*-
"""Tests for product backbone guard (canonical resolve + conflict rules)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_knowledge.services.backbone_guard import (
    CONFLICT_REASON,
    alias_conflicts_with_backbone,
    entity_type_conflicts_with_backbone,
    format_backbone_context,
    load_backbone_constraints,
    relation_conflicts_with_backbone,
    resolve_canonical,
)
from rag_knowledge.services.graph_extraction import GraphBuilder
from rag_knowledge.services.graph_extraction.llm_extractor import LLMGraphExtractor
from tests.test_graph_extraction import chunk, make_db


def _constraints() -> dict:
    return {
        "belongs_to": {
            "PipelineBuilder": {"StampGIS Tools"},
            "StampGIS Tools": {"StampGIS三维产品"},
        },
        "different_from": {frozenset({"管线点表", "管线线表"})},
        "requires": {("三维服务层", "数据存储层")},
        "relations": [
            {
                "source": "PipelineBuilder",
                "relation_type": "belongs_to",
                "target": "StampGIS Tools",
            },
            {
                "source": "管线点表",
                "relation_type": "different_from",
                "target": "管线线表",
            },
            {
                "source": "三维服务层",
                "relation_type": "requires",
                "target": "数据存储层",
            },
        ],
        "canonical_by_alias": {
            "PipelineBuilder": "PipelineBuilder",
            "StampGIS Tools": "StampGIS Tools",
            "StampTools": "StampGIS Tools",
            "StampGIS Server": "StampGIS Server",
            "StampServer": "StampGIS Server",
            "管线点表": "管线点表",
            "管线线表": "管线线表",
            "三维服务层": "三维服务层",
            "数据存储层": "数据存储层",
            "StampGIS三维产品": "StampGIS三维产品",
        },
        "entity_type_by_name": {
            "PipelineBuilder": "Tool",
            "StampGIS Tools": "Product",
            "StampGIS Server": "Product",
            "管线点表": "DataTable",
            "管线线表": "DataTable",
        },
        "doc_categories": {"StampTools", "StampServer"},
    }


def test_resolve_canonical_uses_aliases():
    constraints = _constraints()
    assert resolve_canonical("StampTools", constraints) == "StampGIS Tools"
    assert resolve_canonical("StampServer", constraints) == "StampGIS Server"
    assert resolve_canonical("Unknown", constraints) == "Unknown"


def test_belongs_to_reparent_conflicts():
    constraints = _constraints()
    assert relation_conflicts_with_backbone(
        {
            "source_name": "PipelineBuilder",
            "relation_type": "belongs_to",
            "target_name": "StampGIS Server",
        },
        constraints,
    )
    assert not relation_conflicts_with_backbone(
        {
            "source_name": "PipelineBuilder",
            "relation_type": "belongs_to",
            "target_name": "StampGIS Tools",
        },
        constraints,
    )


def test_belongs_to_conflict_via_alias_canonicalization():
    constraints = _constraints()
    # Official parent is StampGIS Tools; alias StampTools should count as allowed.
    assert not relation_conflicts_with_backbone(
        {
            "source_name": "PipelineBuilder",
            "relation_type": "belongs_to",
            "target_name": "StampTools",
        },
        constraints,
    )
    # Wrong parent written as StampServer alias of StampGIS Server → still conflict.
    assert relation_conflicts_with_backbone(
        {
            "source_name": "PipelineBuilder",
            "relation_type": "belongs_to",
            "target_name": "StampServer",
        },
        constraints,
    )


def test_alias_of_conflicts_different_from():
    constraints = _constraints()
    assert relation_conflicts_with_backbone(
        {
            "source_name": "管线点表",
            "relation_type": "alias_of",
            "target_name": "管线线表",
        },
        constraints,
    )
    assert alias_conflicts_with_backbone(
        {"entity_name": "管线点表", "alias": "管线线表"},
        constraints,
    )
    assert not relation_conflicts_with_backbone(
        {
            "source_name": "管线点表",
            "relation_type": "different_from",
            "target_name": "管线线表",
        },
        constraints,
    )


def test_entity_type_conflict():
    constraints = _constraints()
    assert entity_type_conflicts_with_backbone(
        {"name": "PipelineBuilder", "entity_type": "Procedure"},
        constraints,
    )
    assert not entity_type_conflicts_with_backbone(
        {"name": "PipelineBuilder", "entity_type": "Tool"},
        constraints,
    )


def test_official_requires_restatement_ok():
    constraints = _constraints()
    assert not relation_conflicts_with_backbone(
        {
            "source_name": "三维服务层",
            "relation_type": "requires",
            "target_name": "数据存储层",
        },
        constraints,
    )


def test_format_backbone_context_includes_edges_and_truncates(tmp_path: Path):
    backbone = {
        "schema_version": 1,
        "entities": [
            {
                "name": "StampGIS Server",
                "entity_type": "Product",
                "aliases": ["StampServer"],
                "doc_category": "StampServer",
            },
            {
                "name": "PipelineBuilder",
                "entity_type": "Tool",
                "aliases": [],
                "doc_category": "StampTools",
            },
        ],
        "relations": [
            {
                "source": "PipelineBuilder",
                "relation_type": "belongs_to",
                "target": "StampGIS Server",
            }
        ],
    }
    path = tmp_path / "product_relation_backbone.json"
    path.write_text(json.dumps(backbone, ensure_ascii=False), encoding="utf-8")
    constraints = load_backbone_constraints(path)
    text = format_backbone_context(constraints, max_chars=5000)
    assert "StampGIS Server" in text
    assert "PipelineBuilder" in text
    assert "belongs_to" in text
    assert "StampServer" in text
    empty = format_backbone_context(
        {
            "belongs_to": {},
            "different_from": set(),
            "requires": set(),
            "relations": [],
            "canonical_by_alias": {},
            "entity_type_by_name": {},
            "doc_categories": set(),
        }
    )
    assert empty == "(none)"


def test_pipeline_rejects_conflicting_belongs_to(isolated_storage, monkeypatch):
    db = make_db(
        isolated_storage,
        name="backbone-guard.db",
        data_dir_name="backbone-guard-data",
        chroma_name="backbone-guard-chroma",
    )
    constraints = _constraints()
    # Force section-path rule edge PipelineBuilder -> StampTools to conflict.
    constraints["belongs_to"]["PipelineBuilder"] = {"OtherProduct"}
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.load_backbone_constraints",
        lambda path=None: constraints,
    )
    chunks = [
        chunk(
            chunk_id="c1",
            content="PipelineBuilder 工程设置说明。",
            source="StampTools用户手册.docx",
            doc_category="StampTools",
            section_path="PipelineBuilder > 工程设置",
        )
    ]
    result = GraphBuilder(db=db, chunk_source=lambda: chunks).build_full(force_rebuild=True)
    relations = [
        item
        for item in db.list_extraction_candidates(result.batch_id)
        if item["candidate_kind"] == "relation"
        and item["payload"].get("source_name") == "PipelineBuilder"
        and item["payload"].get("relation_type") == "belongs_to"
    ]
    assert relations, "expected belongs_to candidates from section extractor"
    conflicting = [
        item
        for item in relations
        if item["payload"].get("target_name") == "StampTools" and item["status"] == "rejected"
    ]
    assert conflicting
    assert all(
        (item.get("review_reason") or item.get("reason") or CONFLICT_REASON)
        for item in conflicting
    )
    diagnostics = [
        item
        for item in db.list_extraction_candidates(result.batch_id)
        if item["candidate_kind"] == "diagnostic"
        and item["payload"].get("code") == CONFLICT_REASON
    ]
    assert diagnostics


def test_llm_prompt_includes_backbone_context(isolated_storage):
    isolated_storage()
    constraints = _constraints()
    extractor = LLMGraphExtractor(backbone_constraints=constraints)
    prompt = extractor.build_prompt(
        doc_category="StampTools",
        section_path="PipelineBuilder",
        content="PipelineBuilder 属于 StampTools。",
    )
    assert "Official product backbone" in prompt or "PipelineBuilder" in prompt
    assert "belongs_to" in prompt
    assert "do NOT rewrite official belongs_to" in prompt or "不得改写官方 belongs_to" in prompt
    assert "{backbone_context}" not in prompt


def test_chunk_in_backbone_neighborhood_by_category_and_term():
    from rag_knowledge.services.backbone_guard import chunk_in_backbone_neighborhood

    constraints = _constraints()
    assert chunk_in_backbone_neighborhood(
        {"content": "无关内容", "metadata": {"doc_category": "StampTools"}},
        constraints,
    )
    assert chunk_in_backbone_neighborhood(
        {"content": "介绍 PipelineBuilder 用法", "metadata": {"doc_category": "其他"}},
        constraints,
    )
    assert not chunk_in_backbone_neighborhood(
        {"content": "纯闲聊", "metadata": {"doc_category": "博客"}},
        constraints,
    )
    empty = {
        "belongs_to": {},
        "different_from": set(),
        "requires": set(),
        "relations": [],
        "canonical_by_alias": {},
        "entity_type_by_name": {},
        "doc_categories": set(),
    }
    assert chunk_in_backbone_neighborhood({"content": "x", "metadata": {}}, empty)


def test_assert_ollama_unreachable(monkeypatch):
    import httpx
    from rag_knowledge.services.ollama_health import OllamaUnreachableError, assert_ollama_reachable

    class _Boom:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *_args, **_kwargs):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _Boom())
    with pytest.raises(OllamaUnreachableError):
        assert_ollama_reachable(base_url="http://127.0.0.1:9", timeout=0.1)


def test_pipeline_skips_llm_outside_neighborhood(isolated_storage, monkeypatch):
    from unittest.mock import MagicMock

    db = make_db(
        isolated_storage,
        name="backbone-nb.db",
        data_dir_name="backbone-nb-data",
        chroma_name="backbone-nb-chroma",
    )
    constraints = _constraints()
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.load_backbone_constraints",
        lambda path=None: constraints,
    )
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.assert_ollama_reachable",
        lambda **kwargs: "http://test",
    )
    llm = MagicMock()
    llm.extract.return_value = __import__(
        "rag_knowledge.services.graph_extraction", fromlist=["ExtractionResult"]
    ).ExtractionResult()
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.llm_extractor.LLMGraphExtractor",
        lambda *args, **kwargs: llm,
    )
    chunks = [
        chunk(
            chunk_id="far",
            content="今天天气不错",
            source="blog.md",
            doc_category="博客",
            section_path="随笔",
        ),
        chunk(
            chunk_id="near",
            content="PipelineBuilder 工程设置",
            source="manual.docx",
            doc_category="StampTools",
            section_path="PipelineBuilder > 工程设置",
        ),
    ]
    result = GraphBuilder(db=db, chunk_source=lambda: chunks).build_full(
        force_rebuild=True, include_llm=True
    )
    assert result.stats["llm_chunks_skipped"] == 1
    assert result.stats["llm_chunks_considered"] == 1
    assert llm.extract.call_count == 1


def test_pipeline_llm_runs_for_explicit_doc_category_outside_neighborhood(isolated_storage, monkeypatch):
    """Explicit --doc-category filter enables LLM even outside backbone neighborhood."""
    from unittest.mock import MagicMock

    db = make_db(
        isolated_storage,
        name="backbone-cat.db",
        data_dir_name="backbone-cat-data",
        chroma_name="backbone-cat-chroma",
    )
    constraints = _constraints()
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.load_backbone_constraints",
        lambda path=None: constraints,
    )
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.assert_ollama_reachable",
        lambda **kwargs: "http://test",
    )
    llm = MagicMock()
    llm.extract.return_value = __import__(
        "rag_knowledge.services.graph_extraction", fromlist=["ExtractionResult"]
    ).ExtractionResult()
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.llm_extractor.LLMGraphExtractor",
        lambda *args, **kwargs: llm,
    )
    chunks = [
        chunk(
            chunk_id="webrtc-far",
            content="WebRTC 信令与媒体协商说明，不含命令行。",
            source="webrtc.md",
            doc_category="StampWebRTC",
            section_path="信令",
        ),
    ]
    result = GraphBuilder(db=db, chunk_source=lambda: chunks).build_full(
        force_rebuild=True,
        include_llm=True,
        doc_categories=["StampWebRTC"],
    )
    assert result.stats["llm_chunks_considered"] == 1
    assert result.stats["llm_chunks_category_scoped"] == 1
    assert result.stats["llm_chunks_skipped"] == 0
    assert llm.extract.call_count == 1


def test_pipeline_llm_runs_for_command_rich_outside_neighborhood(isolated_storage, monkeypatch):
    from unittest.mock import MagicMock

    db = make_db(
        isolated_storage,
        name="backbone-cmd.db",
        data_dir_name="backbone-cmd-data",
        chroma_name="backbone-cmd-chroma",
    )
    constraints = _constraints()
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.load_backbone_constraints",
        lambda path=None: constraints,
    )
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.assert_ollama_reachable",
        lambda **kwargs: "http://test",
    )
    llm = MagicMock()
    llm.extract.return_value = __import__(
        "rag_knowledge.services.graph_extraction", fromlist=["ExtractionResult"]
    ).ExtractionResult()
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.llm_extractor.LLMGraphExtractor",
        lambda *args, **kwargs: llm,
    )
    chunks = [
        chunk(
            chunk_id="cmd-far",
            content="安装完成后执行：\nsystemctl restart redis\n检查状态。",
            source="os.md",
            doc_category="基础环境",
            section_path="Redis安装",
        ),
    ]
    result = GraphBuilder(db=db, chunk_source=lambda: chunks).build_full(
        force_rebuild=True, include_llm=True
    )
    assert result.stats["llm_chunks_considered"] == 1
    assert result.stats["llm_chunks_command_rich"] == 1
    assert result.stats["llm_chunks_skipped"] == 0
    assert llm.extract.call_count == 1


def test_pipeline_include_llm_fails_when_ollama_down(isolated_storage, monkeypatch):
    from rag_knowledge.services.ollama_health import OllamaUnreachableError

    db = make_db(
        isolated_storage,
        name="backbone-ollama.db",
        data_dir_name="backbone-ollama-data",
        chroma_name="backbone-ollama-chroma",
    )
    monkeypatch.setattr(
        "rag_knowledge.services.graph_extraction.pipeline.assert_ollama_reachable",
        lambda **kwargs: (_ for _ in ()).throw(OllamaUnreachableError("down")),
    )
    chunks = [chunk(chunk_id="c1", content="x", doc_category="StampTools")]
    with pytest.raises(OllamaUnreachableError):
        GraphBuilder(db=db, chunk_source=lambda: chunks).build_full(include_llm=True)
