"""Tests for graph-assisted query rewrite summary and wiring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from rag_knowledge.config import Config, GraphRetrievalConfig
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.backbone_guard import (
    format_anchor_relation_summary,
    soft_match_backbone_entities,
)
from rag_knowledge.services.graph_query_rewrite import (
    BackboneAnchorResult,
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


def test_soft_match_backbone_stamp_manager():
    constraints = {
        "canonical_by_alias": {
            "StampManager": "StampManager",
            "StampGIS Tools": "StampGIS Tools",
            "StampTools": "StampGIS Tools",
        },
        "entity_type_by_name": {
            "StampManager": "Product",
            "StampGIS Tools": "Product",
        },
        "different_from": set(),
        "relations": [],
    }
    assert soft_match_backbone_entities("介绍一下 stamp manager", constraints) == ["StampManager"]
    assert soft_match_backbone_entities("StampTools 是什么", constraints) == ["StampGIS Tools"]


def test_soft_match_live_backbone_oral_pipeline_tool(monkeypatch):
    """A2: oral「管线工具」must soft-hit PipelineBuilder on live backbone JSON."""
    mock_data = {
        "belongs_to": {},
        "different_from": set(),
        "requires": set(),
        "relations": [],
        "canonical_by_alias": {
            "PipelineBuilder": "PipelineBuilder",
            "\u7ba1\u7ebf\u5de5\u5177": "PipelineBuilder",
            "\u7ba1\u7ebf\u53d1\u5e03\u670d\u52a1": "\u7ba1\u7ebf\u53d1\u5e03\u670d\u52a1"
        },
        "entity_type_by_name": {
            "PipelineBuilder": "Tool"
        },
        "doc_categories": set()
    }
    monkeypatch.setattr(
        "rag_knowledge.services.backbone_guard.load_backbone_constraints",
        lambda *args, **kwargs: mock_data
    )

    from rag_knowledge.services.backbone_guard import load_backbone_constraints

    constraints = load_backbone_constraints()
    hits = soft_match_backbone_entities("\u7ba1\u7ebf\u5de5\u5177\u662f\u4ec0\u4e48", constraints)
    assert "PipelineBuilder" in hits
    assert "PipelineWebGL" not in hits


def test_format_anchor_relation_summary_includes_belongs_to():
    constraints = {
        "canonical_by_alias": {
            "StampManager": "StampManager",
            "StampGIS三维产品": "StampGIS三维产品",
        },
        "entity_type_by_name": {
            "StampManager": "Product",
            "StampGIS三维产品": "Product",
        },
        "different_from": set(),
        "relations": [
            {
                "source": "StampManager",
                "relation_type": "belongs_to",
                "target": "StampGIS三维产品",
            }
        ],
    }
    text = format_anchor_relation_summary(["StampManager"], constraints)
    assert "StampManager" in text
    assert "belongs_to" in text
    assert "StampGIS三维产品" in text


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

    mock_resp = '{"queries":["PipelineBuilder \\u4f7f\\u7528\\u6d41\\u7a0b","PipelineBuilder \\u5de5\\u7a0b\\u8bbe\\u7f6e"]}'
    with patch("rag_knowledge.llm_http.chat_role", return_value=mock_resp):
        specs = rewriter.rewrite("管线发布工具怎么用？", context)

    assert specs
    assert all(spec.kind == "graph_rewrite" for spec in specs)
    assert all(spec.weight == 0.7 for spec in specs)
    assert any("PipelineBuilder" in spec.text for spec in specs)


def _json_payload_stamp_manager() -> str:
    return (
        '{"deconstruct":{"primary_intent":"product_intro","surface_terms":["stamp manager"]},'
        '"canonical_entities":["StampManager"],"avoid":["StampGIS Tools"],'
        '"anchored_queries":["StampManager 产品介绍","StampManager"],'
        '"relation_focus":["StampManager"]}'
    )


def test_backbone_anchor_from_llm_json(isolated_storage):
    isolated_storage()
    constraints = {
        "belongs_to": {},
        "different_from": set(),
        "requires": set(),
        "relations": [
            {
                "source": "StampManager",
                "relation_type": "belongs_to",
                "target": "StampGIS三维产品",
            }
        ],
        "canonical_by_alias": {
            "StampManager": "StampManager",
            "StampGIS三维产品": "StampGIS三维产品",
            "StampGIS Tools": "StampGIS Tools",
        },
        "entity_type_by_name": {
            "StampManager": "Product",
            "StampGIS三维产品": "Product",
            "StampGIS Tools": "Product",
        },
        "doc_categories": set(),
    }
    rewriter = GraphQueryRewriter(Config())
    mock_resp = _json_payload_stamp_manager()
    with patch("rag_knowledge.llm_http.chat_role", return_value=mock_resp):
        result = rewriter.anchor_from_backbone("stamp manager 是什么", constraints=constraints)

    assert result.canonical_entities == ("StampManager",)
    assert any("StampManager" in q for q in result.anchored_queries)
    assert "StampManager" in result.relation_summary
    assert result.retrieval_queries
    assert all(q.kind == "graph_rewrite" for q in result.retrieval_queries)
    assert all(q.weight == 1.1 for q in result.retrieval_queries)


def test_backbone_anchor_heuristic_without_llm(isolated_storage):
    isolated_storage()
    constraints = {
        "belongs_to": {},
        "different_from": {frozenset({"StampManager", "StampGIS Tools"})},
        "requires": set(),
        "relations": [],
        "canonical_by_alias": {
            "StampManager": "StampManager",
            "StampGIS Tools": "StampGIS Tools",
        },
        "entity_type_by_name": {
            "StampManager": "Product",
            "StampGIS Tools": "Product",
        },
        "doc_categories": set(),
    }
    rewriter = GraphQueryRewriter(Config())
    with patch(
        "rag_knowledge.llm_http.chat_role",
        side_effect=RuntimeError("ollama down"),
    ):
        result = rewriter.anchor_from_backbone("介绍 StampManager", constraints=constraints)
    assert result.canonical_entities == ("StampManager",)
    assert "StampGIS Tools" in result.avoid
    assert any("StampManager" in q for q in result.anchored_queries)


def test_soft_hits_override_wrong_llm_canonical(isolated_storage):
    isolated_storage()
    constraints = {
        "belongs_to": {},
        "different_from": set(),
        "requires": set(),
        "relations": [],
        "canonical_by_alias": {
            "PipelineBuilder": "PipelineBuilder",
            "PipelineWebGL": "PipelineWebGL",
            "PiplineBuilder": "PipelineBuilder",
        },
        "entity_type_by_name": {
            "PipelineBuilder": "Tool",
            "PipelineWebGL": "Tool",
        },
        "doc_categories": set(),
    }
    rewriter = GraphQueryRewriter(Config())
    mock_resp = (
        '{"deconstruct":{"primary_intent":"product_relation","surface_terms":["PipelineBuilder"]},'
        '"canonical_entities":["PipelineWebGL"],"avoid":[],'
        '"anchored_queries":["PipelineWebGL \\u4ea7\\u54c1\\u4ecb\\u7ecd"],'
        '"relation_focus":["PipelineWebGL"]}'
    )
    with patch("rag_knowledge.llm_http.chat_role", return_value=mock_resp):
        result = rewriter.anchor_from_backbone(
            "PipelineBuilder 属于哪个产品",
            constraints=constraints,
        )
    assert result.canonical_entities == ("PipelineBuilder",)
    assert "PipelineWebGL" not in result.canonical_entities
    assert any("PipelineBuilder" in q for q in result.anchored_queries)
    assert all("PipelineWebGL" not in q for q in result.anchored_queries)


def test_anchor_fills_query_when_llm_repeats_question(isolated_storage):
    isolated_storage()
    constraints = {
        "belongs_to": {},
        "different_from": set(),
        "requires": set(),
        "relations": [],
        "canonical_by_alias": {"StampManager": "StampManager"},
        "entity_type_by_name": {"StampManager": "Product"},
        "doc_categories": set(),
    }
    rewriter = GraphQueryRewriter(Config())
    mock_resp = (
        '{"deconstruct":{"primary_intent":"product_intro","surface_terms":["StampManager"]},'
        '"canonical_entities":["StampManager"],"avoid":[],'
        '"anchored_queries":["\\u4ecb\\u7ecd\\u4e00\\u4e0b StampManager"],'
        '"relation_focus":[]}'
    )
    with patch("rag_knowledge.llm_http.chat_role", return_value=mock_resp):
        result = rewriter.anchor_from_backbone("介绍一下 StampManager", constraints=constraints)
    assert result.canonical_entities == ("StampManager",)
    assert result.anchored_queries
    assert all(q != "介绍一下 StampManager" for q in result.anchored_queries)


def test_comparison_alias_queries_not_collapsed(isolated_storage):
    isolated_storage()
    constraints = {
        "belongs_to": {},
        "different_from": set(),
        "requires": set(),
        "relations": [],
        "canonical_by_alias": {
            "StampTools": "StampGIS Tools",
            "StampGIS Tools": "StampGIS Tools",
            "StampServer": "StampGIS Server",
            "StampGIS Server": "StampGIS Server",
        },
        "entity_type_by_name": {
            "StampGIS Tools": "Product",
            "StampGIS Server": "Product",
        },
        "doc_categories": set(),
    }
    rewriter = GraphQueryRewriter(Config())
    with patch(
        "rag_knowledge.llm_http.chat_role",
        side_effect=RuntimeError("down"),
    ):
        result = rewriter.anchor_from_backbone(
            "StampTools 和 StampServer 有什么区别",
            constraints=constraints,
        )
    assert set(result.canonical_entities) == {"StampGIS Tools", "StampGIS Server"}
    assert result.anchored_queries, "expected non-empty anchored queries for comparison"
    assert all(q != "StampTools 和 StampServer 有什么区别" for q in result.anchored_queries)


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
        "rag_knowledge.llm_http.chat_role",
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


def test_prepare_graph_plan_merges_backbone_anchor_when_enabled(rewrite_db):
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
    chain._graph_query_rewriter.anchor_from_backbone.return_value = BackboneAnchorResult(
        primary_intent="product_intro",
        canonical_entities=("StampManager",),
        avoid=("StampGIS Tools",),
        anchored_queries=("StampManager 产品介绍",),
        relation_summary="锚点：StampManager",
        retrieval_queries=(
            RetrievalQuery("StampManager 产品介绍", "graph_rewrite", 1.1),
        ),
    )

    plan = RetrievalPlan(
        "definition",
        [RetrievalQuery("stamp manager 是什么", "original", 1.0)],
        8,
        24,
        True,
        True,
        0.9,
    )
    enriched, _returned, docs = chain._prepare_graph_plan("stamp manager 是什么", plan)

    assert any(q.kind == "graph_rewrite" and "StampManager" in q.text for q in enriched.queries)
    assert enriched.queries[0].kind == "original"
    assert enriched.backbone_canonical == ("StampManager",)
    assert "StampManager" in (enriched.backbone_relation_summary or "")
    assert docs == [graph_doc]
    chain._graph_query_rewriter.rewrite.assert_not_called()
    call_kwargs = chain._graph_retriever.retrieve.call_args
    assert call_kwargs is not None
    passed_queries = call_kwargs.kwargs.get("queries") or (
        call_kwargs.args[2] if len(call_kwargs.args) > 2 else None
    )
    assert passed_queries is not None
    assert any("StampManager" in getattr(q, "text", str(q)) for q in passed_queries)


def test_build_messages_injects_backbone_relation_summary():
    msgs = RagChain._build_messages(
        "StampManager 属于谁？",
        "[1] StampManager 是管理端产品。",
        backbone_canonical=("StampManager",),
        backbone_avoid=("StampGIS Tools",),
        backbone_relation_summary="锚点：StampManager\n- StampManager -[belongs_to]-> StampGIS三维产品",
    )
    system = msgs[0]["content"]
    assert "产品主干锚定" in system
    assert "StampManager" in system
    assert "belongs_to" in system


def test_prepare_graph_plan_disabled_without_retriever():
    chain = object.__new__(RagChain)
    chain._graph_retriever = None
    chain._graph_cfg = GraphRetrievalConfig(enabled=False, query_rewrite_enabled=False)
    plan = RetrievalPlan("definition", [RetrievalQuery("q", "original", 1.0)], 4, 12, False, False, 0.5)
    enriched, context, docs = chain._prepare_graph_plan("q", plan)
    assert enriched is plan
    assert context is None
    assert docs == []


def test_prepare_graph_plan_backbone_rewrite_without_graph_retriever():
    chain = object.__new__(RagChain)
    chain._graph_retriever = None
    chain._graph_cfg = GraphRetrievalConfig(enabled=False, query_rewrite_enabled=True)
    chain._graph_query_rewriter = MagicMock()
    chain._graph_query_rewriter.anchor_from_backbone.return_value = BackboneAnchorResult(
        canonical_entities=("StampManager",),
        retrieval_queries=(RetrievalQuery("StampManager 产品介绍", "graph_rewrite", 1.1),),
        relation_summary="锚点：StampManager",
    )
    plan = RetrievalPlan(
        "definition",
        [RetrievalQuery("stamp manager", "original", 1.0)],
        4,
        12,
        False,
        False,
        0.5,
    )
    enriched, context, docs = chain._prepare_graph_plan("stamp manager", plan)
    assert context is None
    assert docs == []
    assert enriched.backbone_canonical == ("StampManager",)
    assert any("StampManager" in q.text for q in enriched.queries)
