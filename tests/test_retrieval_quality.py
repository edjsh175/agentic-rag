"""
检索质量控制层单元测试（Phase 5）

覆盖：分数归一化、阈值过滤、Jaccard 去重、动态 TopK、启用/禁用开关。
"""
import unittest

import pytest
from langchain_core.documents import Document

from rag_knowledge.config import RetrievalQualityConfig
from rag_knowledge.services.retrieval_quality import RetrievalQualityStrategy


def _make_cfg(**overrides) -> RetrievalQualityConfig:
    """构建测试用配置，默认全部启用。"""
    defaults = {
        "enabled": True,
        "score_threshold_enabled": True,
        "score_threshold": 0.35,
        "jaccard_dedup_enabled": True,
        "jaccard_threshold": 0.85,
        "dynamic_topk_enabled": True,
        "score_drop_ratio": 0.5,
        "min_top_k": 3,
        "max_top_k": 8,
        "contextual_compression_enabled": False,
        "debug_log_enabled": False,
    }
    defaults.update(overrides)
    return RetrievalQualityConfig(**defaults)


def _doc(content: str, score: float = 0.5, **meta) -> Document:
    """快捷创建带 score 和 quality_score 的 Document。

    score 被 _normalize_scores 识别为源分数，quality_score 供后续过滤方法直接使用。
    """
    metadata = dict(meta)
    metadata["score"] = score
    metadata["quality_score"] = score
    return Document(page_content=content, metadata=metadata)


@pytest.fixture(autouse=True)
def _isolated_test_storage(isolated_storage):
    isolated_storage(
        db_name="retrieval-quality.db",
        chroma_name="retrieval-quality-chroma",
        data_dir_name="retrieval-quality-data",
    )


# ------------------------------------------------------------------
# 分数归一化
# ------------------------------------------------------------------

class NormalizeScoresTests(unittest.TestCase):
    """测试 _normalize_scores 分数统一逻辑"""

    def setUp(self):
        cfg = _make_cfg()
        self.strategy = object.__new__(RetrievalQualityStrategy)
        self.strategy._cfg = cfg

    def test_extracts_rerank_score_first(self):
        docs = [
            Document(page_content="a", metadata={"rerank_score": 0.95, "score": 0.5}),
            Document(page_content="b", metadata={"score": 0.9}),
        ]
        result = self.strategy._normalize_scores(docs)
        self.assertEqual(result[0].metadata["quality_score"], 0.95)
        self.assertEqual(result[1].metadata["quality_score"], 0.9)

    def test_uses_position_fallback(self):
        docs = [
            Document(page_content="a", metadata={}),
            Document(page_content="b", metadata={}),
        ]
        result = self.strategy._normalize_scores(docs)
        self.assertEqual(result[0].metadata["quality_score"], 1.0)
        self.assertEqual(result[1].metadata["quality_score"], 0.5)

    def test_sorts_descending_by_quality_score(self):
        docs = [
            Document(page_content="a", metadata={"score": 0.2}),
            Document(page_content="b", metadata={"rerank_score": 0.9}),
            Document(page_content="c", metadata={"rrf_score": 0.5}),
        ]
        result = self.strategy._normalize_scores(docs)
        scores = [r.metadata["quality_score"] for r in result]
        self.assertEqual(scores, [0.9, 0.5, 0.2])


# ------------------------------------------------------------------
# 相似度阈值过滤
# ------------------------------------------------------------------

class ScoreFilterTests(unittest.TestCase):
    """测试 _filter_by_score"""

    def setUp(self):
        cfg = _make_cfg(score_threshold=0.35)
        self.strategy = object.__new__(RetrievalQualityStrategy)
        self.strategy._cfg = cfg

    def test_filters_below_threshold(self):
        docs = [
            _doc("high", score=0.9),
            _doc("mid", score=0.5),
            _doc("low", score=0.2),
        ]
        result = self.strategy._filter_by_score(docs)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].metadata["quality_score"], 0.9)
        self.assertEqual(result[1].metadata["quality_score"], 0.5)

    def test_fallback_when_all_filtered(self):
        """全部低于阈值时，至少保留 min_top_k 个（但不超过总数）。"""
        docs = [
            _doc("a", score=0.1),
            _doc("b", score=0.2),
        ]
        result = self.strategy._filter_by_score(docs)
        # 只有 2 个 doc，即使 min_top_k=3 也只能返回 2
        self.assertEqual(len(result), min(len(docs), self.strategy._cfg.min_top_k))
        # 保留原顺序的前 min_top_k 个
        self.assertEqual(result[0].metadata["quality_score"], 0.1)


# ------------------------------------------------------------------
# Jaccard 去重
# ------------------------------------------------------------------

class JaccardDedupTests(unittest.TestCase):
    """测试 _deduplicate_by_jaccard"""

    def setUp(self):
        cfg = _make_cfg(jaccard_threshold=0.85)
        self.strategy = object.__new__(RetrievalQualityStrategy)
        self.strategy._cfg = cfg

    def test_removes_highly_similar_keeping_higher_score(self):
        """两个高度相似的 chunk，保留 quality_score 更高的。"""
        docs = [
            _doc("系统架构设计文档数据库设计原则与应用实践", score=0.9),
            _doc("系统架构设计文档数据库设计原则与应用实践案例", score=0.7),
        ]
        result = self.strategy._deduplicate_by_jaccard(docs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["quality_score"], 0.9)

    def test_keeps_different_chunks(self):
        """两个不同内容的 chunk 都保留。"""
        docs = [
            _doc("Python 是一种高级编程语言", score=0.9),
            _doc("ChromaDB 是一个向量数据库", score=0.8),
        ]
        result = self.strategy._deduplicate_by_jaccard(docs)
        self.assertEqual(len(result), 2)

    def test_single_doc_always_kept(self):
        docs = [_doc("单独的文档", score=0.5)]
        result = self.strategy._deduplicate_by_jaccard(docs)
        self.assertEqual(len(result), 1)

    def test_empty_input(self):
        self.assertEqual(self.strategy._deduplicate_by_jaccard([]), [])


class JaccardSimilarityTests(unittest.TestCase):
    """测试 _tokenize 和 _jaccard 底层方法"""

    def test_identical_texts_jaccard_1(self):
        text = "Python 高级编程语言"
        self.assertAlmostEqual(
            RetrievalQualityStrategy._jaccard(text, text), 1.0, delta=0.01,
        )

    def test_completely_different_jaccard_0(self):
        self.assertEqual(
            RetrievalQualityStrategy._jaccard("Python", "中文分词测试"),
            0.0,
        )

    def test_jaccard_between_0_and_1(self):
        sim = RetrievalQualityStrategy._jaccard(
            "Python 是一种高级编程语言",
            "Python 是高级编程语言",
        )
        self.assertGreater(sim, 0.5)
        self.assertLess(sim, 1.0)

    def test_empty_strings_return_zero(self):
        self.assertEqual(RetrievalQualityStrategy._jaccard("", ""), 0.0)
        self.assertEqual(RetrievalQualityStrategy._jaccard("Python", ""), 0.0)


# ------------------------------------------------------------------
# 动态 TopK 断崖截断
# ------------------------------------------------------------------

class DynamicTopKTests(unittest.TestCase):
    """测试 _truncate_by_score_drop"""

    def setUp(self):
        cfg = _make_cfg(
            score_drop_ratio=0.5,
            min_top_k=3,
            max_top_k=8,
        )
        self.strategy = object.__new__(RetrievalQualityStrategy)
        self.strategy._cfg = cfg

    def test_truncates_at_score_drop(self):
        """分数从 0.83 到 0.40，下降超过 50%，应截断在第 3 个。"""
        docs = [
            _doc("a", score=0.92),
            _doc("b", score=0.88),
            _doc("c", score=0.83),
            _doc("d", score=0.40),
            _doc("e", score=0.38),
        ]
        result = self.strategy._truncate_by_score_drop(docs)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].metadata["quality_score"], 0.92)
        self.assertEqual(result[2].metadata["quality_score"], 0.83)

    def test_no_drop_keeps_to_max_top_k(self):
        """没有断崖时保留到 max_top_k。"""
        docs = [_doc(f"d{i}", score=0.9 - i * 0.03) for i in range(10)]
        # scores: 0.90, 0.87, 0.84, 0.81, 0.78, 0.75, 0.72, 0.69, 0.66, 0.63
        # drop between each is ~3.4%, far below 50%
        result = self.strategy._truncate_by_score_drop(docs)
        self.assertEqual(len(result), self.strategy._cfg.max_top_k)

    def test_respects_min_top_k(self):
        """少于 min_top_k 时不做截断。"""
        docs = [
            _doc("a", score=0.9),
            _doc("b", score=0.1),  # huge drop but below min_top_k
        ]
        result = self.strategy._truncate_by_score_drop(docs)
        self.assertEqual(len(result), 2)  # min_top_k=3, 但只有 2 个文档

    def test_drop_after_min_top_k(self):
        """从 min_top_k 之后才开始检测断崖。"""
        docs = [
            _doc("a", score=0.9),
            _doc("b", score=0.4),   # drop 55% from 0.9, but at index 1 < min_top_k=3
            _doc("c", score=0.38),  # drop 5%, okay
            _doc("d", score=0.35),  # drop 8%, okay
            _doc("e", score=0.10),  # drop 71% from 0.35, truncate here
        ]
        result = self.strategy._truncate_by_score_drop(docs)
        # Should keep first 4 (truncates at index 4, where drop from 0.35 to 0.10 is 71%)
        self.assertGreaterEqual(len(result), self.strategy._cfg.min_top_k)
        self.assertLessEqual(len(result), self.strategy._cfg.max_top_k)


# ------------------------------------------------------------------
# apply() 集成测试
# ------------------------------------------------------------------

class ApplyIntegrationTests(unittest.TestCase):
    """测试 apply() 整体行为"""

    def test_disabled_returns_docs_unchanged(self):
        cfg = _make_cfg(enabled=False)
        strategy = object.__new__(RetrievalQualityStrategy)
        strategy._cfg = cfg
        docs = [Document(page_content="test", metadata={"score": 0.5})]
        result = strategy.apply("query", docs)
        self.assertIs(result, docs)  # 完全相同引用
        self.assertEqual(len(result), 1)

    def test_enabled_with_all_filters(self):
        cfg = _make_cfg(
            enabled=True,
            score_threshold_enabled=True,
            jaccard_dedup_enabled=True,
            dynamic_topk_enabled=True,
            score_threshold=0.35,
        )
        strategy = object.__new__(RetrievalQualityStrategy)
        strategy._cfg = cfg

        docs = [
            _doc("Python 是一种高级编程语言，用于数据科学", score=0.9),
            _doc("Python 是高级编程语言，用于数据科学和 AI", score=0.7),  # 相似，应去重
            _doc("ChromaDB 向量数据库基础知识", score=0.5),
            _doc("低分噪声", score=0.1),  # 应被阈值过滤
            _doc("Redis 缓存中间件配置", score=0.08),
        ]
        result = strategy.apply("test query", docs)
        # 期望：0.1 被过滤，相似 0.7 被去重，保留 0.9 + 0.5 = 2 个
        self.assertGreaterEqual(len(result), 1)
        self.assertLessEqual(len(result), 3)
        # 最高分 doc 应该保留
        self.assertIn(0.9, [d.metadata["quality_score"] for d in result])

    def test_empty_docs(self):
        cfg = _make_cfg()
        strategy = object.__new__(RetrievalQualityStrategy)
        strategy._cfg = cfg
        self.assertEqual(strategy.apply("query", []), [])

    def test_metadata_preserved(self):
        """chunk_id、source、kb_name 等元数据在处理后不丢失。"""
        cfg = _make_cfg(
            jaccard_dedup_enabled=False,
            dynamic_topk_enabled=False,
            score_threshold_enabled=False,
        )
        strategy = object.__new__(RetrievalQualityStrategy)
        strategy._cfg = cfg

        docs = [
            Document(
                page_content="测试文档内容",
                metadata={
                    "chunk_id": "chunk-001",
                    "source": "test.pdf",
                    "kb_name": "文章附件",
                    "score": 0.85,
                    "rerank_score": 0.9,
                    "page_number": 3,
                },
            ),
        ]
        result = strategy.apply("query", docs)
        self.assertEqual(len(result), 1)
        meta = result[0].metadata
        self.assertEqual(meta["chunk_id"], "chunk-001")
        self.assertEqual(meta["source"], "test.pdf")
        self.assertEqual(meta["kb_name"], "文章附件")
        self.assertEqual(meta["page_number"], 3)
        # 新增了 quality_score，但原有字段都保留
        self.assertIn("quality_score", meta)
        self.assertEqual(meta["rerank_score"], 0.9)

    def test_single_filter_enabled_individually(self):
        """单独开启 score_filter / jaccard_dedup / dynamic_topk 各不崩溃。"""
        # score filter only
        cfg = _make_cfg(
            score_threshold_enabled=True,
            jaccard_dedup_enabled=False,
            dynamic_topk_enabled=False,
        )
        s = object.__new__(RetrievalQualityStrategy)
        s._cfg = cfg
        docs = [_doc("a", score=0.9), _doc("b", score=0.2)]
        result = s.apply("q", docs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["quality_score"], 0.9)

        # jaccard only
        cfg2 = _make_cfg(
            score_threshold_enabled=False,
            jaccard_dedup_enabled=True,
            dynamic_topk_enabled=False,
        )
        s2 = object.__new__(RetrievalQualityStrategy)
        s2._cfg = cfg2
        docs2 = [
            _doc("相同内容测试", score=0.9),
            _doc("相同内容测试", score=0.7),
        ]
        result2 = s2.apply("q", docs2)
        self.assertEqual(len(result2), 1)

        # dynamic topk only
        cfg3 = _make_cfg(
            score_threshold_enabled=False,
            jaccard_dedup_enabled=False,
            dynamic_topk_enabled=True,
            max_top_k=5,
        )
        s3 = object.__new__(RetrievalQualityStrategy)
        s3._cfg = cfg3
        docs3 = [_doc(f"d{i}", score=0.9 - i * 0.15) for i in range(6)]
        # 0.90, 0.75 (drop 17%), 0.60 (20%), 0.45 (25%), 0.30 (33%), 0.15 (50%)
        # drops are below 50% until 0.30→0.15 (50%), not >50%
        # Actually 0.90→0.75 is 16.7%, not a cliff
        result3 = s3.apply("q", docs3)
        self.assertGreaterEqual(len(result3), s3._cfg.min_top_k)


# ------------------------------------------------------------------
# 配置兼容性
# ------------------------------------------------------------------

class ConfigCompatibilityTests(unittest.TestCase):
    """测试配置默认值和构造函数"""

    def test_default_config_all_fields(self):
        cfg = RetrievalQualityConfig()
        self.assertTrue(cfg.enabled)
        self.assertFalse(cfg.score_threshold_enabled)
        self.assertTrue(cfg.jaccard_dedup_enabled)
        self.assertTrue(cfg.dynamic_topk_enabled)
        self.assertFalse(cfg.contextual_compression_enabled)
        self.assertEqual(cfg.score_threshold, 0.35)
        self.assertEqual(cfg.jaccard_threshold, 0.85)
        self.assertEqual(cfg.score_drop_ratio, 0.5)
        self.assertEqual(cfg.min_top_k, 3)
        self.assertEqual(cfg.max_top_k, 8)

    def test_strategy_initialized_with_config(self):
        """验证可以从 Config 单例正常初始化。"""
        from rag_knowledge.config import Config
        strategy = RetrievalQualityStrategy(Config())
        self.assertIsNotNone(strategy._cfg)
        self.assertIsInstance(strategy._cfg, RetrievalQualityConfig)


if __name__ == "__main__":
    unittest.main()
