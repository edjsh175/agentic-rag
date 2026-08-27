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
    is_overview_query,
    relation_query_terms,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphRelationAdmissionResult:
    """Admission verdict for one graph relation candidate."""

    verdict: str  # PASS | REJECT
    entity_relevance: str  # HIGH | MEDIUM | LOW | CONFLICT
    intent_relevance: str  # HIGH | MEDIUM | LOW | NONE
    relation_relevance: str  # DIRECT | CONTEXTUAL | IRRELEVANT
    reason: str
    admission_signals: tuple[str, ...] = ()
    canonical_question: str = ""
    answer_intent: str = "general_qa"

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "entity_relevance": self.entity_relevance,
            "intent_relevance": self.intent_relevance,
            "relation_relevance": self.relation_relevance,
            "reason": self.reason,
            "admission_signals": list(self.admission_signals),
            "canonical_question": self.canonical_question,
            "answer_intent": self.answer_intent,
        }


class GraphRelationAdmissionService:
    """Evaluates whether an approved graph relation candidate qualifies as query-level factual Evidence."""

    def __init__(self, graph_db: Any = None):
        self.graph_db = graph_db

    def admit_relation(
        self,
        candidate: GraphRelationCandidate,
        *,
        semantic_task: Any = None,
        question: str = "",
        working_set: GraphWorkingSet | None = None,
        target_entities: list[str] | tuple[str, ...] | None = None,
        task_type: str | None = None,
    ) -> GraphRelationAdmissionResult:
        return self.admit(
            question,
            candidate,
            semantic_task=semantic_task,
            working_set=working_set,
            target_entities=target_entities,
            task_type=task_type,
        )

    @staticmethod
    def _validate_hard_conditions(
        candidate: GraphRelationCandidate,
        working_set: GraphWorkingSet | None,
        intent: str,
    ) -> tuple[bool, str]:
        if str(candidate.review_status or "").strip().lower() != "approved":
            return False, f"unapproved_review_status:{candidate.review_status}"
        if not candidate.relation_id or not candidate.relation_id.strip():
            return False, "missing_relation_id"
        if not candidate.source_name or not candidate.target_name:
            return False, "missing_endpoints"
        if candidate.relation_type not in RELATION_RULES:
            return False, f"unregistered_relation_type:{candidate.relation_type}"
        if not is_answer_evidence_relation(candidate.relation_type, intent):
            return False, f"relation_type_not_answer_evidence:{candidate.relation_type}"
        return True, "ok"

    @classmethod
    def admit(
        cls,
        question: str,
        candidate: GraphRelationCandidate,
        *,
        semantic_task: Any = None,
        working_set: GraphWorkingSet | None = None,
        target_entities: list[str] | tuple[str, ...] | None = None,
        task_type: str | None = None,
        helper_admitter: Callable[[str, GraphRelationCandidate], GraphRelationAdmissionResult | None] | None = None,
    ) -> GraphRelationAdmissionResult:
        """Admit or reject a graph relation candidate."""
        # Clarification has already resolved the question.  Search plans and
        # raw utterances cannot reinterpret the admission intent after this point.
        if semantic_task is None:
            from rag_knowledge.services.query_surface import infer_answer_intent

            normalized_intent, _facets, _source = infer_answer_intent(question, task_type=task_type)
            canonical_question = str(question or "")
            semantic_task_type = str(task_type or "")
        else:
            normalized_intent = str(getattr(semantic_task, "answer_intent", "") or "general_qa").strip().lower()
            canonical_question = str(getattr(semantic_task, "resolved_question", "") or question or "")
            semantic_task_type = str(getattr(semantic_task, "task_type", "") or task_type or "")

        # 1. Hard validations
        valid_hard, hard_reason = cls._validate_hard_conditions(candidate, working_set, normalized_intent)
        if not valid_hard:
            return GraphRelationAdmissionResult(
                verdict="REJECT",
                entity_relevance="LOW",
                intent_relevance="NONE",
                relation_relevance="IRRELEVANT",
                reason=hard_reason,
                admission_signals=("hard_condition_failed",),
                canonical_question=canonical_question,
                answer_intent=normalized_intent,
            )

        q_norm = canonical_question.casefold()
        signals: list[str] = []
        signals.append(f"policy_intent:{normalized_intent}")

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
        is_multi_relation_task = semantic_task_type == "multi_entity_relation" and (source_in_targets or target_in_targets or source_in_q or target_in_q)
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
                canonical_question=canonical_question,
                answer_intent=normalized_intent,
            )

        # Keyword alignment
        matching_terms = relation_query_terms(candidate.relation_type)
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
                canonical_question=canonical_question,
                answer_intent=normalized_intent,
            )

        # An open entity-information task may publish a policy-authorized
        # relation as one partial fact; Coverage decides that it is incomplete.
        if normalized_intent == "general_qa" and entity_relevance == "HIGH":
            return GraphRelationAdmissionResult(
                verdict="PASS",
                entity_relevance=entity_relevance,
                intent_relevance="HIGH",
                relation_relevance="DIRECT",
                reason="general_qa_direct_relation_fact",
                admission_signals=tuple([*signals, "general_qa_direct_fact"]),
                canonical_question=canonical_question,
                answer_intent=normalized_intent,
            )

        # Overview questions can admit belongs_to / requires / different_from for high entity relevance
        is_overview = is_overview_query(q_norm)
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
                canonical_question=canonical_question,
                answer_intent=normalized_intent,
            )

        # 4. Ambiguous cases: optional helper admitter
        deterministic = GraphRelationAdmissionResult(
            verdict="REJECT",
            entity_relevance=entity_relevance,
            intent_relevance="LOW",
            relation_relevance="CONTEXTUAL" if entity_relevance == "HIGH" else "IRRELEVANT",
            reason="insufficient_query_intent_alignment",
            admission_signals=tuple(signals),
            canonical_question=canonical_question,
            answer_intent=normalized_intent,
        )

        if helper_admitter is not None and entity_relevance == "HIGH":
            try:
                res = helper_admitter(canonical_question, candidate)
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
        semantic_task: Any = None,
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
                semantic_task=semantic_task,
                working_set=working_set,
                target_entities=target_entities,
                task_type=task_type,
            )
            key = str(candidate.relation_id or candidate.relation_key)
            results[key] = res
        return results
