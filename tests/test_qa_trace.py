"""Tests for QA pipeline trace store and helpers."""

from __future__ import annotations

import json

from rag_knowledge.config import Config
from rag_knowledge.services.qa_trace import (
    QaTraceBuilder,
    QaTraceStore,
    serialize_candidates,
    serialize_plan,
)


class _Plan:
    intent = "definition"
    confidence = 0.7
    queries = []
    top_k = 4
    candidate_k = 12
    enable_rerank = True
    expand_neighbors = False
    backbone_canonical = ("StampManager",)
    backbone_avoid = ()
    backbone_primary_intent = "product_intro"
    backbone_relation_summary = "summary"
    graph_queries = ()
    graph_chunk_ids = ()
    graph_fallback_reason = None

    def __init__(self):
        from rag_knowledge.services.query_contextualizer import RetrievalQuery
        self.queries = [RetrievalQuery(text="StampGIS", kind="original", weight=1.0)]


def test_serialize_plan_and_candidates():
    plan = serialize_plan(_Plan())
    assert plan["intent"] == "definition"
    assert plan["queries"][0]["text"] == "StampGIS"
    docs = [{
        "content": "hello world " * 40,
        "metadata": {"chunk_id": "c1", "source": "a.md", "section_title": "S1", "score": 0.9},
    }]
    cands = serialize_candidates(docs, max_candidates=5, preview_chars=20)
    assert len(cands) == 1
    assert cands[0]["chunk_id"] == "c1"
    assert len(cands[0]["content_preview"]) <= 20


def test_trace_store_save_list_get_delete(isolated_storage, monkeypatch):
    cfg, _db, _chroma, data_dir = isolated_storage()
    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    Config._instance = None
    cfg = Config()
    assert cfg.qa_trace.enabled is True

    builder = QaTraceBuilder(question="StampGIS 管理中心", path="qa-debug", cfg=cfg)
    assert builder.enabled
    builder.mark("plan")
    builder.set_plan(_Plan())
    builder.mark("retrieve")
    builder.set_retrieval([{
        "content": "管理中心说明",
        "metadata": {"chunk_id": "x", "source": "doc.md"},
    }])
    builder.mark("generate")
    tid = builder.finish(
        answer="当前知识库中未查询到相关内容。",
        source_documents=[],
        evidence={"cited": [], "retrieved_uncited": [], "gaps": [], "conflicts": []},
    )
    assert tid

    store = QaTraceStore(cfg)
    listed = store.list(limit=10)
    assert listed["total"] >= 1
    assert listed["items"][0]["trace_id"] == tid
    assert "StampGIS" in listed["items"][0]["question"]

    detail = store.get(tid)
    assert detail is not None
    assert detail["meta"]["trace_id"] == tid
    assert detail["plan"]["intent"] == "definition"
    assert detail["stages"]["plan"] >= 0
    day_file = data_dir / "qa_traces"
    assert any(day_file.glob(f"*/{tid}.json"))

    assert store.delete(tid) is True
    assert store.get(tid) is None
    listed2 = store.list(limit=10)
    assert all(i["trace_id"] != tid for i in listed2["items"])


def test_trace_disabled_does_not_write(isolated_storage, monkeypatch):
    isolated_storage()
    monkeypatch.setenv("QA_TRACE_ENABLED", "false")
    Config._instance = None
    cfg = Config()
    builder = QaTraceBuilder(question="hi", cfg=cfg)
    assert builder.enabled is False
    tid = builder.finish(answer="x")
    assert tid is None
    store = QaTraceStore(cfg)
    assert store.list()["total"] == 0


def test_index_jsonl_is_append_only_lines(isolated_storage, monkeypatch):
    isolated_storage()
    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    Config._instance = None
    cfg = Config()
    for i in range(3):
        b = QaTraceBuilder(question=f"q{i}", cfg=cfg)
        b.finish(answer=f"a{i}")
    index = cfg.data_dir / "qa_traces" / "index.jsonl"
    lines = [ln for ln in index.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 3
    for ln in lines:
        json.loads(ln)


def test_builder_without_cfg_does_not_write_live_traces(tmp_path, monkeypatch):
    """RagChain stubs pass cfg=None; must never fall back to live data/qa_traces."""
    from pathlib import Path

    live_root = Path("data") / "qa_traces"
    before = set()
    if live_root.exists():
        before = {p.name for p in live_root.rglob("*.json")}

    # Even if env would enable traces, missing cfg must stay disabled.
    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    builder = QaTraceBuilder(question="question", path="query/stream", cfg=None)
    assert builder.enabled is False
    assert builder.finish(answer="trimmed answer [1]") is None

    if live_root.exists():
        after = {p.name for p in live_root.rglob("*.json")}
        assert after == before


def test_stub_ragchain_stream_does_not_pollute_live_traces(monkeypatch):
    """Reproduce the original pollution path: object.__new__(RagChain) without _cfg."""
    import asyncio
    from pathlib import Path

    from rag_knowledge.services.rag import NO_KNOWLEDGE_ANSWER, RagChain

    live_root = Path("data") / "qa_traces"
    before = {p.name for p in live_root.rglob("*.json")} if live_root.exists() else set()

    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    monkeypatch.setenv("ALLOW_LIVE_STORAGE_IN_TESTS", "1")

    chain = object.__new__(RagChain)
    chain._allow_general_knowledge = False
    chain._build_retrieval_query_specs = lambda question, history: [question]
    chain._query_planner = type(
        "PlannerStub",
        (),
        {
            "plan": lambda self, question, queries, force_rerank=False: type(
                "PlanStub",
                (),
                {
                    "queries": queries,
                    "top_k": 4,
                    "candidate_k": 12,
                    "enable_rerank": False,
                    "expand_neighbors": False,
                },
            )()
        },
    )()
    chain._retrieve_multi = lambda *args, **kwargs: ([], "")
    chain._prepare_graph_plan = lambda *a, **k: (
        k.get("plan") or (a[1] if len(a) > 1 else None),
        None,
        [],
    )
    chain._build_graph_kwargs = lambda *a, **k: {}
    chain._anchor_protect_names = lambda plan: ()
    chain._record_chunk_hit_query = lambda docs: None

    async def collect():
        return [e async for e in chain.stream_query("项目部署参数是什么？", allow_general_knowledge=False)]

    events = asyncio.run(collect())
    assert any(e.get("type") == "token" and e.get("data") == NO_KNOWLEDGE_ANSWER for e in events)

    after = {p.name for p in live_root.rglob("*.json")} if live_root.exists() else set()
    assert after == before


def test_store_requires_explicit_config():
    import pytest

    with pytest.raises(ValueError, match="explicit Config"):
        QaTraceStore(None)
