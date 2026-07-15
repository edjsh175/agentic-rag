"""Read-only resolution of extraction candidates against the existing graph."""
from __future__ import annotations

from dataclasses import dataclass, field

from rag_knowledge.models.graph_schema import normalize_entity_name


@dataclass(frozen=True)
class ResolutionDiagnostic:
    code: str
    message: str


@dataclass(frozen=True)
class ResolutionResult:
    action: str
    target_id: str = ""
    diagnostics: list[ResolutionDiagnostic] = field(default_factory=list)


class EntityResolutionService:
    def __init__(self, db):
        self.db = db

    def resolve(self, candidate) -> ResolutionResult:
        name = normalize_entity_name(candidate.name)
        entities = self.db.list_entities()
        for entity in entities:
            if normalize_entity_name(entity.get("name", "")) != name:
                continue
            if entity.get("entity_type") == candidate.entity_type:
                return ResolutionResult("reuse", str(entity.get("id") or ""))
            return ResolutionResult(
                "diagnostic",
                diagnostics=[ResolutionDiagnostic("type_conflict", f"{name}: {entity.get('entity_type')} != {candidate.entity_type}")],
            )

        for alias in self.db.list_aliases():
            if normalize_entity_name(alias.get("alias", "")) == name:
                return ResolutionResult("alias", str(alias.get("entity_id") or ""))

        # Section hierarchy creates path prefixes of existing leaf Sections
        # (e.g. ``Doc::A`` vs ``Doc::A > B``). Treat these as new Section nodes,
        # not possible_duplicate substring collisions.
        if candidate.entity_type == "Section":
            return ResolutionResult("new")

        folded = name.casefold()
        for entity in entities:
            existing = normalize_entity_name(entity.get("name", ""))
            if folded in existing.casefold() or existing.casefold() in folded:
                return ResolutionResult(
                    "diagnostic",
                    diagnostics=[ResolutionDiagnostic("possible_duplicate", f"{name} may duplicate {existing}")],
                )
        return ResolutionResult("new")
