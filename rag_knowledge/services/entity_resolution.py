"""Read-only resolution of extraction candidates against the existing graph."""
from __future__ import annotations

from dataclasses import dataclass, field

from rag_knowledge.services.entity_identity import (
    EntityIdentityService,
    IdentityArbiterProtocol,
    IdentityOutcome,
    TypeArbiterProtocol,
)


@dataclass(frozen=True)
class ResolutionDiagnostic:
    code: str
    message: str


@dataclass(frozen=True)
class ResolutionResult:
    action: str
    target_id: str = ""
    canonical_name: str = ""
    outcome: str = ""
    resolved_type: str = ""
    diagnostics: list[ResolutionDiagnostic] = field(default_factory=list)


class EntityResolutionService:
    def __init__(
        self,
        db,
        *,
        identity_service: EntityIdentityService | None = None,
        arbiter: IdentityArbiterProtocol | None = None,
        type_arbiter: TypeArbiterProtocol | None = None,
    ):
        self.db = db
        self.identity_service = identity_service or EntityIdentityService(
            db=db, arbiter=arbiter, type_arbiter=type_arbiter
        )

    def resolve(
        self,
        candidate,
        *,
        batch_type_index: dict[str, str] | None = None,
        batch_entity_ids: dict[str, str] | None = None,
        batch_display_names: dict[str, str] | None = None,
    ) -> ResolutionResult:
        name = getattr(candidate, "name", "")
        entity_type = getattr(candidate, "entity_type", "")
        evidence_text = getattr(candidate, "evidence_text", "") or ""
        decision = self.identity_service.resolve(
            name,
            entity_type,
            batch_type_index=batch_type_index,
            batch_entity_ids=batch_entity_ids,
            batch_display_names=batch_display_names,
            evidence_text=evidence_text,
        )

        diagnostics = [ResolutionDiagnostic(d.code, d.message) for d in decision.diagnostics]

        action_map = {
            IdentityOutcome.BIND: "reuse",
            IdentityOutcome.ALIAS_OF: "alias",
            IdentityOutcome.NEW: "new",
            IdentityOutcome.CONFLICT: "diagnostic",
            IdentityOutcome.UNCERTAIN: "diagnostic" if diagnostics else "new",
        }
        action = action_map.get(decision.outcome, "new")

        return ResolutionResult(
            action=action,
            target_id=decision.target_entity_id,
            canonical_name=decision.canonical_name,
            outcome=decision.outcome,
            resolved_type=decision.resolved_type,
            diagnostics=diagnostics,
        )
