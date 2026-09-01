"""PRD §12.5: bind ``gap_support_delta`` to the actual Reviewer gap contract.

The gap contract is the ``retrieval_feedback`` produced by the Helper
Grounding Reviewer (``subject_entity_ids`` + ``deficiency_type`` + stable
``gap_id``).  A retrieval only counts as gap support when the newly-added
citable evidence (a) matches the deficiency-type profile for the gap and
(b) does not contradict the flagged subject.  Without a contract the
evaluator makes no claim of gap support (conservative).
"""

from __future__ import annotations

import re
from typing import Any

_ENTITY_NORMALIZE_RE = re.compile(r"[\s，,、/()（）\[\]【】#：:]+")


def _norm_entity(value: Any) -> str:
    return _ENTITY_NORMALIZE_RE.sub("", str(value or "").strip()).casefold()


# deficiency_type -> acceptable (evidence_class, support_scope) pair for text
# evidence.  GRAPH_EDGE_MISSING maps to graph_relation evidence instead.
_DEFICIENCY_PROFILE: dict[str, set[tuple[str, str]] | None] = {
    "NO_DIRECT_EVIDENCE": {("TARGET_DIRECT", "TARGET_SPECIFIC")},
    "SUBJECT_MISMATCH": {("TARGET_DIRECT", "TARGET_SPECIFIC")},
    "CONTEXTUAL_MISSING": {("RELATED_CONTEXT", "CONTEXT_ONLY")},
    "GRAPH_EDGE_MISSING": None,  # graph_relation evidence
}

# Fields on a doc whose entity attribution is compared against the contract's
# subject_entity_ids.
_SUBJECT_FIELDS = (
    "document_entity",
    "evidence_target_entity",
    "scope_entity",
    "entity_name",
    "target_name",
    "source_name",
    "identity_primary_entity",
    "scope_root",
)


class GapSupportEvaluator:
    """Evaluate whether newly-added citable evidence genuinely supports a gap."""

    def __init__(self, gap_contract: dict[str, Any] | None = None) -> None:
        self.gap_contract = dict(gap_contract) if gap_contract else None
        if self.gap_contract is None:
            self._subject_entities: frozenset[str] = frozenset()
            self._deficiency_type = ""
            self._acceptable: set[tuple[str, str]] | None = None
            return
        self._subject_entities = frozenset(
            _norm_entity(item)
            for item in (self.gap_contract.get("subject_entity_ids") or ())
            if _norm_entity(item)
        )
        self._deficiency_type = str(
            self.gap_contract.get("deficiency_type") or ""
        ).strip().upper()
        self._acceptable = _DEFICIENCY_PROFILE.get(self._deficiency_type)

    @property
    def has_contract(self) -> bool:
        return self.gap_contract is not None

    def evaluate(self, docs: list[dict[str, Any]] | None) -> int:
        """How many of the given newly-added citable docs support the gap."""
        if self.gap_contract is None or self._deficiency_type not in _DEFICIENCY_PROFILE:
            # No contract, or an unrecognized deficiency type: no claim.
            return 0
        return sum(1 for doc in (docs or []) if self._supports_gap(doc))

    def _supports_gap(self, doc: dict[str, Any]) -> bool:
        meta = dict(doc.get("metadata") or {}) if isinstance(doc, dict) else {}
        if self._acceptable is None:  # GRAPH_EDGE_MISSING -> graph relation
            if str(meta.get("source_type") or "").strip() != "graph_relation":
                return False
            return self._subject_ok(meta)
        evidence_class = str(meta.get("evidence_class") or "").strip().upper()
        support_scope = str(meta.get("support_scope") or "").strip().upper()
        if (evidence_class, support_scope) not in self._acceptable:
            return False
        return self._subject_ok(meta)

    def _subject_ok(self, meta: dict[str, Any]) -> bool:
        """Subject binding: reject evidence attributed to a *different* subject.

        Contextual evidence (CONTEXTUAL_MISSING) is not subject-bound.  When
        the doc carries no entity attribution we cannot contradict the gap, so
        we do not reject.
        """
        if not self._subject_entities or self._deficiency_type == "CONTEXTUAL_MISSING":
            return True
        attributed = {_norm_entity(meta.get(field)) for field in _SUBJECT_FIELDS}
        attributed.discard("")
        if not attributed:
            return True
        return bool(attributed & self._subject_entities)
