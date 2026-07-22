"""
意图驱动的检索计划层。

QueryContextualizer 负责把追问改写为独立查询；QueryPlanner 负责根据问题意图
决定查询扩展、召回数量、候选池大小和是否启用 rerank。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from rag_knowledge.config import Config
from rag_knowledge.services.query_contextualizer import RetrievalQuery

logger = logging.getLogger(__name__)

_SUPPORTED_INTENTS = {
    "definition",
    "config",
    "procedure",
    "deployment",
    "troubleshooting",
    "comparison",
}

_PROCEDURE_PATTERNS = [
    r"(?i)(如何|怎么|怎样).*(使用|操作|部署|发布|配置|安装|启动|编译|搭建)",
    r"(?i)(操作|部署|发布|配置|安装|编译|搭建).*(流程|步骤|方法|过程|教程|指南)",
    r"(?i).*的(使用|操作|部署|发布).*步骤",
    r"(?i)(如何|怎么).*(进行|完成|实现).*",
]

_DEPLOYMENT_PATTERNS = [
    r"(?i)(如何|怎么|怎样).*(部署|搭建|上线|安装|启动|外网|内网)",
    r"(?i)(部署|搭建|上线|安装|启动).*(流程|步骤|方法|教程|指南)",
]

_TROUBLESHOOTING_PATTERNS = [
    r"(?i)(报错|错误|异常|失败|无法|不能|超时|failed|error|exception|timeout)",
    r"(?i)(怎么|如何).*(解决|排查|修复|处理)",
]

_COMPARISON_PATTERNS = [
    r"(?i)(区别|差异|不同|对比|比较|优缺点|哪个好|选哪个)",
    r"(?i).*(和|与|跟|vs|VS).*(区别|差异|对比|比较)",
]

_CONFIG_PATTERNS = [
    r"(?i)(配置|设置|参数|选项|字段).*(是什么|有哪些|包括|含义|说明)",
    r"(?i).*(工程设置|数据设置|参数设置|配置项)",
]

_CONFLICT_VALUE_PATTERNS = [
    r"(?i)(多个|不同|冲突|不一致|矛盾).{0,8}(值|取值|端口|配置|参数)",
    r"(?i)(值|取值|端口|配置|参数).{0,8}(多个|不同|冲突|不一致|矛盾)",
    r"(?i)(端口|配置|参数).{0,24}(是否|能否).{0,16}(一致|相同)",
    r"(?i)(出现|分别为|取值为).{0,24}\d{2,5}.{0,16}(与|和|及|/)\s*\d{2,5}",
    r"(?i)(表格|正文|配置示例).{0,24}\d{2,5}.{0,24}\d{2,5}",
]

_CONFLICT_TOP_K = 6
_CONFLICT_CANDIDATE_K = 18

_PROCEDURE_STAGE_WORDS = [
    "工程设置",
    "数据设置",
    "参数设置",
    "参数配置",
    "编译级别",
    "数据编译",
    "编译",
    "发布",
    "部署",
    "启动",
    "验证",
    "注意事项",
    "操作步骤",
    "配置流程",
]

_DEPLOYMENT_STAGE_WORDS = [
    "部署",
    "安装",
    "环境要求",
    "配置文件",
    "服务配置",
    "启动",
    "端口",
    "内网部署",
    "外网部署",
    "验证",
    "注意事项",
]

_TROUBLESHOOTING_STAGE_WORDS = [
    "报错",
    "错误信息",
    "原因",
    "排查",
    "解决方法",
    "日志",
    "配置检查",
    "注意事项",
]

_COMPARISON_STAGE_WORDS = [
    "区别",
    "对比",
    "差异",
    "用途",
    "适用场景",
    "优缺点",
]

_STOPWORDS = {
    "如何",
    "怎么",
    "怎样",
    "使用",
    "操作",
    "进行",
    "完成",
    "实现",
    "发布",
    "部署",
    "配置",
    "安装",
    "启动",
    "编译",
    "搭建",
    "流程",
    "步骤",
    "方法",
    "过程",
    "教程",
    "指南",
    "工具",
    "系统",
    "服务",
    "什么",
    "哪些",
    "包括",
    "一下",
    "详细",
    "说明",
    "介绍",
    "进行发布",
}

_INTENT_PROMPT = """你是 RAG 检索规划助手。你不会回答用户问题，只判断问题意图。

只能从以下意图中选择一个：
- definition：询问概念、是什么、含义、功能说明
- config：询问配置项、参数、设置含义
- procedure：询问使用方法、操作流程、发布流程、编译流程
- deployment：询问部署、安装、搭建、启动、内外网配置
- troubleshooting：询问报错、失败、无法运行、排查修复
- comparison：询问区别、对比、优缺点、选型

输出严格 JSON，不要解释，不要 markdown 代码块。

用户问题：{question}

输出 JSON：{{"intent":"definition|config|procedure|deployment|troubleshooting|comparison", "confidence":0.0-1.0}}"""


@dataclass(frozen=True)
class RetrievalPlan:
    """检索计划：由 QueryPlanner 生成，指导后续检索行为。"""

    intent: str
    queries: list[RetrievalQuery]
    top_k: int
    candidate_k: int
    enable_rerank: bool
    expand_neighbors: bool
    confidence: float
    linked_entities: tuple[Any, ...] = ()
    graph_queries: tuple[str, ...] = ()
    graph_chunk_ids: tuple[str, ...] = ()
    excluded_entity_ids: tuple[str, ...] = ()
    graph_revision: str = ""
    graph_fallback_reason: str | None = None
    intent_plan: object | None = None
    backbone_canonical: tuple[str, ...] = ()
    backbone_avoid: tuple[str, ...] = ()
    backbone_relation_summary: str = ""
    backbone_primary_intent: str = ""


class QueryPlanner:
    """通用查询规划器：意图分类 + 阶段词扩展 + 动态检索参数。"""

    def __init__(self, config: Config | None = None):
        self._cfg = config or Config()
        self._planner_cfg = self._cfg.query_planner
        self._llm_model = self._cfg.helper_llm_model
        self._ollama_base = self._cfg.ollama_base_url

    def plan(
        self,
        question: str,
        base_queries: Iterable[RetrievalQuery | str] | None = None,
        *,
        force_rerank: bool = False,
    ) -> RetrievalPlan:
        """根据问题和上下文化查询生成检索计划。"""
        q = (question or "").strip()
        base = self._normalize_base_queries(base_queries, q)

        if not self._planner_cfg.enabled:
            return self._default_plan(base, force_rerank=force_rerank)

        intent, confidence = self._classify_intent(q)
        queries = self._expand_queries(q, base, intent)
        from rag_knowledge.services.query_entity_guard import protect_rewritten_query
        protected_queries = []
        for query in queries:
            protected_text = protect_rewritten_query(q, query.text)
            protected_queries.append(RetrievalQuery(protected_text, query.kind, query.weight))
        queries = self._dedupe_queries(protected_queries)

        top_k, candidate_k, rerank_for_intent = self._params_for_intent(intent)
        if self._conflict_evidence_words(q):
            top_k = max(top_k, _CONFLICT_TOP_K)
            candidate_k = max(candidate_k, _CONFLICT_CANDIDATE_K)
        rerank_requested = force_rerank or rerank_for_intent
        enable_rerank = bool(self._cfg.reranker_enabled and rerank_requested)
        expand_neighbors = intent in {"procedure", "deployment"}

        logger.info(
            "query_plan | intent=%s confidence=%.2f queries=%d top_k=%d "
            "candidate_k=%d rerank=%s neighbors=%s",
            intent,
            confidence,
            len(queries),
            top_k,
            candidate_k,
            enable_rerank,
            expand_neighbors,
        )

        return RetrievalPlan(
            intent=intent,
            queries=queries,
            top_k=top_k,
            candidate_k=candidate_k,
            enable_rerank=enable_rerank,
            expand_neighbors=expand_neighbors,
            confidence=confidence,
        )

    def _default_plan(
        self,
        base_queries: list[RetrievalQuery],
        *,
        force_rerank: bool,
    ) -> RetrievalPlan:
        return RetrievalPlan(
            intent="definition",
            queries=base_queries,
            top_k=self._cfg.retrieval_top_k,
            candidate_k=self._cfg.retrieval_candidate_k,
            enable_rerank=bool(self._cfg.reranker_enabled and force_rerank),
            expand_neighbors=False,
            confidence=1.0,
        )

    def _classify_intent(self, question: str) -> tuple[str, float]:
        try:
            intent, confidence = self._classify_via_llm(question)
            if intent in _SUPPORTED_INTENTS and confidence >= 0.55:
                return intent, confidence
            logger.info(
                "query_plan LLM intent confidence low, fallback heuristic | intent=%s confidence=%.2f",
                intent,
                confidence,
            )
        except Exception as exc:
            logger.warning("query_plan LLM intent failed, fallback heuristic: %s", exc)
        return self._classify_heuristic(question)

    def _classify_via_llm(self, question: str) -> tuple[str, float]:
        resp = httpx.post(
            f"{self._ollama_base}/api/chat",
            json={
                "model": self._llm_model,
                "messages": [{"role": "user", "content": _INTENT_PROMPT.format(question=question)}],
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 96,
                    "top_k": 10,
                    "thinking": False,
                },
            },
            timeout=self._planner_cfg.llm_timeout,
        )
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "").strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        data: dict[str, Any] = json.loads(cleaned)
        intent = str(data.get("intent", "definition")).strip().lower()
        confidence = float(data.get("confidence", 0.5))
        return intent, max(0.0, min(1.0, confidence))

    def _classify_heuristic(self, question: str) -> tuple[str, float]:
        q = question.strip()
        if self._matches(_TROUBLESHOOTING_PATTERNS, q):
            return "troubleshooting", 0.78
        if self._matches(_COMPARISON_PATTERNS, q):
            return "comparison", 0.78
        if self._matches(_DEPLOYMENT_PATTERNS, q):
            return "deployment", 0.78
        if self._matches(_PROCEDURE_PATTERNS, q):
            return "procedure", 0.78
        if self._matches(_CONFIG_PATTERNS, q):
            return "config", 0.76
        return "definition", 0.70

    @staticmethod
    def _matches(patterns: list[str], text: str) -> bool:
        return any(re.search(pattern, text) for pattern in patterns)

    def _params_for_intent(self, intent: str) -> tuple[int, int, bool]:
        cfg = self._planner_cfg
        if intent in {"procedure", "deployment"}:
            return cfg.procedure_top_k, cfg.procedure_candidate_k, True
        if intent == "troubleshooting":
            return cfg.troubleshooting_top_k, cfg.troubleshooting_candidate_k, True
        if intent == "comparison":
            return cfg.comparison_top_k, cfg.comparison_candidate_k, True
        return self._cfg.retrieval_top_k, self._cfg.retrieval_candidate_k, self._cfg.reranker_enabled

    def _expand_queries(
        self,
        question: str,
        base_queries: list[RetrievalQuery],
        intent: str,
    ) -> list[RetrievalQuery]:
        conflict_words = self._conflict_evidence_words(question)
        stage_words = self._stage_words_for_intent(intent)
        expansion_words = conflict_words + stage_words
        if not expansion_words:
            return self._dedupe_queries(base_queries)

        entity = self._extract_core_entity(question, base_queries)
        if not entity:
            return self._dedupe_queries(base_queries)

        expanded = list(base_queries)
        max_expanded = self._planner_cfg.max_expanded_queries
        for word in expansion_words:
            kind = "planner_conflict" if word in conflict_words else "planner_stage"
            weight = 0.6 if kind == "planner_conflict" else 0.45
            expanded.append(RetrievalQuery(f"{entity} {word}", kind, weight))
            if len(expanded) >= max_expanded:
                break
        return self._dedupe_queries(expanded)[:max_expanded]

    def _conflict_evidence_words(self, question: str) -> list[str]:
        if not self._matches(_CONFLICT_VALUE_PATTERNS, question):
            return []
        if re.search(r"(?i)(端口|port)", question):
            return ["端口说明", "端口配置"]
        return ["配置说明", "参数说明"]

    @staticmethod
    def _stage_words_for_intent(intent: str) -> list[str]:
        if intent == "procedure":
            return _PROCEDURE_STAGE_WORDS
        if intent == "deployment":
            return _DEPLOYMENT_STAGE_WORDS
        if intent == "troubleshooting":
            return _TROUBLESHOOTING_STAGE_WORDS
        if intent == "comparison":
            return _COMPARISON_STAGE_WORDS
        return []

    @staticmethod
    def _normalize_base_queries(
        base_queries: Iterable[RetrievalQuery | str] | None,
        fallback: str,
    ) -> list[RetrievalQuery]:
        normalized: list[RetrievalQuery] = []
        for item in base_queries or []:
            if isinstance(item, RetrievalQuery):
                normalized.append(item)
            else:
                text = str(item).strip()
                if text:
                    normalized.append(RetrievalQuery(text, "original", 1.0))
        if not normalized and fallback:
            normalized.append(RetrievalQuery(fallback, "original", 1.0))
        return normalized

    def _extract_core_entity(
        self,
        question: str,
        base_queries: list[RetrievalQuery],
    ) -> str:
        texts = [question] + [item.text for item in base_queries]

        latin_terms: list[str] = []
        for text in texts:
            for match in re.finditer(r"[A-Za-z][A-Za-z0-9_.+-]{1,}", text):
                term = match.group(0).strip("._-+")
                if len(term) >= 2 and term.lower() not in {"the", "and", "for"}:
                    latin_terms.append(term)
        if latin_terms:
            return " ".join(self._dedupe_texts(latin_terms)[:3])

        cleaned = question
        cleaned = re.sub(r"[？?！!，,。；;：:\"'“”‘’（）()\[\]{}]", " ", cleaned)
        for stop in sorted(_STOPWORDS, key=len, reverse=True):
            cleaned = cleaned.replace(stop, " ")
        parts = [part.strip() for part in re.split(r"\s+", cleaned) if part.strip()]
        parts = [part for part in parts if len(part) >= 2 and part not in _STOPWORDS]
        if parts:
            return " ".join(parts[:3])[:40]
        return ""

    @staticmethod
    def _dedupe_queries(queries: list[RetrievalQuery]) -> list[RetrievalQuery]:
        seen: set[str] = set()
        result: list[RetrievalQuery] = []
        for query in queries:
            text = query.text.strip()
            if not text:
                continue
            key = re.sub(r"\s+", " ", text).lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(RetrievalQuery(text, query.kind, query.weight))
        return result

    @staticmethod
    def _dedupe_texts(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result
