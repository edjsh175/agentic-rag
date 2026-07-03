"""TTL cache for retrieval-stage query results."""
from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from threading import Lock


class QueryCache:
    def __init__(self, enabled: bool = False, ttl_seconds: float = 300, capacity: int = 256):
        self.enabled = enabled
        self.ttl_seconds = max(0.001, float(ttl_seconds))
        self.capacity = max(1, capacity)
        self._entries: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._lock = Lock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def make_key(
        *,
        rewritten_query: str,
        kb_name: str | None,
        doc_category: str | None,
        review_status: str | None,
        method: str | None,
        rerank: bool,
        web_search: bool,
    ) -> str:
        payload = {
            "rewritten_query": rewritten_query,
            "kb_name": kb_name,
            "doc_category": doc_category,
            "review_status": review_status,
            "method": method,
            "rerank": rerank,
            "web_search": web_search,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str):
        if not self.enabled:
            return None

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None

            expires_at, value = entry
            if expires_at <= time.time():
                self._entries.pop(key, None)
                self._misses += 1
                return None

            self._entries.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value) -> None:
        if not self.enabled:
            return

        with self._lock:
            self._entries[key] = (time.time() + self.ttl_seconds, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)

    def prune_expired(self) -> None:
        now = time.time()
        with self._lock:
            expired = [key for key, (expires_at, _) in self._entries.items() if expires_at <= now]
            for key in expired:
                self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
            }


_DEFAULT_CACHE: QueryCache | None = None


def get_query_cache(enabled: bool = False, ttl_seconds: float = 300, capacity: int = 256) -> QueryCache:
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        _DEFAULT_CACHE = QueryCache(enabled=enabled, ttl_seconds=ttl_seconds, capacity=capacity)
    else:
        _DEFAULT_CACHE.enabled = enabled
        _DEFAULT_CACHE.ttl_seconds = max(0.001, float(ttl_seconds))
        _DEFAULT_CACHE.capacity = max(1, capacity)
    return _DEFAULT_CACHE


def clear_query_cache() -> None:
    if _DEFAULT_CACHE is not None:
        _DEFAULT_CACHE.clear()
