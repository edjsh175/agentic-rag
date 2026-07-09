"""Profile-driven retrieval intent planning and scoring."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from langchain_core.documents import Document


@dataclass(frozen=True)
class RetrievalIntentProfile:
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
    profiles: tuple[RetrievalIntentProfile, ...] = ()
    query: str = ""

    def expand_query(self, query: str) -> str:
        terms = []
        seen = set((query or "").split())
        for profile in self.profiles:
            for term in profile.recall_terms:
                if term and term not in seen:
                    terms.append(term)
                    seen.add(term)
        if not terms:
            return query
        return f"{query} {' '.join(terms)}"

    def effective_top_k(self, top_k: int | None) -> int | None:
        minimums = [p.candidate_min_k for p in self.profiles if p.candidate_min_k]
        if not minimums:
            return top_k
        return max(top_k or 0, *minimums)

    def apply_quality_scores(self, docs: list[Document]) -> list[Document]:
        if not self.profiles or not docs:
            return docs
        for doc in docs:
            metadata = doc.metadata or {}
            bonus = 0.0
            penalty = 0.0
            for profile in self.profiles:
                profile_bonus, profile_penalty = _score_profile_doc(self.query, profile, doc)
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
    def __init__(self, profiles: Iterable[RetrievalIntentProfile]):
        self._profiles = tuple(profiles)

    @classmethod
    def default(cls) -> "RetrievalIntentResolver":
        return cls(load_intent_profiles())

    def resolve(self, query: str, top_k: int | None = None) -> RetrievalIntentPlan:
        normalized = query or ""
        matched = []
        for profile in self._profiles:
            if not _profile_matches_query(profile, normalized):
                continue
            matched.append(profile)
        return RetrievalIntentPlan(tuple(matched), normalized)


def default_profiles_path() -> Path:
    repo_default = Path(__file__).resolve().parents[2] / "data" / "retrieval_intent_profiles.json"
    env_data_dir = os.getenv("PATH_DATA_DIR")
    if env_data_dir:
        configured = Path(env_data_dir) / "retrieval_intent_profiles.json"
        if configured.exists():
            return configured
        return repo_default
    if os.getenv("PYTEST_CURRENT_TEST"):
        return repo_default
    from rag_knowledge.config import Config

    configured = Config().data_dir / "retrieval_intent_profiles.json"
    return configured if configured.exists() else repo_default


def load_intent_profiles(path: str | Path | None = None) -> tuple[RetrievalIntentProfile, ...]:
    profile_path = Path(path) if path is not None else default_profiles_path()
    if not profile_path.exists():
        return ()
    with open(profile_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("retrieval intent profiles must be a JSON list")
    return tuple(_profile_from_dict(item) for item in raw)


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
    for profile in tuple(profiles or load_intent_profiles()):
        for family in profile.section_families:
            if expected_section in family:
                return any(alias in actual_section for alias in family)
    return False


def _profile_from_dict(item: dict) -> RetrievalIntentProfile:
    if not isinstance(item, dict):
        raise ValueError("retrieval intent profile entries must be objects")
    profile_id = item.get("id")
    if not profile_id:
        raise ValueError("retrieval intent profile requires id")
    return RetrievalIntentProfile(
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


def _as_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value if str(item))


def _profile_matches_query(profile: RetrievalIntentProfile, query: str) -> bool:
    if profile.entity_aliases and not any(alias in query for alias in profile.entity_aliases):
        return False
    if profile.intent_terms and not any(term in query for term in profile.intent_terms):
        return False
    return bool(profile.entity_aliases or profile.intent_terms)


def _score_profile_doc(query: str, profile: RetrievalIntentProfile, doc: Document) -> tuple[float, float]:
    metadata = doc.metadata or {}
    doc_text = _doc_match_text(doc)
    source_text = " ".join(
        [
            metadata.get("source") or "",
            metadata.get("file_name") or "",
            metadata.get("doc_category") or "",
        ]
    )
    bonus = 0.0
    penalty = 0.0

    if any(alias in doc_text for alias in profile.entity_aliases):
        bonus += 0.04
    if any(term in doc_text for term in profile.intent_terms):
        bonus += 0.04
    if any(source in source_text for source in profile.preferred_sources):
        bonus += 0.08
    if any(source in source_text for source in profile.fallback_sources):
        penalty += 0.03

    if _matches_section_family(metadata.get("section_path") or "", profile.section_families):
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
    if not section_path or not alias:
        return False
    if section_path == alias:
        return True
    marker = f"{alias} >"
    return marker in section_path


def _matches_sibling_outside_target(text: str, profile: RetrievalIntentProfile) -> bool:
    target_terms = {alias for family in profile.section_families for alias in family}
    for group in profile.sibling_penalty_groups:
        for term in group:
            if term not in target_terms and term in text:
                return True
    return False
