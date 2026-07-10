import asyncio
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from rag_knowledge.services.rag import RagChain
from rag_knowledge.services.query_contextualizer import RetrievalQuery
from rag_knowledge.models.api import QueryRequest
from rag_knowledge.services.graph_retrieval import GraphContext, LinkedEntity

try:
    from rag_knowledge.api import routes
except ModuleNotFoundError:
    routes = None


def _planner_stub(top_k=4, candidate_k=12, enable_rerank=False, expand_neighbors=False):
    return type(
        "PlannerStub",
        (),
        {
            "plan": lambda self, question, queries, force_rerank=False: type(
                "PlanStub",
                (),
                {
                    "queries": queries,
                    "top_k": top_k,
                    "candidate_k": candidate_k,
                    "enable_rerank": enable_rerank or force_rerank,
                    "expand_neighbors": expand_neighbors,
                },
            )()
        },
    )()


class RagStage6Tests(unittest.TestCase):
    def test_multi_retrieval_fuses_graph_documents_before_postprocessing(self):
        from langchain_core.documents import Document

        chain = object.__new__(RagChain)
        chain._reranker = object()
        chain._strategy = MagicMock()
        chain._strategy.retrieve_many.return_value = [
            Document(page_content="wrong", metadata={"chunk_id": "wrong"}),
            Document(page_content="pipeline", metadata={"chunk_id": "pipeline"}),
        ]
        captured = []
        chain._postprocess_docs_sync = lambda question, docs, *args, **kwargs: captured.extend(docs) or docs
        chain._normalize_source = lambda content, metadata, index: {"content": content, "metadata": metadata}
        chain._format_context = lambda docs: "ctx"

        chain._retrieve_multi(
            ["question", "stage"],
            rerank=True,
            plan_top_k=2,
            plan_candidate_k=4,
            graph_docs=[Document(page_content="pipeline", metadata={"chunk_id": "pipeline"})],
            graph_weight=1.25,
        )

        self.assertEqual([doc.metadata["chunk_id"] for doc in captured], ["pipeline", "wrong"])

    def test_graph_plan_enrichment_is_disabled_without_retriever(self):
        chain = object.__new__(RagChain)
        chain._graph_retriever = None
        plan = type("Plan", (), {"intent": "procedure"})()

        enriched, context, docs = chain._prepare_graph_plan("question", plan)

        self.assertIs(enriched, plan)
        self.assertIsNone(context)
        self.assertEqual(docs, [])

    def test_graph_plan_enrichment_adds_context_and_documents(self):
        from rag_knowledge.services.query_planner import RetrievalPlan

        linked = LinkedEntity("e1", "PipelineBuilder", "Tool", 0.96, "alias_exact", ("e2",))
        context = GraphContext(
            linked_entities=(linked,),
            expanded_entity_ids=("e1",),
            chunk_ids=("c1",),
            retrieval_queries=("PipelineBuilder", "工程设置"),
        )
        graph_doc = type("Doc", (), {})()
        chain = object.__new__(RagChain)
        chain._graph_retriever = MagicMock()
        chain._graph_retriever.retrieve.return_value = (context, [graph_doc])
        chain._graph_retriever.revision.return_value = "rev-1"
        plan = RetrievalPlan("procedure", [], 8, 24, True, True, 0.9)

        enriched, returned_context, docs = chain._prepare_graph_plan("question", plan)

        self.assertEqual(enriched.graph_queries, ("PipelineBuilder", "工程设置"))
        self.assertEqual(enriched.graph_chunk_ids, ("c1",))
        self.assertEqual(enriched.excluded_entity_ids, ("e2",))
        self.assertEqual(enriched.graph_revision, "rev-1:1.25")
        self.assertIs(returned_context, context)
        self.assertEqual(docs, [graph_doc])

    def test_graph_plan_enrichment_falls_back_when_revision_lookup_fails(self):
        from rag_knowledge.services.query_planner import RetrievalPlan

        chain = object.__new__(RagChain)
        chain._graph_retriever = MagicMock()
        chain._graph_retriever.retrieve.side_effect = RuntimeError("graph unavailable")
        plan = RetrievalPlan("definition", [], 4, 12, True, False, 0.9)

        enriched, context, docs = chain._prepare_graph_plan("question", plan)

        self.assertIs(enriched, plan)
        self.assertIsNone(context)
        self.assertEqual(docs, [])

    def test_query_methods_fallback_gracefully_when_graph_retrieval_fails(self):
        from rag_knowledge.services.query_planner import RetrievalPlan

        chain = object.__new__(RagChain)
        chain._graph_cfg = type("Config", (), {"graph_weight": 1.25})()
        chain._graph_retriever = MagicMock()
        chain._graph_retriever.retrieve.side_effect = RuntimeError("db offline")

        chain._build_retrieval_query_specs = lambda question, history: ["question"]
        chain._plan_retrieval = lambda question, queries, force_rerank=False: RetrievalPlan("definition", [], 4, 12, True, False, 0.9)
        chain._retrieve_multi = MagicMock(return_value=([], ""))
        chain._record_chunk_hit_query = MagicMock()
        chain._allow_general_knowledge = False

        res = chain.query("question", allow_general_knowledge=False)
        self.assertEqual(res["source_documents"], [])
        chain._retrieve_multi.assert_called_once_with(
            [], kb_name=None, doc_category=None,
            rerank=True, web_search=False, plan_top_k=4, plan_candidate_k=12, expand_neighbors=False, intent_plan=None
        )

        chain._aretrieve_multi_uncached = AsyncMock(return_value=([], ""))
        res_async = asyncio.run(
            chain.aquery("question", allow_general_knowledge=False)
        )
        self.assertEqual(res_async["source_documents"], [])
        chain._aretrieve_multi_uncached.assert_awaited_once_with(
            [], kb_name=None, doc_category=None,
            rerank=True, web_search=False, plan_top_k=4, plan_candidate_k=12, expand_neighbors=False, intent_plan=None
        )

        chain._query_cache = object()
        chain._aretrieve_uncached = object()
        async def collect():
            return [event async for event in chain.stream_query("question", allow_general_knowledge=False)]

        events = asyncio.run(collect())
        self.assertTrue(any(e.get("type") == "sources" and e.get("data") == [] for e in events))

    def test_graph_enabled_with_no_linked_entity_matches_disabled(self):
        from rag_knowledge.services.query_planner import RetrievalPlan

        # Mock dependencies on RagChain
        chain = object.__new__(RagChain)
        chain._graph_cfg = type("Config", (), {"graph_weight": 1.25})()

        # Scenario A: Graph enabled but retrieve returns no_linked_entity fallback
        chain._graph_retriever = MagicMock()
        chain._graph_retriever.retrieve.return_value = (
            type("Ctx", (), {"fallback_reason": "no_linked_entity", "excluded_chunk_ids": ()})(),
            []
        )
        chain._build_retrieval_query_specs = lambda question, history: ["question"]
        chain._plan_retrieval = lambda question, queries, force_rerank=False: RetrievalPlan("definition", [], 4, 12, True, False, 0.9)
        chain._retrieve_multi = MagicMock(return_value=([{"content": "result"}], "result_ctx"))
        chain._record_chunk_hit_query = MagicMock()
        chain._allow_general_knowledge = False

        res_enabled = chain.query("question", allow_general_knowledge=False)
        chain._retrieve_multi.assert_called_once_with(
            [], kb_name=None, doc_category=None,
            rerank=True, web_search=False, plan_top_k=4, plan_candidate_k=12, expand_neighbors=False, intent_plan=None
        )

        # Scenario B: Graph disabled (_graph_retriever is None)
        chain._graph_retriever = None
        chain._retrieve_multi.reset_mock()
        res_disabled = chain.query("question", allow_general_knowledge=False)

        self.assertEqual(res_enabled, res_disabled)
        chain._retrieve_multi.assert_called_once_with(
            [], kb_name=None, doc_category=None,
            rerank=True, web_search=False, plan_top_k=4, plan_candidate_k=12, expand_neighbors=False, intent_plan=None
        )

    def test_multi_retrieval_passes_query_weights_and_labels_to_strategy(self):
        chain = object.__new__(RagChain)
        chain._reranker = None
        chain._strategy = MagicMock()
        chain._strategy.retrieve_many.return_value = []
        chain._postprocess_docs_sync = (
            lambda question, docs, enabled, target_top_k=None, expand_neighbors=False, intent_plan=None: docs
        )

        specs = [
            RetrievalQuery("current question", "original", 1.0),
            RetrievalQuery("old source", "source_anchor", 0.3),
        ]
        chain._retrieve_multi(specs)

        chain._strategy.retrieve_many.assert_called_once_with(
            ["current question", "old source"],
            kb_name=None,
            doc_category=None,
            review_status="approved",
            method=None,
            query_weights=[1.0, 0.3],
            query_labels=["original", "source_anchor"],
            top_k=None,
            candidate_k=None,
        )

    def test_multi_retrieval_uses_candidate_pool_before_rerank(self):
        chain = object.__new__(RagChain)
        chain._reranker = object()
        chain._strategy = MagicMock()
        chain._strategy.retrieve_many.return_value = []
        chain._postprocess_docs_sync = (
            lambda question, docs, enabled, target_top_k=None, expand_neighbors=False, intent_plan=None: docs
        )

        specs = [
            RetrievalQuery("main question", "original", 1.0),
            RetrievalQuery("stage query", "planner_stage", 0.45),
        ]
        chain._retrieve_multi(
            specs,
            rerank=True,
            plan_top_k=8,
            plan_candidate_k=24,
        )

        chain._strategy.retrieve_many.assert_called_once_with(
            ["main question", "stage query"],
            kb_name=None,
            doc_category=None,
            review_status="approved",
            method=None,
            query_weights=[1.0, 0.45],
            query_labels=["original", "planner_stage"],
            top_k=24,
            candidate_k=24,
        )

    def test_single_query_retrieval_keeps_planner_parameters(self):
        chain = object.__new__(RagChain)
        chain._retrieve = MagicMock(return_value=([], ""))

        chain._retrieve_multi(
            ["question"],
            rerank=True,
            web_search=True,
            plan_top_k=8,
            plan_candidate_k=24,
            expand_neighbors=True, intent_plan=None,
        )

        chain._retrieve.assert_called_once_with(
            "question",
            kb_name=None,
            doc_category=None,
            review_status="approved",
            method=None,
            rerank=True,
            web_search=True,
            top_k_override=8,
            candidate_k_override=24,
            expand_neighbors=True, intent_plan=None,
        )

    def test_single_query_async_retrieval_keeps_planner_parameters(self):
        chain = object.__new__(RagChain)
        chain._aretrieve_with_cache = AsyncMock(return_value=([], ""))

        asyncio.run(
            chain._aretrieve_multi_uncached(
                ["question"],
                rerank=True,
                web_search=True,
                plan_top_k=8,
                plan_candidate_k=24,
                expand_neighbors=True, intent_plan=None,
            )
        )

        chain._aretrieve_with_cache.assert_awaited_once_with(
            rewritten_query="question",
            kb_name=None,
            doc_category=None,
            review_status="approved",
            method=None,
            rerank=True,
            web_search=True,
            top_k_override=8,
            candidate_k_override=24,
            expand_neighbors=True, intent_plan=None,
        )

    def test_stream_route_encodes_named_status_event(self):
        if routes is None:
            self.skipTest("optional API route dependencies are not installed")
        original = routes._rag

        class RagStub:
            async def stream_query(self, *_args, **_kwargs):
                yield {"type": "status", "data": "正在理解问题..."}
                yield {"type": "done"}

        async def collect():
            routes._rag = RagStub()
            response = await routes.query_stream(QueryRequest(question="question"))
            return "".join([chunk async for chunk in response.body_iterator])

        try:
            body = asyncio.run(collect())
        finally:
            routes._rag = original

        self.assertIn("event: status\n", body)
        self.assertIn('"type": "status"', body)

    def test_retrieval_cache_reuses_same_request(self):
        chain = object.__new__(RagChain)
        chain._query_cache = MagicMock()
        chain._query_cache.get.side_effect = [None, {"source_docs": [{"content": "cached"}], "context": "ctx"}]
        chain._query_cache.set = MagicMock()
        chain._aretrieve_uncached = AsyncMock(
            return_value=([{"content": "fresh"}], "fresh ctx")
        )

        first = asyncio.run(
            chain._aretrieve_with_cache(
                rewritten_query="question",
                kb_name="kb",
                doc_category=None,
                review_status="approved",
                method="hybrid",
                rerank=False,
                web_search=False,
            )
        )
        second = asyncio.run(
            chain._aretrieve_with_cache(
                rewritten_query="question",
                kb_name="kb",
                doc_category=None,
                review_status="approved",
                method="hybrid",
                rerank=False,
                web_search=False,
            )
        )

        self.assertEqual(first, ([{"content": "fresh"}], "fresh ctx"))
        self.assertEqual(second, ([{"content": "cached"}], "ctx"))
        chain._aretrieve_uncached.assert_awaited_once()
        chain._query_cache.set.assert_called_once()

    def test_stream_and_non_stream_share_cached_retrieval_path(self):
        chain = object.__new__(RagChain)
        chain._allow_general_knowledge = True
        chain._ollama_base = "http://localhost:11434"
        chain._llm_model = "test-model"
        chain._query_planner = _planner_stub()
        chain._query_cache = MagicMock()
        chain._query_cache.get.side_effect = [None, {"source_docs": [], "context": ""}]
        chain._query_cache.set = MagicMock()
        chain._aretrieve_uncached = AsyncMock(return_value=([], ""))
        chain._rewrite_query = lambda question, history: question
        chain._history_compressor = type(
            "HistoryCompressorStub",
            (),
            {"compress": lambda self, history: (history, None)},
        )()
        chain._budget = type(
            "BudgetStub",
            (),
            {
                "trim": lambda self, docs, context, history, question, agent_prompt=None: (
                    docs,
                    context,
                    history,
                )
            },
        )()

        result = asyncio.run(
            chain.aquery("question", allow_general_knowledge=False)
        )

        async def collect():
            return [
                event
                async for event in chain.stream_query(
                    "question", allow_general_knowledge=False
                )
            ]

        events = asyncio.run(collect())

        self.assertEqual(result["source_documents"], [])
        self.assertEqual(
            [event["data"] for event in events if event["type"] == "status"],
            ["正在理解问题...", "正在检索知识库..."],
        )
        self.assertIn({"type": "sources", "data": []}, events)
        chain._aretrieve_uncached.assert_awaited_once()

    def test_aquery_maps_thinking_true_to_request_rerank(self):
        chain = object.__new__(RagChain)
        chain._build_retrieval_query_specs = lambda question, history: ["question"]
        chain._query_planner = _planner_stub(enable_rerank=True)
        chain._aretrieve_multi_uncached = AsyncMock(return_value=([], ""))
        chain._allow_general_knowledge = False

        result = asyncio.run(
            chain.aquery("question", thinking=True, allow_general_knowledge=False)
        )

        self.assertEqual(result["source_documents"], [])
        chain._aretrieve_multi_uncached.assert_awaited_once_with(
            ["question"],
            kb_name=None,
            doc_category=None,
            rerank=True,
            web_search=False,
            plan_top_k=4,
            plan_candidate_k=12,
            expand_neighbors=False, intent_plan=None,
        )

    def test_aquery_always_requests_rerank_when_thinking_false_or_none(self):
        for thinking in (False, None):
            with self.subTest(thinking=thinking):
                chain = object.__new__(RagChain)
                chain._build_retrieval_query_specs = lambda question, history: ["question"]
                chain._query_planner = _planner_stub(enable_rerank=False)
                chain._aretrieve_multi_uncached = AsyncMock(return_value=([], ""))
                chain._allow_general_knowledge = False

                asyncio.run(
                    chain.aquery(
                        "question",
                        thinking=thinking,
                        allow_general_knowledge=False,
                    )
                )

                chain._aretrieve_multi_uncached.assert_awaited_once_with(
                    ["question"],
                    kb_name=None,
                    doc_category=None,
                    rerank=True,
                    web_search=False,
                    plan_top_k=4,
                    plan_candidate_k=12,
                    expand_neighbors=False, intent_plan=None,
                )

    def test_stream_query_maps_thinking_true_to_request_rerank(self):
        chain = object.__new__(RagChain)
        chain._build_retrieval_query_specs = lambda question, history: ["question"]
        chain._query_planner = _planner_stub(enable_rerank=True)
        chain._aretrieve_multi_uncached = AsyncMock(return_value=([], ""))
        chain._query_cache = MagicMock()
        chain._aretrieve_uncached = AsyncMock(return_value=([], ""))
        chain._allow_general_knowledge = False

        async def collect():
            return [
                event
                async for event in chain.stream_query(
                    "question", thinking=True, allow_general_knowledge=False
                )
            ]

        events = asyncio.run(collect())

        self.assertIn({"type": "sources", "data": []}, events)
        chain._aretrieve_multi_uncached.assert_awaited_once_with(
            ["question"],
            kb_name=None,
            doc_category=None,
            rerank=True,
            web_search=False,
            plan_top_k=4,
            plan_candidate_k=12,
            expand_neighbors=False, intent_plan=None,
        )

    def test_stream_query_always_requests_rerank_when_thinking_false_or_none(self):
        for thinking in (False, None):
            with self.subTest(thinking=thinking):
                chain = object.__new__(RagChain)
                chain._build_retrieval_query_specs = lambda question, history: ["question"]
                chain._query_planner = _planner_stub(enable_rerank=False)
                chain._aretrieve_multi_uncached = AsyncMock(return_value=([], ""))
                chain._query_cache = MagicMock()
                chain._aretrieve_uncached = AsyncMock(return_value=([], ""))
                chain._allow_general_knowledge = False

                async def collect():
                    return [
                        event
                        async for event in chain.stream_query(
                            "question",
                            thinking=thinking,
                            allow_general_knowledge=False,
                        )
                    ]

                asyncio.run(collect())

                chain._aretrieve_multi_uncached.assert_awaited_once_with(
                    ["question"],
                    kb_name=None,
                    doc_category=None,
                    rerank=True,
                    web_search=False,
                    plan_top_k=4,
                    plan_candidate_k=12,
                    expand_neighbors=False, intent_plan=None,
                )

    def test_query_logs_deep_mode_rerank_and_thinking_states(self):
        chain = object.__new__(RagChain)
        chain._allow_general_knowledge = True
        chain._build_retrieval_query_specs = lambda question, history: ["question"]
        chain._query_planner = _planner_stub(enable_rerank=True)
        chain._retrieve_multi = MagicMock(return_value=([{"content": "ctx", "metadata": {"source": "doc", "category": "text"}}], "ctx"))
        chain._history_compressor = type(
            "HistoryCompressorStub",
            (),
            {"compress": lambda self, history: (history, None)},
        )()
        chain._budget = type(
            "BudgetStub",
            (),
            {"trim": lambda self, docs, context, history, question, agent_prompt=None: (docs, context, history)},
        )()
        chain._build_llm = lambda model: type(
            "LlmStub",
            (),
            {"open": None, "invoke": lambda self, messages: type("Resp", (), {"content": "answer [1]"})()},
        )()
        chain._build_messages = lambda *args, **kwargs: [{"role": "user", "content": "question"}]
        chain._filter_cited_sources = lambda answer, source_docs: source_docs

        with patch("rag_knowledge.services.rag.logger.info") as info_log:
            result = chain.query("question", thinking=True)

        self.assertEqual(result["answer"], "answer [1]")
        chain._retrieve_multi.assert_called_once_with(
            ["question"],
            kb_name=None,
            doc_category=None,
            rerank=True,
            web_search=False,
            plan_top_k=4,
            plan_candidate_k=12,
            expand_neighbors=False, intent_plan=None,
        )
        self.assertTrue(
            any(
                "deep_mode=%s" in call.args[0]
                and "rerank=%s" in call.args[0]
                and "thinking=%s" in call.args[0]
                for call in info_log.call_args_list
            )
        )

    def test_query_always_requests_rerank_when_thinking_false_or_none(self):
        for thinking in (False, None):
            with self.subTest(thinking=thinking):
                chain = object.__new__(RagChain)
                chain._allow_general_knowledge = False
                chain._build_retrieval_query_specs = lambda question, history: ["question"]
                chain._query_planner = _planner_stub(enable_rerank=False)
                chain._retrieve_multi = MagicMock(return_value=([], ""))

                result = chain.query(
                    "question",
                    thinking=thinking,
                    allow_general_knowledge=False,
                )

                self.assertEqual(result["source_documents"], [])
                chain._retrieve_multi.assert_called_once_with(
                    ["question"],
                    kb_name=None,
                    doc_category=None,
                    rerank=True,
                    web_search=False,
                    plan_top_k=4,
                    plan_candidate_k=12,
                    expand_neighbors=False, intent_plan=None,
                )

    def test_query_uses_planner_parameters_for_retrieval(self):
        chain = object.__new__(RagChain)
        chain._allow_general_knowledge = False
        chain._build_retrieval_query_specs = lambda question, history: ["question"]
        chain._query_planner = _planner_stub(
            top_k=8,
            candidate_k=24,
            enable_rerank=True,
            expand_neighbors=True,
        )
        chain._retrieve_multi = MagicMock(return_value=([], ""))

        result = chain.query("question", thinking=True, allow_general_knowledge=False)

        self.assertEqual(result["source_documents"], [])
        chain._retrieve_multi.assert_called_once_with(
            ["question"],
            kb_name=None,
            doc_category=None,
            rerank=True,
            web_search=False,
            plan_top_k=8,
            plan_candidate_k=24,
            expand_neighbors=True, intent_plan=None,
        )

    def test_unknown_kb_async_path_merges_two_targets_deterministically(self):
        chain = object.__new__(RagChain)
        chain._reranker = None
        chain._reranker_top_n = 4
        chain._retrieval_k = 4
        chain._retrieval_fetch_k = 12
        chain._retrieval_lambda = 0.7
        chain._reranker_candidate_k = 20
        chain._retrieval_quality_cfg = type(
            "RetrievalQualityCfgStub",
            (),
            {"contextual_compression_enabled": False},
        )()
        chain._quality = type(
            "QualityStub",
            (),
            {"apply": lambda self, question, docs, **kwargs: docs},
        )()
        chain._compress_retrieved_docs = lambda question, docs: docs
        chain._route_query = lambda question: None
        chain._strategy = type(
            "StrategyStub",
            (),
            {},
        )()

        async def fake_aretrieve(question, kb_name=None, **kwargs):
            if kb_name == "文章附件":
                return [
                    type("Doc", (), {"page_content": "a1", "metadata": {"chunk_id": "a1", "kb_name": "文章附件"}})(),
                    type("Doc", (), {"page_content": "a2", "metadata": {"chunk_id": "a2", "kb_name": "文章附件"}})(),
                ]
            return [
                type("Doc", (), {"page_content": "b1", "metadata": {"chunk_id": "b1", "kb_name": "已发布文章"}})(),
                type("Doc", (), {"page_content": "b2", "metadata": {"chunk_id": "b2", "kb_name": "已发布文章"}})(),
            ]

        chain._strategy.aretrieve = fake_aretrieve

        source_docs, _ = asyncio.run(chain._aretrieve_uncached("question"))

        self.assertEqual(
            [item["metadata"]["kb_name"] for item in source_docs],
            ["文章附件", "已发布文章", "文章附件", "已发布文章"],
        )

    def test_unknown_kb_sync_path_merges_two_targets_deterministically(self):
        chain = object.__new__(RagChain)
        chain._reranker = None
        chain._retrieval_k = 4
        chain._retrieval_fetch_k = 12
        chain._retrieval_lambda = 0.7
        chain._reranker_candidate_k = 20
        chain._retrieval_quality_cfg = type(
            "RetrievalQualityCfgStub",
            (),
            {"contextual_compression_enabled": False},
        )()
        chain._quality = type(
            "QualityStub",
            (),
            {"apply": lambda self, question, docs, **kwargs: docs},
        )()
        chain._compress_retrieved_docs = lambda question, docs: docs
        chain._route_query = lambda question: None

        # Mock strategy to track calls and parameters
        strategy_mock = MagicMock()

        def fake_retrieve(question, kb_name=None, doc_category=None, review_status=None, method=None, top_k=None, **kwargs):
            if kb_name == "文章附件":
                return [
                    type("Doc", (), {"page_content": "a1", "metadata": {"chunk_id": "a1", "kb_name": "文章附件"}})(),
                    type("Doc", (), {"page_content": "a2", "metadata": {"chunk_id": "a2", "kb_name": "文章附件"}})(),
                ]
            return [
                type("Doc", (), {"page_content": "b1", "metadata": {"chunk_id": "b1", "kb_name": "已发布文章"}})(),
                type("Doc", (), {"page_content": "b2", "metadata": {"chunk_id": "b2", "kb_name": "已发布文章"}})(),
            ]

        strategy_mock.retrieve = MagicMock(side_effect=fake_retrieve)
        chain._strategy = strategy_mock

        source_docs, _ = chain._retrieve(
            question="test query",
            kb_name=None,
            doc_category="some_cat",
            review_status="approved",
            method="hybrid",
        )

        # Assert that both KBs were searched with strategy.retrieve and correct parameters
        self.assertEqual(strategy_mock.retrieve.call_count, 2)

        strategy_mock.retrieve.assert_any_call(
            "test query",
            kb_name="文章附件",
            doc_category="some_cat",
            review_status="approved",
            method="hybrid",
            top_k=3,  # (4 // 2) + 1 = 3
        )

        strategy_mock.retrieve.assert_any_call(
            "test query",
            kb_name="已发布文章",
            doc_category="some_cat",
            review_status="approved",
            method="hybrid",
            top_k=3,  # (4 // 2) + 1 = 3
        )

        # Assert merging / interleaving behavior
        self.assertEqual(
            [item["metadata"]["kb_name"] for item in source_docs],
            ["文章附件", "已发布文章", "文章附件", "已发布文章"],
        )

        # Assert no Chroma attribute exists or was called
        self.assertFalse(hasattr(chain, "_store"))

    def test_build_messages_injects_entity_hints(self):
        from unittest.mock import patch, MagicMock
        from rag_knowledge.services.rag import RagChain
        from rag_knowledge.services.graph_retrieval import LinkedEntity

        linked = LinkedEntity(
            entity_id="entity-123",
            canonical_name="PipelineBuilder",
            entity_type="Tool",
            confidence=0.95,
            match_method="exact",
            excluded_entity_ids=("entity-456",)
        )

        mock_db = MagicMock()
        mock_db.get_entity.side_effect = lambda eid: {
            "id": "entity-123",
            "name": "PipelineBuilder",
            "canonical_name": "PipelineBuilder",
            "entity_type": "Tool",
            "doc_category": "StampTools"
        } if eid == "entity-123" else {
            "id": "entity-456",
            "name": "管线发布服务",
            "canonical_name": "管线发布服务",
            "entity_type": "Service",
            "doc_category": "StampServer"
        }

        mock_db.list_aliases.return_value = [{"alias": "管线发布工具", "review_status": "approved"}]
        mock_db.list_relations.return_value = [{
            "source_entity_id": "entity-123",
            "target_entity_id": "entity-456",
            "relation_type": "different_from"
        }]

        with patch("rag_knowledge.repository.relational_db.RelationalDB", return_value=mock_db):
            messages = RagChain._build_messages(
                question="test query",
                context="context doc",
                linked_entities=(linked,)
            )

        system_content = messages[0]["content"]
        self.assertIn("## 当前检索实体提示（仅用于消歧，不作为事实来源）", system_content)
        self.assertIn("PipelineBuilder", system_content)
        self.assertIn("管线发布工具", system_content)
        self.assertIn("不要将 PipelineBuilder 与以下相似但不同的实体混同", system_content)
        self.assertIn("管线发布服务，类型 Service，分类 StampServer", system_content)
        self.assertIn("实体提示仅用于帮助区分相似实体，不能替代知识库事实", system_content)


if __name__ == "__main__":
    unittest.main()
