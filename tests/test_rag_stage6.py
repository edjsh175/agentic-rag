import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from rag_knowledge.services.rag import RagChain


class RagStage6Tests(unittest.TestCase):
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
        self.assertEqual(events[0], {"type": "sources", "data": []})
        chain._aretrieve_uncached.assert_awaited_once()

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


if __name__ == "__main__":
    unittest.main()
