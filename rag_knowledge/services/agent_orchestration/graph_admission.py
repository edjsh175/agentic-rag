"""Graph relation candidate admission service (PRD 2026-08-26)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from rag_knowledge.services.agent_orchestration.graph_working_set import (
    GraphRelationCandidate,
    GraphWorkingSet,
)
from rag_knowledge.services.relation_policy import (
    RELATION_RULES,
    is_answer_evidence_relation,
    relation_rule,
)

logger = logging.getLogger(__name__)

_EXACT_PARAMETER_TERMS = ("端口", "port", "参数", "密码", "密钥", "默认值", "路径", "命令", "ip", "url")
_RELATION_INTENT_TERMS = {
    "belongs_to": ("属于", "归属", "产品", "体系", "定位", "是什么", "介绍", "概览", "关系", "架构"),
    "depends_on": ("依赖", "要求", "依赖于", "前提", "需要", "服务", "组件", "关系"),
    "requires": ("依赖", "要求", "需要", "前提", "环境", "组件", "关系"),
    "different_from": ("区别", "不同", "对比", "比较", "差异", "关系"),
    "implements": ("实现", "接口", "协议", "标准", "规范", "关系"),
    "uses": ("使用", "调用", "采用", "关系"),
    "has_service": ("服务", "模块", "包含", "子服务", "组件", "关系"),
    "has_module": ("模块", "包含", "组件", "子系统", "关系"),
}


@dataclass(frozen=True)
class GraphRelationAdmissionResult:
    """Admission verdict for one graph relation candidate."""

    verdict: str  # PASS | REJECT
    entity_relevance: str  # HIGH | MEDIUM | LOW | CONFLICT
    intent_relevance: str  # HIGH | MEDIUM | LOW | NONE
    relation_relevance: str  # DIRECT | CONTEXTUAL | IRRELEVANT
    reason: str
    admission_signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "entity_relevance": self.entity_relevance,
            "intent_relevance": self.intent_relevance,
            "relation_relevance": self.relation_relevance,
            "reason": self.reason,
            "admission_signals": list(self.admission_signals),
        }


class GraphRelationAdmissionService:
    """Evaluates whether an approved graph relation candidate qualifies as query-level factual Evidence."""

    @staticmethod
    def _validate_hard_conditions(
        candidate: GraphRelationCandidate,
        working_set: GraphWorkingSet | None,
    ) -> tuple[bool, str]:
        if str(candidate.review_status or "").strip().lower() != "approved":
            return False, f"unapproved_review_status:{candidate.review_status}"
        if not candidate.relation_id or not candidate.relation_id.strip():
            return False, "missing_relation_id"
        if not candidate.source_name or not candidate.target_name:
            return False, "missing_endpoints"
        if candidate.relation_type not in RELATION_RULES:
            return False, f"unregistered_relation_type:{candidate.relation_type}"
        rule = relation_rule(candidate.relation_type)
        if not rule or not rule.answer_evidence:
            return False, f"relation_type_not_answer_evidence:{candidate.relation_type}"
        return True, "ok"

    @classmethod
    def admit(
        cls,
        question: str,
        candidate: GraphRelationCandidate,
        *,
        working_set: GraphWorkingSet | None = None,
        target_entities: list[str] | tuple[str, ...] | None = None,
        task_type: str | None = None,
        helper_admitter: Callable[[str, GraphRelationCandidate], GraphRelationAdmissionResult | None] | None = None,
    ) -> GraphRelationAdmissionResult:
        """Admit or reject a graph relation candidate."""
        # 1. Hard validations
        valid_hard, hard_reason = cls._validate_hard_conditions(candidate, working_set)
        if not valid_hard:
            return GraphRelationAdmissionResult(
                verdict="REJECT",
                entity_relevance="LOW",
                intent_relevance="NONE",
                relation_relevance="IRRELEVANT",
                reason=hard_reason,
                admission_signals=("hard_condition_failed",),
            )

        q_norm = (question or "").casefold()
        signals: list[str] = []
        rule = relation_rule(candidate.relation_type)

        # 2. Entity Relevance check
        active_targets = {
            str(t).strip().casefold()
            for t in (target_entities or (working_set.exploration_roots if working_set else ()))
            if str(t).strip()
        }
        s_norm = candidate.source_name.casefold()
        t_norm = candidate.target_name.casefold()

        source_in_targets = s_norm in active_targets or any(target in s_norm or s_norm in target for target in active_targets)
        target_in_targets = t_norm in active_targets or any(target in t_norm or t_norm in target for target in active_targets)
        source_in_q = s_norm in q_norm
        target_in_q = t_norm in q_norm

        if (source_in_targets and target_in_targets) or (source_in_q and target_in_q):
            entity_relevance = "HIGH"
            signals.append("both_endpoints_matched")
        elif source_in_targets or target_in_targets or source_in_q or target_in_q:
            entity_relevance = "HIGH"
            signals.append("primary_endpoint_matched")
        else:
            entity_relevance = "MEDIUM" if candidate.depth_from_root <= 1 else "LOW"
            signals.append("indirect_entity_overlap")

        # 3. Intent & Task Relevance check
        is_multi_relation_task = str(task_type or "").strip() == "multi_entity_relation" or (
            ("关系" in q_norm or "区别" in q_norm or "对比" in q_norm) and (source_in_q or target_in_q)
        )
        if is_multi_relation_task:
            intent_relevance = "HIGH"
            relation_relevance = "DIRECT"
            signals.append("multi_entity_relation_matched")
            return GraphRelationAdmissionResult(
                verdict="PASS",
                entity_relevance=entity_relevance,
                intent_relevance=intent_relevance,
                relation_relevance=relation_relevance,
                reason="multi_entity_relation_direct_fact",
                admission_signals=tuple(signals),
            )

        # Exact parameter questions shouldn't admit generic belongs_to relations as evidence
        is_exact_parameter = any(term in q_norm for term in _EXACT_PARAMETER_TERMS)
        if is_exact_parameter and candidate.relation_type in {"belongs_to", "alias_of"}:
            return GraphRelationAdmissionResult(
                verdict="REJECT",
                entity_relevance=entity_relevance,
                intent_relevance="LOW",
                relation_relevance="IRRELEVANT",
                reason="belongs_to_irrelevant_for_exact_parameters",
                admission_signals=("exact_parameter_mismatch",),
            )

        # Keyword alignment
        matching_terms = _RELATION_INTENT_TERMS.get(candidate.relation_type, ())
        has_intent_term = any(term in q_norm for term in matching_terms)

        if has_intent_term and entity_relevance == "HIGH":
            intent_relevance = "HIGH"
            relation_relevance = "DIRECT"
            signals.append("intent_term_and_entity_matched")
            return GraphRelationAdmissionResult(
                verdict="PASS",
                entity_relevance=entity_relevance,
                intent_relevance=intent_relevance,
                relation_relevance=relation_relevance,
                reason="intent_and_entity_direct_match",
                admission_signals=tuple(signals),
            )

        # Overview questions can admit belongs_to / requires / different_from for high entity relevance
        overview_terms = ("是什么", "介绍", "概览", "定位", "作用", "用途", "功能", "组件", "体系")
        is_overview = any(term in q_norm for term in overview_terms)
        if is_overview and entity_relevance == "HIGH" and candidate.relation_type in {"belongs_to", "requires", "different_from", "has_service", "has_module"}:
            intent_relevance = "HIGH"
            relation_relevance = "DIRECT" if candidate.relation_type in {"belongs_to", "has_service", "has_module"} else "CONTEXTUAL"
            signals.append("overview_structural_relation")
            return GraphRelationAdmissionResult(
                verdict="PASS",
                entity_relevance=entity_relevance,
                intent_relevance=intent_relevance,
                relation_relevance=relation_relevance,
                reason="overview_structural_relation_support",
                admission_signals=tuple(signals),
            )

        # 4. Ambiguous cases: optional helper admitter
        deterministic = GraphRelationAdmissionResult(
            verdict="REJECT",
            entity_relevance=entity_relevance,
            intent_relevance="LOW",
            relation_relevance="CONTEXTUAL" if entity_relevance == "HIGH" else "IRRELEVANT",
            reason="insufficient_query_intent_alignment",
            admission_signals=tuple(signals),
        )

        if helper_admitter is not None and entity_relevance == "HIGH":
            try:
                res = helper_admitter(question, candidate)
                if res is not None:
                    return res
            except Exception as exc:  # noqa: BLE001
                logger.debug("helper relation admission failed: %s", exc)

        return deterministic

    @classmethod
    def admit_batch(
        cls,
        question: str,
        candidates: list[GraphRelationCandidate],
        *,
        working_set: GraphWorkingSet | None = None,
        target_entities: list[str] | tuple[str, ...] | None = None,
        task_type: str | None = None,
    ) -> dict[str, GraphRelationAdmissionResult]:
        """Admit a list of relation candidates."""
        results: dict[str, GraphRelationAdmissionResult] = {}
        for candidate in candidates:
            res = cls.admit(
                question,
                candidate,
                working_set=working_set,
                target_entities=target_entities,
                task_type=task_type,
            )
            key = str(candidate.relation_id or candidate.relation_key)
            results[key] = res
        return results
