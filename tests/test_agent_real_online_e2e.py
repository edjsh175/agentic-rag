from __future__ import annotations

import asyncio
import os

import pytest

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.bm25_store import BM25Store
from rag_knowledge.services.qa_trace import QaTraceStore
from rag_knowledge.services.answer_finalizer import REVIEW_BLOCKED_ANSWER, REVIEWER_ERROR_ANSWER
from rag_knowledge.services.rag import CONTROLLER_ERROR_ANSWER, NO_KNOWLEDGE_ANSWER, RagChain


@pytest.mark.integration
def test_real_agent_stream_matches_persisted_trace(monkeypatch):
    """Read the live KB, call the configured Ollama models, and reconcile SSE with QA Trace."""
    monkeypatch.setenv("ALLOW_LIVE_STORAGE_IN_TESTS", "1")
    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    monkeypatch.setenv("RAG_CONFIG", os.environ.get("RAG_CONFIG") or "config-local.ini")

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
            "llm_reasoning_start",
            "llm_reasoning_delta",
            "llm_reasoning_end",
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
    publication = next(
        event.get("data") or {}
        for event in live_lifecycle
        if event.get("type") == "publication"
    )
    assert publication.get("final_mode") in {
        "generated",
        "grounded_rewrite",
        "grounded_partial",
    }
    assert publication.get("review_verdict") == "PASS"
    assert not any(
        event.get("type") == "review_status"
        and (event.get("data") or {}).get("verdict") == "ERROR"
        for event in live_lifecycle
    )

    final_answer = next(event.get("data") for event in events if event.get("type") == "final_answer")
    assert isinstance(final_answer, str) and final_answer.strip()
    assert "查询执行异常" not in final_answer
    assert final_answer not in {
        NO_KNOWLEDGE_ANSWER,
        CONTROLLER_ERROR_ANSWER,
        REVIEW_BLOCKED_ANSWER,
        REVIEWER_ERROR_ANSWER,
    }


@pytest.mark.integration
def test_real_http_agent_sse_matches_trace(monkeypatch):
    """Exercise the real /api/query/stream HTTP SSE boundary with live Agent models and Trace."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    import rag_knowledge.api.routes as routes

    monkeypatch.setenv("ALLOW_LIVE_STORAGE_IN_TESTS", "1")
    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    monkeypatch.setenv("RAG_CONFIG", os.environ.get("RAG_CONFIG") or "config-local.ini")

    Config._instance = None
    RelationalDB._instance = None
    VectorStore._instance = None
    BM25Store._instance = None

    store = VectorStore()
    if store.get_chroma()._collection.count() == 0:
        pytest.skip("live Chroma database is empty")

    routes._rag = RagChain()
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    async def request_sse():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver", timeout=300.0) as client:
            return await client.post(
                "/api/query/stream",
                json={
                    "question": "StampServer 的主要用途是什么？请只根据知识库回答。",
                    "mode": "agent",
                    "allow_general_knowledge": False,
                    "pipeline_events": False,
                },
            )

    response = asyncio.run(request_sse())
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("text/event-stream")

    events = []
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        raw = line[6:].strip()
        if not raw or raw == "[DONE]":
            continue
        import json
        events.append(json.loads(raw))

    event_types = [event.get("type") for event in events]
    assert "llm_reasoning_start" in event_types
    assert "llm_reasoning_delta" in event_types
    assert "tool_start" in event_types
    assert "tool_result" in event_types
    assert "final_answer" in event_types
    assert "trace" in event_types
    assert "status" not in event_types
    assert "pipeline" not in event_types

    main_reasoning = [
        event for event in events
        if event.get("type") == "llm_reasoning_start"
        and (event.get("data") or {}).get("role") == "main"
    ]
    assert main_reasoning

    started_tools = {
        ((event.get("data") or {}).get("step"), (event.get("data") or {}).get("name"))
        for event in events if event.get("type") == "tool_start"
    }
    for event in events:
        if event.get("type") != "tool_result":
            continue
        data = event.get("data") or {}
        assert (data.get("step"), data.get("name")) in started_tools

    trace_event = next(event for event in reversed(events) if event.get("type") == "trace")
    trace_id = (trace_event.get("data") or {}).get("trace_id")
    assert trace_id
    trace = QaTraceStore(Config()).get(trace_id)
    assert trace is not None
    assert trace.get("execution_events")
    assert (trace.get("answer") or {}).get("text")


@pytest.mark.integration
def test_real_pipeline_clarification_locks_pipelinewebgl(monkeypatch):
    """Run the original `pipeline` clarification incident through live KB + Ollama."""
    monkeypatch.setenv("ALLOW_LIVE_STORAGE_IN_TESTS", "1")
    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    monkeypatch.setenv("RAG_CONFIG", os.environ.get("RAG_CONFIG") or "config-local.ini")

    Config._instance = None
    RelationalDB._instance = None
    VectorStore._instance = None
    BM25Store._instance = None

    store = VectorStore()
    from rag_knowledge.services.entity_candidate_resolver import get_entity_candidate_resolver
    resolver = get_entity_candidate_resolver()
    resolution = resolver.resolve_identity("pipeline")
    snapshot = resolver.create_clarification_snapshot(resolution)

    chain = RagChain()

    async def collect() -> list[dict]:
        return [
            event
            async for event in chain.stream_query(
                "pipeline",
                history=[],
                clarification_selected="PipelineWebGL",
                clarification_snapshot_id=snapshot.clarification_id,
                doc_category="StampTools",
                allow_general_knowledge=False,
                agent_orchestration_enabled=True,
                pipeline_events=False,
            )
        ]

    events = asyncio.run(collect())
    trace_event = next(event for event in reversed(events) if event.get("type") == "trace")
    trace_id = trace_event.get("data", {}).get("trace_id")
    assert trace_id

    trace = QaTraceStore(Config()).get(trace_id)
    assert trace is not None
    scope = trace.get("scope") or {}
    assert scope.get("primary_entity") == "PipelineWebGL"
    assert scope.get("is_identity_locked") is True
    assert scope.get("primary_entity") != "PipelineBuilder"

    publication = next(
        (event.get("data") or {})
        for event in events
        if event.get("type") == "publication"
    )
    assert publication.get("final_mode") in {
        "generated",
        "grounded_rewrite",
        "grounded_partial",
    }
    assert publication.get("review_verdict") == "PASS"

    final_answer = next(event.get("data") for event in events if event.get("type") == "final_answer")
    assert isinstance(final_answer, str) and final_answer.strip()
    assert final_answer not in {
        NO_KNOWLEDGE_ANSWER,
        CONTROLLER_ERROR_ANSWER,
        REVIEW_BLOCKED_ANSWER,
        REVIEWER_ERROR_ANSWER,
    }

    source_documents = (trace.get("answer") or {}).get("source_documents") or []
    source_entities = {
        (item.get("metadata") or {}).get("document_entity")
        for item in source_documents
        if isinstance(item, dict)
    }
    assert "PipelineWebGL" in source_entities
    assert "PipelineBuilder" not in source_entities
