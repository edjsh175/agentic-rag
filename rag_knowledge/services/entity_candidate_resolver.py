"""Unified Entity Candidate Resolver & Identity Resolution Subsystem (PRD 2026-08-27).

Single authority for:
1. Entity Registry from product relation backbone and domain catalog.
2. Multi-channel candidate recall (exact, alias, lexical, token/typo).
3. Candidate scoring and ranking (lexical + graph + dialogue context).
4. Identity status resolution (confirmed, ambiguous, unresolved, not_required).
5. Clarification candidate snapshot management and callback validation.
"""
from __future__ import annotations

import difflib
import hashlib
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from rag_knowledge.services.backbone_guard import load_backbone_constraints

logger = logging.getLogger(__name__)

_DEFAULT_ALLOWED_ENTITY_TYPES = frozenset({"Product", "Tool", "Service", "Module"})
_MAX_DISPLAY_OPTIONS = 5
_MIN_DISPLAY_OPTIONS = 3
_SNAPSHOT_TTL_SECONDS = 15 * 60
_SNAPSHOT_MAX_ENTRIES = 1_024


def _normalize_key(text: str) -> str:
    """Casefold and strip all whitespace/delimiters for matching."""
    return re.sub(r"[\s_\-]+", "", str(text or "")).casefold()


def _split_camel_or_tokens(text: str) -> list[str]:
    """Split English CamelCase / tokens and return lowercased parts."""
    parts = re.findall(r"[A-Z][a-z]+|[A-Z]+(?![a-z])|[a-z]+|\d+", text or "")
    return [p.casefold() for p in parts if p]


def _deterministic_entity_id(canonical_name: str) -> str:
    """Deterministic, collision-resistant entity ID."""
    clean = (canonical_name or "").strip().casefold()
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:12]
    return f"ent_{digest}"


@dataclass(frozen=True)
class RegisteredEntity:
    entity_id: str
    canonical_name: str
    display_name: str
    entity_type: str
    doc_category: str | None = None
    description: str | None = None
    aliases: tuple[str, ...] = ()
    belongs_to: str | None = None
    different_from: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "canonical_name": self.canonical_name,
            "display_name": self.display_name,
            "entity_type": self.entity_type,
            "doc_category": self.doc_category,
            "description": self.description,
            "aliases": list(self.aliases),
            "belongs_to": self.belongs_to,
            "different_from": list(self.different_from),
        }


class EntityRegistry:
    """Verified entity universe loaded from backbone constraints & domain catalog."""

    def __init__(
        self,
        constraints: dict | None = None,
        *,
        allowed_types: frozenset[str] | None = None,
    ):
        self._constraints = constraints if constraints is not None else load_backbone_constraints()
        self._allowed_types = allowed_types or _DEFAULT_ALLOWED_ENTITY_TYPES
        self._entities_by_id: dict[str, RegisteredEntity] = {}
        self._entities_by_canonical: dict[str, RegisteredEntity] = {}
        self._alias_to_canonical: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        types_map = self._constraints.get("entity_type_by_name") or {}
        alias_map = self._constraints.get("canonical_by_alias") or {}
        doc_cat_map = self._constraints.get("doc_category_by_name") or {}
        belongs_to_map = self._constraints.get("belongs_to") or {}
        diff_pairs = self._constraints.get("different_from") or []

        # Build different_from lookup
        diff_lookup: dict[str, set[str]] = {}
        for pair in diff_pairs:
            if isinstance(pair, (set, frozenset, list, tuple)) and len(pair) >= 2:
                items = list(pair)
                diff_lookup.setdefault(items[0], set()).add(items[1])
                diff_lookup.setdefault(items[1], set()).add(items[0])

        for name, etype in types_map.items():
            canonical = str(name).strip()
            if not canonical or canonical.endswith(".so"):
                continue
            if etype not in self._allowed_types:
                continue

            entity_id = _deterministic_entity_id(canonical)
            doc_category = doc_cat_map.get(canonical) or None

            # Collect aliases
            aliases: list[str] = []
            for al, target in alias_map.items():
                if target == canonical and al != canonical:
                    aliases.append(al)

            belongs_to_set = belongs_to_map.get(canonical) or set()
            parent = next(iter(belongs_to_set)) if belongs_to_set else None
            different_from = tuple(sorted(diff_lookup.get(canonical, set())))

            reg = RegisteredEntity(
                entity_id=entity_id,
                canonical_name=canonical,
                display_name=canonical,
                entity_type=etype,
                doc_category=doc_category,
                aliases=tuple(sorted(set(aliases))),
                belongs_to=parent,
                different_from=different_from,
            )
            self._entities_by_id[entity_id] = reg
            self._entities_by_canonical[_normalize_key(canonical)] = reg
            self._alias_to_canonical[_normalize_key(canonical)] = canonical
            for alias in aliases:
                self._alias_to_canonical[_normalize_key(alias)] = canonical

    def get_by_id(self, entity_id: str) -> RegisteredEntity | None:
        return self._entities_by_id.get(entity_id)

    def get_by_canonical(self, canonical_name: str) -> RegisteredEntity | None:
        return self._entities_by_canonical.get(_normalize_key(canonical_name))

    def get_by_name(self, name_or_alias: str) -> RegisteredEntity | None:
        canon = self.resolve_canonical_name(name_or_alias)
        if canon:
            return self.get_by_canonical(canon)
        return self.get_by_canonical(name_or_alias)

    def resolve_canonical_name(self, name_or_alias: str) -> str | None:
        key = _normalize_key(name_or_alias)
        return self._alias_to_canonical.get(key)

    def all_entities(self) -> list[RegisteredEntity]:
        return list(self._entities_by_id.values())


@dataclass(frozen=True)
class EntityCandidate:
    """Ranked candidate derived from verified entity registry."""

    entity_id: str
    canonical_name: str
    display_name: str
    entity_type: str
    matched_surface: str | None
    match_sources: tuple[str, ...]
    lexical_score: float
    semantic_score: float | None
    context_score: float
    graph_score: float
    final_score: float
    doc_category: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "canonical_name": self.canonical_name,
            "display_name": self.display_name,
            "entity_type": self.entity_type,
            "matched_surface": self.matched_surface,
            "match_sources": list(self.match_sources),
            "lexical_score": round(self.lexical_score, 4),
            "semantic_score": round(self.semantic_score, 4) if self.semantic_score is not None else None,
            "context_score": round(self.context_score, 4),
            "graph_score": round(self.graph_score, 4),
            "final_score": round(self.final_score, 4),
            "doc_category": self.doc_category,
            "description": self.description,
        }


@dataclass(frozen=True)
class IdentityResolution:
    """Authoritative outcome of entity identity resolution."""

    status: str  # "confirmed" | "ambiguous" | "unresolved" | "not_required"
    surface: str | None
    confirmed_entity_id: str | None
    confirmed_entity_name: str | None
    candidates: tuple[EntityCandidate, ...]
    confidence: float | None
    margin: float | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "surface": self.surface,
            "confirmed_entity_id": self.confirmed_entity_id,
            "confirmed_entity_name": self.confirmed_entity_name,
            "candidates": [c.to_dict() for c in self.candidates],
            "confidence": round(self.confidence, 4) if self.confidence is not None else None,
            "margin": round(self.margin, 4) if self.margin is not None else None,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ClarificationCandidateSnapshot:
    """Frozen snapshot of clarification options published to the user."""

    clarification_id: str
    created_at: float
    expires_at: float
    surface: str | None
    candidate_entity_ids: tuple[str, ...]
    candidates: tuple[EntityCandidate, ...]
    display_candidates: tuple[EntityCandidate, ...]
    identity_resolution: IdentityResolution

    def to_dict(self) -> dict[str, Any]:
        return {
            "clarification_id": self.clarification_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "surface": self.surface,
            "candidate_entity_ids": list(self.candidate_entity_ids),
            "candidates": [c.to_dict() for c in self.candidates],
            "display_candidates": [c.to_dict() for c in self.display_candidates],
            "identity_resolution": self.identity_resolution.to_dict(),
        }


class ClarificationSnapshotStore:
    """Process-local, bounded clarification snapshot store.

    Snapshots are reusable until their TTL expires. They deliberately do not
    survive a process restart or cross a worker boundary; callbacks on another
    worker fail closed and the client must request clarification again.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = _SNAPSHOT_TTL_SECONDS,
        max_entries: int = _SNAPSHOT_MAX_ENTRIES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ttl_seconds = max(1.0, float(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._clock = clock
        self._snapshots: OrderedDict[str, ClarificationCandidateSnapshot] = OrderedDict()
        self._lock = RLock()

    def put(self, snapshot: ClarificationCandidateSnapshot) -> None:
        with self._lock:
            self._purge_expired()
            self._snapshots[snapshot.clarification_id] = snapshot
            self._snapshots.move_to_end(snapshot.clarification_id)
            while len(self._snapshots) > self._max_entries:
                self._snapshots.popitem(last=False)

    def issued_at(self) -> float:
        return self._clock()

    def expires_at(self, created_at: float) -> float:
        return created_at + self._ttl_seconds

    def get(self, snapshot_id: str | None) -> ClarificationCandidateSnapshot | None:
        if not snapshot_id:
            return None
        with self._lock:
            self._purge_expired()
            snapshot = self._snapshots.get(snapshot_id)
            if snapshot is not None:
                self._snapshots.move_to_end(snapshot_id)
            return snapshot

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [sid for sid, snapshot in self._snapshots.items() if snapshot.expires_at <= now]
        for snapshot_id in expired:
            self._snapshots.pop(snapshot_id, None)


_CLARIFICATION_SNAPSHOT_STORE = ClarificationSnapshotStore()


class EntityCandidateResolver:
    """Discovers, ranks, and resolves entity identities from verified registry."""

    def __init__(
        self,
        registry: EntityRegistry | None = None,
        *,
        constraints: dict | None = None,
        confirmed_min_score: float = 0.90,
        confirmed_min_margin: float = 0.15,
        ambiguous_min_score: float = 0.45,
        snapshot_store: ClarificationSnapshotStore | None = None,
    ):
        self.registry = registry or EntityRegistry(constraints=constraints)
        self.confirmed_min_score = float(confirmed_min_score)
        self.confirmed_min_margin = float(confirmed_min_margin)
        self.ambiguous_min_score = float(ambiguous_min_score)
        self._snapshot_store = snapshot_store or _CLARIFICATION_SNAPSHOT_STORE

    def discover_candidates(
        self,
        surface: str,
        *,
        context_entities: tuple[str, ...] | list[str] | None = None,
        linked_entities: tuple[str, ...] | list[str] | None = None,
        doc_category: str | None = None,
    ) -> list[EntityCandidate]:
        """Recall candidates across exact, alias, and lexical channels with scoring."""
        s = (surface or "").strip()
        if not s:
            return []

        s_norm = _normalize_key(s)
        s_tokens = _split_camel_or_tokens(s)
        all_entities = self.registry.all_entities()

        # Channel 1: Exact canonical match
        # Channel 2: Alias match
        # Channel 3: Lexical match (token overlap, substring, prefix/suffix, typo distance)
        matched_scores: dict[str, dict[str, Any]] = {}

        for ent in all_entities:
            c_name = ent.canonical_name
            c_norm = _normalize_key(c_name)
            c_tokens = _split_camel_or_tokens(c_name)
            sources: set[str] = set()
            best_lex = 0.0

            # 1. Exact canonical match
            if s_norm == c_norm:
                sources.add("exact")
                best_lex = max(best_lex, 1.0)
            elif len(c_norm) >= 3 and c_norm in s_norm:
                is_standalone = bool(re.search(rf"(?<![a-zA-Z0-9]){re.escape(c_norm)}(?![a-zA-Z0-9])", s_norm))
                coverage = len(c_norm) / max(len(s_norm), 1)
                if is_standalone:
                    sources.add("exact")
                    best_lex = max(best_lex, 0.95 + 0.05 * coverage)
                else:
                    sources.add("lexical")
                    best_lex = max(best_lex, 0.65 + 0.15 * coverage)

            # 2. Alias match
            for alias in ent.aliases:
                a_norm = _normalize_key(alias)
                if s_norm == a_norm:
                    sources.add("alias")
                    best_lex = max(best_lex, 0.96)
                elif len(a_norm) >= 3 and a_norm in s_norm:
                    is_standalone = bool(re.search(rf"(?<![a-zA-Z0-9]){re.escape(a_norm)}(?![a-zA-Z0-9])", s_norm))
                    coverage = len(a_norm) / max(len(s_norm), 1)
                    if is_standalone:
                        sources.add("alias")
                        best_lex = max(best_lex, 0.92 + 0.04 * coverage)
                    else:
                        sources.add("lexical")
                        best_lex = max(best_lex, 0.60 + 0.15 * coverage)
                elif len(s_norm) >= 2 and s_norm in a_norm:
                    sources.add("lexical")
                    coverage = len(s_norm) / max(len(a_norm), 1)
                    best_lex = max(best_lex, 0.65 + 0.20 * coverage)

            # 3. Token match
            if s_tokens and c_tokens:
                overlap = set(s_tokens) & set(c_tokens)
                if overlap:
                    sources.add("lexical")
                    ratio = len(overlap) / max(len(c_tokens), len(s_tokens), 1)
                    # If surface only mentions the root/stem token (e.g. "WebGL" matches root of "StampWebGL"/"PipelineWebGL")
                    if len(overlap) == 1 and len(c_tokens) > 1 and list(overlap)[0].casefold() == c_tokens[-1].casefold():
                        best_lex = max(best_lex, 0.92)
                    elif len(s_tokens) == 1 and s_tokens[0] in c_tokens:
                        best_lex = max(best_lex, 0.70 + 0.15 * ratio)
                    else:
                        best_lex = max(best_lex, 0.55 + 0.30 * ratio)

            # 4. Normalized substring / prefix / suffix match
            if len(s_norm) >= 2:
                if c_norm.startswith(s_norm) or c_norm.endswith(s_norm):
                    sources.add("lexical")
                    cov = len(s_norm) / max(len(c_norm), 1)
                    best_lex = max(best_lex, 0.68 + 0.22 * cov)
                elif s_norm in c_norm:
                    sources.add("lexical")
                    cov = len(s_norm) / max(len(c_norm), 1)
                    best_lex = max(best_lex, 0.60 + 0.20 * cov)

            # 5. Typo / edit distance similarity
            if len(s_norm) >= 4:
                sim = difflib.SequenceMatcher(None, s_norm, c_norm).ratio()
                if sim >= 0.75:
                    sources.add("lexical")
                    best_lex = max(best_lex, 0.50 + 0.40 * sim)
                else:
                    # Check token-level typo against c_tokens
                    for tok in c_tokens:
                        if len(tok) >= 4:
                            tsim = difflib.SequenceMatcher(None, s_norm, tok).ratio()
                            if tsim >= 0.80:
                                sources.add("lexical")
                                best_lex = max(best_lex, 0.50 + 0.35 * tsim)
                    # Also check against aliases for typos
                    for alias in ent.aliases:
                        a_norm = _normalize_key(alias)
                        if len(a_norm) >= 4:
                            asim = difflib.SequenceMatcher(None, s_norm, a_norm).ratio()
                            if asim >= 0.80:
                                sources.add("lexical")
                                best_lex = max(best_lex, 0.50 + 0.35 * asim)

            if best_lex > 0.0:
                matched_scores[ent.entity_id] = {
                    "entity": ent,
                    "sources": tuple(sorted(sources)),
                    "lexical_score": best_lex,
                }

        # Candidate fusion and context / graph score boosting
        ctx_set = {
            _normalize_key(e) for e in (context_entities or ()) if str(e).strip()
        }
        link_set = {
            _normalize_key(e) for e in (linked_entities or ()) if str(e).strip()
        }

        candidates: list[EntityCandidate] = []
        for ent_id, m in matched_scores.items():
            ent: RegisteredEntity = m["entity"]
            lex_score: float = m["lexical_score"]
            c_norm = _normalize_key(ent.canonical_name)

            # Category filter narrowing: if doc_category specified and mismatches, penalize or filter
            if doc_category:
                cat_norm = doc_category.strip().casefold()
                if ent.doc_category and ent.doc_category.strip().casefold() != cat_norm:
                    lex_score *= 0.5

            context_score = 0.0
            if c_norm in ctx_set:
                context_score = 0.10

            graph_score = 0.0
            if c_norm in link_set:
                graph_score += 0.10
            # If parent belongs_to is in link_set or ctx_set
            if ent.belongs_to and _normalize_key(ent.belongs_to) in (ctx_set | link_set):
                graph_score += 0.08

            final_score = min(1.0, lex_score + context_score + graph_score)

            candidates.append(
                EntityCandidate(
                    entity_id=ent.entity_id,
                    canonical_name=ent.canonical_name,
                    display_name=ent.canonical_name,
                    entity_type=ent.entity_type,
                    matched_surface=s,
                    match_sources=m["sources"],
                    lexical_score=lex_score,
                    semantic_score=None,
                    context_score=context_score,
                    graph_score=graph_score,
                    final_score=final_score,
                    doc_category=ent.doc_category,
                    description=ent.description,
                )
            )

        # Sort descending by final_score, then canonical_name
        candidates.sort(key=lambda c: (c.final_score, -len(c.canonical_name), c.canonical_name), reverse=True)
        return candidates

    def resolve_identity(
        self,
        surface: str | None,
        *,
        entity_binding_required: bool = True,
        context_entities: tuple[str, ...] | list[str] | None = None,
        linked_entities: tuple[str, ...] | list[str] | None = None,
        doc_category: str | None = None,
    ) -> IdentityResolution:
        """Evaluate ranked candidates and determine identity status."""
        if not entity_binding_required:
            return IdentityResolution(
                status="not_required",
                surface=surface,
                confirmed_entity_id=None,
                confirmed_entity_name=None,
                candidates=(),
                confidence=1.0,
                margin=None,
                reason="entity_binding_not_required",
            )

        s = (surface or "").strip()
        if not s:
            return IdentityResolution(
                status="unresolved",
                surface=None,
                confirmed_entity_id=None,
                confirmed_entity_name=None,
                candidates=(),
                confidence=0.0,
                margin=None,
                reason="empty_surface",
            )

        candidates = self.discover_candidates(
            s,
            context_entities=context_entities,
            linked_entities=linked_entities,
            doc_category=doc_category,
        )

        if not candidates:
            return IdentityResolution(
                status="unresolved",
                surface=s,
                confirmed_entity_id=None,
                confirmed_entity_name=None,
                candidates=(),
                confidence=0.0,
                margin=None,
                reason="no_candidates_found",
            )

        top1 = candidates[0]
        top2 = candidates[1] if len(candidates) > 1 else None
        margin = (top1.final_score - top2.final_score) if top2 is not None else top1.final_score

        # Rule 1: Exact match with clean margin -> confirmed
        if "exact" in top1.match_sources:
            return IdentityResolution(
                status="confirmed",
                surface=s,
                confirmed_entity_id=top1.entity_id,
                confirmed_entity_name=top1.canonical_name,
                candidates=tuple(candidates),
                confidence=top1.final_score,
                margin=margin,
                reason="exact_canonical_match",
            )

        # Rule 2: Alias match with distinct margin -> confirmed
        if "alias" in top1.match_sources and (top2 is None or margin >= self.confirmed_min_margin):
            return IdentityResolution(
                status="confirmed",
                surface=s,
                confirmed_entity_id=top1.entity_id,
                confirmed_entity_name=top1.canonical_name,
                candidates=tuple(candidates),
                confidence=top1.final_score,
                margin=margin,
                reason="alias_high_confidence",
            )

        # Rule 3: High score and clear margin -> confirmed
        if top1.final_score >= self.confirmed_min_score and margin >= self.confirmed_min_margin:
            return IdentityResolution(
                status="confirmed",
                surface=s,
                confirmed_entity_id=top1.entity_id,
                confirmed_entity_name=top1.canonical_name,
                candidates=tuple(candidates),
                confidence=top1.final_score,
                margin=margin,
                reason="high_confidence_margin",
            )

        # Rule 4: Ambiguous candidates (scores close, >= ambiguous_min_score)
        if top1.final_score >= self.ambiguous_min_score:
            return IdentityResolution(
                status="ambiguous",
                surface=s,
                confirmed_entity_id=None,
                confirmed_entity_name=None,
                candidates=tuple(candidates),
                confidence=top1.final_score,
                margin=margin,
                reason="entity_identity_ambiguous",
            )

        # Rule 5: Low confidence -> unresolved
        return IdentityResolution(
            status="unresolved",
            surface=s,
            confirmed_entity_id=None,
            confirmed_entity_name=None,
            candidates=tuple(candidates),
            confidence=top1.final_score,
            margin=margin,
            reason="low_confidence_unresolved",
        )

    def create_clarification_snapshot(
        self,
        resolution: IdentityResolution,
        *,
        max_options: int = _MAX_DISPLAY_OPTIONS,
    ) -> ClarificationCandidateSnapshot:
        """Freeze candidates for UI clarification presentation."""
        created_at = self._snapshot_store.issued_at()
        raw_id = f"{resolution.surface}:{created_at}:{id(resolution)}"
        clarification_id = f"clar_{hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:16]}"

        display_cands = resolution.candidates[:max_options]
        entity_ids = tuple(c.entity_id for c in display_cands)

        snapshot = ClarificationCandidateSnapshot(
            clarification_id=clarification_id,
            created_at=created_at,
            expires_at=self._snapshot_store.expires_at(created_at),
            surface=resolution.surface,
            candidate_entity_ids=entity_ids,
            candidates=resolution.candidates,
            display_candidates=display_cands,
            identity_resolution=resolution,
        )
        self._snapshot_store.put(snapshot)
        return snapshot

    def get_snapshot(self, snapshot_id: str | None) -> ClarificationCandidateSnapshot | None:
        """Retrieve frozen snapshot by clarification_id."""
        if not snapshot_id:
            return None
        return self._snapshot_store.get(snapshot_id)

    def validate_callback_selection(
        self,
        selected_id_or_option: str | None,
        *,
        snapshot_id: str | None = None,
    ) -> RegisteredEntity | None:
        """Validate user selection against frozen snapshot and return RegisteredEntity."""
        if not selected_id_or_option:
            return None
        sel = str(selected_id_or_option).strip()
        if sel.lower() in {"other", "fixed_other", "以上都不是"}:
            return None

        if snapshot_id:
            snap = self.get_snapshot(snapshot_id)
            if snap:
                # Find matching display candidate by entity_id or canonical_name
                cand = next(
                    (c for c in snap.display_candidates if c.entity_id == sel or c.canonical_name.casefold() == sel.casefold()),
                    None,
                )
                if cand:
                    return self.registry.get_by_id(cand.entity_id) or RegisteredEntity(
                        entity_id=cand.entity_id,
                        canonical_name=cand.canonical_name,
                        entity_type=cand.entity_type or "Product",
                        doc_category=cand.doc_category,
                    )
                logger.warning("Callback selection '%s' not found in snapshot %s", sel, snapshot_id)
                return None

        logger.warning("Callback selection requires a clarification snapshot")
        return None


_GLOBAL_RESOLVER: EntityCandidateResolver | None = None


def get_entity_candidate_resolver(constraints: dict | None = None) -> EntityCandidateResolver:
    """Singleton getter for default entity candidate resolver."""
    global _GLOBAL_RESOLVER
    if constraints is not None:
        return EntityCandidateResolver(constraints=constraints)
    if _GLOBAL_RESOLVER is None:
        _GLOBAL_RESOLVER = EntityCandidateResolver()
    return _GLOBAL_RESOLVER
