"""Tests for QA pipeline trace store and helpers."""

from __future__ import annotations

import json

from rag_knowledge.config import Config
from rag_knowledge.services.evidence_scope import BindingStrength, EvidenceScope
from rag_knowledge.services import retrieval_diagnostics
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
        "metadata": {
            "chunk_id": "c1",
            "source": "a.md",
            "section_title": "S1",
            "score": 0.9,
            "document_entity": "PipelineWebGL",
            "scope_id": "scope-1",
            "scope_admitted": True,
            "scope_admission_reason": "admissible_entity",
            "provenance_source_type": "direct_entity_chunk",
            "provenance_path": {"root_entity": "PipelineWebGL"},
        },
    }]
    cands = serialize_candidates(docs, max_candidates=5, preview_chars=20)
    assert len(cands) == 1
    assert cands[0]["chunk_id"] == "c1"
    assert cands[0]["scope_admission_reason"] == "admissible_entity"
    assert cands[0]["provenance_source_type"] == "direct_entity_chunk"
    assert cands[0]["provenance_path"]["root_entity"] == "PipelineWebGL"
    assert len(cands[0]["content_preview"]) <= 20


def test_trace_persists_retrieval_diagnostics(isolated_storage, monkeypatch):
    isolated_storage()
    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    Config._instance = None
    cfg = Config()

    scope = EvidenceScope(
        scope_id="scope-diag",
        root_entities=("PipelineWebGL",),
        binding_strength=BindingStrength.EXPLICIT,
        admissible_entities=frozenset({"PipelineWebGL"}),
    )
    builder = QaTraceBuilder(question="PipelineWebGL 怎么配置", cfg=cfg)
    builder.set_scope(scope)
    doc = {
        "content": "配置说明",
        "metadata": {
            "chunk_id": "diag-1",
            "document_entity": "PipelineWebGL",
            "scope_id": "scope-diag",
            "scope_admitted": True,
            "scope_admission_reason": "admissible_entity",
            "provenance_source_type": "direct_entity_chunk",
        },
    }
    retrieval_diagnostics.record_request(
        channel="vector",
        method="similarity",
        query="PipelineWebGL 怎么配置",
        requested_k=8,
        docs=[doc],
        structural_filter={"document_entity": {"$in": ["PipelineWebGL"]}},
    )
    retrieval_diagnostics.record_stage("pre_rerank", [doc])
    retrieval_diagnostics.record_guard({
        "allow_knowledge_answer": True,
        "reason": "ok",
        "provenance_reason": "scope_provenance_valid",
    })
    builder.set_retrieval([doc], retrieval_trace={"intent": "config"})
    tid = builder.finish(answer="配置说明 [1]", source_documents=[doc])

    detail = QaTraceStore(cfg).get(tid)
    diagnostics = detail["retrieval"]["retrieval_trace"]["diagnostics"]
    assert diagnostics["scope_id"] == "scope-diag"
    assert diagnostics["retriever_requests"][0]["channel"] == "vector"
    assert diagnostics["stages"]["pre_rerank"]["count"] == 1
    assert diagnostics["stages"]["pre_rerank"]["scope_admission_reasons"] == {"admissible_entity": 1}
    assert diagnostics["final_guard"]["allow_knowledge_answer"] is True
    assert retrieval_diagnostics.snapshot() == {}


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
    builder.append_grounding_lifecycle({
        "type": "candidate_status",
        "data": {"version": 1, "status": "generated"},
    })
    builder.append_grounding_lifecycle({
        "type": "helper_grounding_review_started",
        "data": {"review_count": 1, "candidate_version": 1},
    })
    builder.append_grounding_lifecycle({
        "type": "review_status",
        "data": {"review_count": 1, "verdict": "PASS", "coverage": "PARTIAL"},
    })
    builder.append_grounding_lifecycle({
        "type": "publication",
        "data": {"final_mode": "grounded_partial", "published_candidate_attempt": 1},
    })
    builder.set_grounding({
        "policy": "strict_kb",
        "verdict": "pass",
        "candidate_attempts": 1,
        "review_attempts": 1,
        "final_mode": "grounded_partial",
        "fallback_used": False,
    })
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
    assert detail["grounding"]["policy"] == "strict_kb"
    assert detail["grounding"]["candidate_attempts"] == 1
    assert detail["grounding"]["final_mode"] == "grounded_partial"
    assert [event["sequence"] for event in detail["grounding"]["lifecycle_events"]] == [1, 2, 3, 4]
    assert detail["grounding"]["lifecycle_events"][-1]["type"] == "publication"
    assert [event["event"] for event in detail["grounding"]["lifecycle_events"]] == [
        "answer_candidate_generated",
        "helper_grounding_review_started",
        "helper_grounding_review_completed",
        "answer_published",
    ]
    day_file = data_dir / "qa_traces"
    assert any(day_file.glob(f"*/{tid}.json"))

    assert store.delete(tid) is True
    assert store.get(tid) is None
    listed2 = store.list(limit=10)
    assert all(i["trace_id"] != tid for i in listed2["items"])


def test_rewrite_failure_has_distinct_lifecycle_event(isolated_storage, monkeypatch):
    isolated_storage()
    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    Config._instance = None
    cfg = Config()
    builder = QaTraceBuilder(question="测试重写失败", cfg=cfg)
    builder.append_grounding_lifecycle({
        "type": "rewrite_status",
        "data": {"status": "failed", "error": "rewrite_empty_candidate"},
    })

    tid = builder.finish(answer="已阻断")
    detail = QaTraceStore(cfg).get(tid)

    assert detail["grounding"]["lifecycle_events"][0]["event"] == "answer_rewrite_failed"


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
    assert any(
        e.get("type") == "clarify"
        or (e.get("type") == "token" and e.get("data") == NO_KNOWLEDGE_ANSWER)
        for e in events
    )

    after = {p.name for p in live_root.rglob("*.json")} if live_root.exists() else set()
    assert after == before


def test_rag_new_trace_preserves_requested_allow_general_knowledge(isolated_storage, monkeypatch):
    cfg, _db, _chroma, _data_dir = isolated_storage()
    monkeypatch.setattr(cfg.qa_trace, "enabled", True)

    from rag_knowledge.services.rag import RagChain

    chain = object.__new__(RagChain)
    chain._cfg = cfg
    chain._llm_model = "test-model"
    builder = chain._new_qa_trace(
        "question",
        allow_general_knowledge=True,
        path="query",
    )
    tid = builder.finish(answer="answer")

    detail = QaTraceStore(cfg).get(tid)
    assert detail["request"]["allow_general_knowledge"] is True
    assert detail["runtime"]["requested_allow_general_knowledge"] is True
    assert detail["runtime"]["grounding_reviewer_enabled"] is True
    assert detail["runtime"]["grounding_reviewer_model"] == cfg.grounding_reviewer_model


def test_store_requires_explicit_config():
    import pytest

    with pytest.raises(ValueError, match="explicit Config"):
        QaTraceStore(None)


def test_retain_forever_keeps_old_traces(isolated_storage, monkeypatch):
    isolated_storage()
    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    monkeypatch.setenv("QA_TRACE_RETAIN_DAYS", "0")
    monkeypatch.setenv("QA_TRACE_MAX_TRACES", "0")
    Config._instance = None
    cfg = Config()
    assert cfg.qa_trace.retain_days == 0
    assert cfg.qa_trace.max_traces == 0

    store = QaTraceStore(cfg)
    old_id = "oldtrace000000000000000000000001"
    day_dir = cfg.data_dir / "qa_traces" / "20200101"
    day_dir.mkdir(parents=True)
    payload = {
        "meta": {
            "trace_id": old_id,
            "created_at": "2020-01-01T12:00:00+08:00",
            "path": "query",
            "elapsed_ms": 1,
            "error": None,
        },
        "request": {"question": "ancient"},
        "runtime": {},
        "stages": {},
        "plan": {},
        "retrieval": {"candidate_count": 0},
        "evidence": {"cited": []},
        "answer": {"text": "old"},
    }
    (day_dir / f"{old_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    # orphan on disk only — list() should heal and keep forever
    listed = store.list(limit=20)
    assert listed["total"] == 1
    assert listed["items"][0]["trace_id"] == old_id
    assert (day_dir / f"{old_id}.json").exists()

    b = QaTraceBuilder(question="new", cfg=cfg)
    b.finish(answer="ok")
    listed2 = store.list(limit=20)
    assert listed2["total"] == 2
    assert (day_dir / f"{old_id}.json").exists()


def test_list_date_filter_and_heal_orphans(isolated_storage, monkeypatch):
    isolated_storage()
    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    Config._instance = None
    cfg = Config()
    store = QaTraceStore(cfg)

    def _write(day: str, tid: str, created: str, question: str) -> None:
        day_dir = cfg.data_dir / "qa_traces" / day
        day_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": {
                "trace_id": tid,
                "created_at": created,
                "path": "query",
                "elapsed_ms": 10,
                "error": None,
            },
            "request": {"question": question},
            "runtime": {},
            "stages": {},
            "plan": {},
            "retrieval": {"candidate_count": 1, "candidates": []},
            "evidence": {"cited": []},
            "answer": {"text": "a"},
        }
        (day_dir / f"{tid}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    _write("20260801", "d1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "2026-08-01T10:00:00+08:00", "day1")
    _write("20260810", "d2bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "2026-08-10T11:00:00+08:00", "day2")
    _write("20260810", "d3cccccccccccccccccccccccccccccc", "2026-08-10T12:00:00+08:00", "day2b")

    all_rows = store.list(limit=50)
    assert all_rows["total"] == 3

    only_10 = store.list(limit=50, date_from="2026-08-10", date_to="2026-08-10")
    assert only_10["total"] == 2
    assert {i["trace_id"] for i in only_10["items"]} == {
        "d2bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "d3cccccccccccccccccccccccccccccc",
    }

    only_01 = store.list(limit=50, date_from="2026-08-01", date_to="2026-08-01")
    assert only_01["total"] == 1
    assert only_01["items"][0]["question"] == "day1"


def test_qa_trace_full_request_parameters(isolated_storage, monkeypatch):
    cfg, _db, _chroma, data_dir = isolated_storage()
    monkeypatch.setattr(cfg.qa_trace, "enabled", True)
    builder = QaTraceBuilder(
        question="测试实体问答",
        path="qa-debug",
        collection_name="rag_knowledge",
        kb_name="测试知识库",
        doc_category="技术文档",
        entity_name="StampServer",
        llm_model="qwen3:7b",
        vision_model="llava:7b",
        thinking=True,
        web_search=False,
        allow_general_knowledge=True,
        agent_prompt="你是严谨的技术专家",
        pinned_chunk_ids=["chunk_1"],
        excluded_chunk_ids=["chunk_9"],
        cfg=cfg,
    )
    tid = builder.finish(answer="回复")
    assert tid is not None
    store = QaTraceStore(cfg)
    detail = store.get(tid)
    assert detail is not None
    req = detail.get("request", {})
    assert req.get("entity_name") == "StampServer"
    assert req.get("doc_category") == "技术文档"
    assert req.get("vision_model") == "llava:7b"
    assert req.get("thinking") is True
    assert req.get("web_search") is False
    assert req.get("allow_general_knowledge") is True
    assert req.get("agent_prompt") == "你是严谨的技术专家"
    assert req.get("pinned_chunk_ids") == ["chunk_1"]
    assert req.get("excluded_chunk_ids") == ["chunk_9"]


def test_qa_trace_records_clarify_block(isolated_storage, monkeypatch):
    """FR-7: set_clarify() lands needs/options/selected/option source in the trace."""
    cfg, _db, _chroma, _data_dir = isolated_storage()
    monkeypatch.setattr(cfg.qa_trace, "enabled", True)
    builder = QaTraceBuilder(question="写一段创建折线的代码", cfg=cfg)
    builder.set_clarify(
        {
            "needs_clarification": True,
            "ask_question": "请选择二次开发调用面（产品线 / 是否写代码）：",
            "selected": "",
            "options": [
                {"label": "StampWebRTC 二次开发（StampUtil）", "entity_name": "StampWebRTC", "source": "backbone_seed"},
                {"label": "StampWebGL 二次开发（StampUtil）", "entity_name": "StampWebGL", "source": "backbone_seed"},
            ],
        }
    )
    tid = builder.finish(answer="")
    store = QaTraceStore(cfg)
    detail = store.get(tid)
    clarify = detail.get("clarify", {})
    assert clarify.get("needs_clarification") is True
    assert clarify.get("selected") == ""
    sources = {o.get("source") for o in clarify.get("options", [])}
    assert sources == {"backbone_seed"}
    entities = {o.get("entity_name") for o in clarify.get("options", [])}
    assert {"StampWebRTC", "StampWebGL"} <= entities
    assert not any(str(e or "").startswith("Pipeline") for e in entities)


def test_qa_trace_records_ordered_decision_events(isolated_storage, monkeypatch):
    cfg, _db, _chroma, _data_dir = isolated_storage()
    monkeypatch.setattr(cfg.qa_trace, "enabled", True)
    builder = QaTraceBuilder(question="pipelien", cfg=cfg)
    builder.add_event(
        "controller_clarification_decided",
        {"decision_source": "main_controller", "needed": True},
    )
    builder.add_event(
        "clarification_candidates_merged",
        {"system": 2, "model_suggested": 1, "final": 4},
    )
    tid = builder.finish(answer="")

    detail = QaTraceStore(cfg).get(tid)
    assert [event["type"] for event in detail["events"]] == [
        "controller_clarification_decided",
        "clarification_candidates_merged",
    ]
    assert [event["sequence"] for event in detail["events"]] == [1, 2]
    assert detail["events"][1]["data"]["final"] == 4


def test_qa_trace_callback_keeps_option_id_and_candidate_metadata(isolated_storage, monkeypatch):
    cfg, _db, _chroma, _data_dir = isolated_storage()
    monkeypatch.setattr(cfg.qa_trace, "enabled", True)
    options = [
        {
            "id": "model_01",
            "label": "Pipeline 发布服务",
            "source": "model_suggested",
            "binding_status": "unresolved",
        },
        {
            "id": "other",
            "label": "以上都不是",
            "source": "fixed_other",
            "binding_status": "other",
        },
    ]
    builder = QaTraceBuilder(
        question="pipelien",
        cfg=cfg,
        clarification_option_id="model_01",
        clarification_selected_candidate=options[0],
        clarification_options=options,
        clarification_selection_kind="option",
    )
    builder.add_event(
        "clarification_selection_received",
        {"option_id": "model_01", "selected_candidate": options[0]},
    )
    builder.add_event(
        "identity_binding_updated",
        {"status": "confirmed_topic", "confirmed_topic": "Pipeline 发布服务"},
    )
    detail = QaTraceStore(cfg).get(builder.finish(answer=""))

    assert detail["request"]["clarification_option_id"] == "model_01"
    assert detail["request"]["clarification_options"] == options
    assert detail["request"]["clarification_selected_candidate"]["source"] == "model_suggested"
    assert [event["type"] for event in detail["events"]] == [
        "clarification_selection_received",
        "identity_binding_updated",
    ]


def test_qa_trace_heal_timezone_and_empty_dirs(isolated_storage, monkeypatch):
    isolated_storage()
    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    Config._instance = None
    cfg = Config()
    store = QaTraceStore(cfg)

    # 1. Test healing of missing created_at from directory name
    day_dir = cfg.data_dir / "qa_traces" / "20260810"
    day_dir.mkdir(parents=True, exist_ok=True)

    tid_missing_date = "m1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    payload = {
        "meta": {
            "trace_id": tid_missing_date,
            "created_at": None,  # Simulate missing created_at
            "path": "query",
            "elapsed_ms": 10,
            "error": None,
        },
        "request": {"question": "missing date test"},
        "runtime": {},
        "stages": {},
        "plan": {},
        "retrieval": {"candidate_count": 0, "candidates": []},
        "evidence": {"cited": []},
        "answer": {"text": "a"},
    }

    json_path = day_dir / f"{tid_missing_date}.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")

    # Run store.list() to trigger self-healing
    listed = store.list(limit=10)
    assert listed["total"] == 1
    # Check that created_at has been restored from folder name "20260810"
    item = listed["items"][0]
    assert item["trace_id"] == tid_missing_date
    assert item["created_at"].startswith("2026-08-10")

    # Also verify that the JSON file on disk was written back with restored date
    saved_payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved_payload["meta"]["created_at"].startswith("2026-08-10")

    # 2. Test removal of stale/dead index entries
    # Manually append a stale entry to the index file
    stale_tid = "s1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    stale_summary = {
        "trace_id": stale_tid,
        "request_id": "stale",
        "created_at": "2026-08-10T12:00:00+08:00",
        "path": "query",
        "file": "20260810/stale.json",
        "question": "stale question",
    }
    with (cfg.data_dir / "qa_traces" / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(stale_summary) + "\n")

    # Before healing, iter_index should return 2 items (m1 + stale)
    assert len(store._iter_index()) == 2

    # Run list() to trigger healing. Since stale.json does not exist, it should be pruned.
    listed2 = store.list(limit=10)
    assert listed2["total"] == 1
    assert len(store._iter_index()) == 1
    assert store._iter_index()[0]["trace_id"] == tid_missing_date

    # 3. Test cleaning up of empty daily directories
    empty_dir = cfg.data_dir / "qa_traces" / "20260723"
    empty_dir.mkdir(parents=True, exist_ok=True)
    assert empty_dir.exists()

    # Run list() to trigger healing
    store.list(limit=10)
    assert not empty_dir.exists()

    # 4. Test timezone aware date filtering
    # Write a trace with specific offset (e.g. UTC-5)
    # 2026-08-10T01:00:00-05:00 corresponds to 2026-08-10T14:00:00+08:00
    # It should be filtered correctly under timezone-aware comparison.
    # To check that datetime-aware list filter works, let's write two items:
    # one with "2026-08-10T23:00:00-01:00" (which is 2026-08-11 in +08:00, but 2026-08-10 in UTC-1)
    # and verify it converts and filters correctly.
    tid_tz1 = "t1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    payload_tz1 = {
        "meta": {
            "trace_id": tid_tz1,
            "created_at": "2026-08-10T23:00:00-01:00",
            "path": "query",
            "elapsed_ms": 10,
            "error": None,
        },
        "request": {"question": "tz test 1"},
        "runtime": {},
        "stages": {},
        "plan": {},
        "retrieval": {"candidate_count": 0, "candidates": []},
        "evidence": {"cited": []},
        "answer": {"text": "a"},
    }
    (day_dir / f"{tid_tz1}.json").write_text(json.dumps(payload_tz1), encoding="utf-8")
    store.list(limit=10)

    # If we filter for "2026-08-11" or "2026-08-10" using local conversion:
    # Let's check what local day astimezone() converts it to.
    from datetime import datetime
    dt_tz1 = datetime.fromisoformat("2026-08-10T23:00:00-01:00")
    local_day = dt_tz1.astimezone().strftime("%Y-%m-%d")

    listed_filtered = store.list(limit=10, date_from=local_day, date_to=local_day)
    tids_filtered = {i["trace_id"] for i in listed_filtered["items"]}
    assert tid_tz1 in tids_filtered
