from __future__ import annotations

import asyncio
import os

import pytest

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.bm25_store import BM25Store
from rag_knowledge.services.qa_trace import QaTraceStore
from rag_knowledge.services.rag import RagChain


@pytest.mark.integration
def test_real_agent_stream_matches_persisted_trace(monkeypatch):
    """Read the live KB, call the configured Ollama models, and reconcile SSE with QA Trace."""
    monkeypatch.setenv("ALLOW_LIVE_STORAGE_IN_TESTS", "1")
    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    monkeypatch.setenv("RAG_CONFIG", "config-local.ini")

    Config._instance = None
    RelationalDB._instance = None
    VectorStore._instance = None
    BM25Store._instance = None

    store = VectorStore()
    if store.get_chroma()._collection.count() == 0:
        pytest.skip("live Chroma database is empty")

    chain = RagChain()

    async def collect() -> list[dict]:
        return [
            event
            async for event in chain.stream_query(
                "StampServer 的主要用途是什么？请只根据知识库回答。",
                history=[],
                allow_general_knowledge=False,
                agent_orchestration_enabled=True,
                pipeline_events=False,
            )
        ]

    events = asyncio.run(collect())
    event_types = [event.get("type") for event in events]
    assert "understanding" in event_types
    assert "decision" in event_types
    assert "final_answer" in event_types
    assert "sources" in event_types
    assert "trace" in event_types
    assert "status" not in event_types
    assert "pipeline" not in event_types

    trace_event = next(event for event in reversed(events) if event.get("type") == "trace")
    trace_id = trace_event.get("data", {}).get("trace_id")
    assert trace_id

    trace = QaTraceStore(Config()).get(trace_id)
    assert trace is not None
    trace_events = trace.get("execution_events") or []
    assert trace_events

    live_lifecycle = [
        event
        for event in events
        if event.get("type")
        in {
            "understanding",
            "decision",
            "guard",
            "tool_start",
            "tool_result",
            "evidence_update",
            "evidence_gap",
            "finalization_requested",
            "finalization_check",
            "evidence_snapshot_created",
            "candidate_status",
            "grounding_review_started",
            "review_status",
            "rewrite_status",
            "publication",
            "error",
        }
    ]
    trace_lifecycle = [
        {"type": event.get("type"), "data": event.get("data")}
        for event in trace_events
        if event.get("type") in {item.get("type") for item in live_lifecycle}
    ]
    projected_live = [
        {"type": event.get("type"), "data": event.get("data")}
        for event in live_lifecycle
    ]
    assert trace_lifecycle == projected_live
    assert not any(
        event.get("type") == "publication"
        and (event.get("data") or {}).get("final_mode") == "reviewer_error"
        for event in live_lifecycle
    )
    assert not any(
        event.get("type") == "review_status"
        and (event.get("data") or {}).get("verdict") == "ERROR"
        for event in live_lifecycle
    )

    final_answer = next(event.get("data") for event in events if event.get("type") == "final_answer")
    assert isinstance(final_answer, str) and final_answer.strip()
    assert "查询执行异常" not in final_answer
