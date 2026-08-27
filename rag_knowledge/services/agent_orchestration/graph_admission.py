"""Graph relation candidate admission service (PRD 2026-08-26)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from rag_knowledge.services.agent_orchestration.graph_working_set import (
    GraphRelationCandidate,
    GraphWorkingSet,
)
from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.services.relation_policy import RELATION_RULES, is_answer_evidence_relation

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

        signals: list[str] = [f"policy_intent:{normalized_intent}"]

        # 2. Entity relevance comes only from the frozen semantic task (or the
        # explicit legacy target list when no SemanticTaskContext is available).
        # Raw-question substrings are not evidence authorization.
        if semantic_task is not None:
            semantic_entities = [
                getattr(semantic_task, "primary_entity", None),
                *(getattr(semantic_task, "mentioned_entities", ()) or ()),
            ]
        else:
            semantic_entities = list(target_entities or (working_set.exploration_roots if working_set else ()))

        def _entity_key(value: Any) -> str:
            return normalize_entity_name(str(value or "")).casefold()

        active_targets = {_entity_key(item) for item in semantic_entities if _entity_key(item)}
        source_in_targets = _entity_key(candidate.source_name) in active_targets
        target_in_targets = _entity_key(candidate.target_name) in active_targets

        if source_in_targets and target_in_targets:
            entity_relevance = "HIGH"
            signals.append("both_endpoints_in_semantic_task")
        elif source_in_targets or target_in_targets:
            entity_relevance = "HIGH"
            signals.append("endpoint_in_semantic_task")
        else:
            entity_relevance = "LOW"
            signals.append("no_endpoint_in_semantic_task")

        if entity_relevance == "HIGH":
            relation_relevance = "DIRECT"
            reason = (
                "multi_entity_relation_direct_fact"
                if semantic_task_type == "multi_entity_relation" and source_in_targets and target_in_targets
                else "policy_authorized_relation_fact"
            )
            return GraphRelationAdmissionResult(
                verdict="PASS",
                entity_relevance="HIGH",
                intent_relevance="HIGH",
                relation_relevance=relation_relevance,
                reason=reason,
                admission_signals=tuple(signals),
                canonical_question=canonical_question,
                answer_intent=normalized_intent,
            )

        # Identity scope is a hard boundary. Helper semantics may judge a
        # relation's meaning, but cannot repair an endpoint that is absent from
        # the frozen SemanticTaskContext.
        return GraphRelationAdmissionResult(
            verdict="REJECT",
            entity_relevance="LOW",
            intent_relevance="NONE",
            relation_relevance="IRRELEVANT",
            reason="relation_endpoints_outside_semantic_task",
            admission_signals=tuple(signals),
            canonical_question=canonical_question,
            answer_intent=normalized_intent,
        )

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
