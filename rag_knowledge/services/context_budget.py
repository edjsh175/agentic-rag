"""
Context auto-trimming for token budget control.

Before sending context and history to the LLM, estimate token usage and trim:
1. history first, from oldest forward
2. then low-quality context chunks, from lowest `quality_score` upward
3. system/question reserves are never trimmed here

Known limitation:
- once only one source chunk remains, this manager keeps it even if that final
  chunk still exceeds the calculated context budget.
"""

from __future__ import annotations

import logging

from rag_knowledge.config import ContextBudgetConfig

logger = logging.getLogger(__name__)


class _TokenEstimator:
    """Internal token-estimation boundary for future tokenizer upgrades."""

    def estimate(self, text: str) -> int:
        raise NotImplementedError


class _CharTokenEstimator(_TokenEstimator):
    def __init__(self, chars_per_token: float):
        self._chars_per_token = chars_per_token

    def estimate(self, text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(text) / self._chars_per_token))


class ContextBudgetManager:
    """Trim context and history according to an estimated token budget."""

    def __init__(
        self,
        cfg: ContextBudgetConfig,
        estimator: _TokenEstimator | None = None,
    ):
        self._cfg = cfg
        self._estimator = estimator or _CharTokenEstimator(cfg.chars_per_token)

    def estimate_tokens(self, text: str) -> int:
        return self._estimator.estimate(text)

    def trim(
        self,
        source_docs: list[dict],
        context: str,
        history: list | None,
        question: str,
        agent_prompt: str | None = None,
    ) -> tuple[list[dict], str, list | None]:
        """
        Trim context and history by estimated token usage.

        Returns:
            (trimmed_source_docs, trimmed_context, trimmed_history)
        """
        cfg = self._cfg
        if not cfg.enabled:
            return source_docs, context, history

        fixed_tokens = cfg.system_reserve + cfg.question_reserve
        if agent_prompt:
            fixed_tokens += self.estimate_tokens(agent_prompt)

        available = cfg.context_window - cfg.generation_reserve - fixed_tokens
        if available <= 0:
            logger.warning(
                "context_budget: fixed reserve (%d tokens) exceeds available budget; skip trim",
                fixed_tokens,
            )
            return source_docs, context, history

        context_budget = int(available * cfg.context_ratio)
        history_budget = available - context_budget

        trimmed_history = list(history) if history else []
        history_tokens_before = self._estimate_history_tokens(trimmed_history)
        while trimmed_history and self._estimate_history_tokens(trimmed_history) > history_budget:
            trimmed_history.pop(0)
        history_tokens_after = self._estimate_history_tokens(trimmed_history)

        removed_history_messages = history_tokens_before != history_tokens_after
        if removed_history_messages:
            removed_rounds = (len(history or []) - len(trimmed_history)) // 2
            logger.info(
                "context_budget: history trimmed | removed %d rounds | %d -> %d tokens | budget=%d",
                removed_rounds,
                history_tokens_before,
                history_tokens_after,
                history_budget,
            )

        context_budget += max(0, history_budget - history_tokens_after)

        trimmed_docs = list(source_docs)
        context_tokens_before = self.estimate_tokens(context)
        if context_tokens_before > context_budget and len(trimmed_docs) > 1:
            ordered_docs = sorted(trimmed_docs, key=self._quality_score)
            for doc in ordered_docs:
                if len(trimmed_docs) <= 1:
                    break
                current_context = _rebuild_context(trimmed_docs)
                if self.estimate_tokens(current_context) <= context_budget:
                    break
                if doc in trimmed_docs:
                    trimmed_docs.remove(doc)

        trimmed_context = (
            _rebuild_context(trimmed_docs) if trimmed_docs != source_docs else context
        )
        context_tokens_after = self.estimate_tokens(trimmed_context)

        removed_chunks = len(source_docs) - len(trimmed_docs)
        if removed_chunks > 0:
            logger.info(
                "context_budget: context trimmed | removed %d chunks | %d -> %d tokens | budget=%d",
                removed_chunks,
                context_tokens_before,
                context_tokens_after,
                context_budget,
            )

        total_estimated = fixed_tokens + context_tokens_after + history_tokens_after
        logger.debug(
            "context_budget: total=%d | window=%d | fixed=%d | context=%d | history=%d",
            total_estimated,
            cfg.context_window,
            fixed_tokens,
            context_tokens_after,
            history_tokens_after,
        )

        return (
            trimmed_docs,
            trimmed_context,
            trimmed_history if trimmed_history else None,
        )

    def _estimate_history_tokens(self, history: list[dict]) -> int:
        return sum(self.estimate_tokens(item.get("content", "")) for item in history)

    @staticmethod
    def _quality_score(doc: dict) -> float:
        try:
            return float(doc.get("metadata", {}).get("quality_score", 0.0))
        except (TypeError, ValueError):
            return 0.0


def _rebuild_context(source_docs: list[dict]) -> str:
    """Rebuild context text from normalized source docs."""
    parts = []
    for item in source_docs:
        meta = item.get("metadata") or {}
        label = "外部来源" if meta.get("source_type") == "external" else "知识库来源"
        url = f" URL: {meta['url']}" if meta.get("url") else ""
        file_name = meta.get("file_name") or meta.get("source") or "未知文件"
        page_label = meta.get("page_label") or meta.get("page") or "-"
        cid = meta.get("citation_id", "-")
        category = meta.get("category") or meta.get("doc_category", "未知")
        parts.append(
            f"[{cid}] [{label}] 文件: {file_name} | "
            f"页码: {page_label} | 类型: {category}{url}\n"
            f"文档片段：{item.get('content', '')}"
        )
    return "\n\n---\n\n".join(parts)
