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
    any_v2_docs = any(
        bool(((doc.get("metadata") if isinstance(doc, dict) else None) or {}).get("candidate_pipeline_v2"))
        or (((doc.get("metadata") if isinstance(doc, dict) else None) or {}).get("source_type") == "graph_relation")
        for doc in docs
    )
    for group in evidence.groups:
        if group.status != "ACTIVE":
            continue
        for doc in group.docs:
            meta = (doc.get("metadata") if isinstance(doc, dict) else None) or {}
            if meta.get("source_type") == "graph_relation" and meta.get("admission_verdict") != "PASS":
                return {"allow_knowledge_answer": False, "reason": "graph_relation_admission_failed"}
            if not meta.get("candidate_pipeline_v2"):
                continue
            if group.kind not in {"retrieve", "relation", "reuse", "previous_turn_cited"}:
                return {"allow_knowledge_answer": False, "reason": "v2_non_retrieve_evidence"}
            if meta.get("admission_verdict") and meta.get("admission_verdict") != "PASS":
                return {"allow_knowledge_answer": False, "reason": "query_admission_failed"}
    grant_groups = [
        group for group in evidence.groups
        if group.status == "ACTIVE" and group.kind == "retrieve" and group.grant_id
    ]
    for group in grant_groups:
        for doc in group.docs:
            meta = (doc.get("metadata") if isinstance(doc, dict) else None) or {}
            if meta.get("source_type") == "external":
                continue
            if meta.get("candidate_pipeline_v2") and meta.get("admission_verdict") != "PASS":
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
    # V2 evidence has already passed query-scoped Text/Graph Admission. Legacy
    # name/anchor heuristics must not become a second evidence authority.
    if head and not any_v2_docs:
        active_heads = [
            group.head_entity
            for group in evidence.groups
            if group.status == "ACTIVE"
            and group.head_entity
            and group.kind in {"retrieve", "reuse"}
        ]
        if active_heads and all(entities_conflict(head, other) for other in active_heads):
            return {"allow_knowledge_answer": False, "reason": "entity_conflict"}

        from rag_knowledge.services.backbone_guard import load_backbone_constraints, resolve_canonical

        constraints = load_backbone_constraints()
        canon = resolve_canonical(head, constraints) or head
        scope = conversation.scope

        is_legacy_evidence_scope = bool(
            scope is not None
            and hasattr(scope, "admissible_entities")
            and not hasattr(scope, "forbidden_rebindings")
        )
        if is_legacy_evidence_scope and getattr(scope, "is_identity_locked", False):
            from rag_knowledge.services.relation_policy import is_scope_traversal_relation

            scope_id = str(getattr(scope, "scope_id", "") or "")
            invalid_reasons: list[str] = []
            kb_docs = []
            for d in docs:
                meta = (d.get("metadata") if isinstance(d, dict) else None) or {}
                if meta.get("source_type") == "external":
                    continue
                kb_docs.append(d)
                if str(meta.get("scope_id") or "") != scope_id:
                    invalid_reasons.append("scope_id_mismatch")
                    continue
                if meta.get("scope_admitted") is not True:
                    invalid_reasons.append("scope_not_admitted")
                    continue
                admission_reason = str(meta.get("scope_admission_reason") or "")
                provenance_type = str(meta.get("provenance_source_type") or "")
                if admission_reason == "materialized_chunk":
                    continue
                if not provenance_type or provenance_type == "legacy_fallback":
                    invalid_reasons.append("untrusted_provenance")
                    continue
                if provenance_type == "graph_relation":
                    provenance_path = meta.get("provenance_path") or {}
                    relation_type = str(
                        provenance_path.get("relation_type")
                        if isinstance(provenance_path, dict)
                        else ""
                    )
                    if not is_scope_traversal_relation(relation_type):
                        invalid_reasons.append("relation_not_scope_admissible")

            if kb_docs and invalid_reasons:
                return {
                    "allow_knowledge_answer": False,
                    "reason": "scope_provenance_failed",
                    "provenance_reason": invalid_reasons[0],
                    "refusal_text": f"知识库中暂未找到与 {canon} 对齐且来源可验证的已审核文档内容，无法可靠回答。",
                }
        elif not grant_groups:
            # 无 V1.6 Grant 的旧路径继续使用启发式对齐，作为迁移期兼容逻辑。
            from rag_knowledge.services.anchor_chunk_filter import chunk_matches_anchor
            from langchain_core.documents import Document

            admissible_canonicals = [canon] if canon else []
            if (
                scope is not None
                and getattr(scope, "primary_root", None) == canon
                and getattr(scope, "admissible_entities", None)
            ):
                admissible_canonicals = sorted(set(admissible_canonicals) | set(scope.admissible_entities))

            if admissible_canonicals:
                aligned_docs = []
                for d in docs:
                    meta = (d.get("metadata") if isinstance(d, dict) else None) or {}
                    doc_obj = Document(page_content=d.get("content", "") if isinstance(d, dict) else "", metadata=meta)
                    if chunk_matches_anchor(doc_obj, canonicals=admissible_canonicals, constraints=constraints):
                        aligned_docs.append(d)
                if not aligned_docs:
                    return {
                        "allow_knowledge_answer": False,
                        "reason": "strict_entity_alignment_failed",
                        "refusal_text": f"知识库中暂未找到与 {canon} 对齐的已审核文档内容，无法可靠回答。",
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
