from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.documents import Document

from rag_knowledge.repository.vector_store import CachedOllamaEmbeddings, VectorStore
from rag_knowledge.services.bm25_store import BM25Store


def _fake_vector(text: str) -> list[float]:
    lowered = (text or "").casefold()
    return [
        3.0 if "kubernetes" in lowered else 0.0,
        2.0 if "docker" in lowered else 0.0,
        1.0 if "compose" in lowered else 0.0,
        float(len(lowered) % 11),
    ]


def _make_test_doc(content: str, review_status: str = "pending") -> Document:
    return Document(
        page_content=content,
        metadata={
            "kb_name": "articles",
            "review_status": review_status,
            "doc_category": "other",
        },
    )


class TestReviewStatusBM25Sync:
    @pytest.fixture(autouse=True)
    def _setup(self, isolated_storage, monkeypatch):
        _, _, chroma_dir, _ = isolated_storage(
            db_name="review-status-sync.db",
            chroma_name="review-status-sync-chroma",
            data_dir_name="review-status-sync-data",
        )
        monkeypatch.setattr(
            CachedOllamaEmbeddings,
            "embed_query",
            lambda self, text: _fake_vector(text),
        )
        monkeypatch.setattr(
            CachedOllamaEmbeddings,
            "embed_documents",
            lambda self, texts: [_fake_vector(text) for text in texts],
        )
        self._chroma_dir = chroma_dir
        self._test_ids: list[str] = []

    def test_bm25_filters_by_metadata_after_rebuild(self):
        store = VectorStore()
        bm25 = BM25Store()

        assert Path(store._persist_dir).resolve() == self._chroma_dir.resolve()

        doc = _make_test_doc(
            "This document describes Kubernetes cluster deployment.",
            "pending",
        )
        ids = store.add_chunks([doc])
        self._test_ids = ids
        chunk_id = ids[0]
        bm25.rebuild()

        pending_results = bm25.search("Kubernetes deployment", review_status="pending")
        pending_ids = {result.metadata.get("chunk_id") for result in pending_results}
        assert chunk_id in pending_ids

        approved_results = bm25.search(
            "Kubernetes deployment",
            review_status="approved",
        )
        approved_ids = {result.metadata.get("chunk_id") for result in approved_results}
        assert chunk_id not in approved_ids

        store.update_metadata([chunk_id], {"review_status": "approved"})
        bm25.rebuild()

        approved_results_after = bm25.search(
            "Kubernetes deployment",
            review_status="approved",
        )
        approved_ids_after = {
            result.metadata.get("chunk_id") for result in approved_results_after
        }
        assert chunk_id in approved_ids_after

        pending_results_after = bm25.search(
            "Kubernetes deployment",
            review_status="pending",
        )
        pending_ids_after = {
            result.metadata.get("chunk_id") for result in pending_results_after
        }
        assert chunk_id not in pending_ids_after

    def test_bm25_stale_without_rebuild(self):
        store = VectorStore()
        bm25 = BM25Store()

        doc = _make_test_doc(
            "Docker Compose multi-container setup guide.",
            "pending",
        )
        ids = store.add_chunks([doc])
        self._test_ids = ids
        chunk_id = ids[0]
        bm25.rebuild()

        store.update_metadata([chunk_id], {"review_status": "approved"})

        pending_results = bm25.search("Docker Compose", review_status="pending")
        pending_ids = {result.metadata.get("chunk_id") for result in pending_results}
        assert chunk_id in pending_ids

        approved_results = bm25.search("Docker Compose", review_status="approved")
        approved_ids = {result.metadata.get("chunk_id") for result in approved_results}
        assert chunk_id not in approved_ids

    def test_bulk_update_triggers_single_rebuild(self):
        store = VectorStore()
        bm25 = BM25Store()

        docs = [
            _make_test_doc(f"Bulk metadata update document {index}", "pending")
            for index in range(3)
        ]
        ids = store.add_chunks(docs)
        self._test_ids = ids

        store.update_metadata(ids, {"review_status": "approved"})
        bm25.rebuild()

        results = bm25.search(
            "Bulk metadata update document",
            review_status="approved",
            top_k=10,
        )
        result_ids = {result.metadata.get("chunk_id") for result in results}
        for chunk_id in ids:
            assert chunk_id in result_ids

    def test_rebuild_clears_empty_collection(self):
        bm25 = BM25Store()
        bm25.rebuild()

        results = bm25.search("arbitrary query", review_status="approved")

        assert results == []
