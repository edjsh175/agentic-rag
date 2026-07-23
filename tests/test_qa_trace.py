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
