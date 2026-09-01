"""Shared dialogue surface signals for clarification and retrieval understanding.

Public helpers used by QueryClarificationService, DialogueUnderstanding, and
backbone-anchor gates — avoid private cross-module imports.
"""
from __future__ import annotations

import re


EXACT_PARAMETER_TERMS: tuple[str, ...] = (
    "端口", "port", "参数", "密码", "密钥", "默认值", "路径", "命令", "ip", "url",
)
_ASCII_PARAMETER_TERMS = frozenset({"ip", "url", "port"})

def normalize_blob(text: str) -> str:
    return (text or "").casefold()


def contains_term(question: str, term: str) -> bool:
    if not term or not question:
        return False
    q = normalize_blob(question)
    t = normalize_blob(term)
    if not t:
        return False
    # Latin identifiers: require token boundaries so a shorter identifier does
    # not accidentally match a longer registered name.
    if re.search(r"[a-z0-9]", t):
        return re.search(rf"(?<![a-z0-9_.-]){re.escape(t)}(?![a-z0-9_.-])", q) is not None
    return t in q


def is_exact_parameter_query(question: str) -> bool:
    """Whether a query explicitly asks for a configuration-like parameter.

    ASCII tokens use word boundaries so a short term such as ``ip`` cannot
    reinterpret unrelated identifiers such as ``pipeline`` or ``shipping``.
    Chinese surface terms intentionally retain substring matching.
    """
    query = normalize_blob(question)
    for term in EXACT_PARAMETER_TERMS:
        folded = term.casefold()
        if folded in _ASCII_PARAMETER_TERMS:
            if re.search(rf"(?<![a-z0-9_]){re.escape(folded)}(?![a-z0-9_])", query):
                return True
        elif folded in query:
            return True
    return False


def infer_answer_intent(question: str, *, task_type: str | None = None) -> tuple[str, tuple[str, ...], str]:
    """Derive canonical answer semantics from the user's question only."""
    declared = str(task_type or "").strip().lower()
    if declared == "multi_entity_relation":
        return "multi_entity_relation", (), "structural_relation"

    query = normalize_blob(question)
    if any(term in query for term in ("关系", "区别", "对比", "比较", "差异")):
        return "comparison", ("comparison",), "explicit_user"
    if is_exact_parameter_query(query):
        return "config", ("config",), "explicit_user"
    if any(term in query for term in ("部署", "安装", "上线", "发布")):
        return "deployment", ("deployment",), "explicit_user"
    if any(term in query for term in ("排错", "故障", "报错", "异常", "解决")):
        return "troubleshooting", ("troubleshooting",), "explicit_user"
    if any(term in query for term in ("如何", "步骤", "启动", "需要")):
        return "procedure", ("procedure",), "explicit_user"
    if any(term in query for term in ("是什么", "介绍", "概览", "定位", "作用", "用途", "功能", "主要功能", "能力")):
        return "definition", ("function",), "explicit_user"
    if any(term in query for term in ("限制", "局限", "前提")):
        return "general_qa", ("limitations",), "explicit_user"
    return "general_qa", (), "fallback"


def question_is_underspecified(question: str) -> bool:
    """True for single-token / ultra-short questions (e.g. pipeline / 管线)."""
    text = (question or "").strip()
    if not text:
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,40}", text):
        return True
    compact = re.sub(r"[\s？?！!。．\.，,、]", "", text)
    return len(compact) <= 4


# 指代/省略开头：主体在前一轮，需要绑定上一轮已确认实体
_ANAPHORA_PREFIX_RE = re.compile(
    r"^(?:它|他|她|它们|他们|她们|这个|那个|这些|那些|该|此|上述|"
    r"前面|之前|刚才|上面|刚刚|继续|接着|然后|还有|另外|其他|再)"
)


def question_refers_to_previous_subject(question: str) -> bool:
    """True when the question opens with anaphora whose subject is in the prior turn."""
    return bool(_ANAPHORA_PREFIX_RE.search((question or "").strip()))


def is_vague_surface_question(question: str) -> bool:
    """True when the surface itself is structurally underspecified."""
    return question_is_underspecified(question)


def is_explicit_comparison(question: str, names: list[str]) -> bool:
    """True when the user already juxtaposes two known entities."""
    q = normalize_blob(question)
    if not any(token in q for token in ("区别", "对比", "不同", " vs ", " versus ", "和")):
        return False
    hit = 0
    for name in names:
        if name and contains_term(question, name):
            hit += 1
            if hit >= 2:
                return True
    return False
