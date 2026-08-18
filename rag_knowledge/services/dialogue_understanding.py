"""DialogueUnderstanding — single understanding exit for clarify / retrieve."""

from __future__ import annotations

import logging
from typing import Any

from rag_knowledge.config import Config
from rag_knowledge.services.conversation_context import (
    UnderstandingResult,
    build_dialogue_focus,
    session_from_history,
)
from rag_knowledge.services.query_contextualizer import QueryContextualizer, RetrievalQuery

logger = logging.getLogger(__name__)


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
            return UnderstandingResult(
                mode="retrieve",
                user_utterance="",
                resolved_question="",
                retrieval_queries=[],
                filters=filters,
                rationale="empty_question",
            )

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
                return UnderstandingResult(
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

        return self._analyze_retrieve(
            q,
            history=history,
            filters=filters,
            rolling_summary=rolling_summary,
        )

    def _analyze_retrieve(
        self,
        question: str,
        *,
        history: list[dict] | None,
        filters: dict[str, Any],
        rolling_summary: str | None = None,
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
