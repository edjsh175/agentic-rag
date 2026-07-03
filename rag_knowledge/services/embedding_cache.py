"""Thread-safe LRU cache for embedding results."""
from __future__ import annotations

from collections import OrderedDict
from threading import Lock


class EmbeddingCache:
    def __init__(self, enabled: bool = True, capacity: int = 10000):
        self.enabled = enabled
        self.capacity = max(1, capacity)
        self._entries: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._lock = Lock()
        self._hits = 0
        self._misses = 0

    def get(self, model: str, text: str) -> list[float] | None:
        if not self.enabled:
            return None

        key = (model, text)
        with self._lock:
            value = self._entries.get(key)
            if value is None:
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return list(value)

    def put(self, model: str, text: str, vector: list[float]) -> None:
        if not self.enabled:
            return

        key = (model, text)
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
            self._entries[key] = list(vector)
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)

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


_DEFAULT_CACHE: EmbeddingCache | None = None


def get_embedding_cache(enabled: bool = True, capacity: int = 10000) -> EmbeddingCache:
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        _DEFAULT_CACHE = EmbeddingCache(enabled=enabled, capacity=capacity)
    else:
        _DEFAULT_CACHE.enabled = enabled
        _DEFAULT_CACHE.capacity = max(1, capacity)
    return _DEFAULT_CACHE
