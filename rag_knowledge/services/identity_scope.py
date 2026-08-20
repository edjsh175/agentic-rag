"""Identity-only scope for conversation-agent orchestration (PRD V1.6)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from rag_knowledge.services.backbone_guard import (
    avoid_names_for_anchors,
    load_backbone_constraints,
    resolve_canonical,
)
from rag_knowledge.services.evidence_scope import BindingStrength


@dataclass(frozen=True)
class IdentityScope:
    """Locks who the conversation is about without constraining evidence exploration."""

    scope_id: str
    primary_entity: str | None
    binding_strength: BindingStrength
    forbidden_rebindings: frozenset[str]
    scope_reason: str
    doc_category: str | None = None
    scope_version: str = "v1.6"

    @property
    def is_identity_locked(self) -> bool:
        return self.binding_strength in {BindingStrength.CONFIRMED, BindingStrength.EXPLICIT}

    @property
    def primary_root(self) -> str | None:
        """Compatibility alias for trace/callers migrating from EvidenceScope."""
        return self.primary_entity

    @property
    def root_entities(self) -> tuple[str, ...]:
        return (self.primary_entity,) if self.primary_entity else ()

    @property
    def fingerprint(self) -> str:
        raw = ":".join((
            self.primary_entity or "",
            self.binding_strength.value,
            ",".join(sorted(self.forbidden_rebindings)),
            self.doc_category or "",
            self.scope_version,
        ))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "primary_entity": self.primary_entity,
            "binding_strength": self.binding_strength.value,
            "is_identity_locked": self.is_identity_locked,
            "forbidden_rebindings": sorted(self.forbidden_rebindings),
            "scope_reason": self.scope_reason,
            "doc_category": self.doc_category,
            "scope_version": self.scope_version,
            "fingerprint": self.fingerprint,
        }


class IdentityScopeResolver:
    """Materialize identity after Stage-1 semantic understanding."""

    @classmethod
    def resolve(
        cls,
        semantic_task: Any,
        *,
        entity_name: str | None = None,
        clarification_selected: str | None = None,
        previous_confirmed_entity: str | None = None,
        doc_category: str | None = None,
        constraints: dict | None = None,
    ) -> IdentityScope:
        constraints = constraints if constraints is not None else load_backbone_constraints()
        explicit = cls._canonical(entity_name, constraints)
        clarified = cls._canonical(clarification_selected, constraints)
        previous = cls._canonical(previous_confirmed_entity, constraints)

        mentioned = tuple(getattr(semantic_task, "mentioned_entities", ()) or ())
        primary = cls._canonical(getattr(semantic_task, "primary_entity", None), constraints)

        if explicit:
            primary = explicit
            strength = BindingStrength.EXPLICIT
            reason = "request_explicit_entity"
        elif clarified:
            primary = clarified
            strength = BindingStrength.CONFIRMED
            reason = "clarification_confirmed"
        elif primary and any(cls._same(primary, item) for item in mentioned):
            strength = BindingStrength.EXPLICIT
            reason = "user_explicit_mention"
        elif primary:
            strength = BindingStrength.INFERRED
            reason = "stage1_resolved_entity"
        elif previous:
            primary = previous
            strength = BindingStrength.CONFIRMED
            reason = "conversation_confirmed_subject"
        else:
            strength = BindingStrength.UNBOUND
            reason = "stage1_unbound"

        forbidden: set[str] = set()
        if primary:
            forbidden.update(avoid_names_for_anchors([primary], constraints))
            forbidden.discard(primary)

        cat = (doc_category or "").strip() or None
        raw = f"{primary or ''}:{strength.value}:{reason}:{cat or ''}"
        scope_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        return IdentityScope(
            scope_id=scope_id,
            primary_entity=primary,
            binding_strength=strength,
            forbidden_rebindings=frozenset(forbidden),
            scope_reason=reason,
            doc_category=cat,
        )

    @staticmethod
    def _canonical(value: Any, constraints: dict) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        return resolve_canonical(raw, constraints) or raw

    @staticmethod
    def _same(left: str, right: str) -> bool:
        a = (left or "").strip().casefold()
        b = (right or "").strip().casefold()
        return bool(a and b and (a == b or a in b or b in a))
