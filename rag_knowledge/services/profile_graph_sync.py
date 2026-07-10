"""Stage retrieval intent profiles into reviewable graph candidates."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Iterable

from rag_knowledge.models.graph_schema import normalize_entity_name, validate_relation
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_extraction.pipeline import BuildBatchResult
from rag_knowledge.services.domain_catalog import DomainCatalogLoader
from rag_knowledge.services.retrieval_intent import RetrievalIntentProfile, load_intent_profiles

GENERIC_FIELD_TERMS = {"字段名", "字段名称", "说明", "数据", "配置", "路径"}
STRONG_CONFIDENCE = 0.8
WEAK_CONFIDENCE = 0.6
PROFILE_CREATED_BY = "rule:profile_sync"


@dataclass(frozen=True)
class ProfileSyncEntityCandidate:
    name: str
    entity_type: str
    doc_category: str = ""
    properties: dict[str, object] = field(default_factory=dict)
    confidence: float = STRONG_CONFIDENCE
    created_by: str = PROFILE_CREATED_BY
    evidence_text: str = ""


@dataclass(frozen=True)
class ProfileSyncAliasCandidate:
    entity_name: str
    alias: str
    confidence: float = STRONG_CONFIDENCE
    source_chunk_id: str = ""
    evidence_text: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProfileSyncRelationCandidate:
    source_name: str
    relation_type: str
    target_name: str
    confidence: float = STRONG_CONFIDENCE
    source_chunk_id: str = ""
    evidence_text: str = ""
    created_by: str = PROFILE_CREATED_BY
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProfileSyncDiagnostic:
    code: str
    message: str
    profile_id: str
    term: str = ""


@dataclass
class ProfileCandidateSet:
    profile_id: str
    entities: list[ProfileSyncEntityCandidate] = field(default_factory=list)
    aliases: list[ProfileSyncAliasCandidate] = field(default_factory=list)
    relations: list[ProfileSyncRelationCandidate] = field(default_factory=list)
    weak_relations: list[ProfileSyncRelationCandidate] = field(default_factory=list)
    diagnostics: list[ProfileSyncDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "entities": [asdict(item) for item in self.entities],
            "aliases": [asdict(item) for item in self.aliases],
            "relations": [asdict(item) for item in self.relations],
            "weak_relations": [asdict(item) for item in self.weak_relations],
            "diagnostics": [asdict(item) for item in self.diagnostics],
        }


@dataclass
class ProfileSyncPreview:
    profiles: list[ProfileCandidateSet]

    def to_dict(self) -> dict:
        return {"profiles": [item.to_dict() for item in self.profiles]}


class ProfileGraphSyncService:
    def __init__(self, db: RelationalDB | None = None, profiles: Iterable[RetrievalIntentProfile] | None = None):
        self.db = db or RelationalDB()
        self._profiles = tuple(profiles) if profiles is not None else load_intent_profiles()
        self._catalog = DomainCatalogLoader()

    def preview(self, profile_id: str | None = None) -> ProfileSyncPreview:
        selected = self._select_profiles(profile_id)
        alias_map = self._build_alias_map(selected)
        return ProfileSyncPreview([self.extract_candidates(profile, alias_map) for profile in selected])

    def build_batch(
        self,
        profile_id: str | None = None,
        review_status: str = "pending",
        persist: bool = True,
    ) -> BuildBatchResult:
        if review_status not in {"pending", "approved"}:
            raise ValueError("review_status must be pending or approved")
        preview = self.preview(profile_id)
        if not persist:
            return BuildBatchResult("", self._stats_from_preview(preview))

        payload = preview.to_dict()
        snapshot = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        profile_ids = [item.profile_id for item in preview.profiles]
        batch_id = self.db.create_extraction_batch(
            "profile_sync",
            {"profile_ids": profile_ids, "review_status": review_status},
            snapshot,
        )

        counts = {"profiles": len(preview.profiles), "entity": 0, "alias": 0, "relation": 0, "diagnostic": 0}
        for item in preview.profiles:
            counts["entity"] += self._stage_candidates(batch_id, "entity", item.profile_id, item.entities, review_status)
            counts["alias"] += self._stage_candidates(batch_id, "alias", item.profile_id, item.aliases, review_status)
            counts["relation"] += self._stage_candidates(batch_id, "relation", item.profile_id, item.relations, review_status)
            counts["relation"] += self._stage_candidates(batch_id, "relation", item.profile_id, item.weak_relations, review_status)
            counts["diagnostic"] += self._stage_diagnostics(batch_id, item.diagnostics)

        with self.db._get_conn() as conn:
            conn.execute(
                "UPDATE extraction_batches SET stats_json = ? WHERE id = ?",
                (json.dumps(counts, ensure_ascii=False, sort_keys=True), batch_id),
            )
        if review_status == "approved":
            self.db.set_extraction_batch_status(batch_id, "approved")
        return BuildBatchResult(batch_id, counts)

    def extract_candidates(
        self,
        profile: RetrievalIntentProfile,
        alias_map: dict[str, str] | None = None,
    ) -> ProfileCandidateSet:
        alias_map = alias_map or self._build_alias_map((profile,))
        result = ProfileCandidateSet(profile.id)
        entity_seen: set[str] = set()
        alias_seen: set[tuple[str, str]] = set()
        relation_seen: set[tuple[str, str, str]] = set()

        canonical_name = normalize_entity_name(profile.entity_aliases[0]) if profile.entity_aliases else ""
        canonical_type = self._infer_entity_type(canonical_name, profile)
        owner_name = self._owner_from_profile(profile)
        if canonical_name and not self._entity_exists(canonical_name):
            self._append_entity(result, entity_seen, canonical_name, canonical_type, f"profile:{profile.id}:entity_aliases")
        for alias in profile.entity_aliases[1:]:
            normalized_alias = normalize_entity_name(alias)
            if not normalized_alias:
                continue
            if self._alias_exists(canonical_name, normalized_alias):
                continue
            key = (canonical_name, normalized_alias)
            if key not in alias_seen:
                result.aliases.append(
                    ProfileSyncAliasCandidate(
                        entity_name=canonical_name,
                        alias=normalized_alias,
                        evidence_text=f"profile:{profile.id}:entity_aliases",
                        metadata={"profile_id": profile.id, "source_field": "entity_aliases"},
                    )
                )
                alias_seen.add(key)

        section_entities, section_aliases, section_relations = self._extract_section_family_candidates(profile, alias_map)
        for entity in section_entities:
            self._append_entity(result, entity_seen, entity.name, entity.entity_type, entity.evidence_text)
        for alias in section_aliases:
            key = (alias.entity_name, alias.alias)
            if key not in alias_seen and not self._alias_exists(alias.entity_name, alias.alias):
                result.aliases.append(alias)
                alias_seen.add(key)
        for relation in section_relations:
            self._append_relation(result, relation_seen, relation)

        field_relations, field_diagnostics = self._extract_field_relations(profile, canonical_name)
        result.diagnostics.extend(field_diagnostics)
        for relation in field_relations:
            target_type = self._infer_entity_type(relation.target_name, profile)
            if target_type == "Field" and not self._entity_exists(relation.target_name):
                self._append_entity(result, entity_seen, relation.target_name, "Field", relation.evidence_text)
            self._append_relation(result, relation_seen, relation)

        for relation in self._extract_sibling_relations(profile, alias_map):
            source_type = self._infer_entity_type(relation.source_name, profile)
            target_type = self._infer_entity_type(relation.target_name, profile)
            ok, _ = validate_relation(source_type, relation.relation_type, target_type)
            if ok:
                if not self._entity_exists(relation.source_name):
                    self._append_entity(result, entity_seen, relation.source_name, source_type, relation.evidence_text)
                if not self._entity_exists(relation.target_name):
                    self._append_entity(result, entity_seen, relation.target_name, target_type, relation.evidence_text)
                self._append_relation(result, relation_seen, relation)

        for relation in self._extract_strategy_relations(profile, canonical_name, canonical_type, owner_name):
            source_type = self._infer_entity_type(relation.source_name, profile)
            target_type = self._infer_entity_type(relation.target_name, profile)
            ok, reason = validate_relation(source_type, relation.relation_type, target_type)
            if not ok:
                result.diagnostics.append(
                    ProfileSyncDiagnostic(
                        "illegal_strategy_relation",
                        reason,
                        profile.id,
                        relation.target_name,
                    )
                )
                continue
            result.weak_relations.append(relation)
            if not self._entity_exists(relation.target_name):
                self._append_entity(result, entity_seen, relation.target_name, target_type, relation.evidence_text)

        return result

    def _stage_candidates(self, batch_id: str, kind: str, profile_id: str, items: list, review_status: str) -> int:
        count = 0
        for item in items:
            payload = asdict(item)
            payload.setdefault("metadata", {})
            payload["metadata"].setdefault("profile_id", profile_id)
            fingerprint = hashlib.sha256(
                json.dumps([kind, self._identity_payload(kind, payload)], ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            candidate_id = self.db.add_extraction_candidate(
                batch_id,
                kind,
                fingerprint,
                payload,
                payload.get("source_chunk_id", ""),
                payload.get("evidence_text", ""),
            )
            if review_status == "approved":
                self.db.review_extraction_candidates(batch_id, [candidate_id], "approved")
            count += 1
        return count

    def _stage_diagnostics(self, batch_id: str, items: list[ProfileSyncDiagnostic]) -> int:
        count = 0
        for item in items:
            payload = asdict(item)
            fingerprint = hashlib.sha256(
                json.dumps(["diagnostic", payload], ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            candidate_id = self.db.add_extraction_candidate(
                batch_id,
                "diagnostic",
                fingerprint,
                payload,
                "",
                payload["message"],
            )
            self.db.review_extraction_candidates(batch_id, [candidate_id], "rejected", payload["message"])
            count += 1
        return count

    def _append_entity(self, result: ProfileCandidateSet, seen: set[str], name: str, entity_type: str, evidence_text: str) -> None:
        normalized = normalize_entity_name(name)
        if not normalized or normalized in seen or self._entity_exists(normalized):
            return
        result.entities.append(
            ProfileSyncEntityCandidate(
                name=normalized,
                entity_type=entity_type,
                confidence=STRONG_CONFIDENCE,
                created_by=PROFILE_CREATED_BY,
                evidence_text=evidence_text,
            )
        )
        seen.add(normalized)

    def _append_relation(self, result: ProfileCandidateSet, seen: set[tuple[str, str, str]], relation: ProfileSyncRelationCandidate) -> None:
        key = (normalize_entity_name(relation.source_name), relation.relation_type, normalize_entity_name(relation.target_name))
        if key in seen or self._relation_exists(*key):
            return
        if self._relation_rejected(*key):
            result.diagnostics.append(
                ProfileSyncDiagnostic(
                    "rejected_relation_exists",
                    f"rejected relation exists: {relation.source_name} {relation.relation_type} {relation.target_name}",
                    result.profile_id,
                    relation.target_name,
                )
            )
            return
        result.relations.append(relation)
        seen.add(key)

    def _extract_section_family_candidates(
        self,
        profile: RetrievalIntentProfile,
        alias_map: dict[str, str],
    ) -> tuple[list[ProfileSyncEntityCandidate], list[ProfileSyncAliasCandidate], list[ProfileSyncRelationCandidate]]:
        entities: list[ProfileSyncEntityCandidate] = []
        aliases: list[ProfileSyncAliasCandidate] = []
        relations: list[ProfileSyncRelationCandidate] = []
        for family in profile.section_families:
            if not family:
                continue
            first_parts = [part.strip() for part in family[0].split(">") if part.strip()]
            if not first_parts:
                continue
            owner_name = normalize_entity_name(first_parts[0])
            owner_type = self._infer_entity_type(owner_name, profile)
            if owner_name and not self._entity_exists(owner_name):
                entities.append(
                    ProfileSyncEntityCandidate(owner_name, owner_type, confidence=STRONG_CONFIDENCE, evidence_text=f"profile:{profile.id}:section_families")
                )
            section_name = normalize_entity_name(family[0])
            if section_name and not self._entity_exists(section_name):
                entities.append(
                    ProfileSyncEntityCandidate(section_name, "Section", properties={"section_path": family[0]}, evidence_text=f"profile:{profile.id}:section_families")
                )
            for alias in family:
                leaf = normalize_entity_name(alias.split(">")[-1].strip())
                canonical = alias_map.get(leaf, leaf)
                canonical_type = self._infer_entity_type(canonical, profile)
                if not self._entity_exists(canonical):
                    entities.append(
                        ProfileSyncEntityCandidate(canonical, canonical_type, confidence=STRONG_CONFIDENCE, evidence_text=f"profile:{profile.id}:section_families")
                    )
                if leaf != canonical and not self._alias_exists(canonical, leaf):
                    aliases.append(
                        ProfileSyncAliasCandidate(
                            entity_name=canonical,
                            alias=leaf,
                            evidence_text=f"profile:{profile.id}:section_families",
                            metadata={"profile_id": profile.id, "source_field": "section_families"},
                        )
                    )
                relations.extend(
                    [
                        ProfileSyncRelationCandidate(owner_name, "has_table", canonical, evidence_text=f"profile:{profile.id}:section_families"),
                        ProfileSyncRelationCandidate(canonical, "belongs_to", owner_name, evidence_text=f"profile:{profile.id}:section_families"),
                        ProfileSyncRelationCandidate(canonical, "defined_in", section_name, evidence_text=f"profile:{profile.id}:section_families"),
                    ]
                )
        return entities, aliases, relations

    def _extract_field_relations(
        self,
        profile: RetrievalIntentProfile,
        canonical_name: str,
    ) -> tuple[list[ProfileSyncRelationCandidate], list[ProfileSyncDiagnostic]]:
        result: list[ProfileSyncRelationCandidate] = []
        diagnostics: list[ProfileSyncDiagnostic] = []
        if not canonical_name:
            return result, diagnostics
        for term in profile.recall_terms:
            normalized = normalize_entity_name(term)
            if not normalized:
                continue
            if normalized in {normalize_entity_name(item) for item in profile.entity_aliases}:
                continue
            if self._is_generic_field_term(normalized):
                diagnostics.append(
                    ProfileSyncDiagnostic(
                        code="generic_recall_term",
                        message=f"generic recall term skipped: {normalized}",
                        profile_id=profile.id,
                        term=normalized,
                    )
                )
                continue
            result.append(
                ProfileSyncRelationCandidate(
                    source_name=canonical_name,
                    relation_type="has_field",
                    target_name=normalized,
                    evidence_text=f"profile:{profile.id}:recall_terms",
                )
            )
        return result, diagnostics

    def _extract_sibling_relations(
        self,
        profile: RetrievalIntentProfile,
        alias_map: dict[str, str],
    ) -> list[ProfileSyncRelationCandidate]:
        relations: list[ProfileSyncRelationCandidate] = []
        for group in profile.sibling_penalty_groups:
            canonicals: list[str] = []
            for name in group:
                normalized = normalize_entity_name(name)
                canonical = alias_map.get(normalized, normalized)
                if canonical not in canonicals:
                    canonicals.append(canonical)
            for index, source in enumerate(canonicals):
                for target in canonicals[index + 1 :]:
                    if source == target:
                        continue
                    relations.append(
                        ProfileSyncRelationCandidate(
                            source_name=source,
                            relation_type="different_from",
                            target_name=target,
                            evidence_text=f"profile:{profile.id}:sibling_penalty_groups",
                        )
                    )
        return relations

    def _extract_strategy_relations(
        self,
        profile: RetrievalIntentProfile,
        canonical_name: str,
        canonical_type: str,
        owner_name: str,
    ) -> list[ProfileSyncRelationCandidate]:
        if not canonical_name:
            return []
        if canonical_type == "DataTable" and owner_name:
            return []
        result: list[ProfileSyncRelationCandidate] = []
        for field_name, sources in (
            ("preferred_sources", profile.preferred_sources),
            ("fallback_sources", profile.fallback_sources),
        ):
            for source in sources:
                target = normalize_entity_name(source)
                if not target:
                    continue
                result.append(
                    ProfileSyncRelationCandidate(
                        source_name=canonical_name,
                        relation_type="belongs_to",
                        target_name=target,
                        confidence=WEAK_CONFIDENCE,
                        evidence_text=f"profile:{profile.id}:{field_name}",
                        created_by=PROFILE_CREATED_BY,
                        metadata={"profile_id": profile.id, "source_field": field_name, "strategy_derived": True},
                    )
                )
        return result

    def _build_alias_map(self, profiles: Iterable[RetrievalIntentProfile]) -> dict[str, str]:
        alias_map: dict[str, str] = {}
        for profile in profiles:
            aliases = [normalize_entity_name(item) for item in profile.entity_aliases if normalize_entity_name(item)]
            if aliases:
                canonical = aliases[0]
                for alias in aliases:
                    alias_map[alias] = canonical
            for group in profile.sibling_penalty_groups:
                current_canonical = ""
                for item in group:
                    normalized = normalize_entity_name(item)
                    if not normalized:
                        continue
                    if normalized.endswith("表"):
                        current_canonical = normalized
                        alias_map.setdefault(normalized, normalized)
                    elif current_canonical:
                        alias_map.setdefault(normalized, current_canonical)
        return alias_map

    def _select_profiles(self, profile_id: str | None) -> tuple[RetrievalIntentProfile, ...]:
        if profile_id is None:
            return self._profiles
        selected = tuple(profile for profile in self._profiles if profile.id == profile_id)
        if not selected:
            raise KeyError(f"profile not found: {profile_id}")
        return selected

    def _owner_from_profile(self, profile: RetrievalIntentProfile) -> str:
        for family in profile.section_families:
            if not family:
                continue
            parts = [part.strip() for part in family[0].split(">") if part.strip()]
            if parts:
                return normalize_entity_name(parts[0])
        return ""

    def _infer_entity_type(self, name: str, profile: RetrievalIntentProfile) -> str:
        normalized = normalize_entity_name(name)
        profile_aliases = {normalize_entity_name(item) for item in profile.entity_aliases}
        if normalized in profile_aliases and (profile.section_families or profile.sibling_penalty_groups):
            return "DataTable"
        resolved = self._catalog.resolve(normalized)
        if resolved and resolved[1] == "Tool" or normalized.endswith("Builder"):
            return "Tool"
        if resolved and resolved[1] == "Service" or normalized.endswith("服务"):
            return "Service"
        if resolved and resolved[1] == "Product":
            return "Product"
        if ">" in normalized:
            return "Section"
        if normalized.endswith("表") or normalized.endswith("数据结构"):
            return "DataTable"
        if profile.recall_terms and normalized in {normalize_entity_name(item) for item in profile.recall_terms}:
            return "Field"
        return "Module"

    def _entity_exists(self, name: str) -> bool:
        return self.db.get_entity_by_name(normalize_entity_name(name)) is not None

    def _alias_exists(self, entity_name: str, alias: str) -> bool:
        entity = self.db.get_entity_by_name(normalize_entity_name(entity_name))
        if not entity:
            return False
        with self.db._get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM aliases WHERE entity_id = ? AND alias = ?",
                (entity["id"], normalize_entity_name(alias)),
            ).fetchone()
            return row is not None

    def _relation_exists(self, source_name: str, relation_type: str, target_name: str) -> bool:
        source = self.db.get_entity_by_name(source_name)
        target = self.db.get_entity_by_name(target_name)
        if not source or not target:
            return False
        return self.db.get_relation_by_details(source["id"], target["id"], relation_type) is not None

    def _relation_rejected(self, source_name: str, relation_type: str, target_name: str) -> bool:
        source = self.db.get_entity_by_name(source_name)
        target = self.db.get_entity_by_name(target_name)
        if not source or not target:
            return False
        with self.db._get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM relations WHERE source_entity_id = ? AND target_entity_id = ? AND relation_type = ? AND review_status = 'rejected'",
                (source["id"], target["id"], relation_type),
            ).fetchone()
            return row is not None

    @staticmethod
    def _identity_payload(kind: str, payload: dict) -> dict:
        keys_by_kind = {
            "entity": ("name", "entity_type"),
            "alias": ("entity_name", "alias"),
            "relation": ("source_name", "relation_type", "target_name"),
            "diagnostic": ("code", "profile_id", "term", "message"),
        }
        return {key: payload.get(key) for key in keys_by_kind[kind]}

    @staticmethod
    def _stats_from_preview(preview: ProfileSyncPreview) -> dict:
        return {
            "profiles": len(preview.profiles),
            "entity": sum(len(item.entities) for item in preview.profiles),
            "alias": sum(len(item.aliases) for item in preview.profiles),
            "relation": sum(len(item.relations) + len(item.weak_relations) for item in preview.profiles),
            "diagnostic": sum(len(item.diagnostics) for item in preview.profiles),
        }

    @staticmethod
    def _is_generic_field_term(term: str) -> bool:
        return len(term) <= 3 or term.isdigit() or term in GENERIC_FIELD_TERMS
