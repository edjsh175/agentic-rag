from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_knowledge.api import routes
from rag_knowledge.models.api import RebuildRequest


def make_client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_audit_consistency_route_delegates_to_service(monkeypatch):
    expected = {"summary": {"consistent": True}}

    class FakeConsistencyService:
        def audit(self, *, source=None):
            assert source == "word/StampTools用户手册.docx"
            return expected

    monkeypatch.setattr(routes, "KnowledgeBaseConsistencyService", lambda: FakeConsistencyService())

    result = routes.audit_consistency(source="word/StampTools用户手册.docx")

    assert result == expected


def test_rebuild_rejects_get():
    assert make_client().get("/rebuild").status_code == 405


def test_rebuild_requires_exact_confirmation():
    response = make_client().post("/rebuild", json={"confirmation": "yes"})
    assert response.status_code == 422


def test_rebuild_returns_503_without_scanner(monkeypatch):
    monkeypatch.setattr(routes, "_scanner", None)
    response = make_client().post(
        "/rebuild",
        json={"confirmation": "REBUILD_KNOWLEDGE_BASE"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "扫描器未初始化，未执行重建"


def test_rebuild_route_uses_rebuild_coordinator(monkeypatch):
    expected = {"message": "知识库已重建", "new_files": 1, "skipped_files": 0, "errors": 0}
    sentinel_cfg = SimpleNamespace(data_dir="data")
    sentinel_scanner = object()
    sentinel_store = object()
    sentinel_consistency = object()

    routes._cfg = sentinel_cfg
    routes._scanner = sentinel_scanner

    monkeypatch.setattr(routes, "VectorStore", lambda: sentinel_store)
    monkeypatch.setattr(routes, "KnowledgeBaseConsistencyService", lambda: sentinel_consistency)

    class FakeCoordinator:
        def __init__(self, **kwargs):
            assert kwargs["cfg"] is sentinel_cfg
            assert kwargs["store"] is sentinel_store
            assert kwargs["scanner"] is sentinel_scanner
            assert kwargs["consistency_service"] is sentinel_consistency

        def run(self):
            return expected

    monkeypatch.setattr(routes, "RebuildCoordinator", FakeCoordinator)

    result = routes.rebuild_knowledge(RebuildRequest(confirmation="REBUILD_KNOWLEDGE_BASE"))

    assert result == expected
