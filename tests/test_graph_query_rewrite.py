"""Tests for graph-assisted query rewrite summary and wiring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from rag_knowledge.config import Config, GraphRetrievalConfig
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_query_rewrite import (
    GraphQueryRewriter,
    GraphRewriteSummary,
    build_medium_graph_summary,
    merge_graph_rewrite_queries,
)
from rag_knowledge.services.graph_retrieval import GraphContext, GraphExpander, LinkedEntity
from rag_knowledge.services.query_contextualizer import RetrievalQuery
from rag_knowledge.services.query_planner import RetrievalPlan
from rag_knowledge.services.rag import RagChain


@pytest.fixture
def rewrite_db(isolated_storage):
    isolated_storage(db_name="graph-query-rewrite.db")
    db = RelationalDB()
    pipeline = db.create_entity("PipelineBuilder", "Tool", doc_category="StampTools")
    product = db.create_entity("StampTools", "Product", doc_category="StampTools")
    sibling = db.create_entity("管线发布服务", "Service", doc_category="StampServer")
    section = db.create_entity(
        "StampTools手册 :: 工具概述 > PipelineBuilder",
        "Section",
        doc_category="StampTools",
    )
    procedure = db.create_entity("PipelineBuilder 使用流程", "Procedure", doc_category="StampTools")

    db.create_alias(pipeline, "管线发布工具", review_status="approved")
    db.create_relation(pipeline, product, "belongs_to", review_status="approved")
    db.create_relation(pipeline, sibling, "different_from", review_status="approved")
    db.create_relation(pipeline, procedure, "requires", review_status="approved")
    defined = db.create_relation(pipeline, section, "defined_in", review_status="approved")
    db.create_link(pipeline, "chunk-pipeline", evidence_text="PipelineBuilder")
    return db, pipeline, product, sibling, section, procedure, defined


def test_build_medium_graph_summary_includes_alias_edges_and_sections(rewrite_db):
    db, pipeline, product, sibling, section, procedure, _defined = rewrite_db
    linked = LinkedEntity(
        pipeline,
        "PipelineBuilder",
        "Tool",
        0.95,
        "alias_exact",
        (sibling,),
    )
    context = GraphExpander(db).expand((linked,), "procedure")
    summary = build_medium_graph_summary(context, db)

    assert summary.linked
    assert summary.linked[0]["name"] == "PipelineBuilder"
    assert "管线发布工具" in summary.linked[0]["aliases"]
    assert "管线发布服务" in summary.avoid
    edge_types = {edge["relation_type"] for edge in summary.edges}
    assert "requires" in edge_types or "belongs_to" in edge_types
    assert any("PipelineBuilder" in path for path in summary.section_paths)
    assert "evidence_text" not in summary.to_dict()


def test_build_medium_graph_summary_respects_caps(rewrite_db):
    db, pipeline, *_rest = rewrite_db
    linked = LinkedEntity(pipeline, "PipelineBuilder", "Tool", 0.9, "exact", ())
    # Fabricate a context with many fake section paths via edges listing
    context = GraphContext(
        linked_entities=(linked,),
        relation_ids=(),
        expanded_entity_ids=(pipeline,),
    )
    summary = build_medium_graph_summary(context, db)
    assert len(summary.linked) <= 4
    assert len(summary.avoid) <= 8
    assert len(summary.edges) <= 12
    assert len(summary.section_paths) <= 6


def test_graph_query_rewriter_parses_llm_json(rewrite_db):
    db, pipeline, _product, sibling, _section, _procedure, _defined = rewrite_db
    linked = LinkedEntity(pipeline, "PipelineBuilder", "Tool", 0.95, "exact", (sibling,))
    context = GraphContext(linked_entities=(linked,), expanded_entity_ids=(pipeline,))
    rewriter = GraphQueryRewriter(Config(), db)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "message": {
            "content": '{"queries":["PipelineBuilder 使用流程","PipelineBuilder 工程设置"]}'
        }
    }
    with patch("rag_knowledge.services.graph_query_rewrite.httpx.post", return_value=mock_resp):
        specs = rewriter.rewrite("管线发布工具怎么用？", context)

    assert specs
    assert all(spec.kind == "graph_rewrite" for spec in specs)
    assert all(spec.weight == 0.7 for spec in specs)
    assert any("PipelineBuilder" in spec.text for spec in specs)


def test_graph_query_rewriter_falls_back_on_llm_failure(rewrite_db):
    db, pipeline, _product, sibling, *_rest = rewrite_db
    linked = LinkedEntity(pipeline, "PipelineBuilder", "Tool", 0.95, "exact", (sibling,))
    context = GraphContext(linked_entities=(linked,), expanded_entity_ids=(pipeline,))
    summary = GraphRewriteSummary(
        linked=({"name": "PipelineBuilder", "aliases": ["管线发布工具"], "type": "Tool"},),
        avoid=("管线发布服务",),
        edges=({"src": "PipelineBuilder", "relation_type": "requires", "tgt": "PipelineBuilder 使用流程"},),
        section_paths=("工具概述 > PipelineBuilder",),
    )
    rewriter = GraphQueryRewriter(Config(), db)
    with patch(
        "rag_knowledge.services.graph_query_rewrite.httpx.post",
        side_effect=RuntimeError("ollama down"),
    ):
        specs = rewriter.rewrite("怎么用？", context, summary=summary)

    assert specs
    assert all(spec.kind == "graph_rewrite" for spec in specs)


def test_merge_graph_rewrite_queries_dedupes():
    base = [RetrievalQuery("原问题", "original", 1.0)]
    rewrite = [
        RetrievalQuery("原问题", "graph_rewrite", 0.7),
        RetrievalQuery("PipelineBuilder 发布", "graph_rewrite", 0.7),
    ]
    merged = merge_graph_rewrite_queries(base, rewrite)
    assert len(merged) == 2
    assert merged[0].kind == "original"
    assert merged[1].text == "PipelineBuilder 发布"


def test_prepare_graph_plan_skips_rewrite_when_flag_off(rewrite_db):
    db, pipeline, _product, sibling, *_rest = rewrite_db
    linked = LinkedEntity(pipeline, "PipelineBuilder", "Tool", 0.95, "exact", (sibling,))
    context = GraphContext(
        linked_entities=(linked,),
        expanded_entity_ids=(pipeline,),
        chunk_ids=("c1",),
        retrieval_queries=("PipelineBuilder",),
    )
    graph_doc = Document(page_content="x", metadata={"chunk_id": "c1"})
    chain = object.__new__(RagChain)
    chain._graph_retriever = MagicMock()
    chain._graph_retriever.retrieve.return_value = (context, [graph_doc])
    chain._graph_retriever.revision.return_value = "rev"
    chain._graph_cfg = GraphRetrievalConfig(enabled=True, query_rewrite_enabled=False, graph_weight=1.25)
    chain._intent_resolver = MagicMock()
    chain._intent_resolver.resolve.return_value = MagicMock()
    chain._intent_resolver.refine_from_graph.return_value = MagicMock()

    plan = RetrievalPlan(
        "procedure",
        [RetrievalQuery("管线发布工具怎么用", "original", 1.0)],
        8,
        24,
        True,
        True,
        0.9,
    )
    with patch("rag_knowledge.services.graph_query_rewrite.GraphQueryRewriter") as rewriter_cls:
        enriched, returned, docs = chain._prepare_graph_plan("管线发布工具怎么用", plan)

    rewriter_cls.assert_not_called()
    assert enriched.queries[0].kind == "original"
    assert len(enriched.queries) == 1
    assert docs == [graph_doc]
    assert returned is context


def test_prepare_graph_plan_merges_rewrite_when_both_enabled(rewrite_db):
    db, pipeline, _product, sibling, *_rest = rewrite_db
    linked = LinkedEntity(pipeline, "PipelineBuilder", "Tool", 0.95, "exact", (sibling,))
    context = GraphContext(
        linked_entities=(linked,),
        expanded_entity_ids=(pipeline,),
        chunk_ids=("c1",),
        retrieval_queries=("PipelineBuilder",),
    )
    graph_doc = Document(page_content="x", metadata={"chunk_id": "c1"})
    chain = object.__new__(RagChain)
    chain._graph_retriever = MagicMock()
    chain._graph_retriever.retrieve.return_value = (context, [graph_doc])
    chain._graph_retriever.revision.return_value = "rev"
    chain._graph_cfg = GraphRetrievalConfig(enabled=True, query_rewrite_enabled=True, graph_weight=1.25)
    chain._intent_resolver = MagicMock()
    chain._intent_resolver.resolve.return_value = MagicMock()
    chain._intent_resolver.refine_from_graph.return_value = MagicMock()
    chain._graph_query_rewriter = MagicMock()
    chain._graph_query_rewriter.rewrite.return_value = [
        RetrievalQuery("PipelineBuilder 使用流程", "graph_rewrite", 0.7)
    ]

    plan = RetrievalPlan(
        "procedure",
        [RetrievalQuery("管线发布工具怎么用", "original", 1.0)],
        8,
        24,
        True,
        True,
        0.9,
    )
    enriched, _returned, docs = chain._prepare_graph_plan("管线发布工具怎么用", plan)

    assert any(q.kind == "graph_rewrite" for q in enriched.queries)
    assert enriched.queries[0].kind == "original"
    assert docs == [graph_doc]
    chain._graph_query_rewriter.rewrite.assert_called_once()


def test_prepare_graph_plan_disabled_without_retriever():
    chain = object.__new__(RagChain)
    chain._graph_retriever = None
    plan = RetrievalPlan("definition", [RetrievalQuery("q", "original", 1.0)], 4, 12, False, False, 0.5)
    enriched, context, docs = chain._prepare_graph_plan("q", plan)
    assert enriched is plan
    assert context is None
    assert docs == []
