"""Shared dialogue surface signals for clarification and retrieval understanding.

Public helpers used by QueryClarificationService, DialogueUnderstanding, and
backbone-anchor gates — avoid private cross-module imports.
"""
from __future__ import annotations

import re

# Wide oral terms that often map to multiple products/modules.
# Bare「管线」仅在问题过短/过泛时启用，避免「管线点表字段」误触发。
WIDE_SURFACE_TERMS: tuple[str, ...] = (
    "pipeline",
    "Pipeline",
    "管线工具",
    "管线发布工具",
    "管线",
)


def normalize_blob(text: str) -> str:
    return (text or "").casefold()


def contains_term(question: str, term: str) -> bool:
    if not term or not question:
        return False
    q = normalize_blob(question)
    t = normalize_blob(term)
    if not t:
        return False
    # Latin identifiers: require token boundary (avoid pipeline ⊂ PipelineBuilder).
    if re.search(r"[a-z0-9]", t):
        return re.search(rf"(?<![a-z0-9_.-]){re.escape(t)}(?![a-z0-9_.-])", q) is not None
    return t in q


def question_is_underspecified(question: str) -> bool:
    """True for single-token / ultra-short questions (e.g. pipeline / 管线)."""
    text = (question or "").strip()
    if not text:
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,40}", text):
        return True
    compact = re.sub(r"[\s？?！!。．\.，,、]", "", text)
    return len(compact) <= 4


def is_vague_surface_question(question: str) -> bool:
    """True when the question is underspecified or hits a wide oral surface term."""
    if question_is_underspecified(question):
        return True
    for term in WIDE_SURFACE_TERMS:
        if term == "管线" and not question_is_underspecified(question):
            continue
        if contains_term(question, term):
            return True
    return False


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
