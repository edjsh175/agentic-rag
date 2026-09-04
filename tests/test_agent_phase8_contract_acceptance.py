"""Phase 8 契约与模拟组件级验收测试套件 (Contract & Simulated Component Acceptance).

定位说明（非真实模型推理与非真实浏览器 E2E）：
本套件通过模拟桩（Mock / In-memory Transport）验证 PRD §13 定义的协议行为与状态流转契约：
1. [13.1 协议契约] 原事故模糊输入分支流式输出 clarification_card_published 单点真源、携带精准快照 ID 并暂停输出；
2. [13.2 协议契约] 回调恢复后的回答生成、审核门禁与发布状态流转；
3. [13.3 协议契约] 元对话解释在无 direct_chat 特权前提下基于会话事实由审核器核准发布；
4. [13.4 协议契约] HTTP SSE 传输时暴露 Main 思维链、阻断审查器内部思考泄露的序列协议。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import rag_knowledge.api.routes as routes
from rag_knowledge.services.agent_orchestration.models import (
    ConversationContext,
    EvidencePool,
    SessionState,
)
from rag_knowledge.services.agent_orchestration.runtime import (
    build_agent_messages,
)
from rag_knowledge.services.answer_finalizer import AnswerFinalizer
from rag_knowledge.services.entity_candidate_resolver import (
    EntityCandidate,
    IdentityResolution,
    get_entity_candidate_resolver,
)
from rag_knowledge.services.helper_grounding_reviewer import (
    ClaimReview,
    HelperGroundingReviewResult,
)
from rag_knowledge.services.rag import RagChain


def _make_mock_reviewer(result: HelperGroundingReviewResult):
    def _reviewer(q, docs, cand):
        return result
    return _reviewer


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


def _make_resolution(surface: str, candidates: tuple[EntityCandidate, ...]) -> IdentityResolution:
    return IdentityResolution(
        status="ambiguous",
        surface=surface,
        confirmed_entity_id=None,
        confirmed_entity_name=None,
        candidates=candidates,
        confidence=0.5 if candidates else None,
        margin=None,
        reason="test_resolution",
    )


def _make_test_chain(mock_agent_result) -> RagChain:
    chain = object.__new__(RagChain)
    chain._cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
        qa_trace=SimpleNamespace(retain_days=0, max_traces=0),
        data_dir="data",
    )
    chain._safe_add_trace_event = lambda *args, **kwargs: None
    chain._run_agent_loop = lambda *args, **kwargs: mock_agent_result
    return chain


# ==============================================================================
# 13.1 原事故真实链 (如何使用pipelienbuilder -> Clarify 单候选确认卡 -> pause)
# ==============================================================================

def test_phase8_original_incident_chain_clarify_published_and_paused():
    """PRD §13.1: '如何使用pipelienbuilder' 触发 clarify tool attempt 并发布确认卡，严格 pause 且不提前输出正文。"""
    cand = _make_candidate("PipelineBuilder", "PipelineBuilder")
    resolution = _make_resolution("pipelienbuilder", (cand,))
    resolver = get_entity_candidate_resolver()
    snapshot = resolver.create_clarification_snapshot(resolution)

    clarify_payload = {
        "needs_clarification": True,
        "ask_question": "您指的是「PipelineBuilder」吗？",
        "clarification_snapshot_id": snapshot.clarification_id,
        "options": [
            {
                "id": "opt_builder",
                "label": "PipelineBuilder",
                "canonical_name": "PipelineBuilder",
                "source": "backbone",
                "binding_status": "canonical",
                "filter": {"entity_name": "PipelineBuilder"},
            },
            {
                "id": "other",
                "label": "以上都不是",
                "source": "fixed_other",
                "binding_status": "unresolved",
                "filter": {},
            },
        ],
    }

    from unittest.mock import AsyncMock, MagicMock
    mock_turn_result = MagicMock()
    mock_turn_result.route = "clarify"
    mock_turn_result.conversation.understanding = None
    mock_turn_result.plan = None
    mock_turn_result.to_trace.return_value = {}
    mock_turn_result.clarify = clarify_payload

    chain = object.__new__(RagChain)
    chain._allow_general_knowledge = False
    chain._record_clarification_card_published = RagChain._record_clarification_card_published
    chain._clarification_card_event = RagChain._clarification_card_event
    chain._record_execution_event = RagChain._record_execution_event
    chain._safe_add_trace_event = RagChain._safe_add_trace_event
    chain._commit_qa_trace = MagicMock(return_value="t_p8_stream")
    chain._run_agent_turn = AsyncMock(return_value=mock_turn_result)

    from rag_knowledge.services.qa_trace import QaTrace
    mock_cfg = MagicMock()
    mock_cfg.data_dir = "data"
    mock_cfg.agent_orchestration.trace_reasoning_policy = "redact"
    trace = QaTrace(question="如何使用pipelienbuilder", cfg=mock_cfg)

    async def collect():
        events = []
        async for evt in chain._stream_agent_query(
            "如何使用pipelienbuilder",
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
        ):
            events.append(evt)
        return events

    events = asyncio.run(collect())
    event_types = [e.get("type") for e in events]

    # 1. 产生 clarification_card_published 事件，且单点真源（彻底废除旧 clarify 别名）
    assert "clarification_card_published" in event_types
    assert "clarify" not in event_types

    # 2. 卡片携带精准 snapshot_id 及单候选 + 以上都不是
    card_evt = next(e for e in events if e.get("type") == "clarification_card_published")
    data = card_evt.get("data") or {}
    assert data["clarification_snapshot_id"] == snapshot.clarification_id
    options = data.get("options") or []
    assert len(options) == 2
    assert options[0]["canonical_name"] == "PipelineBuilder"
    assert options[1]["id"] == "other"

    # 3. 绝不提前输出任何答案正文或 token
    assert "token" not in event_types
    assert "final_answer" not in event_types
    assert "done" in event_types


# ==============================================================================
# 13.2 Callback 继续执行 (确认 PipelineBuilder -> 检索 -> 审核 -> 发布)
# ==============================================================================

def test_phase8_callback_resume_through_publication_and_reviewer():
    """PRD §13.2: 用户选择 PipelineBuilder 后 resume -> retrieve -> compose_answer -> Reviewer 审核 -> 发布。"""
    conv = ConversationContext.from_request(
        "如何使用 PipelineBuilder",
        [],
        clarification_question="如何使用pipelienbuilder",
        clarification_selected="PipelineBuilder",
        clarification_option_id="opt_builder",
        clarification_snapshot_id="snap_p8_001",
    )
    conv.confirmed_entity = "PipelineBuilder"
    conv.identity_status = "confirmed_entity"

    pool = EvidencePool(question_id="q_p8_resume")
    doc_chunk = {
        "content": "PipelineBuilder 负责自动化构建与管线组装，配置文件为 builder.yaml。",
        "metadata": {
            "chunk_id": "chk_pb_1",
            "citation_id": 1,
            "document_entity": "PipelineBuilder",
            "evidence_class": "TARGET_DIRECT",
            "support_scope": "TARGET_SPECIFIC",
        },
    }
    pool.add_retrieve([doc_chunk], query="PipelineBuilder 配置使用")

    mock_review = HelperGroundingReviewResult(
        verdict="PASS",
        coverage="FULL",
        repair_mode="NONE",
        summary="回答完全由 PipelineBuilder 证据支持",
        claim_reviews=[
            ClaimReview(
                claim_id="c1",
                claim="PipelineBuilder 负责自动化构建与管线组装。",
                claim_type="knowledge_claim",
                claim_scope="TARGET_FACT",
                status="supported",
                evidence_ids=(1,),
                reason="证据 1 直接支持",
            )
        ],
        rewrite_actions=[],
    )

    finalizer = AnswerFinalizer()
    events = []

    finalized = finalizer.finalize(
        candidate="PipelineBuilder 负责自动化构建与管线组装。[1]",
        question="如何使用 PipelineBuilder",
        context_docs=[doc_chunk],
        allow_general_knowledge=False,
        helper_reviewer=_make_mock_reviewer(mock_review),
        on_lifecycle_event=lambda evt: events.append(evt),
    )

    assert finalized.answer == "PipelineBuilder 负责自动化构建与管线组装。[1]"
    assert finalized.grounding["verdict"] == "pass"
    assert finalized.grounding["publication_state"] in {"published", "grounded_full"}
    # Reviewer activity 事件可见
    assert any(e.get("type") == "helper_grounding_review_started" for e in events)


# ==============================================================================
# 13.3 Meta Conversation 真实链 (你刚才为什么反问我？ -> 走 Reviewer，无 direct_chat 特权)
# ==============================================================================

def test_phase8_meta_conversation_explanation_under_publication_gate():
    """PRD §13.3: '你刚才为什么反问我？' 只能基于 Snapshot 中的 Conversation/Runtime Evidence，经 Reviewer 审核发布。"""
    conv = ConversationContext.from_request(
        "你刚才为什么反问我？",
        [
            {"role": "user", "content": "如何使用pipelienbuilder"},
            {"role": "assistant", "content": "请确认您具体指的是 PipelineBuilder 吗？"},
        ],
    )

    pool = EvidencePool(question_id="q_p8_meta")

    # 1. 验证 build_agent_messages 包含统一元对话事实约束
    msgs = build_agent_messages(
        question="你刚才为什么反问我？",
        conversation_section=conv.to_prompt(),
        evidence_section=pool.to_prompt("(暂无)"),
    )
    sys_content = msgs[0]["content"]
    assert "如果问题要求解释对话历史或系统上一轮行为" in sys_content
    assert "只能使用 Snapshot 中的 Conversation / Runtime Evidence" in sys_content
    assert "不得把模型记忆或外部通用知识当成解释依据" in sys_content

    # 2. Direct candidate 进入 Publication Gate 审核（无 direct_chat bypass）
    mock_meta_review = HelperGroundingReviewResult(
        verdict="PASS",
        coverage="FULL",
        repair_mode="NONE",
        summary="元对话解释受对话上下文支持",
        claim_reviews=[
            ClaimReview(
                claim_id="c1",
                claim="上一轮输入存在拼写模糊，因此发起了产品确认卡片。",
                claim_type="conversation_claim",
                claim_scope="CONVERSATION_CONTEXT",
                status="supported",
                evidence_ids=(),
                reason="符合前序对话记录",
            )
        ],
        rewrite_actions=[],
    )

    finalizer = AnswerFinalizer()
    events = []

    finalized = finalizer.finalize(
        candidate="因为您上一轮输入的词存在拼写模糊，系统发起了产品确认卡片向您核对。",
        question="你刚才为什么反问我？",
        context_docs=[],
        allow_general_knowledge=False,
        helper_reviewer=_make_mock_reviewer(mock_meta_review),
        on_lifecycle_event=lambda evt: events.append(evt),
    )

    assert finalized.grounding["verdict"] == "pass"
    assert finalized.grounding["publication_state"] in {"published", "grounded_full"}
    assert any(e.get("type") == "helper_grounding_review_started" for e in events)


# ==============================================================================
# 13.4 HTTP SSE 传输与透明度呈现协议
# ==============================================================================

def test_phase8_http_sse_transparency_and_no_reviewer_thinking_leak(monkeypatch):
    """PRD §13.4: HTTP SSE 协议保障 Main Controller reasoning 可见，Reviewer 仅可见活动不泄露 internal thinking。"""
    events = [
        {"type": "llm_reasoning_start", "data": {"call_id": "main_ctrl_1", "role": "main", "stage": "agent_controller"}},
        {"type": "llm_reasoning_delta", "data": {"call_id": "main_ctrl_1", "role": "main", "stage": "agent_controller", "delta": "正在分析用户意图。"}},
        {"type": "llm_reasoning_end", "data": {"call_id": "main_ctrl_1", "role": "main", "stage": "agent_controller"}},
        {"type": "tool_start", "data": {"step": 1, "name": "clarify", "arguments": {"question": "请确认产品"}}},
        {"type": "tool_result", "data": {"step": 1, "name": "clarify", "ok": True, "clarification_snapshot_id": "snap_sse_01"}},
        {"type": "clarification_card_published", "data": {"clarification_snapshot_id": "snap_sse_01", "needs_clarification": True}},
        {"type": "helper_grounding_review_started", "data": {"candidate_version": 1, "review_count": 1, "message": "正在核对事实 Claim。"}},
        {"type": "review_status", "data": {"candidate_version": 1, "review_count": 1, "verdict": "PASS", "coverage": "FULL"}},
        {"type": "done", "data": {}},
    ]

    class _StreamChain:
        async def stream_query(self, *_args, **_kwargs):
            for e in events:
                yield e

    monkeypatch.setattr(routes, "_rag", _StreamChain())
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    async def request():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(
                "/api/query/stream",
                json={"question": "如何使用pipelienbuilder", "mode": "agent"},
            )

    response = asyncio.run(request())
    assert response.status_code == 200
    received = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line[6:] != "[DONE]"
    ]

    # 断言收到的 SSE 事件序列与内容
    assert len(received) == len(events)
    # Controller reasoning 角色必须为 main
    assert received[0]["data"]["role"] == "main"
    assert received[1]["data"]["role"] == "main"
    # Reviewer 没有 llm_reasoning_start/delta（不泄漏内部思考过程）
    reviewer_reasoning = [
        e for e in received
        if e.get("type", "").startswith("llm_reasoning_") and e.get("data", {}).get("role") == "reviewer"
    ]
    assert len(reviewer_reasoning) == 0
    # Reviewer activity 事件可见
    assert any(e["type"] == "helper_grounding_review_started" for e in received)
    assert any(e["type"] == "review_status" for e in received)
    # Clarification card 包含精准 snapshot_id
    card_evt = next(e for e in received if e["type"] == "clarification_card_published")
    assert card_evt["data"]["clarification_snapshot_id"] == "snap_sse_01"
