"""Read-only lookup from file_index.json chunk_id to file record metadata."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ChunkIndexLookupService:
    def __init__(self, file_index_path: str | Path):
        self._file_index_path = Path(file_index_path)
        self._mtime: float | None = object()  # type: ignore[assignment]
        self._lookup: dict[str, dict] = {}
        self._ensure_fresh()

    def _ensure_fresh(self) -> None:
        try:
            mtime = (
                self._file_index_path.stat().st_mtime
                if self._file_index_path.exists()
                else None
            )
        except OSError:
            mtime = None
        if mtime == self._mtime:
            return
        self._lookup = self._load()
        self._mtime = mtime

    def _load(self) -> dict[str, dict]:
        if not self._file_index_path.exists():
            return {}
        try:
            payload = json.loads(self._file_index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("chunk index lookup failed at %s: %s", self._file_index_path, exc)
            return {}
        return {
            str(chunk_id): entry
            for entry in (payload.get("files") or {}).values()
            for chunk_id in (entry.get("chunk_ids") or [])
        }

    def by_chunk_id(self, chunk_id: str) -> dict:
        self._ensure_fresh()
        entry = self._lookup.get(str(chunk_id))
        return dict(entry) if entry else {}

    def all(self) -> dict[str, dict]:
        self._ensure_fresh()
        return {chunk_id: dict(entry) for chunk_id, entry in self._lookup.items()}
