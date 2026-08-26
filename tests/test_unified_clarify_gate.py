import asyncio
import pytest

from rag_knowledge.config import Config
from rag_knowledge.services.rag import RagChain


def test_stream_query_ambiguous_short_circuits_with_clarify_card(isolated_storage, monkeypatch):
    isolated_storage()
    monkeypatch.setenv("AGENT_ORCHESTRATION_ENABLED", "false")
    Config._instance = None

    chain = RagChain()

    async def collect():
        events = []
        async for event in chain.stream_query("pipeline", allow_general_knowledge=False):
            events.append(event)
        return events

    events = asyncio.run(collect())
    types = [e.get("type") for e in events]
    assert "clarify" in types, f"Expected clarify event in stream, got types: {types}"
    clarify_evt = next(e for e in events if e.get("type") == "clarify")
    data = clarify_evt.get("data") or {}
    assert data.get("needs_clarification") is True
    assert len(data.get("options") or []) >= 2
    assert "token" not in types


def test_stream_query_with_clarification_selected_passes_gate(isolated_storage, monkeypatch):
    isolated_storage()
    monkeypatch.setenv("AGENT_ORCHESTRATION_ENABLED", "false")
    Config._instance = None

    chain = RagChain()
    chain._retrieve_multi = lambda *a, **k: ([], "")

    async def collect():
        events = []
        async for event in chain.stream_query(
            "pipeline",
            clarification_selected="PipelineBuilder",
            allow_general_knowledge=False,
        ):
            events.append(event)
        return events

    events = asyncio.run(collect())
    types = [e.get("type") for e in events]
    assert "clarify" not in types
    assert "done" in types


def test_aquery_ambiguous_returns_clarification_payload(isolated_storage, monkeypatch):
    isolated_storage()
    monkeypatch.setenv("AGENT_ORCHESTRATION_ENABLED", "false")
    Config._instance = None

    chain = RagChain()
    res = asyncio.run(chain.aquery("管线", allow_general_knowledge=False))
    assert "clarification" in res
    assert res["clarification"].get("needs_clarification") is True
    assert len(res["clarification"].get("options") or []) >= 2
    assert res.get("answer") == ""
