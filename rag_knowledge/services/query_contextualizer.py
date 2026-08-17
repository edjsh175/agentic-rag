"""
对话式查询上下文化助手（Conversation Contextualizer）

将依赖历史的追问（如"再详细说明一下""第5步是什么意思？"）改写成
适合知识库检索的独立查询，同时不破坏已经完整的独立问题。

核心职责：
- 输入：当前用户问题 + 最近几轮 history + 上一轮 assistant 的来源摘要
- 输出：{ standalone_query, search_queries, is_context_dependent, confidence }

不写死规则，用 LLM 判断；LLM 不可用时回退到启发式方法。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from rag_knowledge.config import Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalQuery:
    """一条带来源类型和融合权重的检索查询。"""

    text: str
    kind: str
    weight: float

# ------------------------------------------------------------------
# 上下文化 Prompt
# ------------------------------------------------------------------

_CONTEXTUALIZE_PROMPT = """你是对话式 RAG 查询上下文化助手。
你不会回答问题，只负责把当前用户问题改写成适合知识库检索的独立查询。

要求：
1. 如果当前问题依赖历史（如"再详细说明一下""第3步呢？""为什么？"），
   请结合对话焦点、历史摘要、最近对话和上一轮来源生成独立问题。
2. 如果当前问题已经完整独立（包含具体对象、技术名词、错误信息等），
   不要强行改写，只需微调使其更适合检索。
3. 保留技术名词、文件名、版本号、产品名、章节名。
4. 输出严格 JSON，不要解释，不要 markdown 代码块。

检索短记忆：
{history_text}

上一轮来源摘要：
{sources_text}

当前问题：
{question}

输出 JSON：
{{"is_context_dependent": true/false, "standalone_query": "改写后的独立问题", "search_queries": ["多角度搜索1", "多角度搜索2"], "confidence": 0.0-1.0}}"""

# ------------------------------------------------------------------
# 启发式关键词（LLM 不可用时的降级方案）
# ------------------------------------------------------------------

# 明显的上下文依赖模式
_CONTEXT_DEPENDENT_PATTERNS = [
    r"^(再|继续|接着|进一步|详细|具体|仔细).*(说|讲|解释|说明|介绍|描述|展开|补充)",
    r"^(什么意思|为什么|怎么做|怎么办|如何操作|如何实现)",
    r"^第\s*\d+\s*[步条项个].*",
    r"^[它这那]个?(是|有|会|能|可以|怎么|如何|为什么)",
    r"^(还有|另外|其他|别的).*",
    r"^(上面|前面|刚才|之前|上一个|刚刚).*",
    r"^(这个|那个|这些|那些)\s*(问题|错误|报错|情况|现象|配置|文件|命令)",
    r"^(能|可以|能否).*(再|更|进一步|多说|补充)",
    r"^(然后|接着|之后|下一步)[呢呀]?$",
    r"^[和跟与]?(上面|前面|之前|刚才).*(一样|类似|相同|相关)",
    r"^(具体|详细|仔细).*(步骤|流程|方法|做法|配置|操作)",
]

# 明显的完整独立问题模式（包含具体名词/技术术语）
_INDEPENDENT_PATTERNS = [
    r"(?i)(docker|k8s|kubernetes|nginx|mysql|redis|git|linux|python|java|node)",
    r"(?i)(报错|[错误异常]信息|error|exception|failed|timeout|denied)",
    r"(?i)(怎么|如何).*(安装|部署|配置|启动|运行|编译|调试|测试|优化)",
    r"(?i)(Rocky|CentOS|Ubuntu|Debian|Windows|macOS).*\d",
    r"(?i)(pip|npm|yum|apt|brew|git)\s+(install|update|upgrade)",
    r"^(什么是|什么叫|介绍一下|解释一下|请说明).*",
    r"(?i)(配置文件|环境变量|命令行|参数|选项|接口|API|SDK)",
]


def _is_context_dependent_heuristic(question: str) -> bool:
    """启发式判断：当前问题是否依赖历史上下文。"""
    q = question.strip()
    if not q:
        return False

    # 问题太短（≤6 个字符）大概率是追问
    if len(q) <= 6:
        return True

    # 先检查是否是明显的独立问题
    for pat in _INDEPENDENT_PATTERNS:
        if re.search(pat, q):
            return False

    # 再检查是否是明显的依赖问题
    for pat in _CONTEXT_DEPENDENT_PATTERNS:
        if re.search(pat, q):
            return True

    # 问题长度适中且无明显特征 → 偏向独立
    return False


def _extract_keywords_from_history(history_text: str) -> set[str]:
    """从历史对话中提取技术关键词（LLM 不可用时的降级方案）。"""
    keywords: set[str] = set()

    # 匹配技术名词模式
    patterns = [
        r"(?:Rocky|CentOS|Ubuntu|Debian|Windows|macOS)\s*(?:Linux)?\s*\d+(?:\.\d+)*",
        r"(?:Docker|Kubernetes|K8s|Nginx|Apache|MySQL|Redis|Git|Python|Java|Node\.js)",
        r"(?:ISO|镜像|下载|安装|配置|部署|启动|运行|编译)",
        r"(?:阿里云|腾讯云|华为云|AWS|Azure|GCP)",
        r"(?:minimal|DVD|Everything|netinstall)\.[a-z]+",
        r"(?:x86_64|aarch64|amd64|arm64)",
        r"[a-zA-Z0-9_-]+\.(?:md|pdf|docx?|txt|xlsx?)",
    ]
    for pat in patterns:
        for match in re.finditer(pat, history_text, re.IGNORECASE):
            keywords.add(match.group(0))

    return keywords


def _build_standalone_heuristic(
    question: str,
    history_text: str,
    last_user: str = "",
    max_len: int = 200,
) -> str:
    """基于启发式规则构建独立查询（LLM 不可用时的降级方案）。"""
    q = question.strip()

    # 提取上一轮的关键词
    keywords = _extract_keywords_from_history(history_text)
    if last_user:
        keywords.update(_extract_keywords_from_history(last_user))

    if not keywords:
        return q

    # 用上一轮关键词 + 当前问题构建独立查询
    kw_str = " ".join(sorted(keywords, key=len, reverse=True)[:8])

    if _is_context_dependent_heuristic(q):
        # 追问型：把关键词前置
        combined = f"{kw_str} {q}"
    else:
        # 独立型：保留原问题
        combined = q

    return combined[:max_len].strip()


def _clean_filename_for_query(filename: str) -> str:
    """从文件名中提取可检索的关键词。

    "0e57a89c3a3e-Linux--如何安装rockyLinux9虚拟机.md"
    → "Linux 如何安装rockyLinux9虚拟机"
    """
    if not filename:
        return ""
    # 去掉扩展名
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    # 去掉前导哈希（如 "0e57a89c3a3e-"）
    name = re.sub(r"^[a-fA-F0-9]{6,}-", "", name)
    # 将连字符和下划线替换为空格
    name = re.sub(r"[-_]+", " ", name)
    # 合并多余空格
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _extract_source_texts(sources: list[dict] | None) -> str:
    """从 sources 列表中提取可读文本摘要。"""
    if not sources:
        return "（无）"

    lines: list[str] = []
    for i, src in enumerate(sources[:4], 1):
        parts: list[str] = []
        if isinstance(src, dict):
            fn = src.get("file_name") or src.get("source", "")
            if fn:
                parts.append(f"文件: {fn}")
            st = src.get("section_title", "")
            if st:
                parts.append(f"章节: {st}")
            pg = src.get("page_label", "")
            if pg and pg != "无页码":
                parts.append(f"页码: {pg}")
        if parts:
            lines.append(f"  [{i}] " + " | ".join(parts))

    return "\n".join(lines) if lines else "（无）"


# ------------------------------------------------------------------
# QueryContextualizer
# ------------------------------------------------------------------


class QueryContextualizer:
    """对话式查询上下文化器。

    使用 LLM 将依赖历史的追问改写成独立的检索查询。
    LLM 不可用时回退到启发式方法。
    """

    def __init__(self, config: Config | None = None):
        if config is None:
            config = Config()
        self._cfg = config
        self._llm_model = config.helper_llm_model
        self._ollama_base = config.ollama_base_url
        self._timeout = 20  # 上下文化超时秒数，不宜太长

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def contextualize(
        self,
        question: str,
        history: list[dict] | None = None,
        *,
        protect_entities: bool = True,
        focus_text: str = "",
        rolling_summary: str = "",
        recent_rounds: int = 2,
    ) -> dict[str, Any]:
        """将用户问题上下文化，返回独立检索查询。

        Args:
            question: 当前用户问题
            history: 最近几轮对话，每条可含 role/content/sources
            protect_entities: 是否在此层做实体保护（Understanding 出口会统一保护时可关）
            focus_text: 结构化对话焦点短文本
            rolling_summary: 半结构化滚动摘要
            recent_rounds: 检索短记忆保留的最近轮数

        Returns:
            {
                "standalone_query": "改写后的独立问题",
                "search_queries": ["多角度搜索查询1", ...],
                "is_context_dependent": true/false,
                "confidence": 0.0-1.0,
            }
        """
        q = (question or "").strip()
        if not q:
            return {
                "standalone_query": q,
                "search_queries": [],
                "is_context_dependent": False,
                "confidence": 1.0,
            }

        history_text = self._format_history(
            history,
            focus_text=focus_text,
            rolling_summary=rolling_summary,
            recent_rounds=recent_rounds,
        )
        last_user = self._last_user_question(history)
        last_sources = self._last_sources(history)
        sources_text = _extract_source_texts(last_sources)

        # 尝试 LLM
        try:
            res = self._contextualize_via_llm(
                q, history_text, sources_text, last_user
            )
        except Exception as e:
            logger.warning("LLM 上下文化失败，回退到启发式: %s", e)
            res = self._contextualize_heuristic(q, history_text, last_user)

        if protect_entities:
            from rag_knowledge.services.query_entity_guard import (
                protect_rewritten_query,
                protect_query_list,
            )
            res["standalone_query"] = protect_rewritten_query(
                q, res["standalone_query"], last_user
            )
            res["search_queries"] = protect_query_list(
                q, res["search_queries"], last_user
            )

        return res

    def build_multi_queries(
        self,
        question: str,
        history: list[dict] | None = None,
    ) -> list[str]:
        """兼容旧调用方，返回查询文本列表。"""
        return [spec.text for spec in self.build_query_specs(question, history)]

    def build_query_specs(
        self,
        question: str,
        history: list[dict] | None = None,
        *,
        protect_entities: bool = True,
        focus_text: str = "",
        rolling_summary: str = "",
        recent_rounds: int = 2,
    ) -> list[RetrievalQuery]:
        """生成带类型与权重的多角度检索查询。"""
        specs, _meta = self.build_query_specs_with_meta(
            question,
            history,
            protect_entities=protect_entities,
            focus_text=focus_text,
            rolling_summary=rolling_summary,
            recent_rounds=recent_rounds,
        )
        return specs

    def build_query_specs_with_meta(
        self,
        question: str,
        history: list[dict] | None = None,
        *,
        protect_entities: bool = True,
        focus_text: str = "",
        rolling_summary: str = "",
        recent_rounds: int = 2,
        drop_history_anchors: bool = False,
    ) -> tuple[list[RetrievalQuery], dict[str, Any]]:
        """生成检索查询，并返回上下文化元数据（供 Understanding 一次消费）。

        历史锚点必须同时通过 LLM 判断、置信度门槛和本地启发式判断。
        启发式认为问题独立时拥有否决权，防止错误历史来源污染新问题。
        drop_history_anchors=True：主题漂移时强制切断 source_anchor / last_user。
        """
        q = (question or "").strip()
        if not q:
            return [], {
                "standalone_query": q,
                "search_queries": [],
                "is_context_dependent": False,
                "confidence": 1.0,
            }

        # 出口保护由 Understanding 统一做时，此处不再保护
        ctx = self.contextualize(
            q,
            history,
            protect_entities=False,
            focus_text=focus_text,
            rolling_summary=rolling_summary,
            recent_rounds=recent_rounds,
        )
        standalone = ctx.get("standalone_query", q)
        search_queries = ctx.get("search_queries", [])
        is_dependent = bool(ctx.get("is_context_dependent", False))
        confidence = float(ctx.get("confidence", 0.5))
        heuristic_dependent = _is_context_dependent_heuristic(q)
        use_history_anchors = (
            (not drop_history_anchors)
            and is_dependent
            and confidence >= 0.6
            and heuristic_dependent
        )

        candidates: list[RetrievalQuery] = [RetrievalQuery(q, "original", 1.0)]
        if standalone and standalone != q:
            candidates.append(RetrievalQuery(standalone, "standalone", 0.8))

        # 独立问题只保留一个扩展查询，避免改写本身造成查询泛化。
        for sq in search_queries:
            if sq:
                candidates.append(RetrievalQuery(sq, "search", 0.6))
                break

        last_user = self._last_user_question(history)
        source_anchors: list[str] = []
        if use_history_anchors:
            source_anchors = self._build_source_anchor_queries(
                self._last_sources(history)
            )
            for anchor in source_anchors:
                if anchor:
                    candidates.append(RetrievalQuery(anchor, "source_anchor", 0.3))
            if last_user and last_user != q:
                candidates.append(RetrievalQuery(last_user, "last_user", 0.3))

        seen: set[str] = set()
        result: list[RetrievalQuery] = []
        if protect_entities:
            from rag_knowledge.services.query_entity_guard import protect_rewritten_query
            protect = lambda text: protect_rewritten_query(q, text, last_user)
        else:
            protect = lambda text: text

        for candidate in candidates:
            text = candidate.text.strip()
            if not text or len(text) < 2:
                continue
            protected_text = protect(text)
            normalized = protected_text.casefold()
            if normalized not in seen:
                seen.add(normalized)
                result.append(RetrievalQuery(protected_text, candidate.kind, candidate.weight))

        logger.info(
            "query_context | dependent=%s confidence=%.2f heuristic_dependent=%s "
            "history_anchors=%s drop_anchors=%s candidates=%d unique=%d kinds=%s",
            is_dependent, confidence, heuristic_dependent, use_history_anchors,
            drop_history_anchors,
            len(candidates), len(result),
            [(item.kind, item.weight) for item in result[:6]],
        )
        meta = dict(ctx)
        meta["drop_history_anchors"] = bool(drop_history_anchors)
        return result[:6], meta

    def _build_source_anchor_queries(
        self, sources: list[dict] | None
    ) -> list[str]:
        """从上一轮来源生成锚点查询。

        用文件名和章节标题构造检索查询，即使改写失败也能拉回同一文档。
        """
        if not sources:
            return []

        anchors: list[str] = []
        for src in sources[:3]:
            if not isinstance(src, dict):
                continue
            parts: list[str] = []
            fn = src.get("file_name") or src.get("source", "")
            if fn:
                # 去掉扩展名和哈希前缀，提取可读部分
                clean_fn = _clean_filename_for_query(fn)
                if clean_fn:
                    parts.append(clean_fn)
            st = src.get("section_title", "")
            if st and st not in parts:
                parts.append(st)
            if parts:
                anchors.append(" ".join(parts))

        return anchors

    def extract_source_summary(
        self, source_docs: list[dict] | None
    ) -> list[dict]:
        """从检索返回的 source_documents 中提取轻量摘要，供前端传递到下一轮 history。

        Args:
            source_docs: RagChain 返回的 source_documents 列表

        Returns:
            精简后的来源摘要列表（只含关键元数据，不含完整 chunk 内容）
        """
        from rag_knowledge.services.conversation_context import extract_source_summaries

        return extract_source_summaries(source_docs)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _format_history(
        self,
        history: list[dict] | None,
        *,
        focus_text: str = "",
        rolling_summary: str = "",
        recent_rounds: int = 2,
    ) -> str:
        """将 history 格式化为检索短记忆（焦点 + 摘要 + 最近少量轮次）。"""
        from rag_knowledge.services.conversation_context import format_retrieval_memory

        return format_retrieval_memory(
            history,
            focus_text=focus_text,
            rolling_summary=rolling_summary,
            recent_rounds=recent_rounds,
        )

    def _last_user_question(self, history: list[dict] | None) -> str:
        """提取上一轮用户问题。"""
        if not history:
            return ""
        for h in reversed(history):
            if h.get("role") == "user":
                return h.get("content", "")
        return ""

    def _last_sources(self, history: list[dict] | None) -> list[dict] | None:
        """提取上一轮 assistant 的来源摘要。"""
        if not history:
            return None
        for h in reversed(history):
            if h.get("role") == "assistant" and h.get("sources"):
                return h["sources"]
        return None

    def _contextualize_via_llm(
        self,
        question: str,
        history_text: str,
        sources_text: str,
        last_user: str = "",
    ) -> dict[str, Any]:
        """通过 LLM 调用完成上下文化。"""
        prompt = _CONTEXTUALIZE_PROMPT.format(
            history_text=history_text,
            sources_text=sources_text,
            question=question,
        )

        from rag_knowledge.llm_http import chat_role

        raw = chat_role(
            self._cfg,
            "llm",
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            num_predict=512,
            timeout=float(self._timeout),
            think=False,
        ).strip()

        # 清洗可能的 markdown 代码块包装
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

        result = json.loads(cleaned)

        # 校验必要字段
        standalone = str(result.get("standalone_query", question)).strip()
        if not standalone or len(standalone) < 2:
            standalone = question

        search_queries = result.get("search_queries", [])
        if not isinstance(search_queries, list):
            search_queries = []
        search_queries = [str(sq).strip() for sq in search_queries if sq]
        if not search_queries:
            search_queries = [standalone]

        is_dependent = bool(result.get("is_context_dependent", False))
        confidence = float(result.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        logger.info(
            "LLM 上下文化: %s → %s (dependent=%s, confidence=%.2f)",
            question[:50], standalone[:60], is_dependent, confidence,
        )

        return {
            "standalone_query": standalone,
            "search_queries": search_queries,
            "is_context_dependent": is_dependent,
            "confidence": confidence,
        }

    def _contextualize_heuristic(
        self,
        question: str,
        history_text: str = "",
        last_user: str = "",
    ) -> dict[str, Any]:
        """启发式上下文化（LLM 不可用时的降级方案）。"""
        is_dependent = _is_context_dependent_heuristic(question)

        if is_dependent and history_text:
            standalone = _build_standalone_heuristic(
                question, history_text, last_user
            )
            # The local detector already confirmed a dependent follow-up; keep
            # confidence at the history-anchor threshold for offline fallback.
            confidence = 0.6
        else:
            standalone = question
            confidence = 0.85

        search_queries = [standalone]
        if is_dependent and len(standalone.split()) > 3:
            # 尝试多角度
            alt = f"{standalone} 详细步骤"
            if alt != standalone:
                search_queries.append(alt)

        logger.info(
            "启发式上下文化: %s → %s (dependent=%s)",
            question[:50], standalone[:60], is_dependent,
        )

        return {
            "standalone_query": standalone,
            "search_queries": search_queries[:3],
            "is_context_dependent": is_dependent,
            "confidence": confidence,
        }

    # ------------------------------------------------------------------
    # 测试辅助方法（供单元测试直接调用，不依赖 LLM）
    # ------------------------------------------------------------------

    def _detect_context_dependence(
        self, question: str, history_text: str = ""
    ) -> bool:
        """[测试辅助] 纯启发式检测，不调用 LLM。"""
        return _is_context_dependent_heuristic(question)

    def _build_standalone_query(
        self,
        question: str,
        last_assistant: str = "",
        last_user: str = "",
        last_sources: list[dict] | None = None,
    ) -> str:
        """[测试辅助] 纯启发式构建独立查询，不调用 LLM。"""
        history_parts: list[str] = []
        if last_user:
            history_parts.append(f"user: {last_user}")
        if last_assistant:
            history_parts.append(f"assistant: {last_assistant}")
        if last_sources:
            for src in last_sources:
                if isinstance(src, dict):
                    fn = src.get("file_name", "")
                    st = src.get("section_title", "")
                    if fn:
                        history_parts.append(f"source: {fn}")
                    if st:
                        history_parts.append(f"section: {st}")

        history_text = "\n".join(history_parts)
        return _build_standalone_heuristic(question, history_text, last_user)

    def _generate_search_queries(
        self, standalone_query: str
    ) -> list[str]:
        """[测试辅助] 从独立查询生成多角度搜索查询。"""
        q = standalone_query.strip()
        if not q:
            return []
        queries = [q]
        # 如果查询较长，尝试拆分
        words = q.split()
        if len(words) > 8:
            queries.append(" ".join(words[: len(words) // 2]))
        return queries


# ------------------------------------------------------------------
# 模块级便捷函数
# ------------------------------------------------------------------

_contextualizer: QueryContextualizer | None = None


def get_contextualizer() -> QueryContextualizer:
    """获取全局 QueryContextualizer 单例。"""
    global _contextualizer
    if _contextualizer is None:
        _contextualizer = QueryContextualizer()
    return _contextualizer
