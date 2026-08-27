"""DialogueUnderstanding — single understanding exit for clarify / retrieve."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, replace
from typing import Any

from rag_knowledge.config import Config
from rag_knowledge.services.conversation_context import (
    UnderstandingResult,
    build_dialogue_focus,
    session_from_history,
)
from rag_knowledge.services.query_contextualizer import QueryContextualizer, RetrievalQuery

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SemanticTaskContext:
    """Stage-1 semantic task structure; it describes intent, not evidence permission."""

    resolved_question: str
    primary_entity: str | None
    mentioned_entities: tuple[str, ...]
    task_type: str
    confidence: float
    answer_intent: str = "general_qa"
    requested_facets: tuple[str, ...] = ()
    intent_source: str = "fallback"
    entity_binding_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SemanticTaskContext":
        payload = data if isinstance(data, dict) else {}
        return cls(
            resolved_question=str(payload.get("resolved_question") or ""),
            primary_entity=str(payload.get("primary_entity") or "").strip() or None,
            mentioned_entities=tuple(
                str(item).strip()
                for item in (payload.get("mentioned_entities") or ())
                if str(item).strip()
            ),
            task_type=str(payload.get("task_type") or "unbound"),
            confidence=float(payload.get("confidence") or 0.0),
            answer_intent=str(payload.get("answer_intent") or "general_qa"),
            requested_facets=tuple(
                str(item).strip()
                for item in (payload.get("requested_facets") or ())
                if str(item).strip()
            ),
            intent_source=str(payload.get("intent_source") or "fallback"),
            entity_binding_required=bool(
                payload.get(
                    "entity_binding_required",
                    str(payload.get("task_type") or "unbound") != "unbound",
                )
            ),
        )


def build_semantic_task_context(
    question: str,
    result: UnderstandingResult,
) -> SemanticTaskContext:
    """Derive entity task structure from Stage-1 output without business-semantic regexes."""
    from rag_knowledge.services.backbone_guard import (
        load_backbone_constraints,
        resolve_canonical,
        soft_match_backbone_entities,
    )
    from rag_knowledge.services.query_entity_guard import (
        detect_correction_or_negation,
        extract_explicit_entities,
    )

    constraints = load_backbone_constraints()
    _is_correction, negated_entities = detect_correction_or_negation(question or "")
    negated_keys = {
        (resolve_canonical(str(item or "").strip(), constraints) or str(item or "").strip()).casefold()
        for item in negated_entities
        if str(item or "").strip()
    }

    def canonical(value: Any) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        return resolve_canonical(raw, constraints) or raw

    def entities_in(text: str) -> list[str]:
        candidates = list(soft_match_backbone_entities(text or "", constraints, max_hits=8))
        candidates.extend(extract_explicit_entities(text or "", exclude_negated=True))
        values: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            value = canonical(item)
            if not value:
                continue
            key = value.casefold()
            if text == question and key in negated_keys:
                continue
            if key in seen:
                continue
            seen.add(key)
            values.append(value)

        # Entity extraction helpers optimize match specificity, not discourse order.
        # Stage 1 must preserve the user's mention order so primary_entity is stable.
        aliases = constraints.get("canonical_by_alias") or {}
        text_cf = (text or "").casefold()

        def explicit_position(form: str) -> int | None:
            key = str(form or "").casefold()
            if not key:
                return None
            start = text_cf.find(key)
            while start >= 0:
                left = text_cf[start - 1] if start else ""
                end = start + len(key)
                right = text_cf[end] if end < len(text_cf) else ""
                # Prevent a generic ASCII alias such as "Pipeline" from stealing
                # position 0 inside the longer explicit entity "PipelineWebGL".
                left_word = left.isascii() and left.isalnum()
                right_word = right.isascii() and right.isalnum()
                if not left_word and not right_word:
                    return start
                start = text_cf.find(key, start + 1)
            return None

        def first_position(value: str) -> tuple[int, int]:
            forms = [value]
            forms.extend(
                str(alias)
                for alias, target in aliases.items()
                if str(target or "").casefold() == value.casefold()
            )
            positions = [pos for pos in (explicit_position(form) for form in forms) if pos is not None]
            return (min(positions) if positions else 10**9, -len(value))

        return sorted(values, key=first_position)

    mentioned = entities_in(question)
    resolved = (result.resolved_question or question or "").strip()
    resolved_entities = entities_in(resolved)
    filter_entity = canonical((result.filters or {}).get("entity_name"))
    focus_entity = canonical((result.focus or {}).get("confirmed_entity"))
    primary = filter_entity or focus_entity or (mentioned[0] if mentioned else None)
    if primary is None and resolved_entities:
        primary = resolved_entities[0]

    structural_entities: list[str] = []
    seen_structural: set[str] = set()
    for item in [*mentioned, *resolved_entities]:
        key = item.casefold()
        if key in seen_structural:
            continue
        seen_structural.add(key)
        structural_entities.append(item)

    if len(structural_entities) >= 2:
        task_type = "multi_entity_relation"
    elif primary:
        task_type = "single_entity"
    else:
        task_type = "unbound"

    from rag_knowledge.services.query_surface import infer_answer_intent, question_is_underspecified

    answer_intent, requested_facets, intent_source = infer_answer_intent(
        question,
        task_type=task_type,
    )

    return SemanticTaskContext(
        resolved_question=resolved,
        primary_entity=primary,
        mentioned_entities=tuple(mentioned),
        task_type=task_type,
        confidence=float(result.confidence),
        answer_intent=answer_intent,
        requested_facets=requested_facets,
        intent_source=intent_source,
        entity_binding_required=(task_type != "unbound" or question_is_underspecified(question)),
    )


def collapse_clarification_selection(
    question: str,
    semantic_task: SemanticTaskContext,
    selected_entity: str,
) -> SemanticTaskContext:
    """Replace an ambiguous Stage-1 state with the user's confirmed entity."""
    selected = (selected_entity or "").strip()
    if not selected:
        return semantic_task

    from rag_knowledge.services.query_surface import infer_answer_intent, question_is_underspecified

    resolved_question = semantic_task.resolved_question
    clarification_only = question_is_underspecified(question)
    if clarification_only:
        resolved_question = f"{selected} 的相关信息"
        answer_intent, requested_facets, intent_source = (
            "general_qa", (), "clarification_default"
        )
    else:
        answer_intent, requested_facets, intent_source = infer_answer_intent(question)
    return replace(
        semantic_task,
        resolved_question=resolved_question,
        primary_entity=selected,
        mentioned_entities=(selected,),
        task_type="single_entity",
        answer_intent=answer_intent,
        requested_facets=requested_facets,
        intent_source=intent_source,
    )


class DialogueUnderstanding:
    """统一对话理解入口：澄清与检索上下文化共享同一出口契约。"""

    def __init__(
        self,
        cfg: Config | None = None,
        *,
        contextualizer: QueryContextualizer | None = None,
        clarification_service: Any | None = None,
    ):
        self._cfg = cfg or Config()
        self._contextualizer = contextualizer or QueryContextualizer(self._cfg)
        self._clarification_service = clarification_service

    def _clarifier(self) -> Any:
        if self._clarification_service is not None:
            return self._clarification_service
        from rag_knowledge.services.query_clarification import QueryClarificationService

        return QueryClarificationService()

    def analyze(
        self,
        question: str,
        *,
        history: list[dict] | None = None,
        entity_name: str | None = None,
        doc_category: str | None = None,
        kb_name: str | None = None,
        rolling_summary: str | None = None,
        run_clarify: bool = False,
        on_reasoning_event=None,
    ) -> UnderstandingResult:
        """
        产出 UnderstandingResult。

        - run_clarify=True：用于 /query/clarify；可能返回 mode=clarify
        - run_clarify=False：用于检索主路径；只产出 mode=retrieve + queries
        """
        q = (question or "").strip()
        filters: dict[str, Any] = {}
        if entity_name and str(entity_name).strip():
            filters["entity_name"] = str(entity_name).strip()
        if doc_category and str(doc_category).strip():
            filters["doc_category"] = str(doc_category).strip()
        if kb_name and str(kb_name).strip():
            filters["kb_name"] = str(kb_name).strip()

        if not q:
            result = UnderstandingResult(
                mode="retrieve",
                user_utterance="",
                resolved_question="",
                retrieval_queries=[],
                filters=filters,
                rationale="empty_question",
            )
            return self._finalize_semantic_task(q, result)

        if run_clarify:
            clarified = self._clarifier().analyze(
                q,
                doc_category=filters.get("doc_category"),
                kb_name=filters.get("kb_name"),
                entity_name=filters.get("entity_name"),
            )
            if clarified.needs_clarification:
                session = session_from_history(
                    history,
                    entity_name=filters.get("entity_name"),
                    doc_category=filters.get("doc_category"),
                    rolling_summary=rolling_summary,
                )
                focus = build_dialogue_focus(q, session)
                result = UnderstandingResult(
                    mode="clarify",
                    user_utterance=q,
                    resolved_question=q,
                    retrieval_queries=[],
                    filters=filters,
                    dialogue_focus=focus.to_text(),
                    focus=focus.to_dict(),
                    clarify=clarified.to_dict(),
                    rationale=clarified.reason or "needs_clarification",
                    confidence=1.0,
                )
                return self._finalize_semantic_task(q, result)

        result = self._analyze_retrieve(
            q,
            history=history,
            filters=filters,
            rolling_summary=rolling_summary,
            on_reasoning_event=on_reasoning_event,
        )
        return self._finalize_semantic_task(q, result)

    @staticmethod
    def _finalize_semantic_task(question: str, result: UnderstandingResult) -> UnderstandingResult:
        return replace(
            result,
            semantic_task_context=build_semantic_task_context(question, result).to_dict(),
        )

    def _analyze_retrieve(
        self,
        question: str,
        *,
        history: list[dict] | None,
        filters: dict[str, Any],
        rolling_summary: str | None = None,
        on_reasoning_event=None,
    ) -> UnderstandingResult:
        last_user = ""
        if history:
            for item in reversed(history):
                if isinstance(item, dict) and item.get("role") == "user":
                    last_user = str(item.get("content") or "")
                    break

        session = session_from_history(
            history,
            entity_name=filters.get("entity_name"),
            doc_category=filters.get("doc_category"),
            rolling_summary=rolling_summary,
        )
        # 先用当前问句构造焦点，改写后再用 resolved 更新 open_question
        focus = build_dialogue_focus(question, session)
        topic_shifted = focus.notes == "topic_shift"

        # 漂移且请求锚定实体与当前显式实体冲突时，去掉 filter 粘滞。
        if topic_shifted and filters.get("entity_name"):
            from rag_knowledge.services.query_entity_guard import extract_explicit_entities

            pinned = str(filters["entity_name"]).strip().casefold()
            q_ents = extract_explicit_entities(question)
            conflicts = True
            for e in q_ents:
                e_cf = e.casefold()
                if pinned == e_cf or pinned in e_cf or e_cf in pinned:
                    conflicts = False
                    break
            if q_ents and conflicts:
                filters = dict(filters)
                filters.pop("entity_name", None)
                session = session_from_history(
                    history,
                    entity_name=None,
                    doc_category=filters.get("doc_category"),
                    rolling_summary=None if topic_shifted else rolling_summary,
                )
                focus = build_dialogue_focus(question, session)
                topic_shifted = focus.notes == "topic_shift"

        if history:
            from rag_knowledge.services.query_entity_guard import (
                detect_correction_or_negation,
                extract_explicit_entities,
            )

            is_corr, _neg_ents = detect_correction_or_negation(question)
            remaining_entities = extract_explicit_entities(question, exclude_negated=True)
            if is_corr and not remaining_entities:
                return UnderstandingResult(
                    mode="direct_chat",
                    user_utterance=question,
                    resolved_question=question,
                    retrieval_queries=[],
                    filters=filters,
                    dialogue_focus=focus.to_text(),
                    focus=focus.to_dict(),
                    is_context_dependent=True,
                    confidence=1.0,
                    rationale="dialogue_correction_or_meta",
                )

            raw_specs, meta = self._contextualizer.build_query_specs_with_meta(
                question,
                history,
                protect_entities=False,
                focus_text=focus.to_text(),
                rolling_summary="" if topic_shifted else (session.rolling_summary or ""),
                recent_rounds=0 if topic_shifted else 2,
                drop_history_anchors=topic_shifted,
                on_reasoning_event=on_reasoning_event,
            )
            specs = self._protect_specs(question, raw_specs, last_user)
            resolved = question
            for spec in specs:
                if spec.kind == "standalone" and spec.text:
                    resolved = spec.text
                    break
            focus = build_dialogue_focus(
                question, session, resolved_question=resolved,
            )
            rationale = "contextualize_topic_shift" if topic_shifted else "contextualize"
            return UnderstandingResult(
                mode="retrieve",
                user_utterance=question,
                resolved_question=resolved,
                retrieval_queries=[self._spec_to_dict(s) for s in specs],
                filters=filters,
                dialogue_focus=focus.to_text(),
                focus=focus.to_dict(),
                is_context_dependent=False if topic_shifted else bool(
                    meta.get("is_context_dependent", False)
                ),
                confidence=float(meta.get("confidence", 0.5)),
                rationale=rationale,
            )

        # 无 history：原问题即检索 query
        specs = [RetrievalQuery(question, "original", 1.0)]
        specs = self._protect_specs(question, specs, last_user="")
        focus = build_dialogue_focus(question, session, resolved_question=question)
        return UnderstandingResult(
            mode="retrieve",
            user_utterance=question,
            resolved_question=question,
            retrieval_queries=[self._spec_to_dict(s) for s in specs],
            filters=filters,
            dialogue_focus=focus.to_text() if (focus.confirmed_entity or focus.topic) else "",
            focus=focus.to_dict() if (focus.confirmed_entity or focus.topic) else {},
            is_context_dependent=False,
            confidence=1.0,
            rationale="original_no_history",
        )

    @staticmethod
    def _spec_to_dict(spec: RetrievalQuery) -> dict[str, Any]:
        return {"text": spec.text, "kind": spec.kind, "weight": float(spec.weight)}

    @staticmethod
    def _protect_specs(
        question: str,
        specs: list[RetrievalQuery],
        last_user: str,
    ) -> list[RetrievalQuery]:
        from rag_knowledge.services.query_entity_guard import protect_rewritten_query

        seen: set[str] = set()
        out: list[RetrievalQuery] = []
        for spec in specs:
            text = (spec.text or "").strip()
            if not text or len(text) < 2:
                continue
            protected = protect_rewritten_query(question, text, last_user or None)
            key = protected.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(RetrievalQuery(protected, spec.kind, spec.weight))
        return out[:6]

    @staticmethod
    def to_retrieval_queries(result: UnderstandingResult) -> list[RetrievalQuery]:
        queries: list[RetrievalQuery] = []
        for item in result.retrieval_queries or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            queries.append(
                RetrievalQuery(
                    text,
                    str(item.get("kind") or "original"),
                    float(item.get("weight") or 1.0),
                )
            )
        return queries
