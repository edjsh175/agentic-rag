"""Phase 1：DialogueUnderstanding 与 query_surface。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from rag_knowledge.services.conversation_context import UnderstandingResult
from rag_knowledge.services.dialogue_understanding import DialogueUnderstanding
from rag_knowledge.services.query_clarification import ClarificationResult, QueryClarificationService
from rag_knowledge.services.query_contextualizer import RetrievalQuery
from rag_knowledge.services.query_surface import (
    contains_term,
    is_vague_surface_question,
    question_is_underspecified,
)


def test_surface_underspecified_and_wide_terms():
    assert question_is_underspecified("pipeline")
    assert question_is_underspecified("管线")
    assert not question_is_underspecified("管线点表字段有哪些")
    assert contains_term("介绍 pipeline 能力", "pipeline")
    assert is_vague_surface_question("pipeline")
    assert is_vague_surface_question("管线工具怎么用")
    # bare 管线 in a long question should not be treated as vague surface
    assert not is_vague_surface_question("管线点表字段有哪些")


def test_understanding_no_history_keeps_original(isolated_storage):
    isolated_storage()
    cfg = MagicMock()
    contextualizer = MagicMock()
    understanding = DialogueUnderstanding(cfg, contextualizer=contextualizer)
    result = understanding.analyze("StampServer 是什么", history=None, run_clarify=False)
    assert result.mode == "retrieve"
    assert result.resolved_question == "StampServer 是什么"
    assert result.rationale == "original_no_history"
    assert result.retrieval_queries[0]["kind"] == "original"
    contextualizer.build_query_specs_with_meta.assert_not_called()


def test_understanding_with_history_protects_once(isolated_storage):
    isolated_storage()
    cfg = MagicMock()
    contextualizer = MagicMock()
    contextualizer.build_query_specs_with_meta.return_value = (
        [
            RetrievalQuery("它是什么", "original", 1.0),
            RetrievalQuery("StampServer 是什么", "standalone", 0.8),
        ],
        {
            "standalone_query": "StampServer 是什么",
            "is_context_dependent": True,
            "confidence": 0.9,
        },
    )
    understanding = DialogueUnderstanding(cfg, contextualizer=contextualizer)
    history = [
        {"role": "user", "content": "StampServer"},
        {"role": "assistant", "content": "介绍", "sources": [{"file_name": "a.md"}]},
        {"role": "user", "content": "它是什么"},
    ]
    result = understanding.analyze(
        "它是什么", history=history, run_clarify=False, entity_name="StampServer",
    )
    assert result.mode == "retrieve"
    assert result.resolved_question == "StampServer 是什么"
    assert result.is_context_dependent is True
    assert result.focus.get("confirmed_entity") == "StampServer"
    assert "实体:StampServer" in result.dialogue_focus
    contextualizer.build_query_specs_with_meta.assert_called_once()
    kwargs = contextualizer.build_query_specs_with_meta.call_args.kwargs
    assert kwargs.get("protect_entities") is False
    assert "StampServer" in (kwargs.get("focus_text") or "")
    assert kwargs.get("recent_rounds") == 2


def test_understanding_clarify_mode(isolated_storage):
    isolated_storage()
    clarifier = MagicMock(spec=QueryClarificationService)
    clarifier.analyze.return_value = ClarificationResult(
        needs_clarification=True,
        ask_question="选一个",
        trigger="pipeline",
        reason="vague_surface_term",
        options=[],
    )
    understanding = DialogueUnderstanding(
        MagicMock(), clarification_service=clarifier,
    )
    result = understanding.analyze("pipeline", run_clarify=True)
    assert result.mode == "clarify"
    assert result.clarify["needs_clarification"] is True
    assert result.rationale == "vague_surface_term"


def test_to_retrieval_queries_roundtrip():
    result = UnderstandingResult(
        mode="retrieve",
        user_utterance="q",
        resolved_question="q",
        retrieval_queries=[
            {"text": "a", "kind": "original", "weight": 1.0},
            {"text": "b", "kind": "search", "weight": 0.6},
        ],
    )
    specs = DialogueUnderstanding.to_retrieval_queries(result)
    assert [s.kind for s in specs] == ["original", "search"]
