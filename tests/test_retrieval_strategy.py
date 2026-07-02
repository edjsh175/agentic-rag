import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document

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


if __name__ == "__main__":
    unittest.main()
