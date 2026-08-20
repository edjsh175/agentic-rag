import time
import unittest

from rag_knowledge.services.query_cache import QueryCache


class QueryCacheTests(unittest.TestCase):
    def test_graph_context_participates_in_cache_key(self):
        base = dict(
            rewritten_query="管线发布工具如何使用",
            kb_name=None,
            doc_category=None,
            review_status="approved",
            method="hybrid",
            rerank=True,
            web_search=False,
        )

        pipeline = QueryCache.make_key(
            **base, graph_enabled=True, graph_entity_ids=("pipeline",), graph_revision="r1"
        )
        service = QueryCache.make_key(
            **base, graph_enabled=True, graph_entity_ids=("service",), graph_revision="r1"
        )
        revised = QueryCache.make_key(
            **base, graph_enabled=True, graph_entity_ids=("pipeline",), graph_revision="r2"
        )
        disabled = QueryCache.make_key(**base, graph_enabled=False)

        self.assertEqual(len({pipeline, service, revised, disabled}), 4)

    def test_explicit_entity_scope_participates_in_cache_key(self):
        base = dict(
            rewritten_query="硬件要求",
            kb_name=None,
            doc_category="StampTools",
            review_status="approved",
            method="hybrid",
            rerank=True,
            web_search=False,
            strict_explicit_target=True,
        )
        webgl = QueryCache.make_key(**base, backbone_canonical=("PipelineWebGL",))
        builder = QueryCache.make_key(**base, backbone_canonical=("PipelineBuilder",))
        generic = QueryCache.make_key(
            **{**base, "strict_explicit_target": False}, backbone_canonical=()
        )

        self.assertEqual(len({webgl, builder, generic}), 3)

    def test_disabled_cache_never_returns_entries(self):
        cache = QueryCache(enabled=False, ttl_seconds=60, capacity=10)
        key = QueryCache.make_key(
            rewritten_query="hello",
            kb_name="kb",
            doc_category=None,
            review_status="approved",
            method="hybrid",
            rerank=True,
            web_search=False,
        )

        cache.set(key, {"value": 1})

        self.assertIsNone(cache.get(key))
        self.assertEqual(cache.stats()["size"], 0)

    def test_ttl_expiry_removes_stale_entries(self):
        cache = QueryCache(enabled=True, ttl_seconds=0.01, capacity=10)
        key = QueryCache.make_key(
            rewritten_query="hello",
            kb_name="kb",
            doc_category=None,
            review_status="approved",
            method="hybrid",
            rerank=False,
            web_search=False,
        )
        cache.set(key, {"value": 1})

        time.sleep(0.02)

        self.assertIsNone(cache.get(key))
        self.assertEqual(cache.stats()["misses"], 1)

    def test_make_key_changes_when_retrieval_inputs_change(self):
        base = QueryCache.make_key(
            rewritten_query="hello",
            kb_name="kb",
            doc_category=None,
            review_status="approved",
            method="hybrid",
            rerank=False,
            web_search=False,
        )
        changed = {
            "kb_name": QueryCache.make_key(
                rewritten_query="hello",
                kb_name="kb-2",
                doc_category=None,
                review_status="approved",
                method="hybrid",
                rerank=False,
                web_search=False,
            ),
            "doc_category": QueryCache.make_key(
                rewritten_query="hello",
                kb_name="kb",
                doc_category="backend",
                review_status="approved",
                method="hybrid",
                rerank=False,
                web_search=False,
            ),
            "method": QueryCache.make_key(
                rewritten_query="hello",
                kb_name="kb",
                doc_category=None,
                review_status="approved",
                method="bm25",
                rerank=False,
                web_search=False,
            ),
            "rerank": QueryCache.make_key(
                rewritten_query="hello",
                kb_name="kb",
                doc_category=None,
                review_status="approved",
                method="hybrid",
                rerank=True,
                web_search=False,
            ),
            "web_search": QueryCache.make_key(
                rewritten_query="hello",
                kb_name="kb",
                doc_category=None,
                review_status="approved",
                method="hybrid",
                rerank=False,
                web_search=True,
            ),
        }

        for other in changed.values():
            self.assertNotEqual(base, other)

    def test_clear_and_prune_expired_affect_cache_size(self):
        cache = QueryCache(enabled=True, ttl_seconds=0.01, capacity=10)
        key = QueryCache.make_key(
            rewritten_query="hello",
            kb_name="kb",
            doc_category=None,
            review_status="approved",
            method="hybrid",
            rerank=False,
            web_search=False,
        )
        cache.set(key, {"value": 1})
        self.assertEqual(cache.stats()["size"], 1)

        time.sleep(0.02)
        cache.prune_expired()
        self.assertEqual(cache.stats()["size"], 0)

        cache.set(key, {"value": 2})
        cache.clear()
        self.assertEqual(cache.stats()["size"], 0)


if __name__ == "__main__":
    unittest.main()
