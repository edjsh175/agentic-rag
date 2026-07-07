"""
检索后处理质量控制层（Phase 5）

对检索返回的文档列表进行后处理：
  1. 分数归一化 → quality_score
  2. 相似度阈值过滤
  3. Jaccard 相似度去重
  4. 动态 TopK 断崖截断
  5. 可选质量过滤组合

所有处理按 quality_score 降序进行，得分越高越相关。
"""
import re
import logging
from typing import Set

from langchain_core.documents import Document

from rag_knowledge.config import Config

logger = logging.getLogger(__name__)

_TABLE_QUERY_HINTS = ("规范", "要求", "字段", "表结构", "点表", "线表", "数据结构")
_TABLE_CONTENT_HINTS = ("字段名", "说明")
_TABLE_SECTION_TERMS = ("点表", "线表", "面表")


class RetrievalQualityStrategy:
    """检索后处理质量控制"""

    def __init__(self, config: Config):
        self._cfg = config.retrieval_quality
        self._cfg_structured = getattr(config, "structured_retrieval", None)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def apply(self, query: str, docs: list[Document]) -> list[Document]:
        """
        对检索结果执行质量控制后处理。

        参数：
          query: 用户问题（保留参数以兼容现有调用）
          docs:  检索返回的 Document 列表

        返回：处理后的 Document 列表，按 quality_score 降序
        """
        if not docs:
            return docs

        # Check if structured retrieval boost is enabled.
        # Fallback if self._cfg_structured is not loaded (e.g. mocked in raw unit tests).
        cfg_structured = getattr(self, "_cfg_structured", None)
        if cfg_structured is None:
            if not self._cfg.enabled:
                return docs
            struct_enabled = True
        else:
            struct_enabled = getattr(cfg_structured, "enabled", True)

        # We normalize scores if EITHER structured boost is enabled OR quality filters are enabled
        if struct_enabled or self._cfg.enabled:
            docs = self._normalize_scores(docs)

        # 1.5 表格类问题增强
        if struct_enabled:
            docs = self._boost_table_chunks(query, docs)

        if not self._cfg.enabled:
            return docs

        debug = self._cfg.debug_log_enabled

        # 2. 相似度阈值过滤
        if self._cfg.score_threshold_enabled:
            docs = self._filter_by_score(docs)

        # 3. Jaccard 去重
        if self._cfg.jaccard_dedup_enabled:
            docs = self._deduplicate_by_jaccard(docs)

        # 4. 动态 TopK 断崖截断
        if self._cfg.dynamic_topk_enabled:
            docs = self._truncate_by_score_drop(docs)

        # Contextual compression now runs in RagChain._retrieve().
        if debug:
            scores = [f"{float(d.metadata.get('quality_score', 0)):.3f}" for d in docs]
            logger.debug(
                "检索质量控制完成 | doc数=%d | scores=%s",
                len(docs), scores,
            )

        return docs

    def _boost_table_chunks(self, query: str, docs: list[Document]) -> list[Document]:
        """Apply a small structured-data bonus for table-oriented queries."""
        normalized_query = (query or "").strip()
        if not normalized_query or not any(hint in normalized_query for hint in _TABLE_QUERY_HINTS):
            return docs

        table_boost = getattr(self._cfg_structured, "table_boost", 0.03) if self._cfg_structured else 0.03
        table_header_boost = getattr(self._cfg_structured, "table_header_boost", 0.01) if self._cfg_structured else 0.01
        section_match_boost = getattr(self._cfg_structured, "section_match_boost", 0.02) if self._cfg_structured else 0.02

        for doc in docs:
            metadata = doc.metadata or {}
            bonus = 0.0
            if metadata.get("content_type") == "table":
                bonus += table_boost
            if metadata.get("chunking_method") == "table":
                bonus += table_boost * 0.5
            page_content = doc.page_content or ""
            if all(hint in page_content for hint in _TABLE_CONTENT_HINTS):
                bonus += table_header_boost

            searchable_text = metadata.get("searchable_text") or page_content
            for term in _TABLE_SECTION_TERMS:
                if term in normalized_query and term in searchable_text:
                    bonus += section_match_boost

            if bonus <= 0:
                continue
            metadata["table_query_boost"] = round(bonus, 4)
            metadata["quality_score"] = float(metadata.get("quality_score", 0.0)) + bonus
            doc.metadata = metadata

        return sorted(
            docs,
            key=lambda d: float(d.metadata.get("quality_score", 0.0)),
            reverse=True,
        )

    # ------------------------------------------------------------------
    # 1. 分数归一化
    # ------------------------------------------------------------------

    def _normalize_scores(self, docs: list[Document]) -> list[Document]:
        """
        统一将多种来源的分数转换成 quality_score。

        优先级：rerank_score > rrf_score > similarity_score > score > 位置兜底

        quality_score 越高越相关。
        """
        for rank, doc in enumerate(docs):
            metadata = doc.metadata or {}

            if "rerank_score" in metadata:
                qs = float(metadata["rerank_score"])
            elif "rrf_score" in metadata:
                qs = float(metadata["rrf_score"])
            elif "similarity_score" in metadata:
                qs = float(metadata["similarity_score"])
            elif "score" in metadata:
                qs = float(metadata["score"])
            else:
                # 位置兜底：越靠前分数越高
                qs = 1.0 / (rank + 1)

            metadata["quality_score"] = qs
            doc.metadata = metadata

        return sorted(
            docs,
            key=lambda d: float(d.metadata.get("quality_score", 0.0)),
            reverse=True,
        )

    # ------------------------------------------------------------------
    # 2. 相似度阈值过滤
    # ------------------------------------------------------------------

    def _filter_by_score(self, docs: list[Document]) -> list[Document]:
        """丢弃 quality_score 低于阈值的 chunk。"""
        threshold = self._cfg.score_threshold
        before = len(docs)

        filtered = [
            doc for doc in docs
            if float(doc.metadata.get("quality_score", 0.0)) >= threshold
        ]

        # 防止全部过滤光：至少保留 min_top_k 个
        if not filtered:
            logger.warning(
                "score filter 移除了全部 %d 个 doc (threshold=%s)，回退保留前 %d 个",
                before, threshold, self._cfg.min_top_k,
            )
            return docs[: self._cfg.min_top_k]

        if self._cfg.debug_log_enabled:
            logger.info(
                "score filter | before=%d | after=%d | threshold=%s",
                before, len(filtered), threshold,
            )

        return filtered

    # ------------------------------------------------------------------
    # 3. Jaccard 去重
    # ------------------------------------------------------------------

    def _deduplicate_by_jaccard(self, docs: list[Document]) -> list[Document]:
        """
        Jaccard 相似度 > threshold 的 chunk 只保留 quality_score 最高的。

        输入已按 quality_score 降序排列，贪心遍历即可。
        """
        threshold = self._cfg.jaccard_threshold
        before = len(docs)
        kept: list[Document] = []

        for doc in docs:
            is_dup = False
            for kept_doc in kept:
                sim = self._jaccard(doc.page_content, kept_doc.page_content)
                if sim > threshold:
                    is_dup = True
                    break
            if not is_dup:
                kept.append(doc)

        if self._cfg.debug_log_enabled:
            logger.info(
                "jaccard dedup | before=%d | after=%d | threshold=%s",
                before, len(kept), threshold,
            )

        return kept

    @staticmethod
    def _tokenize(text: str) -> Set[str]:
        """对中文文本分词，返回去重后的词元集合。"""
        text = text.lower()
        try:
            import jieba
            tokens = jieba.lcut(text)
        except ImportError:
            # jieba 不可用时按空白字符切分
            tokens = text.split()

        result: Set[str] = set()
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            # 跳过纯标点/空白
            if re.fullmatch(r"\W+", token):
                continue
            result.add(token)

        return result

    @classmethod
    def _jaccard(cls, a: str, b: str) -> float:
        """计算两段文本的 Jaccard 相似度。"""
        set_a = cls._tokenize(a)
        set_b = cls._tokenize(b)

        if not set_a or not set_b:
            return 0.0

        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    # ------------------------------------------------------------------
    # 4. 动态 TopK 断崖截断
    # ------------------------------------------------------------------

    def _truncate_by_score_drop(self, docs: list[Document]) -> list[Document]:
        """
        从 min_top_k 开始检测相邻分数断崖。

        当 (prev_score - curr_score) / |prev_score| > score_drop_ratio 时，
        在当前位置截断。

        参数：
          min_top_k: 至少保留的数量（避免上下文不足）
          max_top_k: 最多保留的数量
          score_drop_ratio: 断崖判定比例
        """
        if len(docs) <= self._cfg.min_top_k:
            return docs

        min_k = self._cfg.min_top_k
        max_k = self._cfg.max_top_k
        drop_ratio = self._cfg.score_drop_ratio

        # 先截到上限
        docs = docs[:max_k]

        if len(docs) <= min_k:
            return docs

        for i in range(min_k, len(docs)):
            prev = float(docs[i - 1].metadata.get("quality_score", 0.0))
            curr = float(docs[i].metadata.get("quality_score", 0.0))

            if prev <= 0:
                continue

            drop = (prev - curr) / abs(prev)

            if drop > drop_ratio:
                truncated = docs[:i]
                if self._cfg.debug_log_enabled:
                    logger.info(
                        "dynamic topk | before=%d | after=%d | drop=%.4f | "
                        "prev_score=%.4f | curr_score=%.4f | ratio=%s",
                        len(docs), len(truncated), drop,
                        prev, curr, drop_ratio,
                    )
                return truncated

        if self._cfg.debug_log_enabled:
            logger.info(
                "dynamic topk | before=%d | after=%d | reason=no drop detected",
                len(docs), len(docs),
            )

        return docs
