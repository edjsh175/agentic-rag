"""Per-tool exploration authorization for Agent retrieval (PRD V1.6)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.services.backbone_guard import load_backbone_constraints, resolve_canonical
from rag_knowledge.services.relation_policy import SCOPE_TRAVERSAL_RELATIONS

_ALLOWED_DIRECT_SOURCES = frozenset({
    "user_explicit_mention",
    "stage1_resolved_entity",
    "clarification_confirmed",
    "previous_confirmed_context",
})

# ``different_from`` is intentionally excluded from autonomous graph expansion.
# It describes ambiguity separation and must not become a sibling-exploration grant.
_GRAPH_GRANT_RELATIONS = frozenset(SCOPE_TRAVERSAL_RELATIONS - {"different_from"})


@dataclass(frozen=True)
class ExplorationGrant:
    grant_id: str
    identity_scope_id: str
    target_entities: tuple[str, ...]
    source_type: str
    source_ref: str
    allowed_relations: frozenset[str] = field(default_factory=frozenset)
    max_hops: int = 0
    materialized_chunk_ids: frozenset[str] = field(default_factory=frozenset)
    hop_depth: int = 0
    doc_category: str | None = None
    grant_version: str = "v1.6"
    # Agent V2 marks the grant as identity/tool authorization only.  Legacy
    # callers retain their old structural admission semantics during migration.
    candidate_pipeline_v2: bool = False

    @property
    def fingerprint(self) -> str:
        raw = ":".join((
            self.identity_scope_id,
            ",".join(self.target_entities),
            self.source_type,
            self.source_ref,
            ",".join(sorted(self.allowed_relations)),
            str(self.max_hops),
            str(self.hop_depth),
            ",".join(sorted(self.materialized_chunk_ids)),
            self.doc_category or "",
            self.grant_version,
            str(self.candidate_pipeline_v2),
        ))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    @property
    def root_entities(self) -> tuple[str, ...]:
        """Compatibility alias used by graph exact-link helpers."""
        return self.target_entities

    @property
    def primary_root(self) -> str | None:
        return self.target_entities[0] if self.target_entities else None

    @property
    def admissible_entities(self) -> frozenset[str]:
        """Legacy-only compatibility alias; V2 must never consume this as a filter."""
        return frozenset(self.target_entities)

    @property
    def is_identity_locked(self) -> bool:
        """Compatibility flag: a grant is always structurally constrained when targeted."""
        return bool(self.target_entities or self.materialized_chunk_ids)

    def is_structurally_admissible(
        self,
        chunk_entity: str | None,
        chunk_id: str | None = None,
    ) -> bool:
        cid = str(chunk_id or "").strip()
        if cid and cid in self.materialized_chunk_ids:
            return True
        if not self.target_entities:
            return True
        entity = str(chunk_entity or "").strip()
        if not entity:
            return False
        return any(_same_entity(entity, target) for target in self.target_entities)

    def get_provenance(self, entity: str) -> Any:
        if self.target_entities and not any(_same_entity(entity, target) for target in self.target_entities):
            return None
        return _GrantProvenance(
            source_type=self.source_type,
            source_ref=self.source_ref,
            target_entity=entity or self.primary_root or "",
            grant_id=self.grant_id,
            hop_depth=self.hop_depth,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "identity_scope_id": self.identity_scope_id,
            "target_entities": list(self.target_entities),
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "allowed_relations": sorted(self.allowed_relations),
            "max_hops": self.max_hops,
            "hop_depth": self.hop_depth,
            "materialized_chunk_ids": sorted(self.materialized_chunk_ids),
            "doc_category": self.doc_category,
            "fingerprint": self.fingerprint,
            "grant_version": self.grant_version,
            "candidate_pipeline_v2": self.candidate_pipeline_v2,
        }


@dataclass(frozen=True)
class _GrantProvenance:
    source_type: str
    source_ref: str
    target_entity: str
    grant_id: str
    hop_depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "target_entity": self.target_entity,
            "grant_id": self.grant_id,
            "hop_depth": self.hop_depth,
        }


@dataclass(frozen=True)
class GrantAuthorization:
    authorized: bool
    grant: ExplorationGrant | None = None
    rejection_reason: str = ""
    requested_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "authorized": self.authorized,
            "rejection_reason": self.rejection_reason,
            "requested_target": self.requested_target,
        }
        if self.grant is not None:
            payload.update(self.grant.to_dict())
        return payload


class ExplorationGrantResolver:
    """Authorize one Agent exploration target against audited sources only."""

    def __init__(
        self,
        *,
        identity_scope: Any,
        semantic_task: Any,
        clarification_selected: str | None = None,
        previous_confirmed_entity: str | None = None,
        graph_db: Any = None,
        max_hops: int = 2,
        max_entities: int = 12,
        constraints: dict | None = None,
    ) -> None:
        self.identity_scope = identity_scope
        self.semantic_task = semantic_task
        self.clarification_selected = clarification_selected
        self.previous_confirmed_entity = previous_confirmed_entity
        self.graph_db = graph_db
        self.max_hops = max(0, int(max_hops))
        self.max_entities = max(1, int(max_entities))
        self.constraints = constraints if constraints is not None else load_backbone_constraints()
        self._issued_depth: dict[str, int] = {}

    @staticmethod
    def _parse_targets(target_entity: Any) -> tuple[str, ...]:
        import re

        if not target_entity:
            return ()
        if isinstance(target_entity, (list, tuple, set, frozenset)):
            items = []
            for item in target_entity:
                s = str(item or "").strip()
                if s and s not in items:
                    items.append(s)
            return tuple(items)
        raw = str(target_entity).strip()
        if not raw:
            return ()
        parts = [s.strip() for s in re.split(r"[,，/、\n]+", raw) if s.strip()]
        return tuple(dict.fromkeys(parts))

    def authorize(self, target_entity: Any) -> GrantAuthorization:
        requested_raw = (
            ", ".join(str(x) for x in target_entity)
            if isinstance(target_entity, (list, tuple))
            else str(target_entity or "").strip()
        )
        req_targets = self._parse_targets(target_entity)

        scope_status = str(getattr(self.identity_scope, "identity_status", "unresolved") or "").strip()
        if scope_status == "confirmed_topic":
            if req_targets:
                return GrantAuthorization(
                    authorized=False,
                    rejection_reason="confirmed_topic_cannot_grant_entity",
                    requested_target=requested_raw,
                )
            return GrantAuthorization(
                authorized=True,
                grant=self._new_grant(
                    targets=(),
                    source_type="confirmed_topic",
                    source_ref=f"topic:{getattr(self.identity_scope, 'confirmed_topic', '') or ''}",
                    hop_depth=0,
                ),
                requested_target=None,
            )

        if scope_status != "confirmed_entity" and req_targets:
            return GrantAuthorization(
                authorized=False,
                rejection_reason="identity_not_confirmed",
                requested_target=requested_raw,
            )

        if not req_targets:
            if self._is_unbound_task():
                return GrantAuthorization(
                    authorized=True,
                    grant=self._new_grant(
                        targets=(),
                        source_type="stage1_resolved_entity",
                        source_ref="semantic_task:unbound",
                        hop_depth=0,
                    ),
                    requested_target=None,
                )
            return GrantAuthorization(
                authorized=False,
                rejection_reason="target_entity_required" if scope_status == "confirmed_entity" else "identity_not_confirmed",
                requested_target=requested_raw or None,
            )

        authorized_targets: list[str] = []
        direct_sources: list[str] = []
        relation_sources: list[tuple[str, dict[str, Any], int]] = []
        allowed_relations_set: set[str] = set()

        for req in req_targets:
            canonical = self._canonical(req) or req
            if canonical not in self._issued_depth and len(self._issued_depth) >= self.max_entities:
                return GrantAuthorization(
                    authorized=False,
                    rejection_reason="grant_entity_budget_exhausted",
                    requested_target=requested_raw,
                )
            ds = self._direct_source(canonical)
            if ds:
                authorized_targets.append(canonical)
                direct_sources.append(ds)
                self._remember(canonical, 0)
                continue
            rel = self._find_graph_relation(canonical)
            if rel is not None:
                base_name, relation_row, depth = rel
                authorized_targets.append(canonical)
                relation_sources.append(rel)
                rtype = str(relation_row.get("relation_type") or "")
                if rtype:
                    allowed_relations_set.add(rtype)
                self._remember(canonical, depth)
                continue
            return GrantAuthorization(
                authorized=False,
                rejection_reason="target_not_authorized",
                requested_target=requested_raw,
            )

        if direct_sources:
            source_type = "user_explicit_mention" if any(s == "user_explicit_mention" for s in direct_sources) else direct_sources[0]
            source_ref = self._source_ref(source_type, authorized_targets[0])
            hop_depth = 0
        else:
            source_type = "graph_relation"
            source_ref = f"relation:{relation_sources[0][1].get('id') or ''}" if relation_sources else "graph_relation"
            hop_depth = max((r[2] for r in relation_sources), default=1)

        grant = self._new_grant(
            targets=tuple(authorized_targets),
            source_type=source_type,
            source_ref=source_ref,
            hop_depth=hop_depth,
            allowed_relations=frozenset(allowed_relations_set),
        )
        return GrantAuthorization(True, grant=grant, requested_target=requested_raw)

    def _direct_source(self, target: str) -> str | None:
        confirmed_entities = tuple(getattr(self.identity_scope, "confirmed_entities", ()) or ())
        confirmed_entity = getattr(self.identity_scope, "confirmed_entity", None)
        if confirmed_entity and not confirmed_entities:
            confirmed_entities = (confirmed_entity,)

        identity_primary = self._canonical(getattr(self.identity_scope, "primary_entity", None))
        if identity_primary and not confirmed_entities:
            confirmed_entities = (identity_primary,)
        if not any(_same_entity(target, self._canonical(item) or item) for item in confirmed_entities):
            return None

        reason = str(getattr(self.identity_scope, "scope_reason", "") or "")
        if reason == "clarification_confirmed":
            return "clarification_confirmed"
        if reason in {"previous_confirmed_context", "conversation_confirmed_subject"}:
            return "previous_confirmed_context"
        if reason in {"request_explicit_entity", "user_explicit_mention", "multi_entity_context"}:
            return "user_explicit_mention"
        return "stage1_resolved_entity"

    def _find_graph_relation(self, target: str) -> tuple[str, dict[str, Any], int] | None:
        if self.graph_db is None or self.max_hops <= 0:
            return None
        target_entity = self._graph_entity(target)
        if target_entity is None:
            return None

        bases: list[tuple[str, int]] = []
        for name, depth in self._issued_depth.items():
            bases.append((name, depth))
        primary = self._canonical(getattr(self.identity_scope, "primary_entity", None))
        if primary and not any(_same_entity(primary, name) for name, _ in bases):
            bases.append((primary, 0))
        for item in tuple(getattr(self.identity_scope, "confirmed_entities", ()) or ()):
            canonical = self._canonical(item)
            if canonical and not any(_same_entity(canonical, name) for name, _ in bases):
                bases.append((canonical, 0))
        for base_name, base_depth in bases:
            next_depth = base_depth + 1
            if next_depth > self.max_hops:
                continue
            base_entity = self._graph_entity(base_name)
            if base_entity is None:
                continue
            relations = self.graph_db.list_relations(
                entity_id=str(base_entity.get("id") or ""),
                review_status="approved",
            )
            for relation in relations:
                relation_type = str(relation.get("relation_type") or "")
                if relation_type not in _GRAPH_GRANT_RELATIONS:
                    continue
                src_id = str(relation.get("source_entity_id") or "")
                tgt_id = str(relation.get("target_entity_id") or "")
                other_id = tgt_id if src_id == str(base_entity.get("id") or "") else src_id
                if other_id == str(target_entity.get("id") or ""):
                    return base_name, relation, next_depth
        return None

    def _new_grant(
        self,
        *,
        targets: tuple[str, ...],
        source_type: str,
        source_ref: str,
        hop_depth: int,
        allowed_relations: frozenset[str] = frozenset(),
    ) -> ExplorationGrant:
        materialized = self._materialized_chunks(targets)
        return ExplorationGrant(
            grant_id=uuid.uuid4().hex[:12],
            identity_scope_id=str(getattr(self.identity_scope, "scope_id", "") or ""),
            target_entities=targets,
            source_type=source_type,
            source_ref=source_ref,
            allowed_relations=allowed_relations,
            max_hops=self.max_hops,
            materialized_chunk_ids=frozenset(materialized),
            hop_depth=hop_depth,
            doc_category=getattr(self.identity_scope, "doc_category", None),
        )

    def _materialized_chunks(self, targets: tuple[str, ...]) -> set[str]:
        if self.graph_db is None or not targets:
            return set()
        chunk_ids: set[str] = set()
        for target in targets:
            entity = self._graph_entity(target)
            if entity is None:
                continue
            for link in self.graph_db.list_links(entity_id=str(entity.get("id") or "")):
                chunk_id = str(link.get("chunk_id") or "").strip()
                if chunk_id:
                    chunk_ids.add(chunk_id)
        return chunk_ids

    def _remember(self, target: str, depth: int) -> None:
        existing = self._issued_depth.get(target)
        if existing is not None:
            self._issued_depth[target] = min(existing, depth)
            return
        if len(self._issued_depth) < self.max_entities:
            self._issued_depth[target] = depth

    def _source_ref(self, source_type: str, target: str) -> str:
        if source_type == "clarification_confirmed":
            return f"clarification:{target}"
        if source_type == "previous_confirmed_context":
            return f"previous_context:{target}"
        if source_type == "user_explicit_mention":
            return f"user_query:{target}"
        return f"semantic_task:{target}"

    def _canonical(self, value: Any) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        return resolve_canonical(raw, self.constraints) or raw

    def _graph_entity(self, name: str) -> dict[str, Any] | None:
        if self.graph_db is None:
            return None
        key = normalize_entity_name(name or "").casefold()
        if not key:
            return None
        for entity in self.graph_db.list_entities(review_status="approved"):
            names = (
                str(entity.get("name") or ""),
                str(entity.get("canonical_name") or ""),
            )
            if any(normalize_entity_name(item).casefold() == key for item in names if item):
                return entity
        return None

    def _is_unbound_task(self) -> bool:
        scope_status = str(getattr(self.identity_scope, "identity_status", "") or "").strip()
        if scope_status != "unresolved":
            return False
        raw_mention = str(getattr(self.identity_scope, "raw_entity_mention", "") or "").strip()
        raw_mentions = tuple(getattr(self.identity_scope, "raw_entity_mentions", ()) or ())
        if raw_mention or any(str(item or "").strip() for item in raw_mentions):
            return False
        primary = str(getattr(self.semantic_task, "primary_entity", "") or "").strip()
        mentioned = tuple(getattr(self.semantic_task, "mentioned_entities", ()) or ())
        if primary or mentioned:
            return False
        return True


def _same_entity(left: str | None, right: str | None) -> bool:
    a = normalize_entity_name(str(left or "")).casefold()
    b = normalize_entity_name(str(right or "")).casefold()
    return bool(a and b and a == b)
