"""Phase 4: Clarify 0/1/N 闭环专项测试套件

严格覆盖 PRD §9：
1. [9.1] Main 决定是否澄清，Runtime 不根据候选数硬编码拦截；
2. [9.2] Clarify Handler 支持 0/1/N 三种交互形态；
3. [9.3] 彻底删除正常业务 meaningful_candidates_insufficient；
4. [9.4] Clarify Effect (clarification_card_published, pause=true, terminal_action=clarify_pause) 成功立即停机；
5. [9.5] 0/1/N 各种形态发布与后续 callback 恢复闭环。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from rag_knowledge.services.agent_orchestration.models import (
    AgentBudget,
    AgentDecision,
    ConversationContext,
    EvidencePool,
    SessionState,
    ToolProgressStatus,
)
from rag_knowledge.services.agent_orchestration.runtime import (
    AgentLoop,
    build_agent_registry,
)
from rag_knowledge.services.entity_candidate_resolver import (
    EntityCandidate,
    IdentityResolution,
)
from rag_knowledge.services.rag import RagChain


def _make_rag_chain_with_resolution(resolution: IdentityResolution) -> RagChain:
    chain = object.__new__(RagChain)
    chain._cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
        qa_trace=SimpleNamespace(retain_days=0, max_traces=0),
        data_dir="data",
    )
    chain._safe_add_trace_event = lambda *args, **kwargs: None
    return chain


def _make_candidate(entity_id: str, canonical_name: str) -> EntityCandidate:
    return EntityCandidate(
        entity_id=entity_id,
        canonical_name=canonical_name,
        display_name=canonical_name,
        entity_type="product",
        matched_surface=canonical_name,
        match_sources=("catalog",),
        lexical_score=1.0,
        semantic_score=None,
        context_score=1.0,
        graph_score=1.0,
        final_score=1.0,
    )


def _make_resolution(surface: str, status: str, candidates: tuple[EntityCandidate, ...] = ()) -> IdentityResolution:
    return IdentityResolution(
        status=status,
        surface=surface,
        confirmed_entity_id=None,
        confirmed_entity_name=None,
        candidates=candidates,
        confidence=0.5 if candidates else None,
        margin=None,
        reason="test_resolution",
    )


def test_clarify_zero_candidates_produces_freetext_card_and_pauses():
    """PRD 9.2 & 9.3: 0 meaningful candidates 是正常澄清状态，绝不报错 meaningful_candidates_insufficient，而是出示自由补充卡片并 pause。"""
    conv = ConversationContext.from_request("帮我配置一下那个组件", [])
    # 0 个候选实体（无法识别出具体实体，但 Main 判断当前无法可靠检索）
    conv.identity_resolution = _make_resolution(surface="那个组件", status="ambiguous", candidates=())

    events = []
    def safe_add_event(trace, event_type, data):
        events.append((event_type, data))

    obs = RagChain.execute_clarify_tool(conv, trace=None, args={}, safe_add_trace_event=safe_add_event)

    # 1. 绝不被 DENIED 或报 meaningful_candidates_insufficient
    assert obs.ok is True
    assert obs.status != ToolProgressStatus.DENIED
    assert obs.error is None
    assert obs.data.get("pause") is True

    # 2. 交互形态：0 候选时只有系统附加的“以上都不是”，默认反问为自由补充文案
    clar_payload = obs.data.get("clarify") or {}
    assert clar_payload.get("needs_clarification") is True
    assert clar_payload.get("ask_question") == "请补充您具体指的产品、模块、功能描述或相关上下文。"

    # 选项中只有 1 个 fixed_other（以上都不是）
    options = clar_payload.get("options") or []
    meaningful = [o for o in options if o.get("source") != "fixed_other" and o.get("id") != "other"]
    assert len(meaningful) == 0
    assert any(o.get("id") == "other" or o.get("source") == "fixed_other" for o in options)

    # 验证 Handler 产生 clarification_prepared 事件，且绝不冒充已发布 (clarification_card_published)
    prepared_events = [data for evt_type, data in events if evt_type == "clarification_prepared"]
    assert len(prepared_events) == 1
    assert prepared_events[0]["meaningful_count"] == 0
    assert not any(evt_type == "clarification_card_published" for evt_type, _ in events)


def test_clarify_single_candidate_produces_confirmation_card_and_pauses():
    """PRD 9.2 & 9.3: 1 meaningful candidate 是正常澄清状态，出示单选确认卡片（你指的是 X 吗？）。"""
    conv = ConversationContext.from_request("Pipeline 怎么用？", [])
    # 恰好 1 个候选实体
    cand = _make_candidate("PipelineBuilder", "PipelineBuilder")
    conv.identity_resolution = _make_resolution(surface="Pipeline", status="ambiguous", candidates=(cand,))

    events = []
    def safe_add_event(trace, event_type, data):
        events.append((event_type, data))

    obs = RagChain.execute_clarify_tool(conv, trace=None, args={}, safe_add_trace_event=safe_add_event)

    assert obs.ok is True
    assert obs.data.get("pause") is True

    clar_payload = obs.data.get("clarify") or {}
    assert clar_payload.get("needs_clarification") is True
    # 默认反问文案为单选确认形态
    assert clar_payload.get("ask_question") == "您指的是「PipelineBuilder」吗？"

    options = clar_payload.get("options") or []
    meaningful = [o for o in options if o.get("source") != "fixed_other" and o.get("id") != "other"]
    assert len(meaningful) == 1
    assert "PipelineBuilder" in meaningful[0]["label"]
    # 总选项为：PipelineBuilder + 以上都不是
    assert len(options) == 2

    # 验证事件：只产生 clarification_prepared
    prepared_events = [data for evt_type, data in events if evt_type == "clarification_prepared"]
    assert len(prepared_events) == 1
    assert prepared_events[0]["meaningful_count"] == 1
    assert not any(evt_type == "clarification_card_published" for evt_type, _ in events)


def test_clarify_multiple_candidates_produces_multi_choice_card_and_pauses():
    """PRD 9.2: 2+ meaningful candidates 出示多选候选列表卡片。"""
    conv = ConversationContext.from_request("Builder 端口是多少？", [])
    cand1 = _make_candidate("PipelineBuilder", "PipelineBuilder")
    cand2 = _make_candidate("ModelBuilder", "ModelBuilder")
    conv.identity_resolution = _make_resolution(surface="Builder", status="ambiguous", candidates=(cand1, cand2))

    events = []
    def safe_add_event(trace, event_type, data):
        events.append((event_type, data))

    obs = RagChain.execute_clarify_tool(conv, trace=None, args={}, safe_add_trace_event=safe_add_event)

    assert obs.ok is True
    assert obs.data.get("pause") is True

    clar_payload = obs.data.get("clarify") or {}
    assert clar_payload.get("needs_clarification") is True
    assert clar_payload.get("ask_question") == "您指的是以下哪一个产品或模块？"

    options = clar_payload.get("options") or []
    meaningful = [o for o in options if o.get("source") != "fixed_other" and o.get("id") != "other"]
    assert len(meaningful) == 2
    # 候选 1, 候选 2 + 以上都不是
    assert len(options) == 3

    # 验证事件：只产生 clarification_prepared
    prepared_events = [data for evt_type, data in events if evt_type == "clarification_prepared"]
    assert len(prepared_events) == 1
    assert prepared_events[0]["meaningful_count"] == 2
    assert not any(evt_type == "clarification_card_published" for evt_type, _ in events)


def test_agent_loop_clarify_effect_stops_turn_immediately():
    """PRD 9.4: 一旦 Clarify Effect (clarification_card_published, pause=true) 成功，同一 turn 立即停机，终端状态为 clarify_pause。"""
    conv = ConversationContext.from_request("那个工具怎么用？", [])
    cand = _make_candidate("PipelineBuilder", "PipelineBuilder")
    conv.identity_resolution = _make_resolution(surface="那个工具", status="ambiguous", candidates=(cand,))
    pool = EvidencePool(question_id="q-loop")

    async def async_clarify(args):
        return RagChain.execute_clarify_tool(conv, trace=None, args=args)

    async def async_retrieve(args):
        return None

    handlers = {
        "clarify": async_clarify,
        "retrieve_kb": async_retrieve,
    }

    decisions = [
        AgentDecision(action="tool_call", tool="clarify", arguments={"reason": "用户意图模糊"}, source="llm"),
        # 如果停机失败，第二个决策会试图检索
        AgentDecision(action="tool_call", tool="retrieve_kb", arguments={"search_focus_text": "fallback"}, source="llm"),
    ]
    step_idx = 0

    def decide(*args):
        nonlocal step_idx
        d = decisions[step_idx]
        step_idx += 1
        return d

    events = []

    async def on_event(event):
        events.append(event)

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=5),
        registry=build_agent_registry(),
        handlers=handlers,
        cfg=SimpleNamespace(agent_orchestration=SimpleNamespace(terminal_finalization_v2=True)),
        decide_fn=decide,
        tool_timeout=0,
    )
    result = asyncio.run(loop.run(on_event=on_event))

    # 验证同一 turn 立即停机
    assert result.terminal_action == "clarify_pause"
    assert result.route == "clarify"
    assert result.budget["steps_used"] == 1  # 只执行了 1 步，没有执行第二步
    assert result.clarify is not None
    assert result.clarify["needs_clarification"] is True
    assert result.clarify["ask_question"] == "您指的是「PipelineBuilder」吗？"


def test_clarify_custom_question_overrides_default_phrasing():
    """验证如果 Main 控制器在 arguments 中给出了明确的自定义 question，优先使用 Main 的 question。"""
    conv = ConversationContext.from_request("怎么配置？", [])
    conv.identity_resolution = _make_resolution(surface="配置", status="ambiguous", candidates=())

    obs = RagChain.execute_clarify_tool(
        conv,
        trace=None,
        args={"question": "您是指服务端口配置还是数据库连接配置？"},
    )

    assert obs.ok is True
    clar_payload = obs.data.get("clarify") or {}
    assert clar_payload.get("ask_question") == "您是指服务端口配置还是数据库连接配置？"


def test_clarify_only_denied_on_actual_system_error():
    """PRD 9.3: Handler 只可因真实异常拒绝（缺少 resolution 等），绝不因候选数为 0 或 1 拒绝。"""
    conv = ConversationContext.from_request("测试", [])
    conv.identity_resolution = None  # 缺失身份解析对象

    obs = RagChain.execute_clarify_tool(conv, trace=None, args={})
    assert obs.ok is False
    assert obs.status == ToolProgressStatus.DENIED
    assert obs.error == "identity_resolution_missing"


def test_clarify_callback_recovery_and_closure():
    """PRD 9.2 & 9.5: 验证针对 0/1/N 卡片发起的真实 HTTP QueryRequest callback 回调恢复契约。"""
    from rag_knowledge.models.api import QueryRequest
    from rag_knowledge.api.routes import _resolve_clarification_callback
    from rag_knowledge.services.entity_candidate_resolver import get_entity_candidate_resolver

    resolver = get_entity_candidate_resolver()

    # 1. 0 候选卡片 -> 用户输入 free_text 回调（真实 HTTP: option_id='other', selection_kind='free_text'）
    res_0 = _make_resolution(surface="组件", status="ambiguous", candidates=())
    snap_0 = resolver.create_clarification_snapshot(res_0)

    req_0 = QueryRequest(
        question="帮我配置组件",
        clarification_question="请补充您具体指的产品、模块、功能描述或相关上下文。",
        clarification_snapshot_id=snap_0.clarification_id,
        clarification_option_id="other",
        clarification_selection_kind="free_text",
        clarification_free_text="我想了解三维管网构建器",
    )
    cb_0 = _resolve_clarification_callback(req_0)
    assert cb_0.selection_kind == "free_text"
    assert cb_0.free_text == "我想了解三维管网构建器"
    assert cb_0.option_id == "other"

    conv_0 = ConversationContext.from_request(
        cb_0.question,
        [],
        clarification_question=cb_0.question,
        clarification_selected=cb_0.selected_label,
        clarification_option_id=cb_0.option_id,
        clarification_snapshot_id=cb_0.snapshot_id,
        clarification_selected_candidate=cb_0.selected_candidate,
        clarification_selection_kind=cb_0.selection_kind,
        clarification_free_text=cb_0.free_text,
    )
    assert conv_0.clarification_callback is True
    assert conv_0.clarification_selection_kind == "free_text"
    assert conv_0.clarification_free_text == "我想了解三维管网构建器"
    assert conv_0.confirmed_entity is None

    # 2. 1 候选单选卡片 -> 用户点击选项回调（真实 HTTP: option_id='a', selection_kind='option'）
    cand_1 = _make_candidate("PipelineBuilder", "PipelineBuilder")
    res_1 = _make_resolution(surface="Pipeline", status="ambiguous", candidates=(cand_1,))
    snap_1 = resolver.create_clarification_snapshot(res_1)

    req_1 = QueryRequest(
        question="Pipeline 怎么配置？",
        clarification_question="您指的是「PipelineBuilder」吗？",
        clarification_snapshot_id=snap_1.clarification_id,
        clarification_option_id="a",
        clarification_selection_kind="option",
    )
    cb_1 = _resolve_clarification_callback(req_1)
    assert cb_1.selection_kind == "option"
    assert cb_1.option_id == "a"

    conv_1 = ConversationContext.from_request(
        cb_1.question,
        [],
        clarification_question=cb_1.question,
        clarification_selected=cb_1.selected_label,
        clarification_option_id=cb_1.option_id,
        clarification_snapshot_id=cb_1.snapshot_id,
        clarification_selected_candidate=cb_1.selected_candidate,
        clarification_selection_kind=cb_1.selection_kind,
        clarification_free_text=cb_1.free_text,
    )
    assert conv_1.clarification_callback is True
    assert conv_1.confirmed_entity == "PipelineBuilder"
    assert conv_1.identity_status == "confirmed_entity"

    # 3. N 候选多选卡片 -> 用户选定第 2 个候选（真实 HTTP: option_id='b', selection_kind='option'）
    cand_2a = _make_candidate("PipelineBuilder", "PipelineBuilder")
    cand_2b = _make_candidate("ModelBuilder", "ModelBuilder")
    res_2 = _make_resolution(surface="Builder", status="ambiguous", candidates=(cand_2a, cand_2b))
    snap_2 = resolver.create_clarification_snapshot(res_2)

    req_2 = QueryRequest(
        question="Builder 端口是多少？",
        clarification_question="您指的是以下哪一个产品或模块？",
        clarification_snapshot_id=snap_2.clarification_id,
        clarification_option_id="b",
        clarification_selection_kind="option",
    )
    cb_2 = _resolve_clarification_callback(req_2)
    assert cb_2.selection_kind == "option"
    assert cb_2.option_id == "b"

    conv_2 = ConversationContext.from_request(
        cb_2.question,
        [],
        clarification_question=cb_2.question,
        clarification_selected=cb_2.selected_label,
        clarification_option_id=cb_2.option_id,
        clarification_snapshot_id=cb_2.snapshot_id,
        clarification_selected_candidate=cb_2.selected_candidate,
        clarification_selection_kind=cb_2.selection_kind,
        clarification_free_text=cb_2.free_text,
    )
    assert conv_2.clarification_callback is True
    assert conv_2.confirmed_entity == "ModelBuilder"
    assert conv_2.identity_status == "confirmed_entity"

    # 4. 用户选择“以上都不是”（真实 HTTP: option_id='other', selection_kind='other'）
    req_other = QueryRequest(
        question="Pipeline 怎么配置？",
        clarification_question="您指的是「PipelineBuilder」吗？",
        clarification_snapshot_id=snap_1.clarification_id,
        clarification_option_id="other",
        clarification_selection_kind="other",
    )
    cb_other = _resolve_clarification_callback(req_other)
    assert cb_other.selection_kind == "other"
    assert cb_other.option_id == "other"

    conv_other = ConversationContext.from_request(
        cb_other.question,
        [],
        clarification_question=cb_other.question,
        clarification_selected=cb_other.selected_label,
        clarification_option_id=cb_other.option_id,
        clarification_snapshot_id=cb_other.snapshot_id,
        clarification_selected_candidate=cb_other.selected_candidate,
        clarification_selection_kind=cb_other.selection_kind,
        clarification_free_text=cb_other.free_text,
    )
    assert conv_other.clarification_callback is True
    assert conv_other.confirmed_entity is None
    assert conv_other.identity_status in {"unresolved", "ambiguous", "not_required"}

