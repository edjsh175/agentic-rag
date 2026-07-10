"""Profile-driven retrieval intent planning and scoring."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from langchain_core.documents import Document

LEGACY_FACT_FIELDS = frozenset({
    "entity_aliases",
    "section_families",
    "sibling_penalty_groups",
    "recall_terms",
    "preferred_sources",
    "fallback_sources",
})


@dataclass(frozen=True)
class RetrievalIntentPolicy:
    id: str
    entity_ref: str = ""
    intent_terms: tuple[str, ...] = ()
    query_hints: tuple[str, ...] = ()
    preferred_doc_categories: tuple[str, ...] = ()
    fallback_doc_categories: tuple[str, ...] = ()
    candidate_min_k: int | None = None


@dataclass(frozen=True)
class RetrievalIntentProfile:
    """Legacy migration-only profile with embedded domain facts."""

    id: str
    entity_aliases: tuple[str, ...] = ()
    intent_terms: tuple[str, ...] = ()
    recall_terms: tuple[str, ...] = ()
    section_families: tuple[tuple[str, ...], ...] = ()
    preferred_sources: tuple[str, ...] = ()
    fallback_sources: tuple[str, ...] = ()
    sibling_penalty_groups: tuple[tuple[str, ...], ...] = ()
    candidate_min_k: int | None = None


@dataclass(frozen=True)
class RetrievalIntentPlan:
    policies: tuple[RetrievalIntentPolicy, ...] = ()
    legacy_profiles: tuple[RetrievalIntentProfile | None, ...] = ()
    query: str = ""
    graph_entity_refs: tuple[str, ...] = ()

    @property
    def profiles(self) -> tuple[RetrievalIntentPolicy, ...]:
        return self.policies

    def expand_query(self, query: str) -> str:
        terms = []
        normalized_query = _normalize_text(query)
        seen = {_normalize_text(part) for part in (query or "").split() if part}
        for index, policy in enumerate(self.policies):
            hints = policy.query_hints
            legacy = self.legacy_profiles[index] if index < len(self.legacy_profiles) else None
            if legacy and not hints:
                hints = legacy.recall_terms
            for term in hints:
                normalized_term = _normalize_text(term)
                if term and normalized_term not in seen and normalized_term not in normalized_query:
                    terms.append(term)
                    seen.add(normalized_term)
        if not terms:
            return query
        return f"{query} {' '.join(terms)}"

    def effective_top_k(self, top_k: int | None) -> int | None:
        minimums = [policy.candidate_min_k for policy in self.policies if policy.candidate_min_k]
        if not minimums:
            return top_k
        return max(top_k or 0, *minimums)

    def apply_quality_scores(self, docs: list[Document]) -> list[Document]:
        if not self.policies or not docs:
            return docs
        for doc in docs:
            metadata = doc.metadata or {}
            bonus = 0.0
            penalty = 0.0
            for index, policy in enumerate(self.policies):
                legacy = self.legacy_profiles[index] if index < len(self.legacy_profiles) else None
                if legacy is not None:
                    profile_bonus, profile_penalty = _score_legacy_doc(self.query, legacy, doc)
                else:
                    profile_bonus, profile_penalty = _score_policy_doc(self.query, policy, doc)
                bonus += profile_bonus
                penalty += profile_penalty
            if bonus <= 0 and penalty <= 0:
                continue
            if bonus > 0:
                metadata["intent_profile_boost"] = round(bonus, 4)
            if penalty > 0:
                metadata["intent_profile_penalty"] = round(penalty, 4)
            metadata["quality_score"] = float(metadata.get("quality_score", 0.0)) + bonus - penalty
            doc.metadata = metadata
        return sorted(docs, key=lambda d: float(d.metadata.get("quality_score", 0.0)), reverse=True)


class RetrievalIntentResolver:
    def __init__(
        self,
        policies: Iterable[RetrievalIntentPolicy],
        legacy_profiles: Iterable[RetrievalIntentProfile] | None = None,
    ):
        self._policies = tuple(policies)
        self._legacy_by_id = {
            profile.id: profile for profile in (legacy_profiles or ())
        }

    @classmethod
    def default(cls) -> "RetrievalIntentResolver":
        return cls(load_intent_policies(), load_legacy_intent_profiles())

    def resolve(self, query: str, top_k: int | None = None) -> RetrievalIntentPlan:
        normalized = _normalize_text(query)
        matched_policies: list[RetrievalIntentPolicy] = []
        matched_legacy: list[RetrievalIntentProfile | None] = []
        for policy in self._policies:
            if not _policy_matches_query(policy, normalized):
                continue
            matched_policies.append(policy)
            matched_legacy.append(self._legacy_by_id.get(policy.id))
        return RetrievalIntentPlan(tuple(matched_policies), tuple(matched_legacy), query or "")

    def refine_from_graph(
        self,
        plan: RetrievalIntentPlan,
        *,
        canonical_names: tuple[str, ...],
    ) -> RetrievalIntentPlan:
        if not canonical_names:
            return plan
        normalized_names = {_normalize_text(name) for name in canonical_names}
        existing_ids = {policy.id for policy in plan.policies}
        extra_policies: list[RetrievalIntentPolicy] = []
        extra_legacy: list[RetrievalIntentProfile | None] = []
        for policy in self._policies:
            if policy.id in existing_ids:
                continue
            if policy.entity_ref and _normalize_text(policy.entity_ref) in normalized_names:
                extra_policies.append(policy)
                extra_legacy.append(self._legacy_by_id.get(policy.id))
        if not extra_policies:
            return plan
        return RetrievalIntentPlan(
            plan.policies + tuple(extra_policies),
            plan.legacy_profiles + tuple(extra_legacy),
            plan.query,
            graph_entity_refs=canonical_names,
        )


def default_policies_path() -> Path:
    repo_default = Path(__file__).resolve().parents[2] / "data" / "retrieval_intent_policies.json"
    env_data_dir = os.getenv("PATH_DATA_DIR")
    if env_data_dir:
        configured = Path(env_data_dir) / "retrieval_intent_policies.json"
        if configured.exists():
            return configured
        return repo_default
    if os.getenv("PYTEST_CURRENT_TEST"):
        return repo_default
    from rag_knowledge.config import Config

    configured = Config().data_dir / "retrieval_intent_policies.json"
    return configured if configured.exists() else repo_default


def default_legacy_profiles_path() -> Path:
    repo_default = (
        Path(__file__).resolve().parents[2] / "data" / "migrations" / "retrieval_intent_profiles_v1.json"
    )
    env_data_dir = os.getenv("PATH_DATA_DIR")
    if env_data_dir:
        configured = Path(env_data_dir) / "migrations" / "retrieval_intent_profiles_v1.json"
        if configured.exists():
            return configured
        return repo_default
    if os.getenv("PYTEST_CURRENT_TEST"):
        return repo_default
    from rag_knowledge.config import Config

    configured = Config().data_dir / "migrations" / "retrieval_intent_profiles_v1.json"
    return configured if configured.exists() else repo_default


def load_intent_policies(path: str | Path | None = None) -> tuple[RetrievalIntentPolicy, ...]:
    policy_path = Path(path) if path is not None else default_policies_path()
    if not policy_path.exists():
        return ()
    with open(policy_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("retrieval intent policies must be a JSON list")
    return tuple(_policy_from_dict(item) for item in raw)


def load_legacy_intent_profiles(path: str | Path | None = None) -> tuple[RetrievalIntentProfile, ...]:
    profile_path = Path(path) if path is not None else default_legacy_profiles_path()
    if not profile_path.exists():
        return ()
    with open(profile_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("legacy retrieval intent profiles must be a JSON list")
    return tuple(_legacy_profile_from_dict(item) for item in raw)


def load_intent_profiles(path: str | Path | None = None) -> tuple[RetrievalIntentProfile, ...]:
    """Migration-only loader for legacy profile facts."""
    return load_legacy_intent_profiles(path)


def section_matches_expected(
    actual_section: str,
    expected_section: str | None,
    profiles: Iterable[RetrievalIntentProfile] | None = None,
) -> bool:
    if not expected_section:
        return True
    actual_section = actual_section or ""
    if expected_section in actual_section:
        return True
    for profile in tuple(profiles or load_legacy_intent_profiles()):
        for family in profile.section_families:
            if expected_section in family:
                return any(alias in actual_section for alias in family)
    return False


def _policy_from_dict(item: dict) -> RetrievalIntentPolicy:
    if not isinstance(item, dict):
        raise ValueError("retrieval intent policy entries must be objects")
    for field in LEGACY_FACT_FIELDS:
        if field in item:
            raise ValueError(f"retrieval intent policy must not include legacy fact field: {field}")
    policy_id = item.get("id")
    if not policy_id:
        raise ValueError("retrieval intent policy requires id")
    policy = RetrievalIntentPolicy(
        id=str(policy_id),
        entity_ref=str(item.get("entity_ref") or ""),
        intent_terms=_as_tuple(item.get("intent_terms")),
        query_hints=_as_tuple(item.get("query_hints")),
        preferred_doc_categories=_as_tuple(item.get("preferred_doc_categories")),
        fallback_doc_categories=_as_tuple(item.get("fallback_doc_categories")),
        candidate_min_k=int(item["candidate_min_k"]) if item.get("candidate_min_k") else None,
    )
    _validate_policy(policy)
    return policy


def _legacy_profile_from_dict(item: dict) -> RetrievalIntentProfile:
    if not isinstance(item, dict):
        raise ValueError("legacy retrieval intent profile entries must be objects")
    profile_id = item.get("id")
    if not profile_id:
        raise ValueError("legacy retrieval intent profile requires id")
    profile = RetrievalIntentProfile(
        id=str(profile_id),
        entity_aliases=_as_tuple(item.get("entity_aliases")),
        intent_terms=_as_tuple(item.get("intent_terms")),
        recall_terms=_as_tuple(item.get("recall_terms")),
        section_families=tuple(_as_tuple(family) for family in item.get("section_families", [])),
        preferred_sources=_as_tuple(item.get("preferred_sources")),
        fallback_sources=_as_tuple(item.get("fallback_sources")),
        sibling_penalty_groups=tuple(_as_tuple(group) for group in item.get("sibling_penalty_groups", [])),
        candidate_min_k=int(item["candidate_min_k"]) if item.get("candidate_min_k") else None,
    )
    _validate_legacy_profile(profile)
    return profile


def _as_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(dict.fromkeys(str(item) for item in value if str(item)))


def _validate_policy(policy: RetrievalIntentPolicy) -> None:
    if not policy.entity_ref and not policy.intent_terms:
        raise ValueError(
            f"retrieval intent policy '{policy.id}' requires entity_ref or intent_terms"
        )
    if policy.candidate_min_k is not None and policy.candidate_min_k <= 0:
        raise ValueError(
            f"retrieval intent policy '{policy.id}' requires candidate_min_k > 0"
        )
    preferred = {_normalize_text(category) for category in policy.preferred_doc_categories}
    fallback = {_normalize_text(category) for category in policy.fallback_doc_categories}
    overlap = preferred & fallback
    if overlap:
        raise ValueError(
            f"retrieval intent policy '{policy.id}' has overlapping preferred_doc_categories and fallback_doc_categories: {sorted(overlap)}"
        )


def _validate_legacy_profile(profile: RetrievalIntentProfile) -> None:
    if not profile.entity_aliases and not profile.intent_terms:
        raise ValueError(
            f"legacy retrieval intent profile '{profile.id}' requires entity_aliases or intent_terms"
        )
    if profile.candidate_min_k is not None and profile.candidate_min_k <= 0:
        raise ValueError(
            f"legacy retrieval intent profile '{profile.id}' requires candidate_min_k > 0"
        )
    preferred = {_normalize_text(source) for source in profile.preferred_sources}
    fallback = {_normalize_text(source) for source in profile.fallback_sources}
    overlap = preferred & fallback
    if overlap:
        raise ValueError(
            f"legacy retrieval intent profile '{profile.id}' has overlapping preferred_sources and fallback_sources: {sorted(overlap)}"
        )


def _policy_matches_query(policy: RetrievalIntentPolicy, query: str) -> bool:
    if policy.entity_ref and _normalize_text(policy.entity_ref) not in query:
        return False
    if policy.intent_terms and not any(_normalize_text(term) in query for term in policy.intent_terms):
        return False
    return bool(policy.entity_ref or policy.intent_terms)


def _score_policy_doc(query: str, policy: RetrievalIntentPolicy, doc: Document) -> tuple[float, float]:
    metadata = doc.metadata or {}
    doc_text = _normalize_text(_doc_match_text(doc))
    category_text = _normalize_text(metadata.get("doc_category") or "")
    bonus = 0.0
    penalty = 0.0

    entity_hit = bool(policy.entity_ref) and _normalize_text(policy.entity_ref) in doc_text
    intent_hit = any(_normalize_text(term) in doc_text for term in policy.intent_terms)
    anchored = entity_hit or intent_hit

    if entity_hit:
        bonus += 0.04
    if intent_hit:
        bonus += 0.04
    if anchored and any(_normalize_text(category) in category_text for category in policy.preferred_doc_categories):
        bonus += 0.08
    if anchored and any(_normalize_text(category) in category_text for category in policy.fallback_doc_categories):
        penalty += 0.03
    return bonus, penalty


def _score_legacy_doc(query: str, profile: RetrievalIntentProfile, doc: Document) -> tuple[float, float]:
    metadata = doc.metadata or {}
    doc_text = _normalize_text(_doc_match_text(doc))
    source_text = _normalize_text(" ".join(
        [
            metadata.get("source") or "",
            metadata.get("file_name") or "",
            metadata.get("doc_category") or "",
        ]
    ))
    bonus = 0.0
    penalty = 0.0

    entity_hit = any(_normalize_text(alias) in doc_text for alias in profile.entity_aliases)
    intent_hit = any(_normalize_text(term) in doc_text for term in profile.intent_terms)
    section_hit = _matches_section_family(metadata.get("section_path") or "", profile.section_families)
    recall_hit = any(_normalize_text(term) in doc_text for term in profile.recall_terms)
    anchored = entity_hit or intent_hit or section_hit or recall_hit

    if entity_hit:
        bonus += 0.04
    if intent_hit:
        bonus += 0.04
    if anchored and any(_normalize_text(source) in source_text for source in profile.preferred_sources):
        bonus += 0.08
    if anchored and any(_normalize_text(source) in source_text for source in profile.fallback_sources):
        penalty += 0.03

    if section_hit:
        bonus += 0.08
    if _matches_sibling_outside_target(doc_text, profile):
        penalty += 0.04
    return bonus, penalty


def _doc_match_text(doc: Document) -> str:
    metadata = doc.metadata or {}
    return " ".join(
        [
            metadata.get("section_path") or "",
            metadata.get("searchable_text") or "",
            metadata.get("source") or "",
            metadata.get("file_name") or "",
            metadata.get("doc_category") or "",
            doc.page_content or "",
        ]
    )


def _matches_section_family(section_path: str, section_families: tuple[tuple[str, ...], ...]) -> bool:
    return any(any(_section_alias_matches(section_path, alias) for alias in family) for family in section_families)


def _section_alias_matches(section_path: str, alias: str) -> bool:
    section_path = _normalize_text(section_path)
    alias = _normalize_text(alias)
    if not section_path or not alias:
        return False
    if section_path == alias:
        return True
    marker = f"{alias} >"
    return marker in section_path


def _matches_sibling_outside_target(text: str, profile: RetrievalIntentProfile) -> bool:
    target_terms = {_normalize_text(alias) for family in profile.section_families for alias in family}
    if any(term in text for term in target_terms):
        return False
    for group in profile.sibling_penalty_groups:
        for term in group:
            normalized_term = _normalize_text(term)
            if normalized_term not in target_terms and normalized_term in text:
                return True
    return False


def _normalize_text(value: str | None) -> str:
    return (value or "").casefold()
