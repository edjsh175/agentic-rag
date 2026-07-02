"""
Context 自动裁剪 —— Token 预算控制

在将 context 和 history 发送给 LLM 之前，按 token 预算自动裁剪，
防止超出模型上下文窗口（静默截断导致 system prompt 丢失）。

裁剪优先级（从低到高，即先裁剪优先级低的）：
  1. 对话历史（最先裁剪）—— 从最旧的历史开始删除
  2. 低相关度 context chunk —— 按 quality_score 从低到高移除
  3. 系统提示词 + 当前问题 —— 永不裁剪（作为最低保障）
"""
import logging
from rag_knowledge.config import ContextBudgetConfig

logger = logging.getLogger(__name__)


class ContextBudgetManager:
    """按 token 预算裁剪 context 和 history"""

    def __init__(self, cfg: ContextBudgetConfig):
        self._cfg = cfg

    def estimate_tokens(self, text: str) -> int:
        """用字符数估算 token 数（快速，无网络开销）"""
        if not text:
            return 0
        return max(1, int(len(text) / self._cfg.chars_per_token))

    def trim(
        self,
        source_docs: list[dict],
        context: str,
        history: list | None,
        question: str,
        agent_prompt: str | None = None,
    ) -> tuple[list[dict], str, list | None]:
        """
        按 token 预算裁剪 context 和 history。

        返回：(裁剪后的 source_docs, 裁剪后的 context 文本, 裁剪后的 history)
        """
        cfg = self._cfg
        if not cfg.enabled:
            return source_docs, context, history

        # ---- 1. 计算固定开销（不可裁剪部分）----
        fixed_tokens = cfg.system_reserve + cfg.question_reserve
        if agent_prompt:
            fixed_tokens += self.estimate_tokens(agent_prompt)

        # ---- 2. 计算可用预算 ----
        available = cfg.context_window - cfg.generation_reserve - fixed_tokens
        if available <= 0:
            logger.warning(
                "context_budget: 固定开销 (%d tokens) 已超出可用预算，跳过裁剪",
                fixed_tokens,
            )
            return source_docs, context, history

        context_budget = int(available * cfg.context_ratio)
        history_budget = available - context_budget

        # ---- 3. 裁剪 history（从最旧开始删除）----
        trimmed_history = list(history) if history else []
        if trimmed_history:
            history_tokens_before = sum(
                self.estimate_tokens(h.get("content", "")) for h in trimmed_history
            )
            while trimmed_history:
                used = sum(
                    self.estimate_tokens(h.get("content", "")) for h in trimmed_history
                )
                if used <= history_budget:
                    break
                trimmed_history.pop(0)  # 移除最旧的一条
            history_tokens_after = sum(
                self.estimate_tokens(h.get("content", "")) for h in trimmed_history
            )
            removed_rounds = (len(history) - len(trimmed_history)) // 2
            if removed_rounds > 0:
                logger.info(
                    "context_budget: history 裁剪 | 移除 %d 轮 | %d → %d tokens | budget=%d",
                    removed_rounds,
                    history_tokens_before,
                    history_tokens_after,
                    history_budget,
                )

        # ---- 4. 如果 history 使用的预算少于 history_budget，把节省出的预算让给 context ----
        actual_history_tokens = sum(
            self.estimate_tokens(h.get("content", "")) for h in trimmed_history
        )
        context_budget += max(0, history_budget - actual_history_tokens)

        # ---- 5. 裁剪 context chunk（按 quality_score 从低到高移除）----
        trimmed_docs = list(source_docs)
        context_tokens_before = self.estimate_tokens(context)

        if context_tokens_before > context_budget and len(trimmed_docs) > 1:
            # 按 quality_score 升序排列（分数低的优先移除）
            scored = sorted(
                range(len(trimmed_docs)),
                key=lambda i: float(
                    trimmed_docs[i].get("metadata", {}).get("quality_score", 0.0)
                ),
            )
            for idx in scored:
                if len(trimmed_docs) <= 1:
                    break
                # 估算当前 context 的 token 数
                current_tokens = self.estimate_tokens(
                    _rebuild_context(trimmed_docs)
                )
                if current_tokens <= context_budget:
                    break
                # 找到并移除该文档
                doc_to_remove = source_docs[idx]
                if doc_to_remove in trimmed_docs:
                    trimmed_docs.remove(doc_to_remove)

        trimmed_context = _rebuild_context(trimmed_docs) if trimmed_docs != source_docs else context
        context_tokens_after = self.estimate_tokens(trimmed_context)

        removed_chunks = len(source_docs) - len(trimmed_docs)
        if removed_chunks > 0:
            logger.info(
                "context_budget: context 裁剪 | 移除 %d chunk | %d → %d tokens | budget=%d",
                removed_chunks,
                context_tokens_before,
                context_tokens_after,
                context_budget,
            )

        # ---- 6. 调试日志 ----
        total_estimated = fixed_tokens + context_tokens_after + actual_history_tokens
        logger.debug(
            "context_budget: 总估算 %d tokens | 窗口 %d | 固定 %d | context %d | history %d",
            total_estimated,
            cfg.context_window,
            fixed_tokens,
            context_tokens_after,
            actual_history_tokens,
        )

        return (
            trimmed_docs,
            trimmed_context,
            trimmed_history if trimmed_history else None,
        )


def _rebuild_context(source_docs: list[dict]) -> str:
    """从 source_docs 重建 context 文本（与 RagChain._format_context 保持一致）"""
    parts = []
    for item in source_docs:
        meta = item["metadata"]
        label = "外部来源" if meta.get("source_type") == "external" else "知识库来源"
        url = f" URL: {meta['url']}" if meta.get("url") else ""
        parts.append(
            f"[{meta['citation_id']}] [{label}] 文件: {meta['file_name']} | "
            f"页码: {meta['page_label']} | 类型: {meta.get('category', '未知')}{url}\n"
            f"文档片段：{item['content']}"
        )
    return "\n\n---\n\n".join(parts)
