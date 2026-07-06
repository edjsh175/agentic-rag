import json
import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from rag_knowledge.services.chunk_admin import (
    DOC_CATEGORIES,
    ChunkAdminService,
    RetrievalRefreshError,
    classify_doc_category,
    migrate_doc_categories,
)


class StoreStub:
    def __init__(self, source):
        self.source = source
        self.updates = []

    def get_chunk_stats_source(self):
        return self.source

    def update_metadata(self, ids, metadata):
        existing = set(self.source.get("ids", []))
        matched = [chunk_id for chunk_id in ids if chunk_id in existing]
        self.updates.append((matched, metadata))
        for index, chunk_id in enumerate(self.source.get("ids", [])):
            if chunk_id in matched:
                self.source["metadatas"][index].update(metadata)
        return len(matched)


@pytest.fixture
def file_index(tmp_path: Path) -> Path:
    path = tmp_path / "file_index.json"
    path.write_text(json.dumps({"version": 1, "files": {
        "hash-a": {"file_path": "word/StampServer用户手册.docx", "file_name": "StampServer用户手册.docx", "kb_name": "文章附件", "last_modified": "2026-06-30T10:00:00", "added_at": "2026-07-01T10:00:00", "chunk_ids": ["c1", "c2"]},
        "hash-b": {"file_path": "已发布文章/blog.md", "file_name": "blog.md", "kb_name": "已发布文章", "last_modified": "2026-07-01T09:00:00", "added_at": "2026-07-02T10:00:00", "chunk_ids": ["c3"]},
    }}, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def source():
    return {"ids": ["c1", "c2", "c3"], "documents": ["待审核内容一" * 20, "已通过内容", "博客待审核内容"], "metadatas": [
        {"source": "StampServer用户手册.docx", "review_status": "pending", "doc_category": "StampServer", "section_title": "安装", "page_number": 0, "kb_name": "文章附件"},
        {"source": "StampServer用户手册.docx", "review_status": "approved", "doc_category": "StampServer", "section_title": "启动", "kb_name": "文章附件"},
        {"source": "blog.md", "review_status": "pending", "doc_category": "博客", "section_title": "摘要", "kb_name": "已发布文章"},
    ]}


def build_service(source, file_index, rebuild=None, clear=None):
    return ChunkAdminService(store=StoreStub(source), file_index_path=file_index,
                             rebuild_bm25=rebuild or MagicMock(), clear_cache=clear or MagicMock())


def test_list_defaults_to_pending_and_enriches_fields(source, file_index):
    result = build_service(source, file_index).list_chunks()
    assert result.total == 2
    assert [item.chunk_id for item in result.items] == ["c1", "c3"]
    assert result.items[0].page_label == "1"
    assert result.items[0].indexed_at == "2026-07-01T10:00:00"
    assert len(result.items[0].content_preview) == 80


def test_list_filters_category_filename_and_paginates(source, file_index):
    result = build_service(source, file_index).list_chunks(review_status="all", doc_category="StampServer", filename="server", page=2, page_size=1)
    assert result.total == 2
    assert result.total_pages == 2
    assert [item.chunk_id for item in result.items] == ["c2"]


def test_list_enriches_source_info_from_index_metadata_and_front_matter(source, file_index, tmp_path):
    blog_dir = tmp_path / "已发布文章"
    blog_dir.mkdir()
    (blog_dir / "blog.md").write_text(
        "---\n"
        "title: 博客标题\n"
        "source: https://example.com/post\n"
        "author: 张三\n"
        "platform: CSDN\n"
        "publish_date: 2026-07-01\n"
        "crawled_at: 2026-07-02 09:00:00\n"
        "---\n正文",
        encoding="utf-8",
    )
    service = build_service(source, file_index)
    service._watch_dir = tmp_path

    result = service.list_chunks()
    by_id = {item.chunk_id: item for item in result.items}

    assert by_id["c1"].file_path == "word/StampServer用户手册.docx"
    assert by_id["c1"].last_modified == "2026-06-30T10:00:00"
    assert by_id["c3"].title == "博客标题"
    assert by_id["c3"].source_url == "https://example.com/post"
    assert by_id["c3"].author == "张三"
    assert by_id["c3"].platform == "CSDN"
    assert by_id["c3"].publish_date == "2026-07-01"
    assert by_id["c3"].crawled_at == "2026-07-02 09:00:00"


def test_update_chunk_refreshes_retrieval_once(source, file_index):
    rebuild, clear = MagicMock(), MagicMock()
    service = build_service(source, file_index, rebuild, clear)
    assert service.update_chunk("c1", {"review_status": "approved", "section_title": "新标题"}) == 1
    rebuild.assert_called_once_with()
    clear.assert_called_once_with()


def test_update_chunk_clears_cache_when_rebuild_fails(source, file_index):
    rebuild, clear = MagicMock(side_effect=RuntimeError("broken")), MagicMock()
    with pytest.raises(RuntimeError, match="broken"):
        build_service(source, file_index, rebuild, clear).update_chunk("c1", {"doc_category": "基础环境"})
    clear.assert_called_once_with()


def test_batch_review_deduplicates_and_refreshes_once(source, file_index):
    rebuild, clear = MagicMock(), MagicMock()
    result = build_service(source, file_index, rebuild, clear).batch_review(["c1", "c1", "missing", "c3"], "rejected")
    assert (result.requested_chunks, result.updated_chunks) == (3, 2)
    rebuild.assert_called_once_with()
    clear.assert_called_once_with()


@pytest.mark.parametrize(("file_path", "file_name", "kb_name", "expected"), [
    ("已发布文章/a.md", "a.md", "已发布文章", "博客"),
    ("word/a.docx", "StampTools用户手册.docx", "文章附件", "StampTools"),
    ("upload/a.docx", "陕西耕地保护系统问题.docx", "文章附件", "耕地保护"),
    ("word/a.doc", "东方通用户手册_Rocky9.doc", "文章附件", "基础环境"),
    ("pdf/hash.pdf", "hash.pdf", "文章附件", "其他"),
])
def test_classify_doc_category(file_path, file_name, kb_name, expected):
    assert classify_doc_category(file_path, file_name, kb_name) == expected


def test_category_values_are_product_domains():
    assert DOC_CATEGORIES == ("StampServer", "StampTools", "StampWebRTC", "实景三维", "耕地保护", "矢量瓦片", "基础环境", "博客", "其他")


def test_migration_preview_does_not_write(source, file_index):
    store = StoreStub(source)
    rebuild, clear = MagicMock(), MagicMock()

    result = migrate_doc_categories(
        store=store, file_index_path=file_index, apply=False,
        rebuild_bm25=rebuild, clear_cache=clear,
    )

    assert result["changed_files"] == 2
    assert store.updates == []
    rebuild.assert_not_called()
    clear.assert_not_called()


def test_migration_preview_does_not_open_vector_store(file_index, monkeypatch):
    import rag_knowledge.services.chunk_admin as chunk_admin

    monkeypatch.setattr(
        chunk_admin,
        "VectorStore",
        MagicMock(side_effect=AssertionError("preview must not open Chroma")),
    )

    result = migrate_doc_categories(file_index_path=file_index, apply=False)

    assert result["mode"] == "preview"


def test_migration_apply_updates_both_stores_and_is_idempotent(source, file_index):
    store = StoreStub(source)
    rebuild, clear = MagicMock(), MagicMock()

    first = migrate_doc_categories(
        store=store, file_index_path=file_index, apply=True,
        rebuild_bm25=rebuild, clear_cache=clear,
    )
    second = migrate_doc_categories(
        store=store, file_index_path=file_index, apply=True,
        rebuild_bm25=rebuild, clear_cache=clear,
    )

    payload = json.loads(file_index.read_text(encoding="utf-8"))
    assert first["updated_chunks"] == 3
    assert second["changed_files"] == 0
    assert payload["files"]["hash-a"]["doc_category"] == "StampServer"
    assert payload["files"]["hash-b"]["doc_category"] == "博客"
    rebuild.assert_called_once_with()
    clear.assert_called_once_with()


def test_admin_list_rejects_unknown_category(monkeypatch):
    from rag_knowledge.api import routes

    with pytest.raises(HTTPException) as exc:
        routes.admin_chunks(doc_category="运维管理")
    assert exc.value.status_code == 400


def test_admin_patch_rejects_empty_body():
    from rag_knowledge.api import routes
    from rag_knowledge.models.api import AdminChunkUpdateRequest

    with pytest.raises(HTTPException) as exc:
        routes.update_admin_chunk("c1", AdminChunkUpdateRequest())
    assert exc.value.status_code == 400


def test_admin_patch_returns_not_found(monkeypatch):
    from rag_knowledge.api import routes
    from rag_knowledge.models.api import AdminChunkUpdateRequest

    service = MagicMock()
    service.update_chunk.return_value = 0
    monkeypatch.setattr(routes, "ChunkAdminService", MagicMock(return_value=service))
    with pytest.raises(HTTPException) as exc:
        routes.update_admin_chunk("missing", AdminChunkUpdateRequest(review_status="approved"))
    assert exc.value.status_code == 404


def test_admin_patch_reports_index_refresh_failure(monkeypatch):
    from rag_knowledge.api import routes
    from rag_knowledge.models.api import AdminChunkUpdateRequest

    service = MagicMock()
    service.update_chunk.side_effect = RetrievalRefreshError("broken")
    monkeypatch.setattr(routes, "ChunkAdminService", MagicMock(return_value=service))
    with pytest.raises(HTTPException) as exc:
        routes.update_admin_chunk("c1", AdminChunkUpdateRequest(doc_category="基础环境"))
    assert exc.value.status_code == 500
    assert "metadata 已更新" in exc.value.detail


def test_admin_patch_does_not_misreport_store_failure(monkeypatch):
    from rag_knowledge.api import routes
    from rag_knowledge.models.api import AdminChunkUpdateRequest

    service = MagicMock()
    service.update_chunk.side_effect = ValueError("store failed before update")
    monkeypatch.setattr(routes, "ChunkAdminService", MagicMock(return_value=service))

    with pytest.raises(ValueError, match="store failed before update"):
        routes.update_admin_chunk("c1", AdminChunkUpdateRequest(section_title="新标题"))


def test_batch_route_rejects_pending_and_empty_ids():
    from rag_knowledge.api import routes
    from rag_knowledge.models.api import BatchReviewRequest

    for request in (
        BatchReviewRequest(chunk_ids=["c1"], status="pending"),
        BatchReviewRequest(chunk_ids=[], status="approved"),
    ):
        with pytest.raises(HTTPException) as exc:
            routes.batch_review_admin_chunks(request)
        assert exc.value.status_code == 400


def test_upload_rejects_category_outside_product_domains():
    from rag_knowledge.api import routes

    file = UploadFile(filename="manual.txt", file=io.BytesIO(b"content"))
    with pytest.raises(HTTPException) as exc:
        routes.upload(file=file, doc_category="运维管理")
    assert exc.value.status_code == 400
