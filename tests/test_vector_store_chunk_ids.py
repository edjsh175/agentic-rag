from langchain_core.documents import Document
import pytest

from rag_knowledge.repository.vector_store import VectorStore


class CapturingStore:
    def __init__(self):
        self.documents = []
        self.ids = []

    def add_documents(self, documents, ids):
        self.documents = documents
        self.ids = ids


def _vector_store_with(capture):
    store = object.__new__(VectorStore)
    store._get_store = lambda: capture
    return store


def test_add_chunks_uses_chunk_uid_as_chroma_and_public_chunk_id():
    capture = CapturingStore()
    store = _vector_store_with(capture)
    chunks = [
        Document(
            page_content="first",
            metadata={"chunk_uid": "chk_first", "next_chunk_id": "chk_second"},
        ),
        Document(
            page_content="second",
            metadata={"chunk_uid": "chk_second", "prev_chunk_id": "chk_first"},
        ),
    ]

    ids = store.add_chunks(chunks)

    assert ids == ["chk_first", "chk_second"]
    assert capture.ids == ids
    assert [doc.metadata["chunk_id"] for doc in capture.documents] == ids
    assert [doc.metadata["chunk_uid"] for doc in capture.documents] == ids
    assert capture.documents[0].metadata["next_chunk_id"] == "chk_second"
    assert capture.documents[1].metadata["prev_chunk_id"] == "chk_first"


def test_add_chunks_rolls_back_written_batches_on_later_failure(monkeypatch):
    class FlakyStore:
        def __init__(self):
            self.added = []
            self.deleted = []
            self.calls = 0

        def add_documents(self, documents, ids):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("embed batch failed")
            self.added.extend(ids)

        def delete(self, ids):
            self.deleted.extend(ids)

    monkeypatch.setattr(
        "rag_knowledge.repository.vector_store._EMBED_ADD_BATCH",
        1,
    )
    flaky = FlakyStore()
    store = _vector_store_with(flaky)
    chunks = [
        Document(page_content="one", metadata={"chunk_uid": "chk_1"}),
        Document(page_content="two", metadata={"chunk_uid": "chk_2"}),
    ]

    with pytest.raises(RuntimeError, match="embed batch failed"):
        store.add_chunks(chunks)

    assert flaky.added == ["chk_1"]
    assert flaky.deleted == ["chk_1"]


def test_add_chunks_rejects_missing_chunk_uid_before_store_access():
    store = object.__new__(VectorStore)
    store._get_store = lambda: pytest.fail("store must not be opened")

    with pytest.raises(ValueError, match="chunk_uid is required"):
        store.add_chunks([Document(page_content="missing", metadata={})])


def test_add_chunks_rejects_duplicate_chunk_uid_before_store_access():
    store = object.__new__(VectorStore)
    store._get_store = lambda: pytest.fail("store must not be opened")
    chunks = [
        Document(page_content="one", metadata={"chunk_uid": "chk_same"}),
        Document(page_content="two", metadata={"chunk_uid": "chk_same"}),
    ]

    with pytest.raises(ValueError, match="duplicate chunk_uid"):
        store.add_chunks(chunks)


def test_cached_embeddings_reject_empty_vectors():
    from rag_knowledge.repository.vector_store import _require_nonempty_vector

    with pytest.raises(ValueError, match="empty vector"):
        _require_nonempty_vector([])
    with pytest.raises(ValueError, match="empty vector"):
        _require_nonempty_vector(None)
    assert _require_nonempty_vector([0.1, 0.2]) == [0.1, 0.2]
