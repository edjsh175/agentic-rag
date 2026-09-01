"""Clarification candidate discovery and legacy linear-mode card support.

The Agent Main Controller decides whether to clarify based on IdentityResolution.
Candidate discovery, ranking, and identity status are resolved via EntityCandidateResolver.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any

from rag_knowledge.config import Config
from rag_knowledge.services.entity_candidate_resolver import (
    EntityCandidate,
    EntityCandidateResolver,
    get_entity_candidate_resolver,
)
from rag_knowledge.services.query_surface import (
    is_explicit_comparison,
    question_is_underspecified,
)

logger = logging.getLogger(__name__)

_ALLOWED_ENTITY_TYPES = frozenset({"Product", "Tool", "Service", "Module"})
_RETRIEVAL_DOC_CATEGORIES = frozenset({
    "StampServer",
    "StampTools",
    "StampWebRTC",
    "StampWebGL",
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
    "stampwebgl": "StampWebGL",
}

MAX_CLARIFICATION_OPTIONS = 5


def _normalize_blob(text: str) -> str:
    from rag_knowledge.services.query_surface import normalize_blob
    return normalize_blob(text)


def _question_is_underspecified(question: str) -> bool:
    return question_is_underspecified(question)


def _is_explicit_comparison(question: str, names: list[str]) -> bool:
    return is_explicit_comparison(question, names)


@dataclass(frozen=True)
class CandidateDTO:
    candidate_id: str
    label: str
    canonical_name: str | None = None
    entity_type: str | None = None
    source: str = "backbone"
    binding_status: str = "canonical"
    score: float | None = None
    doc_category: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "label": self.label,
            "canonical_name": self.canonical_name,
            "entity_type": self.entity_type,
            "source": self.source,
            "binding_status": self.binding_status,
            "score": self.score,
            "doc_category": self.doc_category,
        }


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
    entity_id: str | None = None
    source: str | None = None
    canonical_name: str | None = None
    entity_type: str | None = None
    binding_status: str = "canonical"
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "option_id": self.id,
            "entity_id": self.entity_id,
            "candidate_id": self.entity_id or self.id,
            "label": self.label,
            "filter": self.filter.to_dict(),
        }
        if self.source:
            out["source"] = self.source
        if self.canonical_name:
            out["canonical_name"] = self.canonical_name
        if self.entity_type:
            out["entity_type"] = self.entity_type
        if self.binding_status:
            out["binding_status"] = self.binding_status
        if self.score is not None:
            out["score"] = self.score
        return out

    def to_candidate_dto(self) -> CandidateDTO:
        return CandidateDTO(
            candidate_id=self.entity_id or self.id,
            label=self.label,
            canonical_name=self.canonical_name or self.filter.entity_name,
            entity_type=self.entity_type,
            source=self.source or "backbone",
            binding_status=self.binding_status,
            score=self.score,
            doc_category=self.filter.doc_category,
        )


@dataclass
class ClarificationResult:
    needs_clarification: bool
    ask_question: str | None = None
    trigger: str | None = None
    reason: str | None = None
    clarification_snapshot_id: str | None = None
    options: list[ClarificationOption] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "needs_clarification": self.needs_clarification,
            "ask_question": self.ask_question,
            "trigger": self.trigger,
            "reason": self.reason,
            "clarification_snapshot_id": self.clarification_snapshot_id,
            "options": [opt.to_dict() for opt in self.options],
        }


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
    idx = 0
    for raw in options:
        key = raw.label.casefold()
        if key in seen_labels:
            continue
        seen_labels.add(key)
        opt_id = raw.id if raw.id == "other" or raw.source == "fixed_other" else _option_id_at(idx)
        if opt_id != "other":
            idx += 1
        out.append(
            ClarificationOption(
                id=opt_id,
                label=raw.label,
                filter=raw.filter,
                entity_id=raw.entity_id,
                source=raw.source,
                canonical_name=raw.canonical_name,
                entity_type=raw.entity_type,
                binding_status=raw.binding_status,
                score=raw.score,
            )
        )
    return out


def candidate_to_option(cand: EntityCandidate) -> ClarificationOption:
    """Map verified EntityCandidate to ClarificationOption."""
    doc_category = cand.doc_category
    canonical = cand.canonical_name
    entity_type = cand.entity_type
    if doc_category:
        label = f"{canonical}（{doc_category}）"
    elif entity_type:
        label = f"{canonical}（{entity_type}）"
    else:
        label = canonical

    valid_filter_category = doc_category if (doc_category and doc_category in _RETRIEVAL_DOC_CATEGORIES) else None

    return ClarificationOption(
        id="",
        label=label,
        filter=ClarificationFilter(doc_category=valid_filter_category, entity_name=canonical),
        entity_id=cand.entity_id,
        source=cand.match_sources[0] if cand.match_sources else "backbone",
        canonical_name=canonical,
        entity_type=entity_type,
        binding_status="canonical",
        score=cand.final_score,
    )


def merge_clarification_candidates(
    system_candidates: list[ClarificationOption],
    *,
    include_other: bool = True,
    max_options: int = MAX_CLARIFICATION_OPTIONS,
) -> list[ClarificationOption]:
    """Cap verified candidates and append the one fixed Other UI action.

    Callers must pass only the current ranked CandidateSet.  This function never
    discovers entities or accepts model-generated additions.
    """
    merged: list[ClarificationOption] = []
    seen_canonical: set[str] = set()
    seen_labels: set[str] = set()

    for opt in system_candidates:
        can = (opt.canonical_name or opt.filter.entity_name or "").strip().casefold()
        lbl = opt.label.strip().casefold()
        if can and can in seen_canonical:
            continue
        if lbl and lbl in seen_labels:
            continue
        if can:
            seen_canonical.add(can)
        if lbl:
            seen_labels.add(lbl)
        merged.append(opt)
        if len(merged) >= max_options:
            break

    if include_other:
        other_key = "以上都不是".casefold()
        if other_key not in seen_labels:
            merged.append(
                ClarificationOption(
                    id="other",
                    label="以上都不是",
                    filter=ClarificationFilter(),
                    entity_id=None,
                    source="fixed_other",
                    canonical_name=None,
                    entity_type=None,
                    binding_status="unresolved",
                )
            )

    return _assign_option_ids(merged)


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
{{"needs_clarification": true, "ask_question": "您指的是以下哪一个候选实体？", "trigger": "ambiguous_entity"}}
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

    def _resolver(self) -> EntityCandidateResolver:
        return get_entity_candidate_resolver(constraints=self._load_constraints())

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
        clarification_snapshot_id: str | None = None,
    ) -> ClarificationResult | None:
        options = self._narrow_options(options, doc_category=doc_category, kb_name=kb_name)
        options = merge_clarification_candidates(
            options,
            include_other=True,
        )
        meaningful_count = len([opt for opt in options if getattr(opt, "source", None) != "fixed_other"])
        if meaningful_count < 2:
            return None
        return ClarificationResult(
            needs_clarification=True,
            ask_question=ask_question,
            trigger=trigger,
            reason=reason,
            clarification_snapshot_id=clarification_snapshot_id,
            options=options,
        )

    def _call_clarify_llm(self, prompt: str) -> dict[str, Any]:
        if self._llm_caller is not None:
            payload = self._llm_caller(prompt)
            if not isinstance(payload, dict):
                raise ValueError("clarify llm_caller must return a dict")
            return payload

        from rag_knowledge.llm_http import chat_role
        from rag_knowledge.services.model_routing import ModelRoutePolicy

        raw = chat_role(
            self._cfg,
            ModelRoutePolicy(self._cfg).common_stage1_role(),
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            num_predict=256,
            timeout=float(self.llm_timeout_seconds),
            think=False,
            stage="common_clarification",
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
        clarification_snapshot_id: str | None = None,
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
        return self._result_from_options(
            ask_question=ask,
            trigger=trigger,
            reason="llm_ambiguity",
            options=list(seeds),
            doc_category=None,
            kb_name=None,
            clarification_snapshot_id=clarification_snapshot_id,
        )

    def _j3_clarify_result(self, question: str) -> ClarificationResult | None:
        """FR-0b: J3 options from secondary-dev subgraph."""
        from rag_knowledge.services.sdk_code_job import (
            build_j3_clarify_options,
            j3_clarify_options,
        )

        clar = getattr(self._cfg, "clarification", None) if self._cfg is not None else None
        rollback = bool(getattr(clar, "j3_options_rollback_static", False))
        raw_options: list[dict]
        if rollback:
            raw_options = j3_clarify_options()
        else:
            constraints = self._load_constraints()
            raw_options = build_j3_clarify_options(question, constraints)

        options: list[ClarificationOption] = []
        for raw in raw_options:
            options.append(
                ClarificationOption(
                    id="",
                    label=str(raw["label"]),
                    filter=ClarificationFilter(
                        doc_category=raw.get("doc_category"),
                        entity_name=raw.get("entity_name"),
                    ),
                    source=str(raw.get("source") or "") or None,
                )
            )
        return self._result_from_options(
            ask_question="请选择二次开发调用面（产品线 / 是否写代码）：",
            trigger="j3_sdk_code",
            reason="j3_subject_unclear",
            options=options,
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

        from rag_knowledge.services.sdk_code_job import resolve_anchor_binding

        binding = resolve_anchor_binding(
            q,
            entity_name=entity_name,
            constraints=self._load_constraints(),
        )
        if binding.show_j3_card:
            try:
                forced = self._j3_clarify_result(q)
                if forced is not None:
                    return forced
            except Exception as exc:
                logger.warning("j3 clarify gate failed, continue backbone clarify: %s", exc)
        if binding.skip_generic_clarify:
            return ClarificationResult(needs_clarification=False, reason=binding.reason)

        resolver = self._resolver()
        resolution = resolver.resolve_identity(
            q,
            doc_category=doc_category,
        )

        if resolution.status == "confirmed":
            return ClarificationResult(needs_clarification=False, reason="exact_or_high_confidence")
        if resolution.status in {"not_required", "unresolved"}:
            return ClarificationResult(needs_clarification=False, reason=resolution.reason)

        # Apply presentation filters before freezing the snapshot so option ids
        # retain their exact display order during the callback.
        filtered_options = self._narrow_options(
            [candidate_to_option(candidate) for candidate in resolution.candidates],
            doc_category=doc_category,
            kb_name=kb_name,
        )
        displayed_ids = {option.entity_id for option in filtered_options if option.entity_id}
        displayed_resolution = replace(
            resolution,
            candidates=tuple(
                candidate
                for candidate in resolution.candidates
                if candidate.entity_id in displayed_ids
            ),
        )
        snapshot = resolver.create_clarification_snapshot(displayed_resolution)
        seeds = [candidate_to_option(c) for c in snapshot.display_candidates]
        names = [opt.filter.entity_name or opt.label for opt in seeds]
        if _is_explicit_comparison(q, names):
            return ClarificationResult(needs_clarification=False)

        seed_trigger = resolution.surface or "backbone"

        if self.llm_enabled:
            try:
                decided = self._analyze_via_llm(
                    q,
                    seeds,
                    default_trigger=seed_trigger,
                    clarification_snapshot_id=snapshot.clarification_id,
                )
                if decided is not None:
                    return decided
            except Exception as exc:
                logger.warning("clarify LLM failed, falling back to resolver seeds: %s", exc)

        ask = "问题可能对应多个产品/模块，请选择您要查询的方向："
        return self._result_from_options(
            ask_question=ask,
            trigger=seed_trigger,
            reason="ambiguous_candidates",
            options=seeds,
            doc_category=None,
            kb_name=None,
            clarification_snapshot_id=snapshot.clarification_id,
        ) or ClarificationResult(needs_clarification=False)

    def discover_candidates(
        self,
        question: str,
        *,
        doc_category: str | None = None,
        kb_name: str | None = None,
    ) -> list[ClarificationOption]:
        """Discover eligible system seed candidates for a question."""
        q = (question or "").strip()
        if not q:
            return []
        resolver = self._resolver()
        candidates = resolver.discover_candidates(q, doc_category=doc_category)
        options = [candidate_to_option(c) for c in candidates]
        options = self._narrow_options(options, doc_category=doc_category, kb_name=kb_name)
        return _assign_option_ids(options)
