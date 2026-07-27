"""MVP clarification (反问) detection for ambiguous queries.

Returns structured A/B/C options for a separate frontend to render as cards.
Rules: data/disambiguation_rules.json + domain_catalog different_from pairs.
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
    if t in q:
        return True
    # Latin identifiers: word boundary
    if re.search(r"[a-z0-9]", t):
        return re.search(rf"(?<![a-z0-9_.-]){re.escape(t)}(?![a-z0-9_.-])", q) is not None
    return False


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


class QueryClarificationService:
    def __init__(
        self,
        *,
        rules_path: Path | None = None,
        catalog: DomainCatalogLoader | None = None,
        enabled: bool | None = None,
        min_options: int = 2,
        max_options: int = 4,
    ):
        self._catalog = catalog
        self._rules_cache: list[_Rule] | None = None
        if rules_path is not None and enabled is not None:
            self.enabled = enabled
            self.min_options = max(2, min_options)
            self.max_options = max(2, min(max_options, 4))
            self._rules_path = rules_path
            return
        cfg = Config()
        clar = getattr(cfg, "clarification", None)
        self.enabled = enabled if enabled is not None else getattr(clar, "enabled", True)
        self.min_options = max(2, getattr(clar, "min_options", min_options) if clar else min_options)
        self.max_options = max(2, min(getattr(clar, "max_options", max_options) if clar else max_options, 4))
        self._rules_path = rules_path or (cfg.data_dir / "disambiguation_rules.json")

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

    def analyze(
        self,
        question: str,
        *,
        doc_category: str | None = None,
        kb_name: str | None = None,
    ) -> ClarificationResult:
        q = (question or "").strip()
        if not self.enabled or not q:
            return ClarificationResult(needs_clarification=False)

        for rule in self._load_rules():
            hit_trigger = None
            for trigger in rule.triggers:
                if _contains_term(q, trigger):
                    hit_trigger = trigger
                    break
            if not hit_trigger:
                continue
            options = list(rule.options)
            if doc_category:
                options = list(_filter_matches_doc_category(tuple(options), doc_category))
            if kb_name:
                options = [
                    opt for opt in options
                    if not opt.filter.kb_name or opt.filter.kb_name == kb_name
                ]
            if len(options) < self.min_options:
                continue
            return ClarificationResult(
                needs_clarification=True,
                ask_question=rule.ask_question,
                trigger=hit_trigger,
                reason="entity_ambiguity",
                options=options[: self.max_options],
            )

        return ClarificationResult(needs_clarification=False)
