"""Evidence state evaluation and claim alignment."""

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_type": self.gap_type,
            "missing": self.missing,
        }


def normalize_gap_type(raw: str | None) -> str:
    value = str(raw or "").strip()
    return value if value in GAP_TYPES else ""


def evaluate_rules(conversation: ConversationContext, evidence: EvidencePool) -> dict[str, Any]:
    docs = evidence.citable_docs()
    chunk_ids = [_chunk_id(doc) for doc in docs if _chunk_id(doc)]
    if not docs:
        return {"allow_knowledge_answer": False, "reason": "empty_pool"}
    if not chunk_ids:
        return {"allow_knowledge_answer": False, "reason": "no_citable_chunk"}

    scope = conversation.scope
    identity_scope_id = str(getattr(scope, "scope_id", "") or "")

    def _doc_source_type(doc: dict) -> str:
        meta = (doc.get("metadata") if isinstance(doc, dict) else None) or {}
        return str(meta.get("source_type") or "").strip()

    # Admitted evidence = knowledge-base text or graph relations that passed
    # the query-scoped Text/Graph Admission. External sources follow their own
    # protocol and do not count.
    has_admitted_evidence = any(_doc_source_type(doc) != "external" for doc in docs)
    valid_text_evidence = {
        ("TARGET_DIRECT", "TARGET_SPECIFIC"),
        ("RELATED_CONTEXT", "CONTEXT_ONLY"),
    }
    for group in evidence.groups:
        if group.status != "ACTIVE":
            continue
        for doc in group.docs:
            if not evidence._is_citable_document(doc):
                continue
            meta = (doc.get("metadata") if isinstance(doc, dict) else None) or {}
            source_type = _doc_source_type(doc)
            if source_type == "external":
                continue
            if source_type == "graph_relation":
                if str(meta.get("relation_relevance") or "").strip().upper() != "DIRECT":
                    return {"allow_knowledge_answer": False, "reason": "graph_relation_admission_failed"}
                continue
            if group.kind not in {"retrieve", "relation", "reuse", "previous_turn_cited"}:
                return {"allow_knowledge_answer": False, "reason": "text_admission_non_retrieve_evidence"}
            if (
                str(meta.get("evidence_class") or "").strip().upper(),
                str(meta.get("support_scope") or "").strip().upper(),
            ) not in valid_text_evidence:
                return {"allow_knowledge_answer": False, "reason": "query_admission_failed"}
    grant_groups = [
        group for group in evidence.groups
        if group.status == "ACTIVE" and group.kind == "retrieve" and group.grant_id
    ]
    for group in grant_groups:
        for doc in group.docs:
            if not evidence._is_citable_document(doc):
                continue
            meta = (doc.get("metadata") if isinstance(doc, dict) else None) or {}
            if _doc_source_type(doc) == "external":
                continue
            if (
                str(meta.get("evidence_class") or "").strip().upper(),
                str(meta.get("support_scope") or "").strip().upper(),
            ) not in valid_text_evidence:
                return {"allow_knowledge_answer": False, "reason": "query_admission_failed"}
            if str(meta.get("grant_id") or "") != str(group.grant_id or ""):
                return {"allow_knowledge_answer": False, "reason": "grant_id_mismatch"}
            if meta.get("grant_admitted") is not True:
                return {"allow_knowledge_answer": False, "reason": "grant_not_admitted"}
            if identity_scope_id and str(meta.get("identity_scope_id") or "") != identity_scope_id:
                return {"allow_knowledge_answer": False, "reason": "identity_scope_mismatch"}
            target = str(group.target_entity or "").strip().casefold()
            actual = str(
                meta.get("evidence_target_entity")
                or meta.get("scope_entity")
                or meta.get("document_entity")
                or ""
            ).strip().casefold()
            if target and actual:
                targets_set = {t.strip().casefold() for t in re.split(r"[,，/、\n]+", target) if t.strip()}
                if actual not in targets_set and target != actual:
                    return {"allow_knowledge_answer": False, "reason": "grant_target_mismatch"}

    head = conversation.head_entity or conversation.selected_entity
    # Admitted evidence has already passed query-scoped Text/Graph Admission.
    # Legacy name/anchor heuristics must not become a second evidence authority.
    if head and not has_admitted_evidence:
        return {
            "allow_knowledge_answer": False,
            "reason": "no_query_admitted_evidence",
        }

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
