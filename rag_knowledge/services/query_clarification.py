"""Query clarification (反问) before retrieval.

Product backbone is the sole fact source for option seeds. Helper LLM only
decides whether clarification is needed; when true, all seeds are returned.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from rag_knowledge.config import Config
from rag_knowledge.services.query_surface import (
    WIDE_SURFACE_TERMS,
    contains_term,
    is_explicit_comparison,
    question_is_underspecified,
)

logger = logging.getLogger(__name__)

_ALLOWED_ENTITY_TYPES = frozenset({"Product", "Tool", "Service", "Module"})
_RETRIEVAL_DOC_CATEGORIES = frozenset({
    "StampServer",
    "StampTools",
    "StampWebRTC",
    "实景三维",
    "耕地保护",
    "矢量瓦片",
    "基础环境",
    "博客",
    "其他",
})
_OWNER_TO_DOC_CATEGORY = {
    "stamptools": "StampTools",
    "stampserver": "StampServer",
    "stampwebrtc": "StampWebRTC",
}

# Backward-compatible aliases (prefer query_surface public names for new code).
_WIDE_SURFACE_TERMS = WIDE_SURFACE_TERMS
_PIPELINE_FAMILY_TOKENS = frozenset({"pipeline", "管线"})


def _normalize_blob(text: str) -> str:
    from rag_knowledge.services.query_surface import normalize_blob
    return normalize_blob(text)


def _contains_term(question: str, term: str) -> bool:
    return contains_term(question, term)


def _question_is_underspecified(question: str) -> bool:
    return question_is_underspecified(question)


def _is_explicit_comparison(question: str, names: list[str]) -> bool:
    return is_explicit_comparison(question, names)


@dataclass(frozen=True)
class ClarificationFilter:
    doc_category: str | None = None
    entity_name: str | None = None
    kb_name: str | None = None

    def to_dict(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.doc_category:
            out["doc_category"] = self.doc_category
        if self.entity_name:
            out["entity_name"] = self.entity_name
        if self.kb_name:
            out["kb_name"] = self.kb_name
        return out


@dataclass(frozen=True)
class ClarificationOption:
    id: str
    label: str
    filter: ClarificationFilter

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "filter": self.filter.to_dict(),
        }


@dataclass
class ClarificationResult:
    needs_clarification: bool
    ask_question: str | None = None
    trigger: str | None = None
    reason: str | None = None
    options: list[ClarificationOption] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "needs_clarification": self.needs_clarification,
            "ask_question": self.ask_question,
            "trigger": self.trigger,
            "reason": self.reason,
            "options": [opt.to_dict() for opt in self.options],
        }


def _fold_key(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "")).casefold()


def _is_eligible_backbone_entity(name: str, entity_type: str | None) -> bool:
    if not name or name.endswith(".so"):
        return False
    return (entity_type or "") in _ALLOWED_ENTITY_TYPES


def _option_id_at(index: int) -> str:
    """Generate a, b, …, z, aa, ab, … without an upper bound."""
    if index < 0:
        raise ValueError("option index must be non-negative")
    n = index
    chars: list[str] = []
    while True:
        chars.append(chr(ord("a") + (n % 26)))
        n = n // 26 - 1
        if n < 0:
            break
    return "".join(reversed(chars))


def _assign_option_ids(options: list[ClarificationOption]) -> list[ClarificationOption]:
    out: list[ClarificationOption] = []
    seen_labels: set[str] = set()
    for raw in options:
        key = raw.label.casefold()
        if key in seen_labels:
            continue
        seen_labels.add(key)
        out.append(
            ClarificationOption(
                id=_option_id_at(len(out)),
                label=raw.label,
                filter=raw.filter,
            )
        )
    return out


def _latin_family_prefix(name: str) -> str | None:
    """Leading CamelCase token, e.g. PipelineBuilder → Pipeline."""
    parts = re.findall(r"[A-Z][a-z]+|[A-Z]+(?![a-z])|[a-z]+", name or "")
    if not parts:
        return None
    stem = parts[0]
    if len(stem) < 4 or not stem.isalpha():
        return None
    return stem


def _family_tokens_for_names(names: list[str], triggers: list[str]) -> set[str]:
    tokens: set[str] = set()
    for raw in [*names, *triggers]:
        text = str(raw or "").strip()
        if not text:
            continue
        folded = _fold_key(text)
        if any(tok in folded or tok in text for tok in _PIPELINE_FAMILY_TOKENS):
            tokens.update(_PIPELINE_FAMILY_TOKENS)
        prefix = _latin_family_prefix(text)
        if prefix:
            tokens.add(_fold_key(prefix))
        # Short latin trigger itself (e.g. pipeline).
        if re.fullmatch(r"[a-z][a-z0-9]{2,}", folded):
            tokens.add(folded)
    return tokens


def _entity_matches_tokens(name: str, aliases_for: dict[str, list[str]], tokens: set[str]) -> bool:
    candidates = [name, *(aliases_for.get(name) or [])]
    for cand in candidates:
        folded = _fold_key(cand)
        text = cand or ""
        for tok in tokens:
            if not tok:
                continue
            if tok in folded or tok in text:
                return True
    return False


def _aliases_by_canonical(constraints: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for alias, canonical in (constraints.get("canonical_by_alias") or {}).items():
        can = str(canonical or "").strip()
        al = str(alias or "").strip()
        if not can or not al or al == can:
            continue
        out.setdefault(can, []).append(al)
    return out


def _doc_category_for_entity(canonical: str, constraints: dict) -> str | None:
    raw = (constraints.get("doc_category_by_name") or {}).get(canonical)
    if isinstance(raw, str) and raw.strip() in _RETRIEVAL_DOC_CATEGORIES:
        return raw.strip()
    # Enrichment only: map via domain_catalog ownership when available.
    try:
        from rag_knowledge.services.domain_catalog import DomainCatalogLoader

        catalog = DomainCatalogLoader()
        owner = catalog.owner_for(canonical)
        if owner:
            mapped = _OWNER_TO_DOC_CATEGORY.get(owner.casefold())
            if mapped:
                return mapped
        for cat, product_name in catalog._categories.items():  # noqa: SLF001
            if product_name == canonical:
                return cat
    except Exception:
        pass
    return None


def _option_for_backbone_entity(canonical: str, constraints: dict) -> ClarificationOption | None:
    types = constraints.get("entity_type_by_name") or {}
    entity_type = types.get(canonical)
    if not _is_eligible_backbone_entity(canonical, entity_type):
        return None
    doc_category = _doc_category_for_entity(canonical, constraints)
    if doc_category:
        label = f"{canonical}（{doc_category}）"
    elif entity_type:
        label = f"{canonical}（{entity_type}）"
    else:
        label = canonical
    return ClarificationOption(
        id="",
        label=label,
        filter=ClarificationFilter(doc_category=doc_category, entity_name=canonical),
    )


def _collect_backbone_seed_names(
    question: str,
    constraints: dict,
    *,
    triggers: list[str] | None = None,
) -> tuple[list[str], str | None]:
    """Gather all eligible backbone entities for a vague / ambiguous question."""
    from rag_knowledge.services.backbone_guard import (
        avoid_names_for_anchors,
        soft_match_backbone_entities,
    )

    types = constraints.get("entity_type_by_name") or {}
    aliases_for = _aliases_by_canonical(constraints)
    trigger = None
    hit_triggers = list(triggers or [])

    soft_hits = soft_match_backbone_entities(question, constraints, max_hits=50)
    soft_hits = [
        name for name in soft_hits
        if _is_eligible_backbone_entity(name, types.get(name))
    ]
    if soft_hits and trigger is None:
        trigger = soft_hits[0]

    for term in _WIDE_SURFACE_TERMS:
        if not _contains_term(question, term):
            continue
        if term == "管线" and not _question_is_underspecified(question):
            continue
        hit_triggers.append(term)
        if trigger is None:
            trigger = term

    tokens = _family_tokens_for_names(soft_hits, hit_triggers)
    # Soft-hit Latin stems even without wide-term trigger.
    for name in soft_hits:
        prefix = _latin_family_prefix(name)
        if prefix:
            tokens.add(_fold_key(prefix))

    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        key = name.casefold()
        if key in seen:
            return
        if not _is_eligible_backbone_entity(name, types.get(name)):
            return
        seen.add(key)
        names.append(name)

    for name in soft_hits:
        _add(name)

    if tokens:
        for name in sorted(types.keys()):
            if _entity_matches_tokens(name, aliases_for, tokens):
                _add(name)

    # Backbone different_from siblings of current seeds.
    for sibling in avoid_names_for_anchors(names, constraints):
        _add(sibling)

    return names, trigger


_CLARIFY_LLM_PROMPT = """你是 RAG 知识库的歧义预检助手。根据用户问题与候选实体，判断是否必须先反问用户再检索。

规则：
1. 仅当问题意图模糊、主体不明确、或可能对应多个不同产品/模块时，needs_clarification=true。
2. 若问题已明确指向单一实体（即使候选列表有相近项），needs_clarification=false。
3. 比较题（A和B的区别）且两边都已写出时，needs_clarification=false。
4. 若需要反问：ask_question 用简洁中文说明涵盖的方向；不要挑选或删减候选，系统会展示全部候选。
5. 只输出 JSON，不要 markdown，不要解释。

用户问题：
{question}

候选选项（JSON，系统将全部展示）：
{candidates_json}

输出格式：
{{"needs_clarification": false, "ask_question": "", "trigger": ""}}
或
{{"needs_clarification": true, "ask_question": "您指的是以下哪一个产品/服务？", "trigger": "pipeline"}}
"""


class QueryClarificationService:
    def __init__(
        self,
        *,
        enabled: bool | None = None,
        min_options: int = 2,
        llm_enabled: bool | None = None,
        llm_timeout_seconds: float | None = None,
        llm_caller: Any | None = None,
        constraints: dict | None = None,
    ):
        self._llm_caller = llm_caller
        self._constraints = constraints
        if enabled is not None and constraints is not None:
            self.enabled = enabled
            self.min_options = max(2, min_options)
            self.llm_enabled = bool(llm_enabled) if llm_enabled is not None else False
            self.llm_timeout_seconds = float(llm_timeout_seconds or 15.0)
            self._cfg = None
            return
        cfg = Config()
        clar = getattr(cfg, "clarification", None)
        self.enabled = enabled if enabled is not None else getattr(clar, "enabled", True)
        self.min_options = max(2, getattr(clar, "min_options", min_options) if clar else min_options)
        self.llm_enabled = (
            bool(llm_enabled)
            if llm_enabled is not None
            else bool(getattr(clar, "llm_enabled", True))
        )
        self.llm_timeout_seconds = float(
            llm_timeout_seconds
            if llm_timeout_seconds is not None
            else getattr(clar, "llm_timeout_seconds", 15.0)
        )
        self._cfg = cfg

    def _load_constraints(self) -> dict:
        if self._constraints is not None:
            return self._constraints
        from rag_knowledge.services.backbone_guard import load_backbone_constraints

        return load_backbone_constraints()

    def _narrow_options(
        self,
        options: list[ClarificationOption],
        *,
        doc_category: str | None,
        kb_name: str | None,
    ) -> list[ClarificationOption]:
        if doc_category:
            cat = doc_category.casefold()
            options = [
                opt for opt in options
                if not opt.filter.doc_category or opt.filter.doc_category.casefold() == cat
            ]
        if kb_name:
            options = [
                opt for opt in options
                if not opt.filter.kb_name or opt.filter.kb_name == kb_name
            ]
        return options

    def _result_from_options(
        self,
        *,
        ask_question: str,
        trigger: str,
        reason: str,
        options: list[ClarificationOption],
        doc_category: str | None,
        kb_name: str | None,
    ) -> ClarificationResult | None:
        options = self._narrow_options(options, doc_category=doc_category, kb_name=kb_name)
        options = _assign_option_ids(options)
        if len(options) < self.min_options:
            return None
        return ClarificationResult(
            needs_clarification=True,
            ask_question=ask_question,
            trigger=trigger,
            reason=reason,
            options=options,
        )

    def _options_for_names(
        self,
        names: list[str],
        constraints: dict,
    ) -> list[ClarificationOption]:
        opts: list[ClarificationOption] = []
        seen: set[str] = set()
        for name in names:
            key = (name or "").casefold()
            if not key or key in seen:
                continue
            opt = _option_for_backbone_entity(name, constraints)
            if not opt:
                continue
            seen.add(key)
            opts.append(opt)
        return opts

    def _collect_seed_options(
        self,
        question: str,
        *,
        doc_category: str | None,
        kb_name: str | None,
    ) -> tuple[list[ClarificationOption], str | None]:
        constraints = self._load_constraints()
        names, trigger = _collect_backbone_seed_names(question, constraints)
        options = self._options_for_names(names, constraints)
        options = self._narrow_options(options, doc_category=doc_category, kb_name=kb_name)
        options = _assign_option_ids(options)
        return options, trigger

    def _analyze_backbone_fallback(
        self,
        question: str,
        seeds: list[ClarificationOption],
        *,
        default_trigger: str | None,
        doc_category: str | None,
        kb_name: str | None,
    ) -> ClarificationResult | None:
        names = [opt.filter.entity_name or opt.label for opt in seeds]
        if _is_explicit_comparison(question, names):
            return None
        if len(seeds) < self.min_options:
            return None

        trigger = default_trigger or (seeds[0].filter.entity_name if seeds else "backbone")
        if default_trigger and default_trigger in _WIDE_SURFACE_TERMS:
            ask = f"您提到的「{default_trigger}」可能对应不同产品/模块，请选择要查询的方向："
            reason = "vague_surface_term"
        elif len(seeds) >= 2:
            ask = "问题可能对应多个产品/模块，请选择您要查询的方向："
            reason = "multi_entity_match"
        else:
            return None

        return self._result_from_options(
            ask_question=ask,
            trigger=str(trigger),
            reason=reason,
            options=list(seeds),
            doc_category=doc_category,
            kb_name=kb_name,
        )

    def _call_clarify_llm(self, prompt: str) -> dict[str, Any]:
        if self._llm_caller is not None:
            payload = self._llm_caller(prompt)
            if not isinstance(payload, dict):
                raise ValueError("clarify llm_caller must return a dict")
            return payload

        from rag_knowledge.llm_http import chat_role

        raw = chat_role(
            self._cfg,
            "helper_llm",
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            num_predict=256,
            timeout=float(self.llm_timeout_seconds),
            think=False,
        ).strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        payload = json.loads(cleaned)
        if not isinstance(payload, dict):
            raise ValueError("clarify payload is not an object")
        return payload

    def _analyze_via_llm(
        self,
        question: str,
        seeds: list[ClarificationOption],
        *,
        default_trigger: str | None,
    ) -> ClarificationResult | None:
        candidates = [
            {
                "id": opt.id,
                "label": opt.label,
                "entity_name": opt.filter.entity_name,
                "doc_category": opt.filter.doc_category,
            }
            for opt in seeds
        ]
        prompt = _CLARIFY_LLM_PROMPT.format(
            question=question,
            candidates_json=json.dumps(candidates, ensure_ascii=False),
        )
        payload = self._call_clarify_llm(prompt)
        if not bool(payload.get("needs_clarification")):
            return ClarificationResult(
                needs_clarification=False,
                reason="llm_clear",
                trigger=str(payload.get("trigger") or default_trigger or "") or None,
            )

        ask = str(payload.get("ask_question") or "").strip() or "请选择您要查询的具体模块或方向："
        trigger = str(payload.get("trigger") or default_trigger or "").strip() or (default_trigger or "llm")
        # Intentionally ignore option_ids — always return the full seed list.
        return self._result_from_options(
            ask_question=ask,
            trigger=trigger,
            reason="llm_ambiguity",
            options=list(seeds),
            doc_category=None,
            kb_name=None,
        )

    def analyze(
        self,
        question: str,
        *,
        doc_category: str | None = None,
        kb_name: str | None = None,
        entity_name: str | None = None,
    ) -> ClarificationResult:
        q = (question or "").strip()
        if not self.enabled or not q:
            return ClarificationResult(needs_clarification=False)

        # User already picked an entity for this turn — do not ask again.
        if entity_name and str(entity_name).strip():
            return ClarificationResult(needs_clarification=False)

        seeds, seed_trigger = self._collect_seed_options(
            q, doc_category=doc_category, kb_name=kb_name,
        )
        if len(seeds) < self.min_options:
            return ClarificationResult(needs_clarification=False)

        names = [opt.filter.entity_name or opt.label for opt in seeds]
        if _is_explicit_comparison(q, names):
            return ClarificationResult(needs_clarification=False)

        if self.llm_enabled:
            try:
                decided = self._analyze_via_llm(q, seeds, default_trigger=seed_trigger)
                if decided is not None:
                    return decided
            except Exception as exc:
                logger.warning("clarify LLM failed, falling back to backbone seeds: %s", exc)

        fallback = self._analyze_backbone_fallback(
            q,
            seeds,
            default_trigger=seed_trigger,
            doc_category=doc_category,
            kb_name=kb_name,
        )
        return fallback or ClarificationResult(needs_clarification=False)
