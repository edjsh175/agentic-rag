import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from rag_knowledge.services.rag import RagChain
from rag_knowledge.services.query_contextualizer import RetrievalQuery
from rag_knowledge.api import routes
from rag_knowledge.models.api import QueryRequest


class RagStage6Tests(unittest.TestCase):
    def test_multi_retrieval_passes_query_weights_and_labels_to_strategy(self):
        chain = object.__new__(RagChain)
        chain._reranker = None
        chain._strategy = MagicMock()
        chain._strategy.retrieve_many.return_value = []
        chain._postprocess_docs_sync = lambda question, docs, enabled: docs

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
        )

    def test_stream_route_encodes_named_status_event(self):
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
        )

    def test_aquery_keeps_rerank_disabled_when_thinking_false_or_none(self):
        for thinking in (False, None):
            with self.subTest(thinking=thinking):
                chain = object.__new__(RagChain)
                chain._build_retrieval_query_specs = lambda question, history: ["question"]
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
                    rerank=False,
                    web_search=False,
                )

    def test_stream_query_maps_thinking_true_to_request_rerank(self):
        chain = object.__new__(RagChain)
        chain._build_retrieval_query_specs = lambda question, history: ["question"]
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
        )

    def test_stream_query_keeps_rerank_disabled_when_thinking_false_or_none(self):
        for thinking in (False, None):
            with self.subTest(thinking=thinking):
                chain = object.__new__(RagChain)
                chain._build_retrieval_query_specs = lambda question, history: ["question"]
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
                    rerank=False,
                    web_search=False,
                )

    def test_query_logs_deep_mode_rerank_and_thinking_states(self):
        chain = object.__new__(RagChain)
        chain._allow_general_knowledge = True
        chain._build_retrieval_query_specs = lambda question, history: ["question"]
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
            {"invoke": lambda self, messages: type("Resp", (), {"content": "answer [1]"})()},
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
        )
        self.assertTrue(
            any(
                "deep_mode=%s" in call.args[0]
                and "rerank=%s" in call.args[0]
                and "thinking=%s" in call.args[0]
                for call in info_log.call_args_list
            )
        )

    def test_unknown_kb_async_path_merges_two_targets_deterministically(self):
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
            {"apply": lambda self, question, docs: docs},
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
            {"apply": lambda self, question, docs: docs},
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


if __name__ == "__main__":
    unittest.main()

