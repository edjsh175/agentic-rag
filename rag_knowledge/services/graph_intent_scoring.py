"""Graph-backed intent match signals and unified intent scoring."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterable

from langchain_core.documents import Document

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.retrieval_intent import (
    RetrievalIntentPolicy,
    _doc_match_text,
    _normalize_text,
    _section_alias_matches,
)

logger = logging.getLogger(__name__)


def _field_match_terms(name: str) -> tuple[str, ...]:
    normalized = _normalize_text(name)
    if not normalized:
        return ()
    if "." in name:
        leaf = _normalize_text(name.rsplit(".", 1)[-1])
        return tuple(dict.fromkeys([normalized, leaf]))
    return (normalized,)


def _expand_section_paths(
    canonical_paths: tuple[str, ...],
    canonical_name: str,
    aliases: tuple[str, ...],
) -> tuple[str, ...]:
    expanded = list(canonical_paths)
    canonical_key = _normalize_text(canonical_name)
    for path in canonical_paths:
        parts = [part.strip() for part in path.split(">") if part.strip()]
        if not parts:
            continue
        if _normalize_text(parts[-1]) != canonical_key:
            continue
        for alias in aliases:
            expanded.append(" > ".join([*parts[:-1], alias]))
    return tuple(dict.fromkeys(expanded))


@dataclass(frozen=True)
class GraphIntentFacts:
    canonical_name: str
    aliases: tuple[str, ...] = ()
    section_paths: tuple[str, ...] = ()
    sibling_names: tuple[str, ...] = ()
    sibling_aliases: tuple[str, ...] = ()
    field_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntentMatchSignals:
    entity_hit: bool = False
    intent_hit: bool = False
    field_hit: bool = False
    section_hit: bool = False
    sibling_hit: bool = False
    preferred_category_hit: bool = False
    fallback_category_hit: bool = False


class GraphIntentFactProvider:
    def __init__(self, db: RelationalDB | None = None):
        self._db = db

    def _get_db(self) -> RelationalDB:
        return self._db or RelationalDB()

    def load_many(self, entity_refs: Iterable[str]) -> dict[str, GraphIntentFacts]:
        facts: dict[str, GraphIntentFacts] = {}
        for entity_ref in dict.fromkeys(ref for ref in entity_refs if ref):
            loaded = self.load_one(entity_ref)
            if loaded is not None:
                facts[entity_ref] = loaded
        return facts

    def load_one(self, entity_ref: str) -> GraphIntentFacts | None:
        db = self._get_db()
        entity = db.get_entity_by_name(entity_ref)
        if not entity:
            return None
        if entity.get("review_status") != "approved":
            return None

        entity_id = entity["id"]
        aliases = tuple(
            alias["alias"]
            for alias in db.list_aliases(entity_id)
            if alias.get("review_status") == "approved"
        )
        section_paths: list[str] = []
        field_names: list[str] = []
        sibling_names: list[str] = []
        sibling_aliases: list[str] = []

        for relation in db.list_relations(entity_id=entity_id, review_status="approved"):
            relation_type = relation["relation_type"]
            if relation_type == "defined_in" and relation["source_entity_id"] == entity_id:
                section_paths.extend(self._section_paths_for_entity(db, relation["target_entity_id"]))
            elif relation_type == "has_field" and relation["source_entity_id"] == entity_id:
                field_names.append(relation["target_name"])
            elif relation_type == "different_from":
                other_name = (
                    relation["target_name"]
                    if relation["source_entity_id"] == entity_id
                    else relation["source_name"]
                )
                sibling_names.append(other_name)
                other_entity = db.get_entity_by_name(other_name)
                if other_entity:
                    sibling_aliases.extend(
                        alias["alias"]
                        for alias in db.list_aliases(other_entity["id"])
                        if alias.get("review_status") == "approved"
                    )

        raw_section_paths = tuple(dict.fromkeys(section_paths))
        expanded_section_paths = _expand_section_paths(
            raw_section_paths,
            entity["name"],
            aliases,
        )

        return GraphIntentFacts(
            canonical_name=entity["name"],
            aliases=aliases,
            section_paths=expanded_section_paths,
            sibling_names=tuple(dict.fromkeys(sibling_names)),
            sibling_aliases=tuple(dict.fromkeys(sibling_aliases)),
            field_names=tuple(dict.fromkeys(field_names)),
        )

    @staticmethod
    def _section_paths_for_entity(db: RelationalDB, entity_id: str) -> list[str]:
        section = db.get_entity(entity_id)
        if not section:
            return []
        properties = {}
        raw_properties = section.get("properties_json") or "{}"
        try:
            properties = json.loads(raw_properties)
        except json.JSONDecodeError:
            properties = {}
        section_path = properties.get("section_path") or section.get("name") or ""
        return [section_path] if section_path else []

    def section_family_matches(
        self,
        actual_section: str,
        *,
        entity_ref: str,
    ) -> bool:
        facts = self.load_one(entity_ref)
        if not facts:
            return False
        return any(_section_alias_matches(actual_section, path) for path in facts.section_paths)


def build_match_signals(
    policy: RetrievalIntentPolicy,
    facts: GraphIntentFacts | None,
    doc: Document,
) -> IntentMatchSignals:
    metadata = doc.metadata or {}
    doc_text = _normalize_text(_doc_match_text(doc))
    category_text = _normalize_text(metadata.get("doc_category") or "")
    source_text = _normalize_text(
        " ".join(
            [
                metadata.get("source") or "",
                metadata.get("file_name") or "",
                metadata.get("doc_category") or "",
            ]
        )
    )
    section_path = metadata.get("section_path") or ""

    entity_terms: set[str] = set()
    if policy.entity_ref:
        entity_terms.add(_normalize_text(policy.entity_ref))
    if facts:
        entity_terms.add(_normalize_text(facts.canonical_name))
        entity_terms.update(_normalize_text(alias) for alias in facts.aliases)

    entity_hit = any(term and term in doc_text for term in entity_terms)
    intent_hit = any(_normalize_text(term) in doc_text for term in policy.intent_terms)

    field_terms: set[str] = set()
    if facts:
        for name in facts.field_names:
            field_terms.update(_field_match_terms(name))
    field_hit = any(term and term in doc_text for term in field_terms)

    section_hit = False
    if facts:
        section_hit = any(
            _section_alias_matches(section_path, path) for path in facts.section_paths
        )

    sibling_hit = False
    if facts:
        target_terms = set(entity_terms)
        target_terms.update(_normalize_text(path) for path in facts.section_paths)
        if not any(term and term in doc_text for term in target_terms):
            sibling_terms = [
                _normalize_text(name) for name in (*facts.sibling_names, *facts.sibling_aliases)
            ]
            sibling_hit = any(term and term in doc_text for term in sibling_terms)

    preferred_category_hit = any(
        _normalize_text(category) in category_text or _normalize_text(category) in source_text
        for category in policy.preferred_doc_categories
    )
    fallback_category_hit = any(
        _normalize_text(category) in category_text or _normalize_text(category) in source_text
        for category in policy.fallback_doc_categories
    )

    return IntentMatchSignals(
        entity_hit=entity_hit,
        intent_hit=intent_hit,
        field_hit=field_hit,
        section_hit=section_hit,
        sibling_hit=sibling_hit,
        preferred_category_hit=preferred_category_hit,
        fallback_category_hit=fallback_category_hit,
    )


def score_signals(signals: IntentMatchSignals) -> tuple[float, float]:
    anchored = (
        signals.entity_hit
        or signals.intent_hit
        or signals.field_hit
        or signals.section_hit
    )
    bonus = 0.0
    penalty = 0.0
    if signals.entity_hit:
        bonus += 0.04
    if signals.intent_hit:
        bonus += 0.04
    if anchored and signals.preferred_category_hit:
        bonus += 0.08
    if anchored and signals.fallback_category_hit:
        penalty += 0.03
    if signals.section_hit:
        bonus += 0.08
    if signals.sibling_hit:
        penalty += 0.04
    return bonus, penalty


def score_document_with_graph_facts(
    policies: tuple[RetrievalIntentPolicy, ...],
    facts_by_ref: dict[str, GraphIntentFacts],
    doc: Document,
) -> tuple[float, float]:
    bonus = 0.0
    penalty = 0.0
    for policy in policies:
        facts = facts_by_ref.get(policy.entity_ref) if policy.entity_ref else None
        signals = build_match_signals(policy, facts, doc)
        policy_bonus, policy_penalty = score_signals(signals)
        bonus += policy_bonus
        penalty += policy_penalty
    return bonus, penalty


def log_graph_scoring_degraded(
  *,
  reason: str,
  policy_ids: tuple[str, ...],
  graph_revision: str = "",
) -> None:
    logger.warning(
        "graph_intent_scoring_degraded | reason=%s | policies=%s | graph_revision=%s",
        reason,
        list(policy_ids),
        graph_revision or "unknown",
    )
