"""Evidence Gate / Answer Gate and Gap-bound recovery (PRD V1.3 Phase 3)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rag_knowledge.services.agent_orchestration.models import (
    ConversationContext,
    EvidencePool,
)

GAP_TYPES = frozenset({
    "missing_fact",
    "missing_relation",
    "missing_scope",
    "entity_conflict",
    "temporal_conflict",
    "low_relevance",
    "empty_retrieval",
})

RECOVERY_STRATEGIES = frozenset({
    "strip_modifiers",
    "broaden_semantics",
    "add_missing_attribute",
    "increase_entity_constraint",
})

_MODIFIER_RE = re.compile(
    r"(请|帮我|麻烦|详细|完整|具体|仔细|全面|一下|怎么|如何|怎样|怎样才能)"
)


def _labels_overlap(left: str | None, right: str | None) -> bool:
    a = (left or "").strip().casefold()
    b = (right or "").strip().casefold()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def entities_conflict(current: str | None, previous: str | None) -> bool:
    left = (current or "").strip()
    right = (previous or "").strip()
    if not left or not right:
        return False
    return not _labels_overlap(left, right)


def _chunk_id(doc: dict[str, Any]) -> str:
    meta = doc.get("metadata") if isinstance(doc, dict) else None
    if isinstance(meta, dict):
        return str(meta.get("chunk_id") or "")
    return ""


@dataclass
class EvidenceGap:
    gap_type: str
    missing: str = ""
    recovery_strategy: str = ""
    query: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_type": self.gap_type,
            "missing": self.missing,
            "recovery_strategy": self.recovery_strategy,
            "query": self.query,
        }


def normalize_gap_type(raw: str | None) -> str:
    value = str(raw or "").strip()
    return value if value in GAP_TYPES else ""


def normalize_strategy(raw: str | None) -> str:
    value = str(raw or "").strip()
    return value if value in RECOVERY_STRATEGIES else ""


def default_strategy(gap_type: str, recovery_ordinal: int) -> str:
    """Default strategy when the model did not pick one. Not a forced staircase."""
    if gap_type in {"empty_retrieval", "low_relevance"}:
        return "strip_modifiers" if recovery_ordinal <= 1 else "broaden_semantics"
    if gap_type in {"missing_fact", "missing_relation", "missing_scope"}:
        return "add_missing_attribute"
    if gap_type in {"entity_conflict", "temporal_conflict"}:
        return "increase_entity_constraint"
    return "strip_modifiers"


def rewrite_query(
    strategy: str,
    question: str,
    *,
    head_entity: str | None = None,
    missing: str = "",
) -> str:
    q = (question or "").strip()
    entity = (head_entity or "").strip()
    if strategy == "strip_modifiers":
        out = _MODIFIER_RE.sub(" ", q)
        out = re.sub(r"\s+", " ", out).strip() or q
    elif strategy == "broaden_semantics":
        out = f"{entity} 概述".strip() if entity else q
    elif strategy == "add_missing_attribute":
        attr = (missing or "").strip()
        base = entity or q
        out = f"{base} {attr}".strip() if attr else base
    elif strategy == "increase_entity_constraint":
        out = q
        if entity and entity not in q:
            out = f"{entity} {q}".strip()
    else:
        out = q
    if entity and entity not in out:
        out = f"{entity} {out}".strip()
    return out


def evaluate_rules(conversation: ConversationContext, evidence: EvidencePool) -> dict[str, Any]:
    docs = evidence.citable_docs()
    chunk_ids = [_chunk_id(doc) for doc in docs if _chunk_id(doc)]
    if not docs:
        return {"allow_knowledge_answer": False, "reason": "empty_pool"}
    if not chunk_ids:
        return {"allow_knowledge_answer": False, "reason": "no_citable_chunk"}
    head = conversation.head_entity
    if head:
        active_heads = [
            group.head_entity
            for group in evidence.groups
            if group.status == "ACTIVE"
            and group.head_entity
            and group.kind in {"retrieve", "reuse"}
        ]
        if active_heads and all(entities_conflict(head, other) for other in active_heads):
            return {"allow_knowledge_answer": False, "reason": "entity_conflict"}
    return {"allow_knowledge_answer": True, "reason": "ok"}


def classify_rule_gap(conversation: ConversationContext, evidence: EvidencePool) -> EvidenceGap:
    verdict = evaluate_rules(conversation, evidence)
    reason = verdict.get("reason") or ""
    if reason == "entity_conflict":
        return EvidenceGap(gap_type="entity_conflict")
    if reason in {"empty_pool", "no_citable_chunk"}:
        return EvidenceGap(gap_type="empty_retrieval")
    return EvidenceGap(gap_type="low_relevance")


def retrieve_improvement(evidence: EvidencePool) -> int | None:
    groups = [group for group in evidence.groups if group.kind == "retrieve"]
    if len(groups) < 2:
        return None
    first = set(groups[0].chunk_ids)
    later: set[str] = set()
    for group in groups[1:]:
        later.update(group.chunk_ids)
    return 1 if later - first else 0
