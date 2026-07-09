import re

import httpx
import pytest
from langchain_core.messages import AIMessage

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.bm25_store import BM25Store
from rag_knowledge.services.rag import NO_KNOWLEDGE_ANSWER, RagChain


@pytest.mark.integration
def test_followup_prefers_uemodelbuilder_in_real_local_kb(tmp_path, monkeypatch):
    def mock_post(*args, **kwargs):
        raise httpx.ConnectError("Mock connection refused")

    monkeypatch.setattr(httpx, "post", mock_post)
    monkeypatch.setenv("ALLOW_LIVE_STORAGE_IN_TESTS", "1")
    monkeypatch.setenv("PATH_DATA_DIR", str(tmp_path / "entity-guard-env-data"))

    Config._instance = None
    RelationalDB._instance = None
    VectorStore._instance = None
    BM25Store._instance = None

    store = VectorStore()
    chroma = store.get_chroma()
    if chroma._collection.count() == 0:
        pytest.skip("Chroma database is empty, skipping entity guard environment regression test.")

    db = RelationalDB()
    modelbuilder = db.get_entity_by_name("ModelBuilder")
    uemodelbuilder = db.get_entity_by_name("UEModelBuilder")
    if not modelbuilder or not uemodelbuilder:
        pytest.skip("ModelBuilder or UEModelBuilder entity is not available in local graph data.")

    if not db.list_links(entity_id=modelbuilder["id"]) or not db.list_links(entity_id=uemodelbuilder["id"]):
        pytest.skip("ModelBuilder or UEModelBuilder has no linked chunks in local graph data.")

    BM25Store().rebuild()

    class MockLLM:
        def invoke(self, messages, *args, **kwargs):
            system_content = messages[0].content if messages else ""
            citations = re.findall(r"\[(\d+)\]\s+\[(?:知识库来源|外部来源)\]", system_content)
            entities = re.findall(r"- ([A-Za-z][A-Za-z0-9_.-]*)\n  - 类型：", system_content)
            parts = []
            if entities:
                parts.append(", ".join(entities))
            parts.extend(f"[{citation}]" for citation in citations)
            return AIMessage(content="Mock answer " + " ".join(parts).strip())

    monkeypatch.setattr(RagChain, "_build_llm", lambda self, model=None: MockLLM())

    chain = RagChain()
    history = [
        {"role": "user", "content": "ModelBuilder如何使用？"},
        {
            "role": "assistant",
            "content": "ModelBuilder 是一个用于建模的工具。[1]",
            "sources": db.list_links(entity_id=modelbuilder["id"])[:1],
        },
    ]

    response = chain.query("ueModelBuilder呢？", history=history)

    assert response["answer"] != NO_KNOWLEDGE_ANSWER
    serialized_sources = " ".join(
        f"{doc.get('content', '')} {doc.get('metadata', {})}"
        for doc in response["source_documents"]
    ).casefold()
    assert "uemodelbuilder" in serialized_sources
