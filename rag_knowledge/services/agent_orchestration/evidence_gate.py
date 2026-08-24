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
    grant_groups = [
        group for group in evidence.groups
        if group.status == "ACTIVE" and group.kind == "retrieve" and group.grant_id
    ]
    for group in grant_groups:
        for doc in group.docs:
            meta = (doc.get("metadata") if isinstance(doc, dict) else None) or {}
            if meta.get("source_type") == "external":
                continue
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


_CITATION_RE = re.compile(r"\[(\d+)\]|\((\d+)\)")
_RELATION_CLAIM_RE = re.compile(
    r"依赖(?:于)?|需要|要求|属于|包含|调用|使用|连接|关联|协同|配合|联动|交互|"
    r"不同于|区别于|别名|导致|解决|关系|depends\s+on|requires|belongs\s+to|"
    r"uses|causes|solved\s+by|different\s+from",
    re.IGNORECASE,
)
_RELATION_TYPE_HINTS: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (re.compile(r"依赖(?:于)?|depends\s+on", re.IGNORECASE), frozenset({"depends_on", "requires"})),
    (re.compile(r"需要|要求|requires", re.IGNORECASE), frozenset({"requires", "depends_on"})),
    (re.compile(r"属于|belongs\s+to", re.IGNORECASE), frozenset({"belongs_to"})),
    (re.compile(r"不同于|区别于|different\s+from", re.IGNORECASE), frozenset({"different_from"})),
    (re.compile(r"导致|causes", re.IGNORECASE), frozenset({"causes"})),
    (re.compile(r"解决|solved\s+by", re.IGNORECASE), frozenset({"solved_by"})),
    (re.compile(r"使用|uses", re.IGNORECASE), frozenset({"uses_config", "requires", "depends_on"})),
)


def _expected_relation_types(segment: str) -> frozenset[str]:
    expected: set[str] = set()
    for pattern, relation_types in _RELATION_TYPE_HINTS:
        if pattern.search(segment or ""):
            expected.update(relation_types)
    return frozenset(expected)


def evaluate_claim_alignment(
    answer: str,
    evidence: EvidencePool,
    source_docs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Entity-level Claim Guard for V1.6.

    This is intentionally deterministic and conservative: it checks that a claim
    naming entity B cites evidence grouped for B, and that a cross-entity relation
    claim cites an explicit relation-evidence item rather than composing two
    unrelated entity chunks.
    """
    text = str(answer or "").strip()
    if not text:
        return {"allow_claims": True, "reason": "empty_answer", "violations": []}

    citation_map: dict[int, dict[str, Any]] = {}
    for doc in source_docs or []:
        if not isinstance(doc, dict):
            continue
        meta = doc.get("metadata") or {}
        try:
            citation_id = int(meta.get("citation_id"))
        except (TypeError, ValueError):
            continue
        citation_map[citation_id] = meta

    entity_names: list[str] = []
    seen_entities: set[str] = set()
    for group in evidence.groups:
        if group.status != "ACTIVE":
            continue
        target = str(group.target_entity or "").strip()
        if target and target.casefold() not in seen_entities:
            seen_entities.add(target.casefold())
            entity_names.append(target)
        if group.kind == "relation":
            for item in group.provenance:
                if not isinstance(item, dict):
                    continue
                for key in ("source_entity", "target_entity"):
                    value = str(item.get(key) or "").strip()
                    if value and value.casefold() not in seen_entities:
                        seen_entities.add(value.casefold())
                        entity_names.append(value)

    relation_groups = [
        group for group in evidence.groups
        if group.status == "ACTIVE" and group.kind == "relation" and group.relation_key
    ]
    violations: list[dict[str, Any]] = []
    segments = [
        match.group(0).strip()
        for match in re.finditer(
            r"[^。！？!?；;\n]+[。！？!?；;]?(?:\s*(?:\[\d+\]|\(\d+\)))*",
            text,
        )
        if match.group(0).strip()
    ]
    for segment in segments:
        citation_ids: list[int] = []
        for match in _CITATION_RE.finditer(segment):
            raw = match.group(1) or match.group(2)
            try:
                citation_ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        if not citation_ids:
            continue

        cited_meta = [citation_map[cid] for cid in citation_ids if cid in citation_map]
        mentioned = [name for name in entity_names if name.casefold() in segment.casefold()]
        for entity in mentioned:
            aligned = False
            for meta in cited_meta:
                target = str(
                    meta.get("evidence_target_entity")
                    or meta.get("document_entity")
                    or meta.get("scope_entity")
                    or ""
                ).strip()
                relation_key = str(meta.get("relation_key") or "").strip()
                if target and target.casefold() == entity.casefold():
                    aligned = True
                    break
                if relation_key and entity.casefold() in relation_key.casefold():
                    aligned = True
                    break
            if not aligned:
                violations.append({
                    "type": "entity_claim_misaligned",
                    "entity": entity,
                    "citations": citation_ids,
                    "segment": segment[:240],
                })

        relation_claim = len(mentioned) >= 2 and bool(_RELATION_CLAIM_RE.search(segment))
        if relation_claim:
            relation_supported = False
            expected_relation_types = _expected_relation_types(segment)
            for meta in cited_meta:
                relation_key = str(meta.get("relation_key") or "").strip()
                relation_type = str(meta.get("relation_type") or "").strip()
                if not relation_type:
                    match = re.search(r"-\[([^\]]+)\]->", relation_key)
                    relation_type = match.group(1).strip() if match else ""
                entities_match = bool(
                    relation_key
                    and all(name.casefold() in relation_key.casefold() for name in mentioned)
                )
                type_match = not expected_relation_types or relation_type in expected_relation_types
                if entities_match and type_match:
                    relation_supported = True
                    break
            if not relation_supported:
                # A relation group that exists but was not cited still cannot support the claim.
                available_keys = [str(group.relation_key) for group in relation_groups]
                violations.append({
                    "type": "relation_claim_without_relation_evidence",
                    "entities": mentioned,
                    "citations": citation_ids,
                    "available_relation_keys": available_keys[:8],
                    "segment": segment[:240],
                })

    if violations:
        return {
            "allow_claims": False,
            "reason": violations[0]["type"],
            "violations": violations,
        }
    return {"allow_claims": True, "reason": "ok", "violations": []}


def retrieve_improvement(evidence: EvidencePool) -> int | None:
    groups = [group for group in evidence.groups if group.kind == "retrieve"]
    if len(groups) < 2:
        return None
    first = set(groups[0].chunk_ids)
    later: set[str] = set()
    for group in groups[1:]:
        later.update(group.chunk_ids)
    return 1 if later - first else 0
