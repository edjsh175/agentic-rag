# -*- coding: utf-8 -*-
import pytest
import unittest
import httpx
import re
from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.rag import RagChain, NO_KNOWLEDGE_ANSWER
from rag_knowledge.services.bm25_store import BM25Store
from rag_knowledge.services.graph_retrieval import GraphRetriever


@pytest.fixture(autouse=True)
def setup_integration_env(isolated_storage, monkeypatch):
    # Speed up tests by failing LLM calls instantly and falling back to heuristic
    def mock_post(*args, **kwargs):
        raise httpx.ConnectError("Mock connection refused")
    monkeypatch.setattr(httpx, "post", mock_post)

    cfg, _, _, _ = isolated_storage(db_name="regression-integration.db")
    monkeypatch.setattr(cfg.graph_retrieval, "enabled", True)
    
    db = RelationalDB()
    
    # Create entities
    mb = db.create_entity("ModelBuilder", "Tool", doc_category="ModelBuilderDoc")
    uemb = db.create_entity("UEModelBuilder", "Tool", doc_category="UEModelBuilderDoc")
    omb = db.create_entity("ObliqueModelBuilder", "Tool", doc_category="ObliqueModelBuilderDoc")
    pb = db.create_entity("PipelineBuilder", "Tool", doc_category="PipelineBuilderDoc")
    
    # Approve entities and build links
    db.create_link(mb, "chunk-mb", evidence_text="ModelBuilder 使用指南")
    db.create_link(uemb, "chunk-uemb", evidence_text="UEModelBuilder 使用指南")
    db.create_link(omb, "chunk-omb", evidence_text="ObliqueModelBuilder 使用指南")
    db.create_link(pb, "chunk-pb", evidence_text="PipelineBuilder 使用指南")
    
    # Establish different_from relations to active exclusion guard logic
    db.create_relation(mb, "different_from", uemb, review_status="approved")
    db.create_relation(mb, "different_from", omb, review_status="approved")

    # Mock Vector Store and Chroma Collection
    class MockChromaCollection:
        def get(self, ids, include):
            ret_ids = []
            ret_docs = []
            ret_metas = []
            chunks = {
                "chunk-mb": ("ModelBuilder content", "ModelBuilderDoc"),
                "chunk-uemb": ("UEModelBuilder content", "UEModelBuilderDoc"),
                "chunk-omb": ("ObliqueModelBuilder content", "ObliqueModelBuilderDoc"),
                "chunk-pb": ("PipelineBuilder content", "PipelineBuilderDoc"),
            }
            for cid in ids:
                if cid in chunks:
                    content, category = chunks[cid]
                    ret_ids.append(cid)
                    ret_docs.append(content)
                    ret_metas.append({
                        "chunk_id": cid,
                        "source": f"{cid}.md",
                        "doc_category": category,
                        "review_status": "approved"
                    })
            return {
                "ids": ret_ids,
                "documents": ret_docs,
                "metadatas": ret_metas
            }
            
    class MockChroma:
        def __init__(self):
            self._collection = MockChromaCollection()
            
        def get(self, *args, **kwargs):
            return {"ids": [], "documents": [], "metadatas": []}
            
        def as_retriever(self, *args, **kwargs):
            class MockRetriever:
                def invoke(self, query):
                    docs = []
                    
                    from rag_knowledge.services.query_entity_guard import extract_explicit_entities
                    entities_in_query = [e.casefold() for e in extract_explicit_entities(query)]
                    
                    if "uemodelbuilder" in entities_in_query:
                        docs.append(Document(page_content="UEModelBuilder content", metadata={"chunk_id": "chunk-uemb", "source": "chunk-uemb.md", "doc_category": "UEModelBuilderDoc", "review_status": "approved"}))
                    if "obliquemodelbuilder" in entities_in_query:
                        docs.append(Document(page_content="ObliqueModelBuilder content", metadata={"chunk_id": "chunk-omb", "source": "chunk-omb.md", "doc_category": "ObliqueModelBuilderDoc", "review_status": "approved"}))
                    if "pipelinebuilder" in entities_in_query:
                        docs.append(Document(page_content="PipelineBuilder content", metadata={"chunk_id": "chunk-pb", "source": "chunk-pb.md", "doc_category": "PipelineBuilderDoc", "review_status": "approved"}))
                    if "modelbuilder" in entities_in_query and not any(x in entities_in_query for x in ["uemodelbuilder", "obliquemodelbuilder"]):
                        docs.append(Document(page_content="ModelBuilder content", metadata={"chunk_id": "chunk-mb", "source": "chunk-mb.md", "doc_category": "ModelBuilderDoc", "review_status": "approved"}))
                    return docs
                def get_relevant_documents(self, query):
                    return self.invoke(query)
            return MockRetriever()
            
    mock_vs = type("Store", (), {
        "get_chroma": lambda self: MockChroma()
    })()
    
    monkeypatch.setattr(VectorStore, "_instance", mock_vs)
    
    # Rebuild BM25 for integration
    BM25Store().rebuild()
    
    # Mock LLM response
    class MockLLM:
        def invoke(self, messages, *args, **kwargs):
            system_content = messages[0].content if messages else ""
            cids = re.findall(r"\[(\d+)\]\s+\[(?:知识库来源|外部来源)\]", system_content)
            entities = re.findall(r"- ([A-Za-z][A-Za-z0-9_.-]*)\n  - 类型：", system_content)
            content = "Mock Answer referencing components: "
            if entities:
                content += ", ".join(entities) + " "
            content += " ".join(f"[{cid}]" for cid in cids)
            return AIMessage(content=content)
            
    monkeypatch.setattr(RagChain, "_build_llm", lambda self, model=None: MockLLM())


def test_scenario_1_model_builder_only():
    chain = RagChain()
    resp = chain.query("ModelBuilder如何使用？")
    assert resp["answer"] != NO_KNOWLEDGE_ANSWER
    assert any(doc.get("metadata", {}).get("chunk_id") == "chunk-mb" for doc in resp["source_documents"])
    assert "ModelBuilder" in resp["answer"]
    assert "UEModelBuilder" not in resp["answer"]


def test_scenario_2_followup_uemb():
    chain = RagChain()
    history = [
        {"role": "user", "content": "ModelBuilder如何使用？"},
        {"role": "assistant", "content": "ModelBuilder 是一个用于建模的工具。", "sources": [{"file_name": "chunk-mb.md", "chunk_id": "chunk-mb", "section_title": "介绍"}]}
    ]
    resp = chain.query("ueModelBuilder呢？", history=history)
    assert resp["answer"] != NO_KNOWLEDGE_ANSWER
    assert any(doc.get("metadata", {}).get("chunk_id") == "chunk-uemb" for doc in resp["source_documents"])
    assert "UEModelBuilder" in resp["answer"]
    assert not any(doc.get("metadata", {}).get("chunk_id") == "chunk-mb" for doc in resp["source_documents"])


def test_scenario_3_followup_engineering_steps():
    chain = RagChain()
    history = [
        {"role": "user", "content": "ModelBuilder如何使用？"},
        {"role": "assistant", "content": "ModelBuilder 是一个用于建模的工具。", "sources": [{"file_name": "chunk-mb.md", "chunk_id": "chunk-mb", "section_title": "介绍"}]}
    ]
    resp = chain.query("继续说一下工程设置", history=history)
    assert resp["answer"] != NO_KNOWLEDGE_ANSWER
    assert any(doc.get("metadata", {}).get("chunk_id") == "chunk-mb" for doc in resp["source_documents"])
    assert "ModelBuilder" in resp["answer"]


def test_scenario_4_uemb_direct():
    chain = RagChain()
    resp = chain.query("UEModelBuilder如何使用？")
    assert resp["answer"] != NO_KNOWLEDGE_ANSWER
    assert any(doc.get("metadata", {}).get("chunk_id") == "chunk-uemb" for doc in resp["source_documents"])
    assert "UEModelBuilder" in resp["answer"]


def test_scenario_5_omb_followup():
    chain = RagChain()
    history = [
        {"role": "user", "content": "ModelBuilder如何使用？"},
        {"role": "assistant", "content": "ModelBuilder 是一个用于建模的工具。", "sources": [{"file_name": "chunk-mb.md", "chunk_id": "chunk-mb"}]}
    ]
    resp = chain.query("obliqueModelBuilder\u5462\uff1f", history=history)
    assert resp["answer"] != NO_KNOWLEDGE_ANSWER
    assert any(doc.get("metadata", {}).get("chunk_id") == "chunk-omb" for doc in resp["source_documents"])
    assert "ObliqueModelBuilder" in resp["answer"]
    assert not any(doc.get("metadata", {}).get("chunk_id") == "chunk-mb" for doc in resp["source_documents"])


def test_scenario_6_pb_direct():
    chain = RagChain()
    resp = chain.query("PipelineBuilder 值域映射怎么设置？")
    assert resp["answer"] != NO_KNOWLEDGE_ANSWER
    assert any(doc.get("metadata", {}).get("chunk_id") == "chunk-pb" for doc in resp["source_documents"])
    assert "PipelineBuilder" in resp["answer"]


def test_scenario_7_multi_entity_comparison():
    chain = RagChain()
    resp = chain.query("ModelBuilder 和 UEModelBuilder 有什么区别？")
    assert resp["answer"] != NO_KNOWLEDGE_ANSWER
    retrieved_chunk_ids = {doc.get("metadata", {}).get("chunk_id") for doc in resp["source_documents"]}
    assert "chunk-mb" in retrieved_chunk_ids
    assert "chunk-uemb" in retrieved_chunk_ids


def test_scenario_8_multi_entity_uemb_omb():
    chain = RagChain()
    resp = chain.query("UEModelBuilder 和 ObliqueModelBuilder 的区别？")
    assert resp["answer"] != NO_KNOWLEDGE_ANSWER
    retrieved_chunk_ids = {doc.get("metadata", {}).get("chunk_id") for doc in resp["source_documents"]}
    assert "chunk-uemb" in retrieved_chunk_ids
    assert "chunk-omb" in retrieved_chunk_ids


def test_scenario_9_case_insensitivity():
    chain = RagChain()
    resp = chain.query("ueModelBuilder 数据设置")
    assert resp["answer"] != NO_KNOWLEDGE_ANSWER
    assert any(doc.get("metadata", {}).get("chunk_id") == "chunk-uemb" for doc in resp["source_documents"])
