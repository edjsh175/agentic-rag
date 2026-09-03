from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import rag_knowledge.api.routes as routes


class _EventStreamChain:
    def __init__(self, events: list[dict]) -> None:
        self._events = events

    async def stream_query(self, *_args, **_kwargs):
        for event in self._events:
            yield event


def _post_stream(monkeypatch, events: list[dict]) -> list[dict]:
    monkeypatch.setattr(routes, "_rag", _EventStreamChain(events))
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    async def request():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(
                "/api/query/stream",
                json={"question": "验收问题", "mode": "agent", "pipeline_events": False},
            )

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line[6:] != "[DONE]"
    ]


def test_http_sse_preserves_second_retrieval_lifecycle(monkeypatch):
    events = [
        {"type": "llm_reasoning_start", "data": {"call_id": "agent_controller_1", "role": "main", "stage": "agent_controller"}},
        {"type": "tool_start", "data": {"step": 1, "name": "retrieve_kb"}},
        {"type": "tool_result", "data": {"step": 1, "name": "retrieve_kb", "ok": True}},
        {"type": "evidence_update", "data": {"evidence_version": 2}},
        {"type": "llm_reasoning_delta", "data": {"call_id": "agent_controller_2", "role": "main", "stage": "agent_controller", "delta": "补检后继续核对。"}},
        {"type": "tool_start", "data": {"step": 2, "name": "retrieve_kb"}},
        {"type": "tool_result", "data": {"step": 2, "name": "retrieve_kb", "ok": True}},
        {"type": "final_answer", "data": "最终答案"},
        {"type": "trace", "data": {"trace_id": "trace-b"}},
    ]

    received = _post_stream(monkeypatch, events)

    assert received == events
    assert [event["data"]["step"] for event in received if event["type"] == "tool_start"] == [1, 2]


def test_http_sse_preserves_no_native_reasoning_fallback(monkeypatch):
    events = [
        {"type": "llm_reasoning_start", "data": {"call_id": "answer_generator_v1", "role": "main", "stage": "answer_generation"}},
        {"type": "llm_reasoning_end", "data": {"call_id": "answer_generator_v1", "role": "main", "stage": "answer_generation", "reasoning_available": False}},
        {"type": "public_explanation", "data": {"call_id": "answer_generator_v1", "role": "main", "stage": "answer_generation", "source": "system_fallback", "fallback_used": True, "text": "将基于冻结证据组织回答。"}},
        {"type": "final_answer", "data": "最终答案"},
        {"type": "trace", "data": {"trace_id": "trace-d"}},
    ]

    received = _post_stream(monkeypatch, events)

    assert received == events
    assert received[2]["data"]["fallback_used"] is True
    assert received[2]["data"]["source"] == "system_fallback"


def test_http_sse_preserves_reviewer_error_fail_closed(monkeypatch):
    events = [
        {"type": "candidate_status", "data": {"version": 1, "status": "reviewing", "message": "候选回答等待审核。"}},
        {"type": "helper_grounding_review_started", "data": {"candidate_version": 1, "review_count": 1, "message": "正在核对 Candidate V1。"}},
        {"type": "review_status", "data": {"candidate_version": 1, "review_count": 1, "verdict": "ERROR", "coverage": "NONE", "message": "证据审核失败", "error": "invalid_review_protocol"}},
        {"type": "error", "data": {"stage": "review", "code": "invalid_review_protocol", "message": "证据审核失败"}},
        {"type": "final_answer", "data": "当前回答未通过证据审核，暂不输出。"},
        {"type": "trace", "data": {"trace_id": "trace-e"}},
    ]

    received = _post_stream(monkeypatch, events)

    assert received == events
    assert received[2]["data"]["verdict"] == "ERROR"
    assert all(event["type"] != "publication" for event in received)
