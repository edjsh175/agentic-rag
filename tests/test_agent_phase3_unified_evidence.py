from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
import pytest

from rag_knowledge.services.agent_orchestration.models import (
    AgentBudget,
    AgentDecision,
    ConversationContext,
    EvidenceItem,
    EvidencePool,
    EvidenceSnapshot,
    AgentTurnResult,
)
from rag_knowledge.services.agent_orchestration.runtime import (
    AgentLoop,
    ComposeAnswerHandler,
    build_agent_registry,
    build_unified_grounding_docs,
    build_answer_generation_messages,
    AnswerGenerationContext,
)
from rag_knowledge.services.agent_orchestration.evidence_gate import evaluate_rules
from rag_knowledge.services.helper_grounding_reviewer import (
    HelperGroundingReviewer,
    format_evidence_snapshot,
)
from rag_knowledge.services.rag import RagChain


def _make_sample_conversation() -> ConversationContext:
    conv = ConversationContext.from_request("我刚才选的是哪个？", [])
    session = getattr(conv, "session", None)
    if session is not None and hasattr(session, "turns"):
        class DummyTurn:
            def __init__(self, role: str, content: str):
                self.role = role
                self.content = content
        session.turns = [
            DummyTurn("user", "帮我查一下 Pipeline"),
            DummyTurn("assistant", "发现有多个相关的 Pipeline 组件，请问具体是哪一个？"),
        ]
    conv.clarification_history = [
        {
            "question": "请问您指的是哪一个？",
            "selected": "PipelineBuilder",
            "option_id": "opt_1",
            "selection_kind": "candidate",
            "selected_candidate": {"name": "PipelineBuilder"},
        }
    ]
    return conv


def test_build_unified_grounding_docs_defaults_to_conversation_semantics_only():
    conv = _make_sample_conversation()
    runtime_events = [
        {"type": "tool_start", "data": {"name": "clarify"}},
        {"type": "tool_result", "data": {"status": "DENIED"}},
    ]
    steps = [
        {
            "step": 1,
            "tool": "clarify",
            "guard": {"allowed": False, "reason": "clarify_denied_limit"},
            "status": "DENIED",
        },
        {
            "step": 2,
            "tool": "retrieve_kb",
            "guard": {"allowed": True, "reason": None},
            "status": "PROGRESS",
        },
    ]
    observations = [
        {
            "tool": "retrieve_kb",
            "status": "PROGRESS",
            "data": {"found": 2},
            "message": "召回 2 条文档",
        }
    ]

    docs = build_unified_grounding_docs(
        conv,
        runtime_events=runtime_events,
        tool_observations=observations,
        execution_steps=steps,
    )

    source_types = {d["metadata"]["source_type"] for d in docs}
    assert source_types == {"conversation"}

    # 所有提取的运行时与历史证据必须标注 citable=False，防止泄露给前端文献引用
    for d in docs:
        assert d["metadata"]["citable"] is False
        assert "evidence_id" in d["metadata"]

    # 验证澄清选择事实提取正确
    clar_doc = next(d for d in docs if "澄清交互" in d["content"])
    assert "PipelineBuilder" in clar_doc["content"]
    assert clar_doc["metadata"]["selected_entity"] == "PipelineBuilder"
    assert clar_doc["metadata"]["support_scope"] == "CONTEXT_ONLY"

    assert all(d["metadata"]["source_type"] != "runtime_event" for d in docs)
    assert all(d["metadata"]["source_type"] != "tool_observation" for d in docs)


def test_runtime_semantics_are_opt_in_and_raw_tool_data_is_not_exposed():
    conv = _make_sample_conversation()
    docs = build_unified_grounding_docs(
        conv,
        runtime_events=[{"type": "tool_result", "data": {"status": "DENIED", "secret": "hidden"}}],
        tool_observations=[{
            "tool": "retrieve_kb",
            "status": "NO_PROGRESS",
            "summary": "没有新增证据",
            "data": {"secret": "hidden", "n": 8},
        }],
        execution_steps=[{
            "step": 1,
            "tool": "clarify",
            "guard": {"allowed": False, "reason": "internal_guard_code"},
            "progress": "DENIED",
        }],
        include_runtime_semantics=True,
        include_tool_semantics=True,
    )

    text = "\n".join(d["content"] for d in docs)
    source_types = {d["metadata"]["source_type"] for d in docs}
    assert "runtime_event" in source_types
    assert "tool_observation" in source_types
    assert "secret" not in text
    assert "internal_guard_code" not in text
    assert "Guard" not in text
    assert "没有新增证据" in text


def test_snapshot_freezes_unified_evidence_and_preserves_citability():
    conv = _make_sample_conversation()
    pool = EvidencePool(question_id="phase3-test")
    pool.add_retrieve(
        docs=[
            {
                "content": "PipelineBuilder 是核心构建器组件。",
                "metadata": {
                    "chunk_id": "chunk_kb_1",
                    "file_name": "pipeline.md",
                    "source_type": "kb_text",
                    "evidence_class": "TARGET_DIRECT",
                    "support_scope": "TARGET_SPECIFIC",
                },
            }
        ]
    )

    grounding_docs = build_unified_grounding_docs(conv)
    snapshot = pool.create_snapshot(
        verdict={"coverage": "FULL", "can_answer": True},
        grounding_docs=grounding_docs,
    )

    all_docs = snapshot.documents()
    citable_docs = snapshot.citable_documents()

    # all_docs 应包含 KB 证据 + Conversation 证据
    assert len(all_docs) == 1 + len(grounding_docs)
    # citable_docs 必须仅包含 KB 证据
    assert len(citable_docs) == 1
    assert citable_docs[0]["metadata"]["chunk_id"] == "chunk_kb_1"
    assert citable_docs[0]["metadata"].get("citable", True) is True

    # 每个 doc 必须拥有连续不重复的单调自增数字 citation_id
    cids = [d["metadata"]["citation_id"] for d in all_docs]
    assert cids == list(range(1, len(all_docs) + 1))


def test_zero_kb_meta_direct_retains_grounding_evidence_under_real_gate():
    """P0 核心回归：在真实 evaluate_rules 门禁 (allow_knowledge_answer=False, empty_pool) 下，
    _agent_answer_docs 绝不置空 source_docs，Reviewer 必能拿到 Conversation 证据完成 Grounding。"""
    conv = _make_sample_conversation()
    pool = EvidencePool(question_id="zero-kb-pool")
    # 0 KB 证据时，evaluate_rules 返回 allow_knowledge_answer=False, reason="empty_pool"
    real_gate = evaluate_rules(conv, pool)
    assert real_gate["allow_knowledge_answer"] is False
    assert real_gate["reason"] == "empty_pool"

    composition = ComposeAnswerHandler(conv, pool).compose(answer_mode="full")
    snapshot = composition["evidence_snapshot"]

    result = AgentTurnResult(
        conversation=conv,
        evidence=pool,
        evidence_snapshot=snapshot,
        answer_gate=real_gate,
        direct_candidate="你刚才选择的是 PipelineBuilder。",
        terminal_action="controller_direct_candidate",
    )

    rag = RagChain.__new__(RagChain)
    rag._freeze_generation_source_docs = lambda docs: docs

    source_docs, retrieved_source_docs = rag._agent_answer_docs(result)

    # 1. 验证 Reviewer 使用的 source_docs 绝不为空（包含了会话历史），决不能因 allow_knowledge_answer=False 被清空
    assert len(source_docs) > 0
    assert any(d.get("metadata", {}).get("source_type") == "conversation" for d in source_docs)

    # 2. 验证推给前端检索栏的 retrieved_source_docs 严格为空
    assert retrieved_source_docs == []

    # 3. 找到记载用户选择记录的那条具体证据的 citation_id
    selection_doc = next(d for d in source_docs if "PipelineBuilder" in d.get("content", "") and "澄清交互" in d.get("content", ""))
    sel_cid = selection_doc["metadata"]["citation_id"]

    # 4. Reviewer 能够根据真实的 source_docs 判定 Candidate PASS
    def mock_reviewer_caller(messages: list[dict[str, str]]) -> dict[str, Any]:
        return {
            "coverage": "FULL",
            "summary": "依据会话历史事实核验通过",
            "claim_reviews": [
                {
                    "claim_id": "c1",
                    "claim": "你刚才选择的是 PipelineBuilder",
                    "claim_type": "knowledge_claim",
                    "claim_scope": "CONTEXTUAL_FACT",
                    "status": "supported",
                    "evidence_ids": [sel_cid],
                    "reason": "会话历史明确记录了选择项为 PipelineBuilder",
                }
            ],
            "rewrite_actions": [],
        }

    reviewer = HelperGroundingReviewer(mock_reviewer_caller)
    review_res = reviewer.review(conv.user_question, source_docs, result.direct_candidate)
    assert review_res.verdict == "PASS"


def test_conversation_evidence_never_upgrades_to_target_specific():
    """P0 核心回归：会话证据强制为 CONTEXT_ONLY，严禁通过 support_scope 升级成实体技术属性依据。"""
    conv = _make_sample_conversation()
    pool = EvidencePool(question_id="conv-matrix-pool")
    composition = ComposeAnswerHandler(conv, pool).compose(answer_mode="full")
    snapshot = composition["evidence_snapshot"]
    docs = snapshot.documents()

    # 确认所有 conversation 证据均为 CONTEXT_ONLY
    for d in docs:
        if d.get("metadata", {}).get("source_type") == "conversation":
            assert d["metadata"]["support_scope"] == "CONTEXT_ONLY"

    selection_doc = next(d for d in docs if "澄清交互" in d.get("content", ""))
    sel_cid = selection_doc["metadata"]["citation_id"]

    # 越权尝试：试图将“用户选择了 PipelineBuilder”用来证明“PipelineBuilder 支持高并发部署” (TARGET_ATTRIBUTION)
    def mock_illegal_upgrade_caller(messages):
        return {
            "coverage": "FULL",
            "summary": "越权支撑",
            "claim_reviews": [
                {
                    "claim_id": "c1",
                    "claim": "PipelineBuilder 支持高并发自动化部署",
                    "claim_type": "knowledge_claim",
                    "claim_scope": "TARGET_ATTRIBUTION",
                    "status": "supported",
                    "evidence_ids": [sel_cid],
                    "reason": "用户在会话中选了它，所以断言其具备该功能",
                }
            ],
            "rewrite_actions": [],
        }

    reviewer = HelperGroundingReviewer(mock_illegal_upgrade_caller)
    review_res = reviewer.review("PipelineBuilder 具备什么部署功能？", docs, "PipelineBuilder 支持高并发自动化部署。")
    # Python 协议矩阵必须硬拦截为 ERROR，并指出 matrix violation
    assert review_res.verdict == "ERROR"
    assert "claim_support_matrix_violation" in (review_res.error or "")


def test_focus_evidence_ids_preserves_consistent_citation_id_across_components():
    """P0/P1 核心回归：focus_evidence_ids 重排后，Answer Generator 看到的 [1] 与 Reviewer 看到的 1 绝对指向同一篇文档。"""
    conv = ConversationContext.from_request("StampServer 怎么用", [])
    pool = EvidencePool(question_id="focus-test")
    pool.add_retrieve(
        docs=[
            {
                "content": "知识库文档 A：介绍 Stamp 架构",
                "metadata": {
                    "chunk_id": "chunk_A",
                    "file_name": "arch.md",
                    "source_type": "kb_text",
                    "evidence_class": "TARGET_DIRECT",
                    "support_scope": "TARGET_SPECIFIC",
                },
            },
            {
                "content": "知识库文档 B：介绍 StampServer 部署流程",
                "metadata": {
                    "chunk_id": "chunk_B",
                    "file_name": "deploy.md",
                    "source_type": "kb_text",
                    "evidence_class": "TARGET_DIRECT",
                    "support_scope": "TARGET_SPECIFIC",
                },
            },
        ]
    )

    # 通过 focus_evidence_ids 强制让 chunk_B 排在最前
    snapshot = pool.create_snapshot(
        verdict={"coverage": "FULL", "can_answer": True},
        focus_evidence_ids=["chunk_B"],
    )

    docs = snapshot.documents()
    # 验证列表第 1 项为 chunk_B，其 citation_id 也绝对必须是 1
    assert docs[0]["metadata"]["chunk_id"] == "chunk_B"
    assert docs[0]["metadata"]["citation_id"] == 1

    # 验证列表第 2 项为 chunk_A，其 citation_id 也绝对必须是 2
    assert docs[1]["metadata"]["chunk_id"] == "chunk_A"
    assert docs[1]["metadata"]["citation_id"] == 2

    # 1. Answer Generator 看到的格式
    ans_ctx = AnswerGenerationContext.from_snapshot(
        original_question=conv.user_question,
        resolved_question=conv.user_question,
        conversation_context="",
        snapshot=snapshot,
    )
    ans_messages = build_answer_generation_messages(ans_ctx)
    user_prompt = ans_messages[1]["content"]
    # 验证 [1] 对应 chunk_B 的内容
    assert "[1] 来源: deploy.md" in user_prompt
    assert "[2] 来源: arch.md" in user_prompt

    # 2. Reviewer 看到的格式
    rev_formatted = format_evidence_snapshot(docs)
    assert rev_formatted[0]["evidence_id"] == 1
    assert "deploy.md" in rev_formatted[0]["source"]
    assert rev_formatted[1]["evidence_id"] == 2
    assert "arch.md" in rev_formatted[1]["source"]


def test_internal_evidence_blocked_from_sources_even_if_number_present():
    """P0 核心回归：前端 Sources 出口严防死守，即使回答中出现 [2] 等引用，citable=False 的非公开证据绝对不进入 Sources。"""
    conv = _make_sample_conversation()
    pool = EvidencePool(question_id="leak-test")
    pool.add_retrieve([
        {
            "content": "公开文档 1：知识库手册",
            "metadata": {
                "chunk_id": "chunk_kb_1",
                "file_name": "manual.md",
                "source_type": "kb_text",
                "evidence_class": "TARGET_DIRECT",
                "support_scope": "TARGET_SPECIFIC",
            },
        }
    ])
    grounding_docs = build_unified_grounding_docs(conv)
    snapshot = pool.create_snapshot(
        verdict={"coverage": "FULL", "can_answer": True},
        grounding_docs=grounding_docs,
    )
    docs = snapshot.documents()

    # 假设模型在回答中输出了引用编号 [2]（对应内部会话或运行事件）
    answer_text = "根据会话历史记录 [2]，您之前选择过组件。"

    trusted_sources = RagChain._filter_cited_sources(answer_text, docs)
    # 必须绝对过滤掉非 citable 证据，结果必须为空！
    assert trusted_sources == []

    # 只有当引用的是公开可引用的 [1] 时，才允许返回
    valid_answer = "根据手册说明 [1]，系统正常运行。"
    trusted_valid = RagChain._filter_cited_sources(valid_answer, docs)
    assert len(trusted_valid) == 1
    assert trusted_valid[0]["metadata"]["citation_id"] == 1
    assert trusted_valid[0]["metadata"]["chunk_id"] == "chunk_kb_1"


def test_cross_turn_clarification_history_restored_from_history():
    """P1 核心回归：前端与后端交互契约打通，从浏览器 history payload 自然恢复已确认的澄清交互事实。"""
    # 模拟前端 buildChatHistoryPayload 产出的带有 clarification 和 trace_id 的真实 history 列表
    history = [
        {"role": "user", "content": "帮我找一下构建工具"},
        {
            "role": "assistant",
            "content": "我们有多个构建工具，请选择：",
            "trace_id": "trace_round_1",
            "clarification": {
                "question": "请选择具体工具",
                "selected": "PipelineBuilder",
                "option_id": "opt_pipeline",
                "selection_kind": "candidate",
            },
        },
    ]

    # 第 N+1 轮提问：“我刚才选的是哪个？”
    conv = ConversationContext.from_request("我刚才选的是哪个？", history)

    # 验证澄清历史成功恢复
    assert len(conv.clarification_history) == 1
    assert conv.clarification_history[0]["selected"] == "PipelineBuilder"
    assert conv.clarification_history[0]["option_id"] == "opt_pipeline"

    # 验证生成的证据快照中真实包含该跨轮事实，且 support_scope 严格为 CONTEXT_ONLY
    grounding_docs = build_unified_grounding_docs(conv)
    clar_doc = next(d for d in grounding_docs if "澄清交互" in d["content"])
    assert "PipelineBuilder" in clar_doc["content"]
    assert clar_doc["metadata"]["selected_entity"] == "PipelineBuilder"
    assert clar_doc["metadata"]["support_scope"] == "CONTEXT_ONLY"


def test_cross_turn_runtime_event_store_truth_grounding(tmp_path):
    """P0 核心回归：上一轮真实提交到 QaTraceStore 的事件，通过 RuntimeEvidenceProvider 跨请求自动检索并注入快照。"""
    from types import SimpleNamespace
    from rag_knowledge.services.qa_trace import QaTraceStore
    from rag_knowledge.services.runtime_evidence_provider import RuntimeEvidenceProvider

    cfg = SimpleNamespace(
        data_dir=str(tmp_path / "data"),
        qa_trace=SimpleNamespace(retain_days=0, max_traces=0),
    )
    store = QaTraceStore(cfg)

    # 1. 模拟上一轮真实提交到 QaTraceStore 的 Trace（向用户弹出过 1 次卡片）
    prior_trace_id = "trace_prev_turn_999"
    payload = {
        "meta": {
            "trace_id": prior_trace_id,
            "created_at": "2026-09-03T12:00:00",
            "request_id": "req-1",
        },
        "request": {"question": "帮我配置管线"},
        "execution_events": [
            {
                "type": "clarification_card_published",
                "data": {"card_id": "c1", "ask_question": "请选择管线类型"},
                "step": 1,
            }
        ],
        "clarify": {
            "needs_clarification": True,
            "ask_question": "请选择管线类型",
        },
    }
    store.save(payload)

    # 2. 模拟真实下一轮提问与浏览器发送的 history（携带上一轮的 trace_id）
    history = [
        {"role": "user", "content": "帮我配置管线"},
        {
            "role": "assistant",
            "content": "请选择管线类型",
            "trace_id": prior_trace_id,
        },
    ]

    # 3. 通过 RuntimeEvidenceProvider 跨请求自动从 QaTraceStore 提取前序真实事件
    recovered_events = RuntimeEvidenceProvider.collect_events(
        history=history,
        cfg=cfg,
    )
    assert any(e.get("type") == "clarification_card_published" for e in recovered_events)

    # 4. 注入 AgentLoop 并组织回答快照。该问题明确询问系统刚才的行为，
    # 因此显式开启 Runtime Semantic Evidence。
    conv = ConversationContext.from_request("你刚才是不是问了我三次？", history)
    pool = EvidencePool(question_id="runtime-truth-test")

    composition = ComposeAnswerHandler(
        conv, pool, runtime_events=recovered_events, execution_steps=[]
    ).compose(answer_mode="full", include_runtime_semantics=True)

    snapshot = composition["evidence_snapshot"]
    docs = snapshot.documents()

    # 验证语义统计事实准确对账：实际向用户发布 1 次卡片。
    summary_doc = next(d for d in docs if d["metadata"]["evidence_id"] == "event:clarification_summary")
    assert "实际发布卡片=1次" in summary_doc["content"]

    summary_cid = summary_doc["metadata"]["citation_id"]

    # 真实诚实的回答：只问了 1 次 -> Reviewer 判定 PASS
    honest_answer = "我刚才只向您弹出过 1 次澄清卡片，并未询问三次。"

    def mock_honest_evaluator(messages):
        return {
            "coverage": "FULL",
            "summary": "与运行事实对账一致",
            "claim_reviews": [
                {
                    "claim_id": "c1",
                    "claim": "只向用户弹出过 1 次澄清卡片并未询问三次",
                    "claim_type": "knowledge_claim",
                    "claim_scope": "CONTEXTUAL_FACT",
                    "status": "supported",
                    "evidence_ids": [summary_cid],
                    "reason": "运行事件汇总记录卡片弹出次数为 1 次",
                }
            ],
            "rewrite_actions": [],
        }

    reviewer = HelperGroundingReviewer(mock_honest_evaluator)
    res_honest = reviewer.review(conv.user_question, docs, honest_answer)
    assert res_honest.verdict == "PASS"

    # 捏造谎言的回答：承认问了 3 次 -> Reviewer 判定矛盾 REVISE
    lying_answer = "是的，我刚才确实问了您三次。"

    def mock_lying_evaluator(messages):
        return {
            "coverage": "FULL",
            "summary": "回答与真实运行事实矛盾",
            "claim_reviews": [
                {
                    "claim_id": "c1",
                    "claim": "确实问了用户三次",
                    "claim_type": "knowledge_claim",
                    "claim_scope": "CONTEXTUAL_FACT",
                    "status": "contradicted",
                    "evidence_ids": [summary_cid],
                    "reason": "系统运行事件明确记录卡片弹出次数仅为 1 次，而非 3 次",
                }
            ],
            "rewrite_actions": [
                {
                    "claim_id": "c1",
                    "action": "correct_to_evidence",
                    "instruction": "纠正：系统仅向用户弹出过 1 次澄清卡片",
                }
            ],
        }

    reviewer_liar = HelperGroundingReviewer(mock_lying_evaluator)
    res_lying = reviewer_liar.review(conv.user_question, docs, lying_answer)
    assert res_lying.verdict == "REVISE"
    assert res_lying.claim_reviews[0].status == "contradicted"


def test_nonstream_agent_clarify_records_real_publication_event_and_returns_trace_id():
    """非流式 Agent clarify 必须与 SSE 分支对称：真实写事件，并把 trace_id 返回给客户端持久化。"""
    chain = object.__new__(RagChain)

    result = SimpleNamespace(
        conversation=SimpleNamespace(understanding=None, scope=None),
        plan=None,
        terminal_action="controller_clarify",
        route="clarify",
        clarify={
            "needs_clarification": True,
            "ask_question": "请选择组件",
            "clarification_snapshot_id": "snap_nonstream",
            "options": [{"id": "opt_1", "label": "PipelineBuilder", "filter": {}}],
        },
        to_trace=lambda: {"route": "clarify"},
    )

    async def fake_run_agent_turn(*_args, **_kwargs):
        return result

    chain._run_agent_turn = fake_run_agent_turn
    chain._commit_qa_trace = lambda *_args, **_kwargs: "trace_nonstream_clarify"

    class TraceStub:
        def __init__(self):
            self.execution_events: list[dict[str, Any]] = []

        def set_understanding(self, _value):
            return None

        def set_scope(self, _value):
            return None

        def set_plan(self, _value):
            return None

        def set_agent(self, _value):
            return None

        def set_clarify(self, _value):
            return None

        def record_execution_event(self, event):
            self.execution_events.append(dict(event))

    trace = TraceStub()
    output = asyncio.run(chain._aquery_agent(
        "构建工具",
        [],
        llm_model="fixture-model",
        kb_name=None,
        doc_category=None,
        entity_name=None,
        thinking=False,
        web_search=False,
        allow_general_knowledge=False,
        agent_prompt=None,
        include_evidence=False,
        clarification_question=None,
        clarification_selected=None,
        trace=trace,
    ))

    card_events = [e for e in trace.execution_events if e.get("type") == "clarification_card_published"]
    assert len(card_events) == 1
    assert card_events[0]["data"]["snapshot_id"] == "snap_nonstream"
    assert output["trace_id"] == "trace_nonstream_clarify"
    assert output["clarification"]["clarification_snapshot_id"] == "snap_nonstream"


def test_browser_contract_full_interaction_cycle_restores_evidence(tmp_path):
    """P0 终局回归：真实前端生命周期（请求 A 弹卡片 -> 用户点击 -> 请求 B 回调复用同一 assistant 消息 -> 保护 published_trace_id -> 追问请求 C 恢复事实）"""
    from types import SimpleNamespace
    from rag_knowledge.models.api import QueryRequest
    from rag_knowledge.services.qa_trace import QaTraceStore
    from rag_knowledge.services.runtime_evidence_provider import RuntimeEvidenceProvider

    cfg = SimpleNamespace(
        data_dir=str(tmp_path / "data"),
        qa_trace=SimpleNamespace(retain_days=0, max_traces=0),
    )
    store = QaTraceStore(cfg)

    # 1. 请求 A：系统决定 clarify，发布澄清卡片，提交 Trace A
    trace_a = "trace_A_publish_card"
    store.save({
        "meta": {"trace_id": trace_a, "created_at": "2026-09-03T12:00:00"},
        "request": {"question": "构建"},
        "execution_events": [
            {
                "type": "clarification_card_published",
                "data": {"card_id": "c_init", "ask_question": "请选组件"},
                "step": 1,
            }
        ],
        "clarify": {"needs_clarification": True, "ask_question": "请选组件"},
    })

    # 模拟前端处理请求 A 的 SSE 回调：
    # 收到 onClarify 与 onTrace(trace_a)
    assistant_msg = {
        "id": "msg_ai_1",
        "role": "assistant",
        "content": "",
        "trace_id": trace_a,
        "clarification": {
            "ask_question": "请选组件",
            "clarification_snapshot_id": "snap_101",
            "published_trace_id": trace_a,
            "options": [
                {"id": "opt_pipeline", "label": "PipelineBuilder"},
                {"id": "other", "label": "以上都不是"},
            ],
        },
    }

    # 2. 用户点击 "PipelineBuilder"，前端复用同一个 assistant_msg 发起请求 B (callback)
    assistant_msg["clarification"]["selectedId"] = "opt_pipeline"

    # 请求 B 执行完毕：生成正式回答并提交 Trace B（没有发布新卡片，clarify.needs_clarification 即使为 True 也不含卡片发布事件）
    trace_b = "trace_B_callback_answer"
    store.save({
        "meta": {"trace_id": trace_b, "created_at": "2026-09-03T12:00:05"},
        "request": {"question": "构建", "clarification_selected": "PipelineBuilder"},
        "execution_events": [
            {
                "type": "tool_result",
                "data": {"name": "retrieve_kb", "step": 1},
                "step": 1,
            }
        ],
        "clarify": {"needs_clarification": True, "selected": "PipelineBuilder"},
    })

    # 模拟前端处理请求 B 的 onTrace 回调：
    # 按照 ChatView.vue 的新逻辑：targetMsg.trace_id = trace_b，published_trace_id 保持不变，response_trace_id = trace_b
    assistant_msg["trace_id"] = trace_b
    if not assistant_msg["clarification"].get("published_trace_id"):
        assistant_msg["clarification"]["published_trace_id"] = trace_b
    elif assistant_msg["clarification"].get("selectedId"):
        assistant_msg["clarification"]["response_trace_id"] = trace_b
    assistant_msg["content"] = "已为您检索并配置 PipelineBuilder。"
    assistant_msg["sources"] = [
        {"source": "pipeline.md", "chunk_id": "chunk_p", "citation_id": 1, "preview": "PipelineBuilder 指南"}
    ]

    # 验证前端核心 Provenance：published_trace_id 绝对没有被 trace_b 覆盖！
    assert assistant_msg["clarification"]["published_trace_id"] == trace_a
    assert assistant_msg["clarification"]["response_trace_id"] == trace_b
    assert assistant_msg["trace_id"] == trace_b

    # 3. 用户在下一轮提问（请求 C）：“我们刚才确认的是哪个组件？你刚才问了我几次？”
    # 前端调用 buildChatHistoryPayload 序列化 history
    selected_opt = next(o for o in assistant_msg["clarification"]["options"] if o["id"] == assistant_msg["clarification"]["selectedId"])
    history_item = {
        "role": assistant_msg["role"],
        "content": assistant_msg["content"],
        "trace_id": assistant_msg["trace_id"],
        "clarification": {
            "question": assistant_msg["clarification"]["ask_question"],
            "selected": selected_opt["label"],
            "option_id": assistant_msg["clarification"]["selectedId"],
            "snapshot_id": assistant_msg["clarification"]["clarification_snapshot_id"],
            "selection_kind": "candidate",
            "published_trace_id": assistant_msg["clarification"]["published_trace_id"],
            "response_trace_id": assistant_msg["clarification"]["response_trace_id"],
        },
        "sources": [{"file_name": "pipeline.md", "chunk_id": "chunk_p", "citation_id": 1}],
    }
    client_payload = {
        "question": "我们刚才确认的是哪个组件？你刚才向我确认了几次？",
        "history": [
            {"role": "user", "content": "帮我构建"},
            history_item,
        ],
    }

    # 4. 后端接收 QueryRequest 并解析
    query_req = QueryRequest(**client_payload)
    history_dicts = [h.model_dump() for h in query_req.history]
    conv = ConversationContext.from_request(query_req.question, history_dicts)
    assert len(conv.clarification_history) == 1
    assert conv.clarification_history[0]["selected"] == "PipelineBuilder"

    # 5. RuntimeEvidenceProvider 同时检索 trace_a (卡片发布) 与 trace_b (回调执行)
    recovered_events = RuntimeEvidenceProvider.collect_events(history=history_dicts, cfg=cfg)

    # 严格断言：
    # - 真实存在的卡片发布事件（来自 trace_a）被准确收集（刚好 1 次）
    # - trace_b 没有发布新卡片，Provider 绝没有臆造或把事件算在 trace_b 头上！
    card_events = [e for e in recovered_events if e.get("type") == "clarification_card_published"]
    assert len(card_events) == 1
    assert card_events[0]["data"]["ask_question"] == "请选组件"

    # 6. 组织快照并核验。用户追问包含刚才的交互行为，因此显式开启运行事实。
    pool = EvidencePool(question_id="browser-full-lifecycle-test")
    composition = ComposeAnswerHandler(
        conv, pool, runtime_events=recovered_events, execution_steps=[]
    ).compose(answer_mode="full", include_runtime_semantics=True)

    snapshot = composition["evidence_snapshot"]
    docs = snapshot.documents()

    # 严格对账：
    # 会话事实：包含 PipelineBuilder
    assert any("PipelineBuilder" in d["content"] for d in docs)
    # 运行事实：精确记录实际发布卡片=1次
    summary_doc = next(d for d in docs if d["metadata"]["evidence_id"] == "event:clarification_summary")
    assert "实际发布卡片=1次" in summary_doc["content"]

    # 诚实回答通过 Reviewer 核验
    honest_answer = "我们刚才确认的组件是 PipelineBuilder，在此之前我只向您弹出过 1 次澄清卡片。"
    summary_cid = summary_doc["metadata"]["citation_id"]

    def mock_honest_evaluator(messages):
        return {
            "coverage": "FULL",
            "summary": "回答与会话历史及系统事件对账一致",
            "claim_reviews": [
                {
                    "claim_id": "c1",
                    "claim": "确认组件为 PipelineBuilder 且仅弹出过 1 次卡片",
                    "claim_type": "knowledge_claim",
                    "claim_scope": "CONTEXTUAL_FACT",
                    "status": "supported",
                    "evidence_ids": [summary_cid],
                    "reason": "会话历史与运行事实汇总一致",
                }
            ],
            "rewrite_actions": [],
        }

    reviewer = HelperGroundingReviewer(mock_honest_evaluator)
    res = reviewer.review(conv.user_question, docs, honest_answer)
    assert res.verdict == "PASS"

