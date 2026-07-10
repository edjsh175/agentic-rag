"""Read-only lookup from file_index.json chunk_id to file record metadata."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ChunkIndexLookupService:
    def __init__(self, file_index_path: str | Path):
        self._file_index_path = Path(file_index_path)
        self._lookup = self._load()

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
        entry = self._lookup.get(str(chunk_id))
        return dict(entry) if entry else {}

    def all(self) -> dict[str, dict]:
        return {chunk_id: dict(entry) for chunk_id, entry in self._lookup.items()}
