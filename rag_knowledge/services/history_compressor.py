"""
历史消息压缩与摘要服务

在历史对话较长时，将较旧的历史记录压缩为文本摘要，存入 LRU 缓存，
只发送最新几轮原始对话 + 摘要，以此节省上下文 token 且避免完全遗忘。

基于增量总结算法，并使用 LRU 缓存避免每次都调用 LLM 总结。
"""
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

import httpx

from rag_knowledge.config import Config, HistoryCompressionConfig

logger = logging.getLogger(__name__)


class LRUCache:
    """简单的 LRU 缓存，用于存储已生成的摘要"""

    def __init__(self, capacity: int = 256):
        self.capacity = capacity
        self.cache = OrderedDict()

    def get(self, key: str) -> Optional[str]:
        if key not in self.cache:
            return None
        # 移动到末尾表示最新访问
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key: str, value: str) -> None:
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)  # 弹出最久未使用的项


class HistoryCompressor:
    """历史记录压缩器"""

    def __init__(self, cfg: HistoryCompressionConfig, main_cfg: Config):
        self._cfg = cfg
        self._main_cfg = main_cfg
        self._cache = LRUCache(capacity=200)
        self._lock = threading.RLock()
        self._background_task: threading.Thread | None = None
        self._cooldown_until = 0.0

    def _cache_get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._cache.get(key)

    def _cache_put(self, key: str, value: str) -> None:
        with self._lock:
            self._cache.put(key, value)

    def _hash_history(self, history_slice: list[dict]) -> str:
        """根据历史对话片段内容计算唯一的 Hash 值"""
        # 只取 role 和 content 两个关键属性序列化哈希
        normalized = [
            {"role": h.get("role", ""), "content": h.get("content", "")}
            for h in history_slice
        ]
        dumped = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()

    def _format_history_text(self, history_slice: list[dict]) -> str:
        """将历史对话片段格式化为人类可读文本"""
        lines = []
        for h in history_slice:
            role = "用户" if h.get("role") == "user" else "助手"
            content = h.get("content", "").strip()
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _call_llm_summarize(self, prompt: str) -> str:
        """调用本地 Ollama LLM 进行摘要生成"""
        try:
            url = f"{self._main_cfg.ollama_base_url}/api/chat"
            payload = {
                "model": self._main_cfg.helper_llm_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是一个严谨且精炼的历史对话摘要助手。你的任务是清晰、扼要地概括对话的历史背景，去掉冗余闲聊，提炼核心结论和需求，仅返回总结后的中文摘要文本，严禁加入任何解释、推理（如 <think></think> 标签）或多余话术。",
                    },
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 256,
                    "top_k": 10,
                    "thinking": False,
                },
            }
            logger.debug("开始调用本地 LLM 压缩对话历史...")
            response = httpx.post(url, json=payload, timeout=45)
            response.raise_for_status()

            result = response.json()
            content = result.get("message", {}).get("content", "").strip()

            # 清洗 DeepSeek-R1 等模型的思考过程内容（如果有的话）
            import re
            cleaned_content = re.sub(r"(?is)<think>.*?</think>", "", content).strip()
            return cleaned_content
        except Exception as e:
            logger.error("历史记录总结调用 LLM 失败: %s", e)
            return ""

    def _generate_summary(
        self, older_history: list[dict], all_history: list[dict], current_rounds: int
    ) -> str:
        """
        生成摘要。如果存在之前更小范围的摘要缓存，则执行增量合并总结，
        否则进行全量总结。
        """
        # 尝试寻找此前可能的最长前缀摘要
        # 逐轮（步长2个message）倒着找
        best_prefix_hash = None
        best_prefix_idx = 0
        for i in range(len(older_history) - 2, 0, -2):
            prefix_slice = older_history[:i]
            prefix_hash = self._hash_history(prefix_slice)
            cached_sum = self._cache_get(prefix_hash)
            if cached_sum:
                best_prefix_hash = prefix_hash
                best_prefix_idx = i
                break

        if best_prefix_hash and best_prefix_idx > 0:
            # 找到前缀摘要，进行增量合并总结
            prev_summary = self._cache_get(best_prefix_hash)
            new_additions = older_history[best_prefix_idx:]
            new_text = self._format_history_text(new_additions)

            prompt = (
                f"你已知先前的历史对话摘要如下：\n"
                f"\"\"\"\n{prev_summary}\n\"\"\"\n\n"
                f"在此之后，用户与助手又进行了如下的追加对话：\n"
                f"\"\"\"\n{new_text}\n\"\"\"\n\n"
                f"请结合先前的摘要和这些追加对话，生成一段合并后的最新完整历史对话摘要。\n"
                f"要求：保留历史的核心背景、之前的关键共识与现在的最新提问进展，精简闲聊，控制在 300 字以内。"
            )
            logger.info(
                "history_compressor: 增量总结触发 | 使用前缀哈希(前 %d 条)",
                best_prefix_idx,
            )
            summary = self._call_llm_summarize(prompt)
            if not summary:
                # 降级：如果增量失败，回退到全量总结
                logger.warning("history_compressor: 增量总结失败，回退到全量总结...")
                return self._generate_full_summary(older_history)
            return summary
        else:
            # 未找到任何前缀，执行全量总结
            return self._generate_full_summary(older_history)

    def _generate_full_summary(self, older_history: list[dict]) -> str:
        """对全部 older_history 生成全量总结"""
        history_text = self._format_history_text(older_history)
        prompt = (
            f"请根据以下用户和助手的历史对话，生成一段简洁的中文摘要，"
            f"概括之前讨论的核心主题、提出的关键需求、达成的共识或做出的重要决策。\n"
            f"要求：删去无意义的寒暄和重叠讨论，直奔核心，字数控制在 300 字以内。\n\n"
            f"对话历史：\n"
            f"\"\"\"\n{history_text}\n\"\"\""
        )
        logger.info("history_compressor: 全量总结触发 | 条数=%d", len(older_history))
        return self._call_llm_summarize(prompt)

    def compress(
        self, history: list[dict] | None
    ) -> tuple[list[dict] | None, Optional[str]]:
        """
        压缩历史记录。

        参数：
          history: 原始对话历史列表 [{"role": "user", "content": "..."}]

        返回：
          (recent_history, history_summary)
          - recent_history: 裁剪后保留的最近 N 轮原始对话（可以传给 messages）
          - history_summary: 较早对话的总结摘要（需要注入到 system prompt 中），没有则为 None
        """
        if not self._cfg.enabled or not history or len(history) < 2:
            return history, None

        # 确保历史记录是偶数条（轮次整齐）
        total_len = len(history)
        # 如果最后一条是 user (说明这是客户端在发送 query 前拼接好的上一轮，但正常 history 参数里
        # 如果包含当前 question，在 RagChain 里 question 是单独作为参数 q 传递的，
        # routes.py: req.history 里面是之前的完整对话，不包含当前 question)
        # 偶数条代表完整的问答轮数
        total_rounds = total_len // 2

        max_rounds = self._cfg.max_raw_rounds
        min_rounds = self._cfg.min_raw_rounds

        # 轮数未超限，不触发总结
        if total_rounds <= max_rounds:
            return history, None

        # 超出上限，切分出 older_history 和 recent_history
        # 保留最近的 min_rounds 轮为原始消息
        recent_msg_count = min_rounds * 2
        older_history = history[:-recent_msg_count]
        recent_history = history[-recent_msg_count:]

        # 针对 older_history 计算 Hash 缓存 key
        older_hash = self._hash_history(older_history)
        with self._lock:
            cached_summary = self._cache.get(older_hash)
            if cached_summary:
                logger.info("history_compressor: 摘要缓存命中 (0ms) | HASH=%s", older_hash[:8])
                return recent_history, cached_summary

            now = time.monotonic()
            if self._background_task is not None or now < self._cooldown_until:
                return history, None

            task = threading.Thread(
                target=self._summarize_in_background,
                args=(older_hash, older_history, history, total_rounds),
                name="history-summary",
                daemon=True,
            )
            self._background_task = task
            task.start()

        return history, None

    def _summarize_in_background(
        self,
        older_hash: str,
        older_history: list[dict],
        history: list[dict],
        total_rounds: int,
    ) -> None:
        try:
            summary = self._generate_summary(older_history, history, total_rounds)
            with self._lock:
                if summary:
                    self._cache.put(older_hash, summary)
                    logger.info(
                        "history_compressor: 后台摘要生成并存入缓存 | 长度=%d | HASH=%s",
                        len(summary),
                        older_hash[:8],
                    )
                else:
                    self._cooldown_until = time.monotonic() + self._cfg.failure_cooldown_seconds
                    logger.warning("history_compressor: 后台摘要失败，进入全局冷却")
        except Exception:
            with self._lock:
                self._cooldown_until = time.monotonic() + self._cfg.failure_cooldown_seconds
            logger.exception("history_compressor: 后台摘要任务异常")
        finally:
            with self._lock:
                self._background_task = None
