# -*- coding: utf-8 -*-
"""Phase 6 自动化验证套件：Tool Attempt / Tool Effect UI 语义与三方对账 (PRD §11).

覆盖核心断言：
1. [PRD §11.1 & §11.3] Clarify DENIED 时只记录 attempt，绝不产生 effect (attempt_count=1, effect_count=0)；
2. [PRD §11.3] 模拟历史事故场景：连续 3 次 clarify 被拒绝，QaTrace 严格记录 attempt_count=3, effect_count=0；
3. [PRD §11.2] Clarification Card 只能由 effect 事件 (clarification_card_published) 生成，tool_start 自身绝不发布卡片；
4. [PRD §11.3] 正常发布卡片时，QaTrace 与 SSE 达成 attempt_count=1, effect_count=1 的准确对账。
"""

from unittest.mock import MagicMock, AsyncMock
import pytest

from rag_knowledge.services.qa_trace import QaTrace
from rag_knowledge.services.rag import RagChain
from rag_knowledge.services.agent_orchestration.models import (
    ToolProgressStatus,
)


def _make_dummy_trace() -> QaTrace:
    mock_cfg = MagicMock()
    mock_cfg.data_dir = "data"
    mock_cfg.agent_orchestration.trace_reasoning_policy = "redact"
    return QaTrace(question="test query", cfg=mock_cfg)


def test_clarify_denied_records_attempt_without_effect():
    """PRD 11.1 & 11.3: 当缺少身份解析状态时，clarify 返回 DENIED，不生成 card_published。"""
    trace = _make_dummy_trace()

    # 模拟 conv 缺少 identity_resolution
    dummy_conv = MagicMock()
    dummy_conv.identity_resolution = None

    # 模拟 AgentLoop 调用 clarify 工具
    trace.add_event("tool_start", {"name": "clarify", "arguments": {"question": "请澄清"}})
    obs = RagChain.execute_clarify_tool(dummy_conv, trace=trace, safe_add_trace_event=trace.add_event)
    trace.add_event("tool_end", {"name": "clarify", "progress": "DENIED", "status": "DENIED"})

    assert obs.status == ToolProgressStatus.DENIED
    assert obs.ok is False
    assert obs.error == "identity_resolution_missing"

    # 断言 Trace 记录了 1 次 attempt，但 0 次 effect，且明确对账 result=DENIED, pause=False
    stats = trace.get_clarification_stats()
    assert stats["attempt_count"] == 1
    assert stats["effect_count"] == 0
    assert stats["result"] == "DENIED"
    assert stats["pause"] is False

    # 断言没有任何 card_published 事件
    events = [e.get("type") or e.get("event") for e in trace.to_dict()["events"]]
    assert "clarification_card_published" not in events


def test_multiple_denied_clarify_attempts_reconcile_attempt3_effect0():
    """PRD 11.3: 模拟历史事故场景：连续 3 次尝试发起 clarify 全部被拒绝，对账记录 attempt=3, effect=0, result=DENIED, pause=False。"""
    trace = _make_dummy_trace()
    dummy_conv = MagicMock()
    dummy_conv.identity_resolution = None

    # 模拟 Controller 循环调用了 3 次 clarify
    for i in range(3):
        trace.add_event("tool_start", {"name": "clarify", "arguments": {"step": i + 1}})
        obs = RagChain.execute_clarify_tool(dummy_conv, trace=trace, safe_add_trace_event=trace.add_event)
        trace.add_event("tool_end", {"name": "clarify", "progress": "DENIED", "status": "DENIED"})
        assert obs.status == ToolProgressStatus.DENIED

    stats = trace.get_clarification_stats()
    assert stats["attempt_count"] == 3
    assert stats["effect_count"] == 0
    assert stats["result"] == "DENIED"
    assert stats["pause"] is False

    payload = trace.to_dict()
    assert payload["clarify"]["attempt_count"] == 3
    assert payload["clarify"]["effect_count"] == 0
    assert payload["clarify"]["result"] == "DENIED"
    assert payload["clarify"]["pause"] is False


def test_clarification_card_only_emitted_by_effect_publish_boundary():
    """PRD 11.2: 验证 tool_start(name=clarify) 绝不创建卡片，只有发布边界写入 clarification_card_published。"""
    trace = _make_dummy_trace()

    # 1. 模拟身份解析成功
    dummy_conv = MagicMock()
    dummy_res = MagicMock()
    dummy_conv.identity_resolution = dummy_res

    # 构造候选解析器模拟返回值
    mock_snapshot = MagicMock()
    mock_snapshot.clarification_id = "clar_snap_test_101"
    mock_snapshot.surface = "产品名称"
    mock_snapshot.candidate_entity_ids = ["e1", "e2"]
    mock_snapshot.display_candidates = []

    with pytest.MonkeyPatch.context() as mp:
        mock_resolver = MagicMock()
        mock_resolver.create_clarification_snapshot.return_value = mock_snapshot
        mp.setattr(
            "rag_knowledge.services.entity_candidate_resolver.get_entity_candidate_resolver",
            lambda: mock_resolver,
        )

        # 2. Controller 调用 clarify 工具
        trace.add_event("tool_start", {"name": "clarify", "arguments": {}})
        obs = RagChain.execute_clarify_tool(dummy_conv, trace=trace, safe_add_trace_event=trace.add_event)
        trace.add_event("tool_end", {"name": "clarify", "status": "SUCCESS", "progress": "COMPLETED"})

        assert obs.ok is True
        assert obs.data.get("pause") is True
        clarify_payload = obs.data.get("clarify")
        assert clarify_payload is not None

        # 此时 Tool 执行完毕，但卡片尚未发布到 HTTP/SSE 出口
        # 关键断言：绝对没有 clarification_card_published 事件，但工具已成功且返回 pause
        stats_pre = trace.get_clarification_stats()
        assert stats_pre["attempt_count"] == 1
        assert stats_pre["effect_count"] == 0
        assert stats_pre["result"] == "SUCCESS"
        assert stats_pre["pause"] is True

        # 3. 模拟进入发布边界（流式或非流式发布卡片）
        RagChain._record_clarification_card_published(trace, clarify_payload)

        # 4. 发布后：effect_count 升级为 1，三方对账一致
        stats_post = trace.get_clarification_stats()
        assert stats_post["attempt_count"] == 1
        assert stats_post["effect_count"] == 1
        assert stats_post["result"] == "SUCCESS"
        assert stats_post["pause"] is True

        pub_events = [
            e for e in trace.to_dict()["events"]
            if (e.get("type") == "clarification_card_published" or e.get("event") == "clarification_card_published")
        ]
        assert len(pub_events) == 1
        assert pub_events[0]["data"]["clarification_snapshot_id"] == "clar_snap_test_101"


@pytest.mark.anyio
async def test_production_stream_agent_query_clarify_branch_single_source_of_truth():
    """PRD §11.2 & P3: 真实运行生产 RagChain._stream_agent_query 的澄清分支，验证只输出 clarification_card_published，绝无旧 clarify 别名。"""
    chain = object.__new__(RagChain)
    chain._allow_general_knowledge = False
    chain._record_clarification_card_published = RagChain._record_clarification_card_published
    chain._clarification_card_event = RagChain._clarification_card_event
    chain._record_execution_event = RagChain._record_execution_event
    chain._safe_add_trace_event = RagChain._safe_add_trace_event
    chain._commit_qa_trace = MagicMock(return_value="t_stream_1")
    chain._run_agent_turn = AsyncMock()

    mock_turn_result = MagicMock()
    mock_turn_result.route = "clarify"
    mock_turn_result.conversation.understanding = None
    mock_turn_result.plan = None
    mock_turn_result.to_trace.return_value = {}
    mock_turn_result.clarify = {
        "needs_clarification": True,
        "ask_question": "请选择产品",
        "clarification_snapshot_id": "snap_stream_real_999",
        "options": [{"id": "opt1", "label": "Pipeline"}],
    }
    chain._run_agent_turn.return_value = mock_turn_result

    trace = _make_dummy_trace()
    stream_gen = chain._stream_agent_query(
        "如何使用Pipeline",
        history=[],
        llm_model=None,
        kb_name=None,
        doc_category=None,
        entity_name=None,
        thinking=False,
        web_search=False,
        allow_general_knowledge=False,
        agent_prompt=None,
        pipeline_events=False,
        pinned_chunk_ids=None,
        excluded_chunk_ids=None,
        path=None,
        clarification_question=None,
        clarification_selected=None,
        trace=trace,
    )

    events = [evt async for evt in stream_gen]
    event_types = [e["type"] for e in events if isinstance(e, dict) and "type" in e]

    # 关键断言：
    # 1. clarification_card_published 恰好出现 1 次
    assert event_types.count("clarification_card_published") == 1
    # 2. 生产流式事件中绝对不再有旧 clarify 事件
    assert "clarify" not in event_types
    # 3. 产生的内容完全正确
    card_evt = next(e for e in events if e.get("type") == "clarification_card_published")
    assert card_evt["data"]["clarification_snapshot_id"] == "snap_stream_real_999"


@pytest.mark.anyio
async def test_production_agent_loop_tool_result_emits_clarification_snapshot_id():
    """PRD 11.2 & P3: 真实运行生产 AgentLoop，验证执行 clarify 工具时发出的 TOOL_RESULT 事件原生携带 clarification_snapshot_id。"""
    from rag_knowledge.services.agent_orchestration.runtime import (
        AgentLoop,
        AgentBudget,
        AgentDecision,
        ToolObservation,
        ConversationContext,
        EvidencePool,
        ExecutionEventType,
        build_agent_registry,
    )
    from types import SimpleNamespace

    conv = ConversationContext.from_request("如何使用Pipeline", [])
    pool = EvidencePool(question_id="q1")

    # 构造返回澄清工具观测的真实 Handler
    async def fake_clarify_handler(context, **kwargs):
        return ToolObservation(
            tool="clarify",
            ok=True,
            summary="出示反问澄清卡片",
            data={
                "pause": True,
                "clarification_snapshot_id": "snap_prod_verified_888",
                "clarify": {
                    "clarification_snapshot_id": "snap_prod_verified_888",
                    "ask_question": "请选择产品",
                },
            },
        )

    decisions = [
        AgentDecision(action="tool_call", tool="clarify", arguments={"question": "请选择"}),
    ]

    def decide_fn(*args, **kwargs):
        if decisions:
            return decisions.pop(0)
        return AgentDecision(action="final_answer", answer="done")

    captured_events = []

    async def on_event(event):
        captured_events.append(event)

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=2),
        registry=build_agent_registry(),
        handlers={"clarify": fake_clarify_handler},
        cfg=SimpleNamespace(agent_orchestration=SimpleNamespace(terminal_finalization_v2=True)),
        decide_fn=decide_fn,
        tool_timeout=0,
    )

    await loop.run(on_event=on_event)

    tool_results = [
        e for e in captured_events
        if isinstance(e, dict) and e.get("type") == "tool_result"
    ]
    assert len(tool_results) >= 1
    data = tool_results[0]["data"]
    assert data["name"] == "clarify"
    assert data.get("clarification_snapshot_id") == "snap_prod_verified_888"
