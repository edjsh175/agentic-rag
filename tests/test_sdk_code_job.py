"""Phase 0 gates for J3 SDK code job (PRD V1.3)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from rag_knowledge.services.query_clarification import QueryClarificationService
from rag_knowledge.services.query_contextualizer import RetrievalQuery
from rag_knowledge.services.query_planner import QueryPlanner, RetrievalPlan
from rag_knowledge.services.sdk_code_job import (
    COM_SENTINEL,
    EXPLORER_OPS_SENTINEL,
    J3_PRIMARY_INTENT,
    build_j3_retrieval_texts,
    drop_pipeline_graph_rewrites,
    named_j3_product,
    resolve_job,
    should_skip_backbone_guess,
    strip_j2_stage_queries,
)


@pytest.fixture(autouse=True)
def _isolated(isolated_storage):
    isolated_storage(
        db_name="sdk-job.db",
        chroma_name="sdk-job-chroma",
        data_dir_name="sdk-job-data",
    )


class TestResolveJob:
    def test_line_style_code_needs_clarify(self):
        d = resolve_job("写一段创建折线的代码，线颜色设为红色、线宽为 3。")
        assert d.job == "j3"
        assert d.needs_j3_clarify is True
        assert should_skip_backbone_guess(d) is True

    def test_style_only_not_j3(self):
        d = resolve_job("创建多边形并设置填充色")
        assert d.job == "other"
        assert d.needs_j3_clarify is False

    def test_pipeline_builder_stays_j2(self):
        d = resolve_job("PipelineBuilder 如何新建工程")
        assert d.job == "j2"
        assert d.needs_j3_clarify is False

    def test_selected_webrtc_is_clear_j3(self):
        d = resolve_job(
            "写一段创建折线的代码，线颜色设为红色、线宽为 3。",
            entity_name="StampWebRTC",
        )
        assert d.job == "j3"
        assert d.subject_clear is True
        assert d.needs_j3_clarify is False

    def test_stamputil_still_needs_product_clarify(self):
        d = resolve_job("Vue3 如何引入 StampUtil 并初始化")
        assert d.job == "j3"
        assert d.needs_j3_clarify is True

    def test_explorer_sentinel(self):
        d = resolve_job("写一段创建折线的代码", entity_name=EXPLORER_OPS_SENTINEL)
        assert d.job == "j2"
        assert d.canonical_hint is None


class TestClarifyD8:
    def test_j3_unclear_returns_subgraph_options(self):
        from rag_knowledge.services.backbone_guard import load_backbone_constraints
        from rag_knowledge.services.sdk_code_job import (
            OPTION_SOURCE_BACKBONE,
            OPTION_SOURCE_ROLLBACK,
            OPTION_SOURCE_TASK_EXIT,
        )

        svc = QueryClarificationService(
            enabled=True,
            llm_enabled=False,
            constraints=load_backbone_constraints(),
        )
        result = svc.analyze("写一段创建折线的代码，线颜色设为红色、线宽为 3。")
        assert result.needs_clarification is True
        assert result.reason == "j3_subject_unclear"
        entities = [o.filter.entity_name for o in result.options]
        assert entities[:2] == ["StampWebRTC", "StampWebGL"]
        assert "SDK" not in entities
        assert "二次开发与集成层" not in entities
        assert "PipelineWebGL" not in entities
        sources = {o.source for o in result.options}
        assert OPTION_SOURCE_ROLLBACK not in sources
        assert OPTION_SOURCE_BACKBONE in sources
        assert OPTION_SOURCE_TASK_EXIT in sources

    def test_stamputil_vue_still_clarifies_from_subgraph(self):
        from rag_knowledge.services.backbone_guard import load_backbone_constraints
        from rag_knowledge.services.sdk_code_job import OPTION_SOURCE_ROLLBACK

        svc = QueryClarificationService(
            enabled=True,
            llm_enabled=False,
            constraints=load_backbone_constraints(),
        )
        result = svc.analyze("Vue3 如何引入 StampUtil 并初始化")
        assert result.needs_clarification is True
        entities = {o.filter.entity_name for o in result.options}
        assert "StampWebRTC" in entities and "StampWebGL" in entities
        assert not any(str(e or "").startswith("Pipeline") for e in entities)
        assert OPTION_SOURCE_ROLLBACK not in {o.source for o in result.options}

    def test_pipeline_wide_term_not_j3_card(self):
        from rag_knowledge.services.backbone_guard import load_backbone_constraints

        svc = QueryClarificationService(
            enabled=True,
            llm_enabled=False,
            constraints=load_backbone_constraints(),
        )
        result = svc.analyze("管线怎么新建工程")
        # Must not be the J3 sdk card (G0-C2-neg / G0-5)
        assert result.reason != "j3_subject_unclear"
        entities = {o.filter.entity_name for o in result.options}
        assert "StampWebRTC" not in entities or result.reason != "j3_subject_unclear"

    def test_sdk_aux_selection_reasks(self):
        from rag_knowledge.services.backbone_guard import load_backbone_constraints

        svc = QueryClarificationService(
            enabled=True,
            llm_enabled=False,
            constraints=load_backbone_constraints(),
        )
        result = svc.analyze(
            "写一段创建折线的代码，线颜色设为红色、线宽为 3。",
            entity_name="SDK",
        )
        assert result.needs_clarification is True
        assert result.reason == "j3_subject_unclear"


class TestPlannerNoJ2Stage:
    def test_j3_question_skips_procedure_stages(self):
        planner = QueryPlanner()
        planner._planner_cfg.enabled = True
        planner._cfg.reranker_enabled = False
        planner._classify_via_llm = MagicMock(return_value=("procedure", 0.95))

        plan = planner.plan(
            "写一段创建折线的代码，线颜色设为红色、线宽为 3。",
            [RetrievalQuery("写一段创建折线的代码，线颜色设为红色、线宽为 3。", "original", 1.0)],
        )
        assert plan.job == "j3"
        kinds = [q.kind for q in plan.queries]
        assert "planner_stage" not in kinds
        blob = " ".join(q.text for q in plan.queries)
        assert "工程设置" not in blob
        assert "数据编译" not in blob


class TestRewriteHelpers:
    def test_strip_and_drop_pipeline_rewrite(self):
        queries = [
            RetrievalQuery("原问", "original", 1.0),
            RetrievalQuery("原问 工程设置", "planner_stage", 0.45),
            RetrievalQuery("PipelineWebGL 创建折线", "graph_rewrite", 1.1),
            RetrievalQuery("StampWebRTC StampUtil", "graph_rewrite", 1.1),
        ]
        stripped = strip_j2_stage_queries(queries)
        kept, policy = drop_pipeline_graph_rewrites(stripped)
        assert all(q.kind != "planner_stage" for q in kept)
        assert not any("PipelineWebGL" in q.text for q in kept)
        assert any("StampWebRTC" in q.text for q in kept)
        assert policy == "drop"

    def test_j3_force_texts_no_intro(self):
        texts = build_j3_retrieval_texts(
            "写一段创建折线的代码，线颜色设为红色、线宽为 3。",
            "StampWebRTC",
        )
        blob = " ".join(texts)
        assert "介绍" not in blob
        assert "工程设置" not in blob
        assert "StampUtil" in blob
        assert "StampWebRTC" in blob


class TestPhase1Lexicon:
    def test_soft_match_webrtc_interface_alias(self):
        from rag_knowledge.services.backbone_guard import (
            load_backbone_constraints,
            soft_match_backbone_entities,
            avoid_names_for_anchors,
        )

        constraints = load_backbone_constraints()
        hits = soft_match_backbone_entities("WebRTC接口说明书里怎么创建折线", constraints)
        assert "StampWebRTC" in hits
        assert "PipelineWebGL" not in hits

    def test_different_from_webgl_pair(self):
        from rag_knowledge.services.backbone_guard import (
            load_backbone_constraints,
            avoid_names_for_anchors,
        )

        constraints = load_backbone_constraints()
        avoid = avoid_names_for_anchors(["StampWebGL"], constraints)
        assert "PipelineWebGL" in avoid
        avoid_rtc = avoid_names_for_anchors(["StampWebRTC"], constraints)
        assert "PipelineWebRTC" in avoid_rtc

    def test_named_alias_webrtc_interface(self):
        assert named_j3_product("查一下 WebRTC 接口怎么引入") == "StampWebRTC"
        assert named_j3_product("StampGIS平台WebGL 二次开发") == "StampWebGL"

    def test_product_intro_not_forced_j3(self):
        d = resolve_job("StampWebRTC 是什么")
        assert d.job == "other"
        assert d.needs_j3_clarify is False

    def test_named_product_with_action_forces_j3_template(self):
        from rag_knowledge.services.rag import RagChain
        from rag_knowledge.services.query_planner import RetrievalPlan

        chain = object.__new__(RagChain)
        chain._graph_cfg = MagicMock(query_rewrite_enabled=True)
        plan = RetrievalPlan(
            intent="procedure",
            queries=[RetrievalQuery("StampWebRTC 写一段创建折线的代码", "original", 1.0)],
            top_k=8,
            candidate_k=24,
            enable_rerank=False,
            expand_neighbors=False,
            confidence=0.9,
        )
        out = RagChain._apply_backbone_anchor_rewrite(
            chain,
            "StampWebRTC 写一段创建折线的代码，线颜色红色",
            plan,
            entity_name=None,
        )
        assert out.backbone_canonical == ("StampWebRTC",)
        assert out.backbone_primary_intent == J3_PRIMARY_INTENT
        assert out.rewrite_template == "j3"
        blob = " ".join(q.text for q in out.queries)
        assert "StampUtil" in blob
        assert "介绍" not in blob
        assert "Pipeline" not in blob


class TestForceBackboneJ3:
    def test_force_stampwebrtc_uses_sdk_code(self):
        from rag_knowledge.services.rag import RagChain

        chain = object.__new__(RagChain)
        plan = RetrievalPlan(
            intent="procedure",
            queries=[RetrievalQuery("写一段创建折线的代码", "original", 1.0)],
            top_k=8,
            candidate_k=24,
            enable_rerank=False,
            expand_neighbors=False,
            confidence=0.9,
            job="j3",
        )
        # Avoid loading real backbone file side effects: force path resolves name as-is.
        out = RagChain._force_backbone_entity(
            chain,
            "写一段创建折线的代码，线颜色设为红色、线宽为 3。",
            plan,
            "StampWebRTC",
        )
        assert out.backbone_canonical == ("StampWebRTC",)
        assert out.backbone_primary_intent == J3_PRIMARY_INTENT
        assert out.rewrite_template == "j3"
        blob = " ".join(q.text for q in out.queries)
        assert "介绍" not in blob
        assert "StampUtil" in blob

    def test_skip_guess_when_j3_unclear(self):
        from rag_knowledge.services.rag import RagChain

        chain = object.__new__(RagChain)
        chain._graph_cfg = MagicMock(query_rewrite_enabled=True)
        plan = RetrievalPlan(
            intent="procedure",
            queries=[
                RetrievalQuery("写一段创建折线的代码", "original", 1.0),
                RetrievalQuery("写一段创建折线的代码 工程设置", "planner_stage", 0.45),
            ],
            top_k=8,
            candidate_k=24,
            enable_rerank=False,
            expand_neighbors=False,
            confidence=0.9,
            job="j3",
        )
        out = RagChain._apply_backbone_anchor_rewrite(
            chain,
            "写一段创建折线的代码，线颜色设为红色、线宽为 3。",
            plan,
            entity_name=None,
        )
        assert out.backbone_canonical == ()
        assert out.graph_rewrite_policy == "drop"
        assert all(q.kind != "planner_stage" for q in out.queries)
        assert not any("Pipeline" in q.text for q in out.queries)

    def test_explorer_selection_no_canonical(self):
        from rag_knowledge.services.rag import RagChain

        chain = object.__new__(RagChain)
        plan = RetrievalPlan(
            intent="procedure",
            queries=[RetrievalQuery("写一段创建折线的代码", "original", 1.0)],
            top_k=8,
            candidate_k=24,
            enable_rerank=False,
            expand_neighbors=False,
            confidence=0.9,
        )
        out = RagChain._apply_backbone_anchor_rewrite(
            chain,
            "写一段创建折线的代码",
            plan,
            entity_name=EXPLORER_OPS_SENTINEL,
        )
        assert out.backbone_canonical == ()
        assert out.job == "j2"
        assert out.rewrite_template == "explorer_ops"

    def test_com_reject_helper(self):
        from rag_knowledge.services.rag import RagChain
        from rag_knowledge.services.sdk_code_job import COM_PHASE0_REJECT_ANSWER

        chain = object.__new__(RagChain)
        rejected = RagChain._com_phase0_reject_if_needed(
            chain,
            "写一段创建折线的代码",
            entity_name=COM_SENTINEL,
        )
        assert rejected is not None
        assert rejected["answer"] == COM_PHASE0_REJECT_ANSWER


class TestSdkManualPrefer:
    def test_hint_for_polyline_includes_api_name(self):
        from rag_knowledge.services.sdk_code_job import build_sdk_manual_bm25_hint

        hint = build_sdk_manual_bm25_hint("如何在地图上绘制折线？")
        assert hint is not None
        assert "createElementLineParams" in hint
        assert "接口说明书" in hint

    def test_prefer_manual_over_cookbook(self):
        from langchain_core.documents import Document

        from rag_knowledge.services.sdk_code_job import prefer_sdk_manual_docs

        cookbook = Document(
            page_content="linecolor",
            metadata={
                "source": "01-webrtc-create-polyline-linecolor-linewidth.md",
                "rrf_score": 0.02,
            },
        )
        manual = Document(
            page_content="createElementLineParams linewidth",
            metadata={
                "source": "StampGIS平台WebRTC接口说明书.docx",
                "document_profile": "api_doc",
                "rrf_score": 0.01,
            },
        )
        ranked = prefer_sdk_manual_docs([cookbook, manual])
        assert "接口说明书" in ranked[0].metadata["source"]
        assert ranked[0].metadata["sdk_evidence_tier"] == 0
        assert ranked[1].metadata["sdk_evidence_tier"] == 2

    def test_multi_query_prefer_applies_after_rrf(self):
        """多查询 RRF 合并后仍须优选手册（修复 cookbook 挤占 top 的问题）。"""
        from langchain_core.documents import Document

        from rag_knowledge.services.retrieval_strategy import RetrievalStrategy

        cookbook = Document(
            page_content="创建折线 线颜色 线宽",
            metadata={
                "source": "01-webrtc-create-polyline-linecolor-linewidth.md",
                "chunk_id": "chk_cookbook_01",
                "review_status": "approved",
            },
        )
        manual = Document(
            page_content="StampUtil.createElementLineParams(params) linecolor linewidth",
            metadata={
                "source": "StampGIS平台WebRTC接口说明书.docx",
                "document_profile": "api_doc",
                "chunk_id": "chk_manual_rtc",
                "review_status": "approved",
            },
        )
        strategy = RetrievalStrategy()
        strategy.retrieve = lambda *a, **k: [cookbook, manual]
        docs = strategy.retrieve_many(
            ["写一段创建折线的代码", "StampUtil createElementLineParams linecolor"],
            top_k=4,
            candidate_k=8,
        )
        assert docs, "expected non-empty multi-query result"
        assert "接口说明书" in docs[0].metadata["source"]


class TestTraceClarifyBlock:
    """FR-7: qa_trace clarify block rebuilt from the J3 gate decision."""

    def test_j3_unclear_plan_records_subgraph_options(self):
        from rag_knowledge.services.query_contextualizer import RetrievalQuery
        from rag_knowledge.services.query_planner import RetrievalPlan
        from rag_knowledge.services.rag import RagChain

        chain = object.__new__(RagChain)
        plan = RetrievalPlan(
            intent="procedure",
            queries=[RetrievalQuery("写一段创建折线的代码", "original", 1.0)],
            top_k=8,
            candidate_k=24,
            enable_rerank=False,
            expand_neighbors=False,
            confidence=0.9,
            job="j3",
            rewrite_template="j3_unclear_no_guess",
            graph_rewrite_policy="drop",
        )
        clarify = RagChain._build_trace_clarify(
            chain,
            "写一段创建折线的代码，线颜色设为红色、线宽为 3。",
            plan,
            clarification_question=None,
            clarification_selected=None,
        )
        assert clarify["needs_clarification"] is True
        entities = {o["entity_name"] for o in clarify["options"]}
        assert "StampWebRTC" in entities and "StampWebGL" in entities
        assert not any(str(e or "").startswith("Pipeline") for e in entities)
        sources = {o["source"] for o in clarify["options"]}
        assert "backbone_seed" in sources
        assert clarify["selected"] == ""

    def test_selected_j3_plan_records_selection_and_options(self):
        from rag_knowledge.services.query_contextualizer import RetrievalQuery
        from rag_knowledge.services.query_planner import RetrievalPlan
        from rag_knowledge.services.rag import RagChain

        chain = object.__new__(RagChain)
        plan = RetrievalPlan(
            intent="procedure",
            queries=[RetrievalQuery("写一段创建折线的代码", "original", 1.0)],
            top_k=8,
            candidate_k=24,
            enable_rerank=False,
            expand_neighbors=False,
            confidence=0.9,
            job="j3",
            rewrite_template="j3",
        )
        clarify = RagChain._build_trace_clarify(
            chain,
            "写一段创建折线的代码，线颜色设为红色、线宽为 3。",
            plan,
            clarification_question="请选择二次开发调用面：",
            clarification_selected="StampWebRTC 二次开发（StampUtil）",
        )
        assert clarify["needs_clarification"] is True
        assert clarify["selected"] == "StampWebRTC 二次开发（StampUtil）"
        entities = {o["entity_name"] for o in clarify["options"]}
        assert "StampWebRTC" in entities and "StampWebGL" in entities


class TestReferentUniqueness:
    """2026-08-14 PRD: named legal anchor skips family clarify."""

    def _svc(self):
        from rag_knowledge.services.backbone_guard import load_backbone_constraints

        return QueryClarificationService(
            enabled=True,
            llm_enabled=False,
            constraints=load_backbone_constraints(),
        )

    def test_named_webrtc_code_skips_clarify(self):
        from rag_knowledge.services.sdk_code_job import COM_PHASE0_REJECT_ANSWER

        q = "在 StampWebRTC 中，如何用代码初始化并加载 StampUtil？"
        d = resolve_job(q)
        assert d.job == "j3"
        assert d.needs_j3_clarify is False
        result = self._svc().analyze(q)
        assert result.needs_clarification is False
        assert not any(
            str(o.filter.entity_name or "").startswith("Pipeline") for o in result.options
        )
        assert "未查询到相关内容" not in COM_PHASE0_REJECT_ANSWER

    def test_named_webgl_code_skips_clarify(self):
        q = "使用 StampWebGL 接口写一段创建折线的代码，线颜色为红色，线宽为 3。"
        result = self._svc().analyze(q)
        assert result.needs_clarification is False
        d = resolve_job(q)
        assert d.canonical_hint == "StampWebGL"

    def test_via_code_still_j3_card(self):
        q = "如何通过代码修改多边形的填充颜色并设置透明度？"
        d = resolve_job(q)
        assert d.job == "j3"
        assert d.needs_j3_clarify is True
        result = self._svc().analyze(q)
        assert result.needs_clarification is True
        assert result.reason == "j3_subject_unclear"
        entities = {o.filter.entity_name for o in result.options}
        assert "StampWebRTC" in entities and "StampWebGL" in entities
        assert "PipelineWebGL" not in entities

    def test_style_only_not_j3(self):
        q = "怎么设透明度"
        d = resolve_job(q)
        assert d.job != "j3"
        result = self._svc().analyze(q)
        assert result.reason != "j3_subject_unclear"

    def test_j3_pipeline_named_stays_j3_card(self):
        q = "在 PipelineWebGL 中用代码写一段创建多边形的代码，设置填充色。"
        result = self._svc().analyze(q)
        assert result.needs_clarification is True
        assert result.reason == "j3_subject_unclear"
        entities = {o.filter.entity_name for o in result.options}
        assert "PipelineWebGL" not in entities

        from rag_knowledge.services.query_contextualizer import RetrievalQuery
        from rag_knowledge.services.query_planner import RetrievalPlan
        from rag_knowledge.services.rag import RagChain

        chain = object.__new__(RagChain)
        chain._graph_cfg = MagicMock(query_rewrite_enabled=True)
        plan = RetrievalPlan(
            intent="procedure",
            queries=[RetrievalQuery(q, "original", 1.0)],
            top_k=8,
            candidate_k=24,
            enable_rerank=False,
            expand_neighbors=False,
            confidence=0.9,
        )
        out = RagChain._apply_backbone_anchor_rewrite(
            chain, q, plan, entity_name="PipelineWebGL",
        )
        assert out.backbone_canonical == ()
        assert out.rewrite_template == "j3_blocklist_drop"

    def test_j2_named_pipeline_builder_skips_clarify(self):
        q = "在 PipelineBuilder 中如何新建工程？"
        d = resolve_job(q)
        assert d.job == "j2"
        result = self._svc().analyze(q)
        assert result.needs_clarification is False

    def test_j1_named_webrtc_skips_clarify(self):
        result = self._svc().analyze("StampWebRTC 是什么")
        assert result.needs_clarification is False

    def test_j1_family_webgl_still_clarifies(self):
        result = self._svc().analyze("WebGL 客户端主要提供哪些三维展示功能？")
        assert result.needs_clarification is True
        assert result.reason != "j3_subject_unclear"
        entities = {o.filter.entity_name for o in result.options}
        assert "WebGL" in entities
        assert "StampWebGL" in entities
        assert "PipelineWebGL" in entities

    def test_com_reject_copy_not_miss_template(self):
        from rag_knowledge.services.rag import RagChain
        from rag_knowledge.services.sdk_code_job import COM_PHASE0_REJECT_ANSWER, COM_SENTINEL

        chain = object.__new__(RagChain)
        rejected = RagChain._com_phase0_reject_if_needed(
            chain, "写一段创建折线的代码", entity_name=COM_SENTINEL,
        )
        assert rejected is not None
        assert not rejected["answer"].startswith("当前知识库中未查询到相关内容")
        assert "StampUtil" in rejected["answer"]
        assert rejected["answer"] == COM_PHASE0_REJECT_ANSWER

    def test_unmapped_selected_does_not_skip_j3_reject(self):
        from rag_knowledge.services.rag import RagChain

        chain = object.__new__(RagChain)
        rejected = RagChain._j3_clarify_reject_if_needed(
            chain,
            "写一段创建折线的代码，线颜色设为红色、线宽为 3。",
            entity_name=None,
            clarification_selected="随便写点别的",
        )
        assert rejected is not None
        assert "请先选择调用面" in rejected["answer"]
