import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from rag_knowledge.api.routes import router
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.knowledge_graph import KnowledgeGraphService
from fastapi import FastAPI

app = FastAPI()
app.include_router(router)
client = TestClient(app)

@pytest.fixture(autouse=True)
def test_setup(isolated_storage):
    _, _, _, data_dir = isolated_storage(
        db_name="test_doc_graph.db",
        data_dir_name="doc-graph-data",
    )
    db = RelationalDB()
    yield db

def test_document_graph_type_parameter(monkeypatch):
    """Test switching between graph_type=product and graph_type=document."""
    db = RelationalDB()
    # Create product backbone entity
    db.create_entity("StampTools", "Product", "StampTools")

    # Stub vector store source for document graph
    mock_vector = MagicMock()
    mock_vector.get_chunk_stats_source.return_value = {
        "ids": ["c1", "c2"],
        "documents": ["content 1", "content 2"],
        "metadatas": [
            {
                "source": "StampTools用户手册.docx",
                "doc_category": "StampTools",
                "section_path": "PipelineBuilder > 数据规范",
            },
            {
                "source": "StampServer用户手册.docx",
                "doc_category": "StampServer",
                "section_path": "服务部署 > 基础环境设置",
            },
        ],
    }
    monkeypatch.setattr(KnowledgeGraphService, "vector_store", property(lambda self: mock_vector))

    # 1. Product graph
    resp_product = client.get("/admin/knowledge_graph/data?graph_type=product")
    assert resp_product.status_code == 200
    data_product = resp_product.json()
    assert any(n["label"] == "StampTools" for n in data_product["nodes"])

    # 2. Document graph
    resp_doc = client.get("/admin/knowledge_graph/data?graph_type=document")
    assert resp_doc.status_code == 200
    data_doc = resp_doc.json()
    doc_labels = {n["label"] for n in data_doc["nodes"]}
    assert "StampTools用户手册.docx" in doc_labels
    assert "PipelineBuilder" in doc_labels
    assert "数据规范" in doc_labels
    assert any(e["label"] == "has_section" for e in data_doc["edges"])

    # 3. Document graph with category filter
    resp_doc_filtered = client.get("/admin/knowledge_graph/data?graph_type=document&doc_category=StampTools")
    assert resp_doc_filtered.status_code == 200
    data_doc_filtered = resp_doc_filtered.json()
    filtered_labels = {n["label"] for n in data_doc_filtered["nodes"]}
    assert "StampTools用户手册.docx" in filtered_labels
    assert "StampServer用户手册.docx" not in filtered_labels
