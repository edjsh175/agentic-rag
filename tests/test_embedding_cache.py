import unittest

from rag_knowledge.services.embedding_cache import EmbeddingCache


class EmbeddingCacheTests(unittest.TestCase):
    def test_hits_and_misses_are_tracked(self):
        cache = EmbeddingCache(enabled=True, capacity=2)

        self.assertIsNone(cache.get("model-a", "hello"))
        cache.put("model-a", "hello", [0.1, 0.2])
        self.assertEqual(cache.get("model-a", "hello"), [0.1, 0.2])

        stats = cache.stats()
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["hits"], 1)

    def test_lru_eviction_discards_oldest_entry(self):
        cache = EmbeddingCache(enabled=True, capacity=2)
        cache.put("model-a", "one", [1.0])
        cache.put("model-a", "two", [2.0])
        self.assertEqual(cache.get("model-a", "one"), [1.0])

        cache.put("model-a", "three", [3.0])

        self.assertIsNone(cache.get("model-a", "two"))
        self.assertEqual(cache.get("model-a", "one"), [1.0])
        self.assertEqual(cache.get("model-a", "three"), [3.0])

    def test_model_name_is_part_of_the_cache_key(self):
        cache = EmbeddingCache(enabled=True, capacity=4)
        cache.put("model-a", "same-text", [1.0])
        cache.put("model-b", "same-text", [2.0])

        self.assertEqual(cache.get("model-a", "same-text"), [1.0])
        self.assertEqual(cache.get("model-b", "same-text"), [2.0])

    def test_clear_resets_entries_and_counters(self):
        cache = EmbeddingCache(enabled=True, capacity=2)
        cache.put("model-a", "hello", [0.1])
        cache.get("model-a", "hello")

        cache.clear()

        self.assertIsNone(cache.get("model-a", "hello"))
        stats = cache.stats()
        self.assertEqual(stats["size"], 0)
        self.assertEqual(stats["hits"], 0)
        self.assertEqual(stats["misses"], 1)


if __name__ == "__main__":
    unittest.main()
