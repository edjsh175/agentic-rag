"""Lightweight persistent telemetry for online chunk hit statistics."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock

from rag_knowledge.config import Config


class ChunkHitTelemetry:
    """Persist query-level chunk hit counters in a small JSON file."""

    def __init__(self, path: Path | None = None):
        cfg = Config()
        self._path = path or (cfg.data_dir / "chunk_hit_stats.json")
        self._lock = Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_query(self, source_docs: list[dict] | None) -> None:
        chunk_ids = {
            str((doc or {}).get("metadata", {}).get("chunk_id", "")).strip()
            for doc in (source_docs or [])
        }
        chunk_ids.discard("")

        with self._lock:
            payload = self.read()
            payload["total_queries"] += 1
            if chunk_ids:
                payload["hit_queries"] += 1
            for chunk_id in sorted(chunk_ids):
                payload["chunk_hits"][chunk_id] = payload["chunk_hits"].get(chunk_id, 0) + 1
            payload["last_updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._write(payload)

    def read(self) -> dict:
        if not self._path.exists():
            return self._empty_payload()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return self._empty_payload()
        return {
            "version": 1,
            "total_queries": int(payload.get("total_queries", 0) or 0),
            "hit_queries": int(payload.get("hit_queries", 0) or 0),
            "chunk_hits": {
                str(chunk_id): int(count)
                for chunk_id, count in (payload.get("chunk_hits") or {}).items()
                if str(chunk_id).strip()
            },
            "last_updated_at": payload.get("last_updated_at"),
        }

    @staticmethod
    def _empty_payload() -> dict:
        return {
            "version": 1,
            "total_queries": 0,
            "hit_queries": 0,
            "chunk_hits": {},
            "last_updated_at": None,
        }

    def _write(self, payload: dict) -> None:
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
