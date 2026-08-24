import re

import httpx
import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import CachedOllamaEmbeddings, VectorStore
from rag_knowledge.services.bm25_store import BM25Store
from rag_knowledge.services.rag import NO_KNOWLEDGE_ANSWER, RagChain
from rag_knowledge.services.helper_grounding_reviewer import HelperGroundingReviewer


def _fake_vector(text: str) -> list[float]:
    lowered = (text or "").casefold()
    return [
        5.0 if "uemodelbuilder" in lowered else 0.0,
        4.0 if "obliquemodelbuilder" in lowered else 0.0,
        3.0 if "modelbuilder" in lowered and "uemodelbuilder" not in lowered and "obliquemodelbuilder" not in lowered else 0.0,
        2.0 if "builder" in lowered else 0.0,
        float(len(lowered) % 7),
    ]


def _extract_context_sources(system_content: str) -> list[tuple[str, str]]:
    return re.findall(r"\[(\d+)\]\s+\[[^\]]+\]\s+文件:\s*([^\s|]+)", system_content)


def _extract_linked_entities(system_content: str) -> list[str]:
    entities: list[str] = []
    for line in system_content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        name = stripped[2:].strip()
        if not name or name.startswith(("类型：", "来源：", "约束：")):
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", name):
            entities.append(name)
    return entities


@pytest.fixture(autouse=True)
def setup_real_chain_env(isolated_storage, monkeypatch):
    def mock_post(*args, **kwargs):
        raise httpx.ConnectError("Mock connection refused")

    monkeypatch.setattr(httpx, "post", mock_post)
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

    cfg, _, _, _ = isolated_storage(
        db_name="entity-guard-real.db",
        chroma_name="entity-guard-real-chroma",
    )
    monkeypatch.setattr(cfg.graph_retrieval, "enabled", True)

    store = VectorStore()
    db = RelationalDB()

    mb = db.create_entity("ModelBuilder", "Tool", doc_category="ModelBuilderDoc")
    uemb = db.create_entity("UEModelBuilder", "Tool", doc_category="UEModelBuilderDoc")
    omb = db.create_entity("ObliqueModelBuilder", "Tool", doc_category="ObliqueModelBuilderDoc")

    docs = [
        Document(
            page_content="ModelBuilder usage guide and workflow.",
            metadata={
                "chunk_uid": "chk_modelbuilder",
                "source": "modelbuilder.md",
                "doc_category": "ModelBuilderDoc",
                "review_status": "approved",
            },
        ),
        Document(
            page_content="UEModelBuilder usage guide and data settings.",
            metadata={
                "chunk_uid": "chk_uemodelbuilder",
                "source": "uemodelbuilder.md",
                "doc_category": "UEModelBuilderDoc",
                "review_status": "approved",
            },
        ),
        Document(
            page_content="ObliqueModelBuilder usage guide.",
            metadata={
                "chunk_uid": "chk_obliquemodelbuilder",
                "source": "obliquemodelbuilder.md",
                "doc_category": "ObliqueModelBuilderDoc",
                "review_status": "approved",
            },
        ),
    ]
    chunk_ids = store.add_chunks(docs)
    db.create_link(mb, chunk_ids[0], evidence_text="ModelBuilder usage guide", source="modelbuilder.md")
    db.create_link(uemb, chunk_ids[1], evidence_text="UEModelBuilder usage guide", source="uemodelbuilder.md")
    db.create_link(omb, chunk_ids[2], evidence_text="ObliqueModelBuilder usage guide", source="obliquemodelbuilder.md")

    BM25Store().rebuild()

    class MockLLM:
        def invoke(self, messages, *args, **kwargs):
            system_content = messages[0].content if messages else ""
            citations_by_file = _extract_context_sources(system_content)
            entities = _extract_linked_entities(system_content)
            parts = []
            if entities:
                parts.append(", ".join(entities))
            target_files = {f"{entity.casefold()}.md" for entity in entities}
            selected_citations = [
                citation_id
                for citation_id, file_name in citations_by_file
                if file_name.casefold() in target_files
            ]
            if not selected_citations and citations_by_file:
                selected_citations = [citations_by_file[0][0]]
            parts.extend(f"[{citation}]" for citation in selected_citations)
            return AIMessage(content="Mock answer " + " ".join(parts).strip())

    monkeypatch.setattr(RagChain, "_build_llm", lambda self, model=None: MockLLM())
    pass_reviewer = HelperGroundingReviewer(lambda _messages: """{
        "verdict": "PASS",
        "coverage": "FULL",
        "summary": "entity guard real-KB fixture pass",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "fixture answer",
            "claim_type": "knowledge_claim",
            "evidence_ids": [1],
            "status": "supported",
            "reason": "fixture isolates retrieval/entity-guard behavior"
        }],
        "rewrite_actions": []
    }""")
    monkeypatch.setattr(RagChain, "_helper_grounding_reviewer", lambda self: pass_reviewer)


def test_real_vectorstore_bm25_and_rag_chain_keep_followup_on_uemodelbuilder():
    chain = RagChain()
    history = [
        {"role": "user", "content": "ModelBuilder如何使用？"},
        {
            "role": "assistant",
            "content": "ModelBuilder 是一个用于建模的工具。[1]",
            "sources": [{"file_name": "modelbuilder.md", "chunk_id": "unused", "section_title": "介绍"}],
        },
    ]

    response = chain.query("ueModelBuilder呢？", history=history)

    assert response["answer"] != NO_KNOWLEDGE_ANSWER
    chunk_ids = {doc.get("metadata", {}).get("chunk_id") for doc in response["source_documents"]}
    file_names = {doc.get("metadata", {}).get("file_name") for doc in response["source_documents"]}
    assert "uemodelbuilder.md" in file_names
    assert "modelbuilder.md" not in file_names
    assert len(chunk_ids) >= 1
