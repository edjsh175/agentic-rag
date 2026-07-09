import json
import pytest
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.api.routes import router
from rag_knowledge.models.api import EntityTypeEnum, RelationTypeEnum, LinkTypeEnum, DocCategoryEnum

# Create test FastAPI app
app = FastAPI()
app.include_router(router)
client = TestClient(app)


@pytest.fixture(autouse=True)
def test_setup(isolated_storage, monkeypatch):
    """Isolate DB and file_index for each test case."""
    _, _, _, data_dir = isolated_storage(
        db_name="test_rag_relational.db",
        data_dir_name="kg-data",
    )
    
    # 3. Create dummy file_index.json
    file_index_content = {
        "version": 1,
        "files": {
            "hash-1": {
                "file_path": "doc1.md",
                "file_name": "doc1.md",
                "kb_name": "文章附件",
                "last_modified": "2026-07-01T10:00:00",
                "added_at": "2026-07-02T10:00:00",
                "chunk_ids": ["chunk-1", "chunk-2"]
            }
        }
    }
    file_index_file = data_dir / "file_index.json"
    file_index_file.write_text(json.dumps(file_index_content, ensure_ascii=False), encoding="utf-8")
    
    # 4. Reset RelationalDB singleton to force connection initialization on the new path
    db = RelationalDB()
    
    yield db


@pytest.fixture
def mock_vector_store(monkeypatch):
    """Stub VectorStore to mock ChromaDB's get method."""
    mock_chroma = MagicMock()
    mock_collection = MagicMock()
    mock_chroma._collection = mock_collection
    
    # Default return value for collection.get
    mock_collection.get.return_value = {
        "ids": ["chunk-1", "chunk-2"],
        "documents": ["This is chunk 1 content", "This is chunk 2 content"],
        "metadatas": [
            {"source": "doc1.md", "section_title": "Section A"},
            {"source": "doc1.md", "section_title": "Section B"}
        ]
    }
    
    monkeypatch.setattr(VectorStore, "get_chroma", lambda self: mock_chroma)
    return mock_collection


def test_sqlite_foreign_key_cascade(test_setup):
    """Verify that SQLite foreign_keys=ON is enabled and cascade deletes relations & links."""
    db = test_setup
    
    # Verify foreign_keys is ON
    with db._get_conn() as conn:
        fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk_on == 1

    # Create entities
    e1_id = db.create_entity("Entity A", "功能模块", "StampServer")
    e2_id = db.create_entity("Entity B", "数据文件", "StampServer")
    
    # Create relation & link
    r_id = db.create_relation(e1_id, e2_id, "依赖")
    assert r_id is not None
    
    link_id = db.create_link(e1_id, "chunk-1", "主要描述")
    assert link_id is not None

    # Deleting entity A should cascade delete relation and link
    deleted = db.delete_entity(e1_id)
    assert deleted is True

    # Check relation is gone
    relations = db.list_relations()
    assert len(relations) == 0

    # Check link is gone
    links = db.list_links(entity_id=e1_id)
    assert len(links) == 0


def test_entity_create_happy_and_duplicate(test_setup):
    """Test entity creation and duplicate name handling."""
    # 1. Happy path creation
    payload = {
        "name": "User Module",
        "entity_type": "功能模块",
        "doc_category": "StampServer"
    }
    resp = client.post("/admin/knowledge_graph/entities", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "User Module"
    assert data["entity_type"] == "功能模块"
    assert data["doc_category"] == "StampServer"
    assert data["created"] is True
    assert "id" in data

    entity_id = data["id"]

    # 2. Duplicate creation returns 200 OK with created=False
    resp_dup = client.post("/admin/knowledge_graph/entities", json=payload)
    assert resp_dup.status_code == 200
    data_dup = resp_dup.json()
    assert data_dup["id"] == entity_id
    assert data_dup["created"] is False


def test_entity_create_validation_errors(test_setup):
    """Test validation errors for entity creation."""
    # 1. Invalid entity_type
    payload = {
        "name": "Invalid Type Entity",
        "entity_type": "非法类型",
        "doc_category": "StampServer"
    }
    resp = client.post("/admin/knowledge_graph/entities", json=payload)
    assert resp.status_code == 422

    # 2. Invalid doc_category
    payload = {
        "name": "Invalid Category Entity",
        "entity_type": "功能模块",
        "doc_category": "非法分类"
    }
    resp = client.post("/admin/knowledge_graph/entities", json=payload)
    assert resp.status_code == 422

    # 3. Empty name
    payload = {
        "name": "",
        "entity_type": "功能模块",
    }
    resp = client.post("/admin/knowledge_graph/entities", json=payload)
    assert resp.status_code == 422


def test_entity_update(test_setup):
    """Test PATCH endpoint for entity updates."""
    # Create entity
    db = test_setup
    eid = db.create_entity("Old Name", "功能模块", "StampServer")

    # 1. Update everything
    payload = {
        "name": "New Name",
        "entity_type": "数据文件",
        "doc_category": "StampTools"
    }
    resp = client.patch(f"/admin/knowledge_graph/entities/{eid}", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "New Name"
    assert data["entity_type"] == "数据文件"
    assert data["doc_category"] == "StampTools"

    # 2. Update with empty body
    resp_empty = client.patch(f"/admin/knowledge_graph/entities/{eid}", json={})
    assert resp_empty.status_code == 400
    assert "至少提供" in resp_empty.json()["detail"]

    # 3. Update non-existent entity
    resp_missing = client.patch("/admin/knowledge_graph/entities/missing-id", json={"name": "Test"})
    assert resp_missing.status_code == 404

    # 4. Name conflict
    conflict_id = db.create_entity("Conflict Name", "功能模块", "StampServer")
    resp_conflict = client.patch(f"/admin/knowledge_graph/entities/{eid}", json={"name": "Conflict Name"})
    assert resp_conflict.status_code == 409
    assert "already exists" in resp_conflict.json()["detail"]


def test_entity_delete(test_setup):
    """Test DELETE endpoint for entities is idempotent."""
    db = test_setup
    eid = db.create_entity("Delete Me", "功能模块", "StampServer")

    # First delete
    resp1 = client.delete(f"/admin/knowledge_graph/entities/{eid}")
    assert resp1.status_code == 200
    assert resp1.json()["success"] is True

    # Second delete (idempotent)
    resp2 = client.delete(f"/admin/knowledge_graph/entities/{eid}")
    assert resp2.status_code == 200
    assert resp2.json()["success"] is True


def test_relation_create(test_setup):
    """Test creation of relations, duplicates, self-loops, and missing entities."""
    db = test_setup
    e1_id = db.create_entity("Node A", "功能模块", "StampServer")
    e2_id = db.create_entity("Node B", "功能模块", "StampServer")

    # 1. Happy path
    payload = {
        "source_id": e1_id,
        "target_id": e2_id,
        "relation_type": "依赖"
    }
    resp = client.post("/admin/knowledge_graph/relations", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["source_id"] == e1_id
    assert data["target_id"] == e2_id
    assert data["relation_type"] == "依赖"
    assert data["created"] is True

    # 2. Duplicate relation returns 200 OK with created=False
    resp_dup = client.post("/admin/knowledge_graph/relations", json=payload)
    assert resp_dup.status_code == 200
    assert resp_dup.json()["created"] is False

    # 3. Self loop is rejected
    payload_self = {
        "source_id": e1_id,
        "target_id": e1_id,
        "relation_type": "依赖"
    }
    resp_self = client.post("/admin/knowledge_graph/relations", json=payload_self)
    assert resp_self.status_code == 400
    assert "Self-loop" in resp_self.json()["detail"]

    # 4. Missing entity returns 404
    payload_missing = {
        "source_id": e1_id,
        "target_id": "missing-entity-id",
        "relation_type": "依赖"
    }
    resp_missing = client.post("/admin/knowledge_graph/relations", json=payload_missing)
    assert resp_missing.status_code == 404


def test_relation_delete(test_setup):
    """Test relation deletion is idempotent."""
    db = test_setup
    e1_id = db.create_entity("Node A", "功能模块", "StampServer")
    e2_id = db.create_entity("Node B", "功能模块", "StampServer")
    r_id = db.create_relation(e1_id, e2_id, "依赖")

    # Delete
    resp1 = client.delete(f"/admin/knowledge_graph/relations/{r_id}")
    assert resp1.status_code == 200
    assert resp1.json()["success"] is True

    # Idempotent delete
    resp2 = client.delete(f"/admin/knowledge_graph/relations/{r_id}")
    assert resp2.status_code == 200
    assert resp2.json()["success"] is True


def test_entity_chunk_link_crud_and_list(test_setup, mock_vector_store):
    """Test linking/unlinking entities to chunks, and listing chunks for an entity."""
    db = test_setup
    eid = db.create_entity("Linked Entity", "功能模块", "StampServer")

    # 1. Link entity to chunk-1 (happy path)
    payload = {
        "chunk_id": "chunk-1",
        "link_type": "主要描述"
    }
    resp = client.post(f"/admin/knowledge_graph/entities/{eid}/chunks", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["entity_id"] == eid
    assert data["chunk_id"] == "chunk-1"
    assert data["link_type"] == "主要描述"
    assert data["created"] is True

    # 2. Duplicate link returns 200 OK with created=False
    resp_dup = client.post(f"/admin/knowledge_graph/entities/{eid}/chunks", json=payload)
    assert resp_dup.status_code == 200
    assert resp_dup.json()["created"] is False

    # 3. Missing entity returns 404
    resp_missing_entity = client.post("/admin/knowledge_graph/entities/missing-entity/chunks", json=payload)
    assert resp_missing_entity.status_code == 404

    # 4. Missing chunk (not in file_index.json) returns 404
    resp_missing_chunk = client.post(f"/admin/knowledge_graph/entities/{eid}/chunks", json={
        "chunk_id": "missing-chunk-id",
        "link_type": "主要描述"
    })
    assert resp_missing_chunk.status_code == 404

    # 5. List chunks associated with entity
    resp_list = client.get(f"/admin/knowledge_graph/entities/{eid}/chunks")
    assert resp_list.status_code == 200
    list_data = resp_list.json()
    assert len(list_data) == 1
    assert list_data[0]["chunk_id"] == "chunk-1"
    assert list_data[0]["file_name"] == "doc1.md"
    assert list_data[0]["section_title"] == "Section A"
    assert list_data[0]["content"] == "This is chunk 1 content"

    # 6. Unlink entity and chunk
    resp_unlink = client.delete(f"/admin/knowledge_graph/entities/{eid}/chunks/chunk-1")
    assert resp_unlink.status_code == 200
    assert resp_unlink.json()["success"] is True

    # List chunks again (should be empty)
    resp_list_empty = client.get(f"/admin/knowledge_graph/entities/{eid}/chunks")
    assert resp_list_empty.status_code == 200
    assert len(resp_list_empty.json()) == 0


def test_get_graph_data(test_setup):
    """Test fetching full and filtered graph data, and ensuring no orphan edges."""
    db = test_setup
    
    # Create nodes with different categories
    e1_id = db.create_entity("Entity Server 1", "功能模块", "StampServer")
    e2_id = db.create_entity("Entity Server 2", "功能模块", "StampServer")
    e3_id = db.create_entity("Entity Tools", "功能模块", "StampTools")

    # Create relations
    db.create_relation(e1_id, e2_id, "依赖")
    db.create_relation(e1_id, e3_id, "包含")

    # 1. Fetch full graph data
    resp_all = client.get("/admin/knowledge_graph/data")
    assert resp_all.status_code == 200
    data_all = resp_all.json()
    assert len(data_all["nodes"]) == 3
    assert len(data_all["edges"]) == 2

    # 2. Fetch filtered graph data (doc_category=StampServer)
    resp_filtered = client.get("/admin/knowledge_graph/data?doc_category=StampServer")
    assert resp_filtered.status_code == 200
    data_filtered = resp_filtered.json()
    assert len(data_filtered["nodes"]) == 2
    assert all(n["doc_category"] == "StampServer" for n in data_filtered["nodes"])
    # Edge to e3 (StampTools) should be removed to prevent orphan edges
    assert len(data_filtered["edges"]) == 1
    assert data_filtered["edges"][0]["source"] == e1_id
    assert data_filtered["edges"][0]["target"] == e2_id
