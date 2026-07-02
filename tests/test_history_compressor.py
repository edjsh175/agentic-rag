"""
单元测试：历史消息压缩与摘要控制 (HistoryCompressor)
"""
import unittest
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
        self.main_cfg.ollama_base_url = "http://localhost:11434"
        self.main_cfg.llm_model = "test-model"
        self.compressor = HistoryCompressor(self.cfg, self.main_cfg)

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

    @patch("httpx.post")
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

        # 首次调用：应该调用 LLM 并生成摘要
        recent, summary = self.compressor.compress(history)

        self.assertEqual(len(recent), 4)  # 最近 2 轮（4条消息：u4, a4, u5, a5）
        self.assertEqual(recent[0]["content"], "u4")
        self.assertEqual(summary, "这是 1-3 轮的摘要")
        self.assertEqual(mock_post.call_count, 1)

        # 第二次调用（相同历史）：应该命中缓存，不调用 LLM
        recent_cached, summary_cached = self.compressor.compress(history)
        self.assertEqual(recent_cached, recent)
        self.assertEqual(summary_cached, summary)
        self.assertEqual(mock_post.call_count, 1)  # 仍然是 1

    @patch("httpx.post")
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
        self.assertEqual(summary1, "Summary-1")
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
        self.assertEqual(summary2, "Summary-2 (Merged)")
        self.assertEqual(mock_post.call_count, 1)

        # 检查是否使用了增量 prompt：包含 "Summary-1"
        call_args = mock_post.call_args[1]
        user_prompt = call_args["json"]["messages"][1]["content"]
        self.assertIn("Summary-1", user_prompt)
        self.assertIn("用户: u4", user_prompt)
        self.assertIn("助手: a4", user_prompt)
        self.assertIn("用户: u5", user_prompt)
        self.assertIn("助手: a5", user_prompt)


if __name__ == "__main__":
    unittest.main()
