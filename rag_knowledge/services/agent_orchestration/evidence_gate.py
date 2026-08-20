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

GAP_LABELS: dict[str, str] = {
    "low_relevance": "检索相关度较低",
    "empty_retrieval": "未命中直接匹配内容",
    "missing_fact": "缺少关键事实支撑",
    "missing_relation": "缺少实体关联信息",
    "missing_scope": "范围定义不明确",
    "entity_conflict": "实体指向存在歧义",
    "temporal_conflict": "时间范围存在冲突",
}

STRATEGY_LABELS: dict[str, str] = {
    "strip_modifiers": "精简修饰词二次检索",
    "broaden_semantics": "扩展泛化概念重新检索",
    "add_missing_attribute": "补充关键属性定向检索",
    "increase_entity_constraint": "强化核心实体精准检索",
}


def format_recovery_notice(gap_type: str, strategy: str) -> str:
    """Format a user-friendly, professional progress notice adhering to PRD V1.5 natural semantics."""
    if strategy == "add_missing_attribute" or gap_type in {"missing_fact", "missing_relation"}:
        return "发现当前资料缺少关键事实，正在进一步深入查询..."
    if strategy == "broaden_semantics" or gap_type == "missing_scope":
        return "正在从相关资料中进一步深入核实关键范围与概念..."
    return "发现当前资料缺少关键信息，正在进一步深入查询..."


def format_recovery_thought(gap_type: str, strategy: str, query: str) -> str:
    """Format a professional agent thought description without raw code enums."""
    gap_label = GAP_LABELS.get(gap_type, "证据不足")
    strat_label = STRATEGY_LABELS.get(strategy, "定向补检")
    return f"检测到当前证据{gap_label}，采用策略【{strat_label}】发起补充检索：{query}"


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

        if scope is not None and getattr(scope, "is_identity_locked", False):
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
        else:
            # 未锁定主体时保留旧的启发式对齐逻辑，作为迁移期兼容路径。
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
