"""
历史消息压缩与摘要服务

在历史对话较长时，将较旧的历史记录压缩为文本摘要，存入 LRU 缓存，
只发送最新几轮原始对话 + 摘要，以此节省上下文 token 且避免完全遗忘。

基于增量总结算法，并使用 LRU 缓存避免每次都调用 LLM 总结。

Phase 0：缓存未命中时默认确定性降级为 truncate_recent（仍后台预热摘要），
避免当次静默传全量 history。
"""
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal, Optional

from rag_knowledge.config import Config, HistoryCompressionConfig

logger = logging.getLogger(__name__)

CacheMissPolicy = Literal["truncate_recent", "async"]


@dataclass
class CompressResult:
    history: list[dict] | None
    summary: Optional[str]
    fallback: Literal[
        "none",
        "summary_cache",
        "truncate_recent",
        "cooldown_truncate",
        "disabled",
    ]
    scheduled_background: bool = False
    background_busy: bool = False
    pending_rewarm_queued: bool = False
    older_hash_prefix: str = ""
    reason: str = ""


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
        # 后台忙碌时只保留「最新」一次 older 预热请求；完成后若仍未命中则续跑。
        self._pending_job: tuple[str, list[dict], list[dict], int] | None = None

    def _cache_get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._cache.get(key)

    def _cache_put(self, key: str, value: str) -> None:
        with self._lock:
            self._cache.put(key, value)

    def _start_background_summary(
        self,
        older_hash: str,
        older_history: list[dict],
        history: list[dict],
        total_rounds: int,
    ) -> bool:
        """启动后台摘要线程。调用方须已持有锁或确认无并发竞态。返回是否新启动。"""
        if self._background_task is not None:
            self._pending_job = (
                older_hash,
                list(older_history),
                list(history),
                int(total_rounds),
            )
            return False
        task = threading.Thread(
            target=self._summarize_in_background,
            args=(older_hash, list(older_history), list(history), int(total_rounds)),
            name="history-summary",
            daemon=True,
        )
        self._background_task = task
        task.start()
        return True

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
        """调用配置的 helper_llm 进行摘要生成"""
        try:
            from rag_knowledge.llm_http import chat_role

            logger.debug("开始调用 helper_llm 压缩对话历史...")
            content = chat_role(
                self._main_cfg,
                "llm",
                [
                    {
                        "role": "system",
                        "content": (
                            "你是严谨的历史对话摘要助手。只输出半结构化中文摘要，"
                            "格式必须恰好四行：\n"
                            "主题：...\n"
                            "实体：...\n"
                            "结论：...\n"
                            "未决：...\n"
                            "禁止编造知识库事实；禁止输出解释、<think> 或 markdown。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                num_predict=256,
                timeout=45.0,
                think=False,
                num_ctx=self._main_cfg.context_budget.context_window,
            )
            return self._ensure_structured_summary((content or "").strip())
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
                f"请合并为最新半结构化摘要，必须恰好四行：\n"
                f"主题：...\n实体：...\n结论：...\n未决：...\n"
                f"保留已确认实体与未决问题，删去闲聊，总长不超过 300 字。"
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
        """对全部 older_history 生成半结构化摘要。"""
        history_text = self._format_history_text(older_history)
        prompt = (
            f"请根据以下用户和助手的历史对话，生成半结构化中文摘要。\n"
            f"必须恰好四行：\n"
            f"主题：...\n实体：...\n结论：...\n未决：...\n"
            f"要求：删去寒暄与重叠讨论；不编造知识库事实；总长不超过 300 字。\n\n"
            f"对话历史：\n"
            f"\"\"\"\n{history_text}\n\"\"\""
        )
        logger.info("history_compressor: 全量总结触发 | 条数=%d", len(older_history))
        return self._call_llm_summarize(prompt)

    @staticmethod
    def _ensure_structured_summary(text: str) -> str:
        """确保摘要为半结构化四行；散文则包裹，避免无约束长文。"""
        raw = (text or "").strip()
        if not raw:
            return ""
        has_topic = ("主题：" in raw) or ("主题:" in raw)
        has_entity = ("实体：" in raw) or ("实体:" in raw)
        if has_topic and has_entity:
            return raw[:400]
        compact = " ".join(raw.split())[:200]
        return (
            f"主题：对话延续\n"
            f"实体：\n"
            f"结论：{compact}\n"
            f"未决：\n"
        )

    def compress(
        self,
        history: list[dict] | None,
        *,
        on_cache_miss: CacheMissPolicy = "truncate_recent",
    ) -> tuple[list[dict] | None, Optional[str]]:
        """
        压缩历史记录。

        参数：
          history: 原始对话历史列表 [{"role": "user", "content": "..."}]
          on_cache_miss:
            - truncate_recent（默认）：未命中时立即返回最近 N 轮，后台预热摘要
            - async：旧行为，未命中时当次仍返回全量 history

        返回：
          (recent_history, history_summary)
          - recent_history: 裁剪后保留的最近 N 轮原始对话（可以传给 messages）
          - history_summary: 较早对话的总结摘要（需要注入到 system prompt 中），没有则为 None
        """
        result = self.compress_detailed(history, on_cache_miss=on_cache_miss)
        return result.history, result.summary

    def compress_detailed(
        self,
        history: list[dict] | None,
        *,
        on_cache_miss: CacheMissPolicy = "truncate_recent",
    ) -> CompressResult:
        """带决策元数据的压缩，供 GenerationPack / trace 使用。"""
        if not self._cfg.enabled or not history or len(history) < 2:
            return CompressResult(
                history=history,
                summary=None,
                fallback="disabled" if not self._cfg.enabled else "none",
                reason="disabled_or_short",
            )

        total_len = len(history)
        total_rounds = total_len // 2
        max_rounds = self._cfg.max_raw_rounds
        min_rounds = self._cfg.min_raw_rounds

        if total_rounds <= max_rounds:
            return CompressResult(
                history=history,
                summary=None,
                fallback="none",
                reason="within_limit",
            )

        recent_msg_count = min_rounds * 2
        older_history = history[:-recent_msg_count]
        recent_history = history[-recent_msg_count:]
        older_hash = self._hash_history(older_history)
        hash_prefix = older_hash[:8]

        with self._lock:
            cached_summary = self._cache.get(older_hash)
            if cached_summary:
                logger.info("history_compressor: 摘要缓存命中 (0ms) | HASH=%s", hash_prefix)
                return CompressResult(
                    history=recent_history,
                    summary=cached_summary,
                    fallback="summary_cache",
                    older_hash_prefix=hash_prefix,
                    reason="cache_hit",
                )

            now = time.monotonic()
            in_cooldown = now < self._cooldown_until
            busy = self._background_task is not None
            scheduled = False
            pending_queued = False
            if not busy and not in_cooldown:
                scheduled = self._start_background_summary(
                    older_hash, older_history, history, total_rounds
                )
            elif busy:
                self._pending_job = (
                    older_hash,
                    list(older_history),
                    list(history),
                    total_rounds,
                )
                pending_queued = True
                logger.info(
                    "history_compressor: 缓存未命中且后台忙碌，排队最新 older | HASH=%s | keep=%d",
                    hash_prefix,
                    len(recent_history),
                )

        if on_cache_miss == "async" and not in_cooldown:
            return CompressResult(
                history=history,
                summary=None,
                fallback="none",
                scheduled_background=scheduled or busy,
                background_busy=busy and not scheduled,
                pending_rewarm_queued=pending_queued,
                older_hash_prefix=hash_prefix,
                reason="async_miss_full_history",
            )

        fallback = "cooldown_truncate" if in_cooldown else "truncate_recent"
        logger.info(
            "history_compressor: 缓存未命中确定性降级 | fallback=%s | keep=%d | "
            "scheduled=%s | busy=%s | pending=%s",
            fallback,
            len(recent_history),
            scheduled or busy,
            busy and not scheduled,
            pending_queued,
        )
        return CompressResult(
            history=recent_history,
            summary=None,
            fallback=fallback,
            scheduled_background=scheduled or busy,
            background_busy=busy and not scheduled,
            pending_rewarm_queued=pending_queued,
            older_hash_prefix=hash_prefix,
            reason=fallback,
        )

    def _summarize_in_background(
        self,
        older_hash: str,
        older_history: list[dict],
        history: list[dict],
        total_rounds: int,
    ) -> None:
        summary = ""
        try:
            summary = self._generate_summary(older_history, history, total_rounds) or ""
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
            next_job = None
            with self._lock:
                self._background_task = None
                pending = self._pending_job
                self._pending_job = None
                in_cooldown = time.monotonic() < self._cooldown_until
                if pending and not in_cooldown:
                    pending_hash = pending[0]
                    if self._cache.get(pending_hash) is None:
                        next_job = pending
                        logger.info(
                            "history_compressor: 续跑过期/排队的最新 older | HASH=%s",
                            pending_hash[:8],
                        )
            if next_job:
                with self._lock:
                    self._start_background_summary(*next_job)
