import asyncio
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document
import jieba
from rank_bm25 import BM25Okapi

from rag_knowledge.services.bm25_store import BM25Store
from rag_knowledge.services.retrieval_strategy import RetrievalStrategy


def _doc(chunk_id: str, content: str = "text") -> Document:
    return Document(page_content=content, metadata={"chunk_id": chunk_id})


class RetrievalStrategyTests(unittest.TestCase):
    def test_rrf_accumulates_scores_and_deduplicates(self):
        result = RetrievalStrategy._rrf_fuse(
            [[_doc("a"), _doc("b")], [_doc("b"), _doc("c")]],
            rrf_k=60,
            top_k=3,
        )
        self.assertEqual([doc.metadata["chunk_id"] for doc in result], ["b", "a", "c"])

    def test_rrf_is_stable_for_ties_and_applies_top_k(self):
        result = RetrievalStrategy._rrf_fuse(
            [[_doc("b")], [_doc("a")]], rrf_k=60, top_k=1
        )
        self.assertEqual([doc.metadata["chunk_id"] for doc in result], ["a"])

    def test_rrf_handles_empty_branches_and_missing_ids(self):
        missing = Document(page_content="missing", metadata={})
        self.assertEqual(RetrievalStrategy._rrf_fuse([[], []], 60, 4), [])
        result = RetrievalStrategy._rrf_fuse([[], [missing, _doc("a")]], 60, 4)
        self.assertEqual([doc.metadata["chunk_id"] for doc in result], ["a"])

    def test_weighted_rrf_prioritizes_primary_query_and_records_provenance(self):
        result = RetrievalStrategy._rrf_fuse(
            [[_doc("primary")], [_doc("anchor")]],
            rrf_k=60,
            top_k=2,
            weights=[1.0, 0.3],
            labels=["original", "source_anchor"],
        )

        self.assertEqual(
            [doc.metadata["chunk_id"] for doc in result],
            ["primary", "anchor"],
        )
        self.assertGreater(
            result[0].metadata["rrf_score"], result[1].metadata["rrf_score"]
        )
        self.assertEqual(result[0].metadata["matched_query_kinds"], ["original"])

    def test_unweighted_rrf_remains_equal_weight_compatible(self):
        result = RetrievalStrategy._rrf_fuse(
            [[_doc("b")], [_doc("a")]], rrf_k=60, top_k=2
        )
        self.assertEqual([doc.metadata["chunk_id"] for doc in result], ["a", "b"])
        self.assertAlmostEqual(
            result[0].metadata["rrf_score"], result[1].metadata["rrf_score"]
        )

    def test_sync_and_async_multi_query_use_same_weights(self):
        strategy = object.__new__(RetrievalStrategy)
        strategy._cfg = SimpleNamespace(
            retrieval_strategy="hybrid",
            retrieval_candidate_k=4,
            retrieval_rrf_k=60,
            retrieval_top_k=2,
        )

        def fake_retrieve(query, **kwargs):
            return [_doc("primary" if query == "q1" else "anchor")]

        async def fake_aretrieve(query, **kwargs):
            return fake_retrieve(query, **kwargs)

        strategy.retrieve = fake_retrieve
        strategy.aretrieve = fake_aretrieve
        kwargs = {
            "query_weights": [1.0, 0.3],
            "query_labels": ["original", "source_anchor"],
        }

        sync_result = strategy.retrieve_many(["q1", "q2"], **kwargs)
        async_result = asyncio.run(strategy.aretrieve_many(["q1", "q2"], **kwargs))

        self.assertEqual(
            [doc.metadata["chunk_id"] for doc in sync_result],
            [doc.metadata["chunk_id"] for doc in async_result],
        )

    def test_single_successful_weighted_branch_keeps_rrf_metadata(self):
        strategy = object.__new__(RetrievalStrategy)
        strategy._cfg = SimpleNamespace(
            retrieval_strategy="hybrid",
            retrieval_candidate_k=4,
            retrieval_rrf_k=60,
            retrieval_top_k=2,
        )
        strategy.retrieve = lambda query, **kwargs: (
            [_doc("anchor")] if query == "anchor" else []
        )

        async def fake_aretrieve(query, **kwargs):
            return strategy.retrieve(query, **kwargs)

        strategy.aretrieve = fake_aretrieve

        result = strategy.retrieve_many(
            ["primary", "anchor"],
            query_weights=[1.0, 0.3],
            query_labels=["original", "source_anchor"],
        )

        self.assertEqual(result[0].metadata["matched_query_kinds"], ["source_anchor"])
        self.assertAlmostEqual(result[0].metadata["rrf_score"], 0.3 / 61)
        async_result = asyncio.run(
            strategy.aretrieve_many(
                ["primary", "anchor"],
                query_weights=[1.0, 0.3],
                query_labels=["original", "source_anchor"],
            )
        )
        self.assertEqual(
            async_result[0].metadata["matched_query_kinds"], ["source_anchor"]
        )
        self.assertAlmostEqual(async_result[0].metadata["rrf_score"], 0.3 / 61)

    def test_hybrid_passes_same_filters_to_both_branches(self):
        strategy = object.__new__(RetrievalStrategy)
        strategy._cfg = SimpleNamespace(
            retrieval_fusion_method="rrf",
            retrieval_candidate_k=12,
            retrieval_rrf_k=60,
            retrieval_top_k=4,
        )
        strategy._retrieve_vector = MagicMock(return_value=[_doc("a")])
        strategy._retrieve_bm25 = MagicMock(return_value=[_doc("b")])

        strategy._retrieve_hybrid("query", "kb", "category", "approved")

        strategy._retrieve_vector.assert_called_once_with(
            "query", kb_name="kb", doc_category="category",
            review_status="approved", search_type="similarity", top_k=12,
        )
        strategy._retrieve_bm25.assert_called_once_with(
            "query", kb_name="kb", doc_category="category",
            review_status="approved", top_k=12,
        )

    def test_async_hybrid_runs_two_branches_concurrently(self):
        strategy = object.__new__(RetrievalStrategy)
        strategy._cfg = SimpleNamespace(
            retrieval_fusion_method="rrf",
            retrieval_candidate_k=12,
            retrieval_rrf_k=60,
            retrieval_top_k=4,
        )

        async def fake_vector(*args, **kwargs):
            await asyncio.sleep(0.05)
            return [_doc("a")]

        async def fake_bm25(*args, **kwargs):
            await asyncio.sleep(0.05)
            return [_doc("b")]

        strategy._aretrieve_vector = fake_vector
        strategy._aretrieve_bm25 = fake_bm25

        started = time.perf_counter()
        result = asyncio.run(
            strategy._aretrieve_hybrid("query", "kb", "category", "approved")
        )
        elapsed = time.perf_counter() - started

        self.assertEqual([doc.metadata["chunk_id"] for doc in result], ["a", "b"])
        self.assertLess(elapsed, 0.095)

    def test_aretrieve_dispatches_hybrid_and_bm25(self):
        strategy = object.__new__(RetrievalStrategy)
        strategy._cfg = SimpleNamespace(retrieval_strategy="hybrid")

        async def return_empty(*args, **kwargs):
            return []

        strategy._aretrieve_vector = return_empty
        strategy._aretrieve_bm25 = return_empty
        strategy._aretrieve_hybrid = MagicMock(side_effect=return_empty)

        asyncio.run(strategy.aretrieve("q", method="hybrid"))
        asyncio.run(strategy.aretrieve("q", method="bm25"))

        strategy._aretrieve_hybrid.assert_called_once()

    def test_retrieve_dispatches_existing_methods_and_rejects_unknown(self):
        strategy = object.__new__(RetrievalStrategy)
        strategy._cfg = SimpleNamespace(retrieval_strategy="mmr")
        strategy._retrieve_vector = MagicMock(return_value=[])
        strategy._retrieve_bm25 = MagicMock(return_value=[])
        strategy._retrieve_hybrid = MagicMock(return_value=[])

        strategy.retrieve("q", method="mmr")
        strategy.retrieve("q", method="similarity")
        strategy.retrieve("q", method="bm25")
        strategy.retrieve("q", method="hybrid")

        self.assertEqual(strategy._retrieve_vector.call_count, 2)
        strategy._retrieve_bm25.assert_called_once()
        strategy._retrieve_hybrid.assert_called_once()
        with self.assertRaises(ValueError):
            strategy.retrieve("q", method="unknown")

    def test_bm25_build_adds_chroma_id_to_metadata(self):
        store = object.__new__(BM25Store)
        store._bm25 = None
        store._docs = []
        store._metadatas = []
        chroma = MagicMock()
        chroma.get.return_value = {
            "documents": ["测试文档"],
            "metadatas": [{"kb_name": "kb"}],
            "ids": ["chunk-1"],
        }
        vector_store = MagicMock()
        vector_store.get_chroma.return_value = chroma

        with patch("rag_knowledge.services.bm25_store.VectorStore", return_value=vector_store):
            store._build_index()

        self.assertEqual(store._docs[0].metadata["chunk_id"], "chunk-1")
        self.assertEqual(store._metadatas[0]["chunk_id"], "chunk-1")

    def test_bm25_build_skips_empty_document_collection(self):
        store = object.__new__(BM25Store)
        store._bm25 = None
        store._docs = []
        store._metadatas = []
        chroma = MagicMock()
        chroma.get.return_value = {
            "documents": [],
            "metadatas": [],
            "ids": [],
        }
        vector_store = MagicMock()
        vector_store.get_chroma.return_value = chroma

        with patch("rag_knowledge.services.bm25_store.VectorStore", return_value=vector_store):
            store._build_index()

        self.assertIsNone(store._bm25)
        self.assertEqual(store._docs, [])
        self.assertEqual(store._metadatas, [])

    def test_bm25_build_skips_empty_tokenized_corpus(self):
        store = object.__new__(BM25Store)
        store._bm25 = None
        store._docs = []
        store._metadatas = []
        chroma = MagicMock()
        chroma.get.return_value = {
            "documents": ["   ", ""],
            "metadatas": [{"kb_name": "kb-a"}, {"kb_name": "kb-b"}],
            "ids": ["chunk-a", "chunk-b"],
        }
        vector_store = MagicMock()
        vector_store.get_chroma.return_value = chroma

        with patch("rag_knowledge.services.bm25_store.VectorStore", return_value=vector_store), \
             patch("rag_knowledge.services.bm25_store.jieba.cut", side_effect=[[], []]), \
             patch("rag_knowledge.services.bm25_store.BM25Okapi") as bm25_cls:
            store._build_index()

        bm25_cls.assert_not_called()
        self.assertIsNone(store._bm25)
        self.assertEqual(store._docs, [])
        self.assertEqual(store._metadatas, [])


if __name__ == "__main__":
    unittest.main()
