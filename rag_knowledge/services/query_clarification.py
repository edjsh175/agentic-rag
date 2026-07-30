"""Query clarification (反问) before retrieval.

Helper LLM decides whether clarification is needed. Rules / catalog / wide terms
only supply option seeds and act as fallback when the LLM is unavailable.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rag_knowledge.config import Config
from rag_knowledge.services.domain_catalog import CatalogSeedEntity, DomainCatalogLoader

logger = logging.getLogger(__name__)

_OPTION_IDS = ("a", "b", "c", "d")

_OWNER_TO_DOC_CATEGORY = {
    "stamptools": "StampTools",
    "stampserver": "StampServer",
    "stampwebrtc": "StampWebRTC",
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


@dataclass(frozen=True)
class _Rule:
    triggers: tuple[str, ...]
    ask_question: str
    options: tuple[ClarificationOption, ...]
    priority: int = 0


def _normalize_blob(text: str) -> str:
    return (text or "").casefold()


def _contains_term(question: str, term: str) -> bool:
    if not term or not question:
        return False
    q = _normalize_blob(question)
    t = _normalize_blob(term)
    if not t:
        return False
    # Latin identifiers: require token boundary (avoid pipeline ⊂ PipelineBuilder).
    if re.search(r"[a-z0-9]", t):
        return re.search(rf"(?<![a-z0-9_.-]){re.escape(t)}(?![a-z0-9_.-])", q) is not None
    return t in q


def _doc_category_for_entity(catalog: DomainCatalogLoader, entity_name: str) -> str | None:
    owner = catalog.owner_for(entity_name)
    if owner:
        mapped = _OWNER_TO_DOC_CATEGORY.get(owner.casefold())
        if mapped:
            return mapped
    for cat, product_name in catalog._categories.items():  # noqa: SLF001
        if product_name == entity_name:
            return cat
    return None


def _option_for_entity(
    catalog: DomainCatalogLoader,
    entity_name: str,
    *,
    label_suffix: str = "",
) -> ClarificationOption | None:
    resolved = catalog.resolve(entity_name)
    canonical = resolved[0] if resolved else entity_name
    doc_category = _doc_category_for_entity(catalog, canonical)
    label = canonical
    if label_suffix:
        label = f"{label}（{label_suffix}）"
    elif doc_category:
        label = f"{canonical}（{doc_category}）"
    return ClarificationOption(
        id="",
        label=label,
        filter=ClarificationFilter(doc_category=doc_category, entity_name=canonical),
    )


def _assign_option_ids(options: list[ClarificationOption], max_options: int) -> list[ClarificationOption]:
    out: list[ClarificationOption] = []
    seen_labels: set[str] = set()
    for raw in options:
        key = raw.label.casefold()
        if key in seen_labels:
            continue
        seen_labels.add(key)
        if len(out) >= max_options:
            break
        opt_id = _OPTION_IDS[len(out)]
        out.append(
            ClarificationOption(id=opt_id, label=raw.label, filter=raw.filter)
        )
    return out


def _rules_from_catalog(catalog: DomainCatalogLoader, max_options: int) -> list[_Rule]:
    rules: list[_Rule] = []
    for seed in catalog.seeds():
        if not seed.different_from:
            continue
        opts: list[ClarificationOption] = []
        primary = _option_for_entity(catalog, seed.name)
        if primary:
            opts.append(primary)
        for other in seed.different_from:
            opt = _option_for_entity(catalog, other)
            if opt:
                opts.append(opt)
        opts = _assign_option_ids(opts, max_options)
        if len(opts) < 2:
            continue
        triggers = tuple(dict.fromkeys([seed.name, *seed.aliases]))
        ask = (
            f"「{seed.name}」在资料中可能对应不同产品/模块，请选择您要查询的方向："
        )
        rules.append(_Rule(triggers=triggers, ask_question=ask, options=tuple(opts), priority=10))
    return rules


def _parse_rules_file(path: Path, max_options: int) -> list[_Rule]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("invalid disambiguation rules %s: %s", path, exc)
        return []
    if not isinstance(data, list):
        return []
    rules: list[_Rule] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        triggers = item.get("trigger_entity") or item.get("triggers") or []
        if isinstance(triggers, str):
            triggers = [triggers]
        if not isinstance(triggers, list):
            continue
        ask = str(item.get("ask_question") or "").strip()
        raw_opts = item.get("context_options") or item.get("options") or []
        if not ask or not isinstance(raw_opts, list):
            continue
        opts: list[ClarificationOption] = []
        for raw in raw_opts:
            if not isinstance(raw, dict):
                continue
            label = str(raw.get("label") or "").strip()
            filt = raw.get("filter") if isinstance(raw.get("filter"), dict) else raw
            if not label or not isinstance(filt, dict):
                continue
            opts.append(
                ClarificationOption(
                    id="",
                    label=label,
                    filter=ClarificationFilter(
                        doc_category=filt.get("doc_category"),
                        entity_name=filt.get("entity_name"),
                        kb_name=filt.get("kb_name"),
                    ),
                )
            )
        opts = _assign_option_ids(opts, max_options)
        if len(opts) < 2:
            continue
        priority = int(item.get("priority") or 100)
        rules.append(
            _Rule(
                triggers=tuple(str(t).strip() for t in triggers if str(t).strip()),
                ask_question=ask,
                options=tuple(opts),
                priority=priority,
            )
        )
    return rules


def _filter_matches_doc_category(
    options: tuple[ClarificationOption, ...],
    doc_category: str | None,
) -> tuple[ClarificationOption, ...]:
    if not doc_category:
        return options
    cat = doc_category.casefold()
    matched = [
        opt for opt in options
        if not opt.filter.doc_category or opt.filter.doc_category.casefold() == cat
    ]
    return tuple(matched)


# Wide oral terms that often map to multiple products/modules.
# Bare「管线」仅在问题过短/过泛时启用，避免「管线点表字段」误触发。
_WIDE_SURFACE_TERMS: tuple[str, ...] = (
    "pipeline",
    "Pipeline",
    "管线工具",
    "管线发布工具",
    "管线",
)


def _question_is_underspecified(question: str) -> bool:
    """True for single-token / ultra-short questions (e.g. pipeline / 管线)."""
    text = (question or "").strip()
    if not text:
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,40}", text):
        return True
    compact = re.sub(r"[\s？?！!。．\.，,、]", "", text)
    return len(compact) <= 4


def _is_explicit_comparison(question: str, names: list[str]) -> bool:
    """Skip clarify when the user already juxtaposes two known entities."""
    q = _normalize_blob(question)
    if not any(token in q for token in ("区别", "对比", "不同", " vs ", " versus ", "和")):
        return False
    hit = 0
    for name in names:
        if name and _contains_term(question, name):
            hit += 1
            if hit >= 2:
                return True
    return False


_CLARIFY_LLM_PROMPT = """你是 RAG 知识库的歧义预检助手。根据用户问题与候选实体，判断是否必须先反问用户再检索。

规则：
1. 仅当问题意图模糊、主体不明确、或可能对应多个不同产品/模块时，needs_clarification=true。
2. 若问题已明确指向单一实体（即使候选列表有相近项），needs_clarification=false。
3. 比较题（A和B的区别）且两边都已写出时，needs_clarification=false。
4. 若需要反问：只从候选里选 2~{max_options} 个 option_id；ask_question 用简洁中文；禁止编造候选之外的 id。
5. 只输出 JSON，不要 markdown，不要解释。

用户问题：
{question}

候选选项（JSON）：
{candidates_json}

输出格式：
{{"needs_clarification": false, "ask_question": "", "trigger": "", "option_ids": []}}
或
{{"needs_clarification": true, "ask_question": "请选择…", "trigger": "pipeline", "option_ids": ["a", "b"]}}
"""


class QueryClarificationService:
    def __init__(
        self,
        *,
        rules_path: Path | None = None,
        catalog: DomainCatalogLoader | None = None,
        enabled: bool | None = None,
        min_options: int = 2,
        max_options: int = 4,
        llm_enabled: bool | None = None,
        llm_timeout_seconds: float | None = None,
        llm_caller: Any | None = None,
    ):
        self._catalog = catalog
        self._rules_cache: list[_Rule] | None = None
        self._llm_caller = llm_caller
        self._ollama_base = ""
        self._llm_model = ""
        if rules_path is not None and enabled is not None:
            self.enabled = enabled
            self.min_options = max(2, min_options)
            self.max_options = max(2, min(max_options, 4))
            self._rules_path = rules_path
            # Unit tests default to deterministic rule/heuristic path unless overridden.
            self.llm_enabled = bool(llm_enabled) if llm_enabled is not None else False
            self.llm_timeout_seconds = float(llm_timeout_seconds or 15.0)
            return
        cfg = Config()
        clar = getattr(cfg, "clarification", None)
        self.enabled = enabled if enabled is not None else getattr(clar, "enabled", True)
        self.min_options = max(2, getattr(clar, "min_options", min_options) if clar else min_options)
        self.max_options = max(2, min(getattr(clar, "max_options", max_options) if clar else max_options, 4))
        self._rules_path = rules_path or (cfg.data_dir / "disambiguation_rules.json")
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
        self._ollama_base = cfg.ollama_base_url
        self._llm_model = cfg.helper_llm_model
        self._cfg = cfg

    def _catalog_loader(self) -> DomainCatalogLoader:
        if self._catalog is None:
            self._catalog = DomainCatalogLoader()
        return self._catalog

    def _load_rules(self) -> list[_Rule]:
        if self._rules_cache is not None:
            return self._rules_cache
        catalog = self._catalog_loader()
        rules = _parse_rules_file(self._rules_path, self.max_options)
        rules.extend(_rules_from_catalog(catalog, self.max_options))
        rules.sort(key=lambda r: (-r.priority, r.ask_question))
        self._rules_cache = rules
        return rules

    def _narrow_options(
        self,
        options: list[ClarificationOption],
        *,
        doc_category: str | None,
        kb_name: str | None,
    ) -> list[ClarificationOption]:
        if doc_category:
            options = list(_filter_matches_doc_category(tuple(options), doc_category))
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
        options = _assign_option_ids(options, self.max_options)
        if len(options) < self.min_options:
            return None
        return ClarificationResult(
            needs_clarification=True,
            ask_question=ask_question,
            trigger=trigger,
            reason=reason,
            options=options[: self.max_options],
        )

    def _options_for_names(self, names: list[str]) -> list[ClarificationOption]:
        catalog = self._catalog_loader()
        opts: list[ClarificationOption] = []
        seen: set[str] = set()
        for name in names:
            key = (name or "").casefold()
            if not key or key in seen:
                continue
            opt = _option_for_entity(catalog, name)
            if not opt:
                continue
            seen.add(key)
            opts.append(opt)
        return opts

    def _merge_seed_options(self, batches: list[list[ClarificationOption]]) -> list[ClarificationOption]:
        merged: list[ClarificationOption] = []
        seen: set[str] = set()
        for batch in batches:
            for opt in batch:
                key = "|".join(
                    [
                        (opt.filter.entity_name or "").casefold(),
                        (opt.filter.doc_category or "").casefold(),
                        (opt.filter.kb_name or "").casefold(),
                        opt.label.casefold(),
                    ]
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(ClarificationOption(id="", label=opt.label, filter=opt.filter))
        return merged

    def _collect_seed_options(
        self,
        question: str,
        *,
        doc_category: str | None,
        kb_name: str | None,
    ) -> tuple[list[ClarificationOption], str | None]:
        """Gather candidate options from rules / catalog / wide terms (seeds only)."""
        batches: list[list[ClarificationOption]] = []
        trigger: str | None = None
        for rule in self._load_rules():
            hit_trigger = None
            for term in rule.triggers:
                if _contains_term(question, term):
                    hit_trigger = term
                    break
            if not hit_trigger:
                continue
            if trigger is None:
                trigger = hit_trigger
            batches.append(list(rule.options))

        # Reuse heuristic collector for soft/wide expansion without early return semantics.
        soft_hits: list[str] = []
        try:
            from rag_knowledge.services.backbone_guard import soft_match_backbone_entities

            soft_hits = soft_match_backbone_entities(question)
        except Exception as exc:
            logger.debug("soft_match for clarify seeds skipped: %s", exc)
        if soft_hits:
            batches.append(self._options_for_names(soft_hits))
            if trigger is None and soft_hits:
                trigger = soft_hits[0]

        catalog = self._catalog_loader()
        for term in _WIDE_SURFACE_TERMS:
            if not _contains_term(question, term):
                continue
            if term == "管线" and not _question_is_underspecified(question):
                continue
            seed_names: list[str] = []
            resolved = catalog.resolve(term)
            if resolved:
                seed_names.append(resolved[0])
            if term.casefold() in {"pipeline", "管线工具", "管线发布工具", "管线"}:
                seed_names.extend(["PipelineBuilder", "管线发布服务"])
            expanded: list[str] = []
            for seed_name in seed_names:
                expanded.append(seed_name)
                for seed in catalog.seeds():
                    seed_canonical = (catalog.resolve(seed.name) or (seed.name, ""))[0]
                    if seed_canonical != seed_name and seed.name != seed_name:
                        continue
                    expanded.extend(list(seed.different_from or []))
                    break
            batches.append(self._options_for_names(expanded))
            if trigger is None:
                trigger = term

        options = self._merge_seed_options(batches)
        options = self._narrow_options(options, doc_category=doc_category, kb_name=kb_name)
        options = _assign_option_ids(options, self.max_options)
        return options, trigger

    def _analyze_wide_or_uncertain(
        self,
        question: str,
        *,
        doc_category: str | None,
        kb_name: str | None,
    ) -> ClarificationResult | None:
        """Heuristic fallback when helper LLM is unavailable."""
        catalog = self._catalog_loader()

        soft_hits: list[str] = []
        try:
            from rag_knowledge.services.backbone_guard import soft_match_backbone_entities

            soft_hits = soft_match_backbone_entities(question)
        except Exception as exc:
            logger.debug("soft_match for clarify skipped: %s", exc)

        if len(soft_hits) >= 2 and not _is_explicit_comparison(question, soft_hits):
            result = self._result_from_options(
                ask_question="问题可能对应多个产品/模块，请选择您要查询的方向：",
                trigger=soft_hits[0],
                reason="multi_entity_match",
                options=self._options_for_names(soft_hits),
                doc_category=doc_category,
                kb_name=kb_name,
            )
            if result:
                return result

        for term in _WIDE_SURFACE_TERMS:
            if not _contains_term(question, term):
                continue
            if term == "管线" and not _question_is_underspecified(question):
                continue

            seed_names: list[str] = []
            resolved = catalog.resolve(term)
            if resolved:
                seed_names.append(resolved[0])
            if term.casefold() in {"pipeline", "管线工具", "管线发布工具", "管线"}:
                seed_names.extend(["PipelineBuilder", "管线发布服务"])

            expanded: list[str] = []
            for seed_name in seed_names:
                expanded.append(seed_name)
                for seed in catalog.seeds():
                    seed_canonical = (catalog.resolve(seed.name) or (seed.name, ""))[0]
                    if seed_canonical != seed_name and seed.name != seed_name:
                        continue
                    expanded.extend(list(seed.different_from or []))
                    break

            names: list[str] = []
            seen: set[str] = set()
            for name in expanded:
                key = name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                names.append(name)

            if _is_explicit_comparison(question, names):
                continue

            result = self._result_from_options(
                ask_question=f"您提到的「{term}」可能对应不同产品/模块，请选择要查询的方向：",
                trigger=term,
                reason="vague_surface_term",
                options=self._options_for_names(names),
                doc_category=doc_category,
                kb_name=kb_name,
            )
            if result:
                return result

        if len(soft_hits) == 1:
            canonical = soft_hits[0]
            if _contains_term(question, canonical):
                return None
            siblings: list[str] = [canonical]
            for seed in catalog.seeds():
                resolved = catalog.resolve(seed.name)
                seed_canonical = resolved[0] if resolved else seed.name
                if seed_canonical != canonical and seed.name != canonical:
                    continue
                siblings.extend(list(seed.different_from or []))
                break
            if len(siblings) >= 2:
                return self._result_from_options(
                    ask_question=f"「{canonical}」在资料中可能与相近模块混淆，请确认查询方向：",
                    trigger=canonical,
                    reason="uncertain_entity_link",
                    options=self._options_for_names(siblings),
                    doc_category=doc_category,
                    kb_name=kb_name,
                )

        return None

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
            max_options=self.max_options,
            candidates_json=json.dumps(candidates, ensure_ascii=False),
        )
        payload = self._call_clarify_llm(prompt)
        if not bool(payload.get("needs_clarification")):
            return ClarificationResult(
                needs_clarification=False,
                reason="llm_clear",
                trigger=str(payload.get("trigger") or default_trigger or "") or None,
            )

        wanted_ids = payload.get("option_ids") or payload.get("options") or []
        if not isinstance(wanted_ids, list):
            wanted_ids = []
        id_set = {str(item).strip().casefold() for item in wanted_ids if str(item).strip()}
        by_id = {opt.id.casefold(): opt for opt in seeds if opt.id}
        chosen = [by_id[i] for i in id_set if i in by_id]
        # Preserve original seed order when ids were provided out of order.
        if chosen:
            order = {opt.id.casefold(): idx for idx, opt in enumerate(seeds)}
            chosen.sort(key=lambda opt: order.get(opt.id.casefold(), 999))
        else:
            chosen = list(seeds)

        ask = str(payload.get("ask_question") or "").strip() or "请选择您要查询的具体模块或方向："
        trigger = str(payload.get("trigger") or default_trigger or "").strip() or (default_trigger or "llm")
        return self._result_from_options(
            ask_question=ask,
            trigger=trigger,
            reason="llm_ambiguity",
            options=chosen,
            doc_category=None,
            kb_name=None,
        )

    def _analyze_rules_fallback(
        self,
        question: str,
        *,
        doc_category: str | None,
        kb_name: str | None,
    ) -> ClarificationResult | None:
        for rule in self._load_rules():
            hit_trigger = None
            for term in rule.triggers:
                if _contains_term(question, term):
                    hit_trigger = term
                    break
            if not hit_trigger:
                continue
            result = self._result_from_options(
                ask_question=rule.ask_question,
                trigger=hit_trigger,
                reason="entity_ambiguity",
                options=list(rule.options),
                doc_category=doc_category,
                kb_name=kb_name,
            )
            if result:
                return result
        return self._analyze_wide_or_uncertain(
            question, doc_category=doc_category, kb_name=kb_name,
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

        if self.llm_enabled:
            try:
                decided = self._analyze_via_llm(q, seeds, default_trigger=seed_trigger)
                if decided is not None:
                    return decided
            except Exception as exc:
                logger.warning("clarify LLM failed, falling back to rules/heuristic: %s", exc)

        fallback = self._analyze_rules_fallback(
            q, doc_category=doc_category, kb_name=kb_name,
        )
        return fallback or ClarificationResult(needs_clarification=False)
