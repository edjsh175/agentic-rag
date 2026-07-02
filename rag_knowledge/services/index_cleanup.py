"""Shared helpers for removing indexed file content from the knowledge base."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from rag_knowledge.repository.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class IndexedFileCleanupResult:
    file_hash: str
    file_name: str
    index_removed: bool
    deleted_chunks: int
    should_rebuild_bm25: bool


def cleanup_indexed_file(
    file_hash: str,
    *,
    data_dir: Path | None = None,
    index_data: dict | None = None,
    persist: bool = True,
) -> IndexedFileCleanupResult:
    """Remove a file's vectors and index entry using file_index.json as the source of truth."""
    if index_data is None and data_dir is None:
        raise ValueError("data_dir or index_data is required")

    index_path = data_dir / "file_index.json" if data_dir is not None else None
    index = index_data

    if index is None:
        if index_path is None or not index_path.exists():
            return IndexedFileCleanupResult(file_hash, "", False, 0, False)
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("加载 file_index.json 失败，跳过清理 %s: %s", file_hash, exc)
            return IndexedFileCleanupResult(file_hash, "", False, 0, False)

    files = index.setdefault("files", {})
    entry = files.pop(file_hash, None)
    if not entry:
        return IndexedFileCleanupResult(file_hash, "", False, 0, False)

    chunk_ids = list(entry.get("chunk_ids") or [])
    if chunk_ids:
        VectorStore().delete(chunk_ids)
        logger.info("已删除向量: %s (%d 块)", entry.get("file_name", file_hash), len(chunk_ids))

    if persist and index_path is not None:
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return IndexedFileCleanupResult(
        file_hash=file_hash,
        file_name=entry.get("file_name", ""),
        index_removed=True,
        deleted_chunks=len(chunk_ids),
        should_rebuild_bm25=bool(chunk_ids),
    )
