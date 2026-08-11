"""
单元测试：历史消息压缩与摘要控制 (HistoryCompressor)
"""
import unittest
import threading
import time
from unittest.mock import MagicMock, patch

from rag_knowledge.config import Config, HistoryCompressionConfig
from rag_knowledge.services.history_compressor import HistoryCompressor, LRUCache


class TestLRUCache(unittest.TestCase):
    def test_lru_behavior(self):
        cache = LRUCache(capacity=3)
        cache.put("k1", "v1")
        cache.put("k2", "v2")
        cache.put("k3", "v3")

        self.assertEqual(cache.get("k1"), "v1")
        cache.put("k4", "v4")  # 此时 k2 应该被淘汰，因为 k1 刚才被 get 访问过，k2 变成了最久未使用的项

        self.assertIsNone(cache.get("k2"))
        self.assertEqual(cache.get("k3"), "v3")
        self.assertEqual(cache.get("k4"), "v4")


class TestHistoryCompressor(unittest.TestCase):

    def setUp(self):
        self.cfg = HistoryCompressionConfig(
            enabled=True,
            min_raw_rounds=2,
            max_raw_rounds=4
        )
        self.main_cfg = MagicMock(spec=Config)
        endpoint_mock = self.main_cfg.endpoint_for.return_value
        endpoint_mock.normalized_provider.return_value = "ollama"
        endpoint_mock.resolved_base_url.return_value = "http://localhost:11434"
        endpoint_mock.max_retries = 3
        endpoint_mock.concurrency_limit = 5
        endpoint_mock.role = "helper_llm"
        endpoint_mock.model = "helper-model"
        self.main_cfg.ollama_base_url = "http://localhost:11434"
        self.main_cfg.llm_model = "test-model"
        self.main_cfg.helper_llm_model = "helper-model"
        self.compressor = HistoryCompressor(self.cfg, self.main_cfg)

    def wait_for_background(self, timeout: float = 2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.compressor._lock:
                task = self.compressor._background_task
                pending = self.compressor._pending_job
            if task is None and pending is None:
                return
            if task is not None:
                task.join(0.05)
            else:
                time.sleep(0.02)
        self.fail("background summary did not finish")

    def test_cache_miss_returns_immediately_and_deduplicates_background_work(self):
        history = [
            {"role": role, "content": f"m{i}"}
            for i in range(10)
            for role in ("user", "assistant")
        ]
        started = threading.Event()
        release = threading.Event()

        def summarize(*_args):
            started.set()
            release.wait(1)
            return "summary"

        self.compressor._generate_summary = summarize
        started_at = time.monotonic()
        recent, summary = self.compressor.compress(history)
        elapsed = time.monotonic() - started_at

        # Phase 0：未命中默认确定性降级为最近 min_raw_rounds 轮
        self.assertEqual(len(recent), 4)
        self.assertIsNone(summary)
        self.assertLess(elapsed, 0.2)
        self.assertTrue(started.wait(1))

        self.compressor.compress(history)
        release.set()
        self.wait_for_background()

        recent, summary = self.compressor.compress(history)
        self.assertEqual(len(recent), 4)
        self.assertEqual(summary, "summary")

    def test_busy_miss_queues_latest_older_for_rewarm(self):
        """后台仍在跑时新 older 入队；完成后续跑最新 hash，避免摘要饥饿。"""
        history_v1 = [
            {"role": role, "content": f"v1-{i}"}
            for i in range(10)
            for role in ("user", "assistant")
        ]
        history_v2 = history_v1 + [
            {"role": "user", "content": "v2-u"},
            {"role": "assistant", "content": "v2-a"},
        ]
        started = threading.Event()
        release = threading.Event()
        call_lens: list[int] = []

        def summarize(older_history, *_args):
            call_lens.append(len(older_history))
            if len(call_lens) == 1:
                started.set()
                release.wait(2)
                return "sum-v1"
            return "sum-v2"

        self.compressor._generate_summary = summarize
        r1 = self.compressor.compress_detailed(history_v1)
        self.assertTrue(started.wait(1))
        self.assertTrue(r1.scheduled_background)
        self.assertFalse(r1.pending_rewarm_queued)

        r2 = self.compressor.compress_detailed(history_v2)
        self.assertTrue(r2.background_busy)
        self.assertTrue(r2.pending_rewarm_queued)
        self.assertIsNone(r2.summary)

        release.set()
        self.wait_for_background(timeout=3)

        hit = self.compressor.compress_detailed(history_v2)
        self.assertEqual(hit.fallback, "summary_cache")
        self.assertEqual(hit.summary, "sum-v2")
        self.assertEqual(call_lens, [16, 18])  # 10→11 轮：older=总消息-4

    def test_cache_miss_async_policy_keeps_full_history(self):
        history = [
            {"role": role, "content": f"m{i}"}
            for i in range(10)
            for role in ("user", "assistant")
        ]
        started = threading.Event()
        release = threading.Event()

        def summarize(*_args):
            started.set()
            release.wait(1)
            return "summary"

        self.compressor._generate_summary = summarize
        recent, summary = self.compressor.compress(history, on_cache_miss="async")
        self.assertEqual(recent, history)
        self.assertIsNone(summary)
        self.assertTrue(started.wait(1))
        release.set()
        self.wait_for_background()

    def test_failed_summary_enters_global_cooldown(self):
        self.cfg.failure_cooldown_seconds = 300
        history = [
            {"role": role, "content": f"m{i}"}
            for i in range(10)
            for role in ("user", "assistant")
        ]
        calls = 0

        def fail(*_args):
            nonlocal calls
            calls += 1
            return ""

        self.compressor._generate_summary = fail
        recent, summary = self.compressor.compress(history)
        self.assertEqual(len(recent), 4)
        self.assertIsNone(summary)
        self.wait_for_background()
        recent2, _ = self.compressor.compress(history + [
            {"role": "user", "content": "new"},
            {"role": "assistant", "content": "new"},
        ])
        # 冷却期内仍确定性截断，且不再调度新任务
        self.assertEqual(len(recent2), 4)
        self.assertEqual(calls, 1)

    def test_no_compression_within_limit(self):
        # 3 轮（6条消息），小于或等于 max_raw_rounds (4)
        history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},
            {"role": "user", "content": "今天天气怎么样"},
            {"role": "assistant", "content": "晴天"},
            {"role": "user", "content": "谢谢"},
            {"role": "assistant", "content": "不客气"},
        ]
        recent, summary = self.compressor.compress(history)
        self.assertEqual(recent, history)
        self.assertIsNone(summary)

    def test_default_threshold_does_not_schedule_at_twenty_rounds(self):
        compressor = HistoryCompressor(HistoryCompressionConfig(), self.main_cfg)
        history = [
            {"role": role, "content": f"m{i}"}
            for i in range(20)
            for role in ("user", "assistant")
        ]
        compressor._generate_summary = MagicMock(return_value="summary")

        recent, summary = compressor.compress(history)

        self.assertEqual(recent, history)
        self.assertIsNone(summary)
        compressor._generate_summary.assert_not_called()

    def test_disabled_compressor_never_schedules_background_work(self):
        self.cfg.enabled = False
        history = [
            {"role": role, "content": f"m{i}"}
            for i in range(10)
            for role in ("user", "assistant")
        ]
        self.compressor._generate_summary = MagicMock(return_value="summary")

        self.compressor.compress(history)

        self.compressor._generate_summary.assert_not_called()

    @patch("httpx.Client.post")
    def test_compression_exceeds_limit_with_caching(self, mock_post):
        # 5 轮（10条消息），大于 max_raw_rounds (4)
        # 会保留最近 min_raw_rounds (2轮=4条消息) 也就是最后4条为原始消息
        # 剩下的前 6 条消息会被压缩
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
            {"role": "user", "content": "u4"},
            {"role": "assistant", "content": "a4"},
            {"role": "user", "content": "u5"},
            {"role": "assistant", "content": "a5"},
        ]

        # 模拟 LLM 返回摘要
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "这是 1-3 轮的摘要"}
        }
        mock_post.return_value = mock_response

        # 首次调用：确定性截断最近轮，后台生成摘要
        recent, summary = self.compressor.compress(history)
        self.assertEqual(len(recent), 4)
        self.assertEqual(recent[0]["content"], "u4")
        self.assertIsNone(summary)
        self.wait_for_background()
        self.assertEqual(mock_post.call_count, 1)

        # 第二次调用（相同历史）：命中缓存，不调用 LLM
        recent_cached, summary_cached = self.compressor.compress(history)
        self.assertEqual(len(recent_cached), 4)
        self.assertEqual(recent_cached[0]["content"], "u4")
        self.assertEqual(
            summary_cached,
            HistoryCompressor._ensure_structured_summary("这是 1-3 轮的摘要"),
        )
        self.assertEqual(mock_post.call_count, 1)  # 仍然是 1

    @patch("httpx.Client.post")
    def test_incremental_compression(self, mock_post):
        # 准备历史对话：首先触发第 1 阶段的压缩
        history_step1 = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
            {"role": "user", "content": "u4"},
            {"role": "assistant", "content": "a4"},
            {"role": "user", "content": "u5"},
            {"role": "assistant", "content": "a5"},
        ]

        # step1 压缩前 3 轮（u1-a3），最近 2 轮（u4-a5）保留为原始消息
        mock_response1 = MagicMock()
        mock_response1.json.return_value = {
            "message": {"content": "Summary-1"}
        }
        mock_post.return_value = mock_response1

        recent1, summary1 = self.compressor.compress(history_step1)
        self.assertEqual(len(recent1), 4)
        self.assertEqual(recent1[0]["content"], "u4")
        self.assertIsNone(summary1)
        self.wait_for_background()
        self.assertEqual(mock_post.call_count, 1)

        # 追加新消息到 history_step2 (总共 7 轮)
        # 最近 2 轮（u6-a7）保留。被总结部分变为前 5 轮（u1-a5）。
        # 因为前 3 轮（u1-a3）已经被总结为 Summary-1 且缓存在 LRU，所以此次总结是增量的：
        # 输入 Summary-1 + 新增的第 4-5 轮对话（u4-a5）
        history_step2 = history_step1 + [
            {"role": "user", "content": "u6"},
            {"role": "assistant", "content": "a6"},
            {"role": "user", "content": "u7"},
            {"role": "assistant", "content": "a7"},
        ]

        mock_response2 = MagicMock()
        mock_response2.json.return_value = {
            "message": {"content": "Summary-2 (Merged)"}
        }
        # 重置 mock_post 并改变返回值
        mock_post.reset_mock()
        mock_post.return_value = mock_response2

        recent2, summary2 = self.compressor.compress(history_step2)
        self.assertEqual(len(recent2), 4)
        self.assertEqual(recent2[0]["content"], "u6")
        self.assertIsNone(summary2)
        self.wait_for_background()
        self.assertEqual(mock_post.call_count, 1)

        # 检查是否使用了增量 prompt：包含 "Summary-1"
        call_args = mock_post.call_args[1]
        user_prompt = call_args["json"]["messages"][1]["content"]
        self.assertIn("Summary-1", user_prompt)
        self.assertIn("用户: u4", user_prompt)
        self.assertIn("助手: a4", user_prompt)
        self.assertIn("用户: u5", user_prompt)
        self.assertIn("助手: a5", user_prompt)

        # 缓存命中后应带回半结构化摘要
        recent3, summary3 = self.compressor.compress(history_step2)
        self.assertEqual(len(recent3), 4)
        self.assertEqual(
            summary3,
            HistoryCompressor._ensure_structured_summary("Summary-2 (Merged)"),
        )


class TestStructuredSummary(unittest.TestCase):
    def test_ensure_structured_keeps_four_line_format(self):
        raw = "主题：管线\n实体：PipelineBuilder\n结论：已确认字段\n未决：部署步骤\n"
        self.assertEqual(
            HistoryCompressor._ensure_structured_summary(raw),
            raw.strip()[:400],
        )

    def test_ensure_structured_wraps_prose(self):
        out = HistoryCompressor._ensure_structured_summary("随便写的散文摘要")
        self.assertIn("主题：对话延续", out)
        self.assertIn("结论：随便写的散文摘要", out)


if __name__ == "__main__":
    unittest.main()
