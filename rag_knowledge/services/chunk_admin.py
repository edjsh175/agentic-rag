"""Query and update knowledge chunks for the admin review workspace."""
from __future__ import annotations

import json
from math import ceil
from pathlib import Path
from typing import Callable

from rag_knowledge.config import Config
from rag_knowledge.models.api import AdminChunkItem, AdminChunkListResponse, ReviewResponse
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.query_cache import clear_query_cache


DOC_CATEGORIES = (
    "StampServer", "StampTools", "StampWebRTC", "实景三维", "耕地保护",
    "矢量瓦片", "基础环境", "博客", "其他",
)
REVIEW_STATUSES = ("pending", "approved", "rejected")


class RetrievalRefreshError(RuntimeError):
    """Metadata changed, but BM25 or query-cache refresh failed."""


def classify_doc_category(file_path: str, file_name: str, kb_name: str) -> str:
    """Classify existing material using deterministic product-domain rules."""
    if kb_name == "已发布文章" or "已发布文章" in file_path:
        return "博客"
    text = f"{file_path} {file_name}".lower()
    for category in ("StampServer", "StampTools", "StampWebRTC"):
        if category.lower() in text:
            return category
    if "实景三维" in text:
        return "实景三维"
    if "耕地保护" in text:
        return "耕地保护"
    if "矢量瓦片" in text:
        return "矢量瓦片"
    if any(keyword in text for keyword in ("虚拟机", "rocky", "东方通", "部署环境")):
        return "基础环境"
    return "其他"


def _default_rebuild_bm25() -> None:
    from rag_knowledge.services.bm25_store import BM25Store
    BM25Store().rebuild()


def migrate_doc_categories(
    *,
    store: VectorStore | None = None,
    file_index_path: Path | None = None,
    apply: bool = False,
    rebuild_bm25: Callable[[], None] | None = None,
    clear_cache: Callable[[], None] | None = None,
) -> dict:
    """Preview or apply deterministic product-domain categories to indexed files."""
    index_path = file_index_path or (Config().data_dir / "file_index.json")
    rebuild = rebuild_bm25 or _default_rebuild_bm25
    clear = clear_cache or clear_query_cache
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    changes = []

    for file_hash, entry in (payload.get("files") or {}).items():
        category = classify_doc_category(
            str(entry.get("file_path") or ""),
            str(entry.get("file_name") or ""),
            str(entry.get("kb_name") or ""),
        )
        if entry.get("doc_category") == category:
            continue
        changes.append({
            "file_hash": file_hash,
            "file_name": entry.get("file_name", ""),
            "from": entry.get("doc_category"),
            "to": category,
            "chunk_ids": list(entry.get("chunk_ids") or []),
        })

    updated_chunks = 0
    if apply and changes:
        target_store = store or VectorStore()
        for change in changes:
            entry = payload["files"][change["file_hash"]]
            entry["doc_category"] = change["to"]
            updated_chunks += target_store.update_metadata(
                change["chunk_ids"],
                {"doc_category": change["to"]},
            )
        index_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            rebuild()
        finally:
            clear()

    return {
        "mode": "apply" if apply else "preview",
        "changed_files": len(changes),
        "updated_chunks": updated_chunks,
        "changes": changes,
    }


class ChunkAdminService:
    def __init__(self, *, store: VectorStore | None = None, file_index_path: Path | None = None,
                 rebuild_bm25: Callable[[], None] | None = None,
                 clear_cache: Callable[[], None] | None = None):
        cfg = Config()
        self._store = store or VectorStore()
        self._file_index_path = file_index_path or (cfg.data_dir / "file_index.json")
        self._watch_dir = cfg.watch_dir
        self._rebuild_bm25 = rebuild_bm25 or _default_rebuild_bm25
        self._clear_cache = clear_cache or clear_query_cache

    def list_chunks(self, *, review_status: str = "pending", doc_category: str = "all",
                    filename: str | None = None, page: int = 1,
                    page_size: int = 20) -> AdminChunkListResponse:
        source = self._store.get_chunk_stats_source()
        ids = source.get("ids") or []
        documents = source.get("documents") or []
        metadatas = source.get("metadatas") or []
        files = self._file_lookup()
        filename_query = (filename or "").strip().casefold()
        items = []
        for chunk_id, content, metadata in zip(ids, documents, metadatas):
            meta = metadata or {}
            file_data = files.get(str(chunk_id), {})
            status = str(meta.get("review_status") or "pending")
            category = str(meta.get("doc_category") or "其他")
            source_name = str(meta.get("source") or file_data.get("file_name") or "")
            file_name = str(file_data.get("file_name") or source_name)
            file_path = str(meta.get("file_path") or file_data.get("file_path") or "")
            front_matter = self._front_matter(file_path)
            if review_status != "all" and status != review_status:
                continue
            if doc_category != "all" and category != doc_category:
                continue
            if filename_query and filename_query not in file_name.casefold():
                continue
            text = str(content or "")
            items.append(AdminChunkItem(
                chunk_id=str(chunk_id), file_name=file_name, source=source_name,
                section_title=str(meta.get("section_title") or ""), doc_category=category,
                review_status=status, content_preview=text[:80], content=text,
                kb_name=meta.get("kb_name") or file_data.get("kb_name"),
                page_label=self._page_label(meta), indexed_at=file_data.get("added_at"),
                file_path=file_path or None,
                kb_path=meta.get("kb_path") or file_data.get("kb_path"),
                title=self._first_text(meta, front_matter, "title", "article_title"),
                source_url=self._source_url(meta, front_matter),
                author=self._first_text(meta, front_matter, "author"),
                platform=self._first_text(meta, front_matter, "platform"),
                publish_date=self._first_text(meta, front_matter, "publish_date"),
                last_modified=file_data.get("last_modified"),
                crawled_at=self._first_text(meta, front_matter, "crawled_at"),
            ))
        items.sort(key=lambda item: (item.indexed_at or "", item.file_name.casefold(), item.chunk_id))
        total = len(items)
        start = (page - 1) * page_size
        return AdminChunkListResponse(
            items=items[start:start + page_size], total=total, page=page, page_size=page_size,
            total_pages=ceil(total / page_size) if total else 0,
        )

    def update_chunk(self, chunk_id: str, changes: dict) -> int:
        updated = self._store.update_metadata([chunk_id], changes)
        if updated:
            self._refresh_retrieval()
        return updated

    def batch_review(self, chunk_ids: list[str], status: str) -> ReviewResponse:
        unique_ids = list(dict.fromkeys(chunk_ids))
        updated = self._store.update_metadata(unique_ids, {"review_status": status})
        if updated:
            self._refresh_retrieval()
        return ReviewResponse(
            message=f"已将 {updated} 个 chunk 更新为 {status}", updated_chunks=updated,
            requested_chunks=len(unique_ids), status=status,
        )

    def _refresh_retrieval(self) -> None:
        refresh_error = None
        try:
            self._rebuild_bm25()
        except Exception as exc:
            refresh_error = exc
        try:
            self._clear_cache()
        except Exception as exc:
            refresh_error = refresh_error or exc
        if refresh_error is not None:
            raise RetrievalRefreshError(str(refresh_error)) from refresh_error

    def _file_lookup(self) -> dict[str, dict]:
        if not self._file_index_path.exists():
            return {}
        try:
            payload = json.loads(self._file_index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {
            str(chunk_id): entry
            for entry in (payload.get("files") or {}).values()
            for chunk_id in (entry.get("chunk_ids") or [])
        }

    def _front_matter(self, file_path: str) -> dict[str, str]:
        if not file_path or not file_path.lower().endswith(".md"):
            return {}
        path = Path(file_path)
        if not path.is_absolute():
            path = self._watch_dir / file_path
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {}
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}
        result: dict[str, str] = {}
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value:
                result[key] = value
        return result

    @staticmethod
    def _first_text(metadata: dict, front_matter: dict[str, str], *keys: str) -> str | None:
        for key in keys:
            value = metadata.get(key)
            if value not in (None, ""):
                return str(value)
            value = front_matter.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    @classmethod
    def _source_url(cls, metadata: dict, front_matter: dict[str, str]) -> str | None:
        return (
            cls._first_text(metadata, front_matter, "source_url", "url", "source_link")
            or cls._first_text({}, front_matter, "source")
        )

    @staticmethod
    def _page_label(metadata: dict) -> str:
        page_label = metadata.get("page_label")
        if page_label not in (None, ""):
            return str(page_label)
        page_number = metadata.get("page_number", metadata.get("page"))
        if page_number in (None, ""):
            return "无页码"
        try:
            return str(int(page_number) + 1)
        except (TypeError, ValueError):
            return str(page_number)
