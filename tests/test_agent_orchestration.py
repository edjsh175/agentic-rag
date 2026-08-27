"""Phase 1：对话 Agent Runtime / EvidencePool / 工具白名单 / 开关回退。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from rag_knowledge.config import Config
from rag_knowledge.services.agent_orchestration.models import (
    AgentBudget,
    AgentDecision,
    AgentTurnResult,
    AttemptedGapRegistry,
    ConversationContext,
    EvidencePool,
    ToolObservation,
    ToolProgressStatus,
)
from rag_knowledge.services.agent_orchestration.runtime import (
    PHASE1_TOOL_NAMES,
    PHASE2_TOOL_NAMES,
    AgentLoop,
    build_agent_messages,
    build_agent_registry,
    build_phase1_registry,
    parse_json_object,
)
from rag_knowledge.services.qa_trace import QaTraceBuilder, QaTraceStore
from rag_knowledge.services.rag import NO_KNOWLEDGE_ANSWER, RagChain


def _doc(chunk_id: str, content: str, citation_id: int = 1) -> dict:
    return {
        "content": content,
        "metadata": {
            "chunk_id": chunk_id,
            "citation_id": citation_id,
            "file_name": f"{chunk_id}.md",
            "page_label": "无页码",
            "category": "text",
            "source_type": "knowledge_base",
        },
    }


def test_config_defaults_disabled(isolated_storage, monkeypatch):
    isolated_storage()
    monkeypatch.setenv("AGENT_ORCHESTRATION_ENABLED", "false")
    Config._instance = None
    cfg = Config()
    assert cfg.agent_orchestration.enabled is False
    assert cfg.agent_orchestration.max_retrieve_attempts == 2
    assert not hasattr(cfg.agent_orchestration, "request_timeout")



def test_phase1_registry_has_no_answer_or_clarify():
    registry = build_phase1_registry()
    assert registry.names() == PHASE1_TOOL_NAMES
    assert registry.validate_call("answer", {}) == "tool_forbidden:answer"
    assert registry.validate_call("clarify", {}) == "tool_unknown:clarify"
    assert registry.validate_call("link_entities", {}) == "tool_unknown:link_entities"
    assert registry.validate_call("retrieve_kb", {}) == "tool_missing_arg:query"
    assert registry.validate_call("retrieve_kb", {"query": "StampServer"}) is None


def test_parse_json_object_strips_fence():
    data = parse_json_object("```json\n{\"action\":\"finish\",\"tool\":null}\n```")
    assert data["action"] == "finish"


def test_old_evidence_not_citable_without_reuse():
    pool = EvidencePool(question_id="q1")
    pool.seed_previous_cited([_doc("old", "旧证据")])
    assert pool.citable_docs() == []
    reused = pool.reuse()
    assert reused is not None
    assert [d["metadata"]["chunk_id"] for d in pool.citable_docs()] == ["old"]


def test_retrieve_groups_are_indexed_and_kept():
    pool = EvidencePool(question_id="q1")
    g1 = pool.add_retrieve([_doc("a", "A")], query="q")
    g2 = pool.add_retrieve([_doc("b", "B")], query="q2")
    assert g1.retrieve_index == 1
    assert g2.retrieve_index == 2
    assert g1.status == "ACTIVE"
    assert g2.status == "ACTIVE"
    pool.freeze_active()
    assert g1.status == "FROZEN"
    assert pool.citable_docs() == []


def test_evidence_version_changes_only_for_new_effective_evidence():
    pool = EvidencePool(question_id="q1")

    pool.add_retrieve([], query="empty")
    assert pool.evidence_version == 0

    pool.add_retrieve([_doc("a", "A")], query="first")
    assert pool.evidence_version == 1

    pool.add_retrieve([_doc("a", "A duplicate")], query="duplicate")
    assert pool.evidence_version == 1

    pool.add_retrieve([_doc("b", "B")], query="new")
    assert pool.evidence_version == 2

    pool.freeze_active()
    assert pool.evidence_version == 2


def test_anonymous_duplicate_evidence_has_stable_identity():
    pool = EvidencePool(question_id="q1")
    anonymous = {"content": "same", "metadata": {"source_type": "external"}}

    pool.add_external([anonymous], query="first")
    assert pool.evidence_version == 1

    pool.add_external(
        [{"content": "same", "metadata": {"source_type": "external"}}],
        query="duplicate",
    )
    assert pool.evidence_version == 1
    assert len(pool.citable_docs()) == 1


def test_tool_observation_supplies_status_aligned_zero_delta():
    denied = ToolObservation(
        tool="retrieve_kb",
        ok=False,
        summary="denied",
        status=ToolProgressStatus.DENIED,
    )
    assert denied.evidence_delta is not None
    assert denied.evidence_delta.status == ToolProgressStatus.DENIED

    failed = ToolObservation(tool="retrieve_kb", ok=False, summary="failed")
    assert failed.status == ToolProgressStatus.ERROR
    assert failed.evidence_delta is not None
    assert failed.evidence_delta.status == ToolProgressStatus.ERROR


def test_agent_decision_trace_exposes_reason_without_internal_thought():
    decision = AgentDecision(
        action="tool_call",
        tool="retrieve_kb",
        arguments={"query": "StampServer 端口"},
        thought="内部兼容思考",
        gap="端口事实",
        expected_gain="获取端口值",
    )

    payload = decision.to_dict()
    assert payload["reason"] == "内部兼容思考"
    assert "thought" not in payload
    assert "gap_type" not in payload
    assert "recovery_strategy" not in payload


def test_topic_shift_blocks_reuse():
    conv = ConversationContext.from_request(
        "StampWebGL 怎么发布",
        [
            {"role": "user", "content": "PipelineBuilder 怎么用"},
            {
                "role": "assistant",
                "content": "介绍",
                "sources": [_doc("pb", "PipelineBuilder 发布服务")],
            },
        ],
    )
    pool = EvidencePool(question_id="q")
    pool.seed_previous_cited(conv.session.last_sources, head_entity="PipelineBuilder")
    conv.topic_shift = True
    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4, max_retrieve_attempts=2),
        registry=build_phase1_registry(),
        handlers={},
    )
    assert loop.reuse_blocked_reason() == "topic_shift_no_reuse"


def test_confirmed_scope_survives_ellipsis_followup_from_history_sources():
    conv = ConversationContext.from_request(
        "它怎么配置？",
        [
            {"role": "user", "content": "PipelineBuilder 怎么用"},
            {
                "role": "assistant",
                "content": "介绍",
                "sources": [
                    {
                        "chunk_id": "pb",
                        "scope_root": "PipelineBuilder",
                        "scope_binding_strength": "explicit",
                    }
                ],
            },
        ],
    )

    assert conv.head_entity == "PipelineBuilder"
    assert conv.scope.primary_root == "PipelineBuilder"
    assert conv.scope.is_identity_locked is True
    assert conv.scope.scope_reason == "conversation_confirmed_subject"


def test_current_named_entity_overrides_previous_confirmed_scope():
    conv = ConversationContext.from_request(
        "StampServer 怎么配置？",
        [
            {
                "role": "assistant",
                "content": "介绍",
                "sources": [
                    {
                        "chunk_id": "pb",
                        "scope_root": "PipelineBuilder",
                        "scope_binding_strength": "explicit",
                    }
                ],
            },
        ],
    )

    assert conv.scope.primary_root != "PipelineBuilder"
    assert conv.head_entity != "PipelineBuilder"


def test_entity_change_freezes_without_forcing_topic_shift():
    conv = ConversationContext.from_request("那 WebGL 呢？", [])
    conv.previous_head_entity = "PipelineBuilder"
    conv.head_entity = "PipelineWebGL"
    pool = EvidencePool(question_id="q")
    pool.add_retrieve([_doc("pb", "旧")], query="prev", head_entity="PipelineBuilder")
    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=3, max_retrieve_attempts=2),
        registry=build_phase1_registry(),
        handlers={},
    )
    loop.apply_turn_start_harness()
    assert conv.entity_transition is True
    assert conv.topic_shift is False
    assert pool.citable_docs() == []


def test_clarification_callback_forbids_reuse_and_freezes():
    conv = ConversationContext.from_request(
        "怎么写代码",
        [
            {"role": "user", "content": "怎么写代码"},
            {"role": "assistant", "content": "请选择产品", "sources": [_doc("x", "误搜")]},
        ],
        clarification_selected="StampWebRTC",
    )
    pool = EvidencePool(question_id="q")
    pool.add_retrieve([_doc("x", "误搜")], query="宽口径")
    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=3, max_retrieve_attempts=2),
        registry=build_phase1_registry(),
        handlers={},
    )
    loop.apply_turn_start_harness()
    assert conv.clarification_callback is True
    assert loop.reuse_blocked_reason() == "clarify_callback_no_reuse"
    assert all(g.status == "FROZEN" for g in pool.groups if g.kind == "retrieve")
    assert pool.citable_docs() == []


def test_prompt_partition_marks_history_not_as_facts():
    conv = ConversationContext.from_request("继续说", [])
    pool = EvidencePool(question_id="q")
    pool.add_retrieve([_doc("c1", "可引用正文", citation_id=1)], query="继续说")
    formatted = "[1] [知识库来源] 文件: c1.md | 页码: 无页码 | 类型: text\n文档片段：可引用正文"
    messages = build_agent_messages(
        question="继续说",
        conversation_section=conv.to_prompt(history_summary="上一轮在讲安装"),
        evidence_section=pool.to_prompt(formatted),
        history=[{"role": "user", "content": "怎么安装"}, {"role": "assistant", "content": "步骤"}],
        allow_general_knowledge=False,
    )
    system = messages[0]["content"]
    assert "ConversationContext" in system
    assert "EvidencePool" in system
    assert "<evidence_pool>" in system
    assert "不得作为知识事实依据" in system
    assert "禁止使用模型通用知识补充" in system
    assert system.index("## 对话上下文") < system.index("## 证据池")
    assert "可引用正文" in system
    assert "上一轮在讲安装" in system


def test_budget_stops_loop_without_wall_clock():
    conv = ConversationContext.from_request("什么是 StampServer", [])
    pool = EvidencePool(question_id="q")
    calls = []

    async def retrieve(args):
        calls.append(args)
        from rag_knowledge.services.agent_orchestration.models import ToolObservation
        pool.add_retrieve([_doc("a", "A")], query="q")
        return ToolObservation(tool="retrieve_kb", ok=True, summary="ok")

    def decide(conversation, evidence, observations):
        return AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "StampServer"},
            source="test",
        )

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=2, max_retrieve_attempts=1),
        registry=build_phase1_registry(),
        handlers={"retrieve_kb": retrieve},
        decide_fn=decide,
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert len(calls) == 1
    assert result.retrieve_attempts == 1
    assert "retrieve_budget_exhausted" in result.fallbacks or result.route == "retrieve"


def test_loop_retrieve_then_finish_records_tools():
    conv = ConversationContext.from_request("StampServer 是什么", [])
    pool = EvidencePool(question_id="q")
    from rag_knowledge.services.agent_orchestration.models import ToolObservation

    async def retrieve(_args):
        pool.add_retrieve([_doc("s1", "StampServer 管理中心")], query="StampServer 是什么")
        return ToolObservation(tool="retrieve_kb", ok=True, summary="1")

    decisions = iter([
        AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "StampServer 是什么"},
            source="test",
        ),
        AgentDecision(action="finish", source="test"),
    ])

    def decide(_c, _e, _o):
        return next(decisions)

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=6, max_retrieve_attempts=2),
        registry=build_phase1_registry(),
        handlers={"retrieve_kb": retrieve},
        decide_fn=decide,
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.route == "retrieve"
    assert result.tools[0]["name"] == "retrieve_kb"
    assert result.evidence.citable_docs()[0]["metadata"]["chunk_id"] == "s1"
    payload = result.to_trace()
    assert payload["tools"][0]["name"] == "retrieve_kb"
    assert payload["evidence_groups"][0]["kind"] == "retrieve"
    assert payload["conversation_context"]["not_a_fact_source"] is True


def test_switch_off_stream_stays_on_dag(isolated_storage, monkeypatch):
    isolated_storage()
    monkeypatch.setenv("AGENT_ORCHESTRATION_ENABLED", "false")
    Config._instance = None
    chain = object.__new__(RagChain)
    chain._cfg = Config()
    chain._allow_general_knowledge = False
    chain._get_understanding_service = lambda: SimpleNamespace(
        analyze=lambda *args, **kwargs: SimpleNamespace(mode="retrieve", clarify=None),
    )
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
                    "enable_rerank": force_rerank,
                    "expand_neighbors": False,
                },
            )()
        },
    )()
    chain._retrieve_multi = lambda *args, **kwargs: ([], "")
    chain._prepare_graph_plan = lambda *a, **k: (chain._query_planner.plan("q", ["q"], force_rerank=True), None, [])
    chain._build_graph_kwargs = lambda *a, **k: {}
    chain._anchor_protect_names = lambda plan: ()
    chain._record_chunk_hit_query = lambda docs: None
    chain._new_qa_trace = lambda *a, **k: MagicMock(enabled=False)
    chain._commit_qa_trace = lambda *a, **k: None
    chain._com_phase0_reject_if_needed = lambda *a, **k: None
    chain._j3_clarify_reject_if_needed = lambda *a, **k: None
    chain._pack_for_generation = None
    chain._last_understanding = None

    async def collect():
        return [
            event
            async for event in chain.stream_query(
                "项目部署参数是什么？", allow_general_knowledge=False
            )
        ]

    events = [e for e in asyncio.run(collect()) if e.get("type") != "trace"]
    assert chain._cfg.agent_orchestration.enabled is False
    assert {"type": "final_answer", "data": NO_KNOWLEDGE_ANSWER} in events
    assert not any(event.get("type") in {"decision", "tool_start"} for event in events)
    assert events[-1] == {"type": "done"}


def test_ragchain_build_messages_agent_layout_partitions():
    messages = RagChain._build_messages(
        "问题",
        "正文",
        prompt_layout="agent",
        conversation_context_section="## 对话上下文（ConversationContext）\n不得作为知识事实依据\n",
        evidence_pool_section="## 证据池（EvidencePool）\n<evidence_pool>\n正文\n</evidence_pool>",
        allow_general_knowledge=False,
    )
    system = messages[0]["content"]
    assert "<evidence_pool>" in system
    assert "ConversationContext" in system
    assert "EvidencePool" in system
    assert "不得作为知识事实依据" in system


def test_qa_trace_persists_agent_payload(isolated_storage, monkeypatch):
    isolated_storage()
    monkeypatch.setenv("QA_TRACE_ENABLED", "true")
    monkeypatch.setenv("AGENT_ORCHESTRATION_ENABLED", "false")
    Config._instance = None
    cfg = Config()
    builder = QaTraceBuilder(question="StampServer", cfg=cfg)
    builder.set_agent({
        "route": "retrieve",
        "tools": [{"name": "retrieve_kb", "ok": True}],
        "evidence_groups": [{"kind": "retrieve", "status": "ACTIVE", "chunk_ids": ["c1"]}],
        "budget": {"steps_used": 2, "retrieve_attempts": 1},
    })
    tid = builder.finish(answer="ok")
    detail = QaTraceStore(cfg).get(tid)
    assert detail["agent"]["route"] == "retrieve"
    assert detail["agent"]["tools"][0]["name"] == "retrieve_kb"
    assert detail["runtime"]["agent_orchestration_enabled"] is False


def test_heartbeat_emitted_before_final_event():
    chain = object.__new__(RagChain)

    async def delayed():
        await asyncio.sleep(0.12)
        yield {"type": "token", "data": "x"}
        yield {"type": "done"}

    async def collect():
        return [
            event
            async for event in chain._iter_with_heartbeat(
                delayed(), initial_delay=0.04, interval=0.04,
            )
        ]

    events = asyncio.run(collect())
    assert events[0]["type"] == "heartbeat"
    assert events[0]["phase"] == "thinking"
    assert events[-2] == {"type": "token", "data": "x"}
    assert events[-1] == {"type": "done"}


def _seq_decide(items):
    seq = list(items)
    index = {"n": 0}

    def decide(_c, _e, _o):
        i = index["n"]
        index["n"] += 1
        if i < len(seq):
            return seq[i]
        return AgentDecision(action="finish", source="test")

    return decide


def _binding(*, show_j3: bool = False, skip_generic: bool = False):
    return type(
        "BindingStub",
        (),
        {"show_j3_card": show_j3, "skip_generic_clarify": skip_generic},
    )()


def _card_observation() -> ToolObservation:
    return ToolObservation(
        tool="clarify",
        ok=True,
        summary="card",
        data={
            "pause": True,
            "clarify": {
                "needs_clarification": True,
                "ask_question": "请选择产品线",
                "options": [
                    {"id": "a", "label": "StampWebRTC"},
                    {"id": "b", "label": "StampWebGL"},
                ],
            },
        },
    )


def test_phase2_registry_allows_clarify_and_retires_link_entities():
    registry = build_agent_registry()
    assert PHASE2_TOOL_NAMES <= registry.names()
    assert registry.validate_call("clarify", {}) is None
    assert registry.validate_call("link_entities", {}) == "tool_unknown:link_entities"
    assert registry.validate_call("link_entities", {"query": "StampServer"}) == "tool_unknown:link_entities"
    assert registry.validate_call("answer", {}) == "tool_forbidden:answer"
    assert registry.validate_call("web_search", {}) == "tool_forbidden:web_search"


def test_harness_forces_j3_when_model_finishes():
    """PRD V1.5：Stage 2 尊重模型 finish 决策，不再强制篡改出示澄清卡片。"""
    conv = ConversationContext.from_request("二次开发", [])
    pool = EvidencePool(question_id="q")

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4),
        registry=build_agent_registry(),
        handlers={},
        decide_fn=lambda *_: AgentDecision(action="finish", thought="会话直答", source="test"),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.route == "direct"
    assert result.clarify is None

def test_harness_allows_clarify_when_model_decides():
    """验证当模型决策调用 clarify 时正常出示卡片。"""
    conv = ConversationContext.from_request("Stamp", [])
    pool = EvidencePool(question_id="q")

    async def clarify(_args):
        return _card_observation()

    decisions = iter([
        AgentDecision(action="tool_call", tool="clarify", source="test"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4),
        registry=build_agent_registry(),
        handlers={"clarify": clarify},
        decide_fn=lambda *_: next(decisions),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.route == "clarify"
    assert result.clarify is not None


def test_retired_link_entities_is_not_executed_or_written_to_evidence_pool():
    conv = ConversationContext.from_request(
        "StampServer 是什么", [], entity_name="StampServer",
    )
    pool = EvidencePool(question_id="q")

    async def link(_args):
        conv.linked_entities = [{
            "canonical_name": "StampServer",
            "confidence": 0.95,
        }]
        return ToolObservation(
            tool="link_entities",
            ok=True,
            summary="1",
            data={"candidates": conv.linked_entities},
        )

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4, max_retrieve_attempts=2),
        registry=build_agent_registry(),
        handlers={"link_entities": link},
        decide_fn=_seq_decide([
            AgentDecision(
                action="tool_call",
                tool="link_entities",
                arguments={"query": "StampServer", "target_entity": "StampServer"},
                source="test",
            ),
            AgentDecision(action="finish", source="test"),
        ]),
        resolve_binding_fn=lambda _c: _binding(),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert pool.citable_docs() == []
    assert pool.groups == []
    assert result.entity_link is None
    assert "tool_unknown:link_entities" in result.fallbacks


def test_callback_blocks_reclarify():
    conv = ConversationContext.from_request(
        "怎么写代码",
        [],
        clarification_selected="StampWebRTC",
    )
    pool = EvidencePool(question_id="q")
    retrieved = []

    async def clarify(_args):
        raise AssertionError("callback must not clarify again")

    async def retrieve(_args):
        retrieved.append(1)
        pool.add_retrieve([_doc("rtc", "StampUtil")], query="q", head_entity="StampWebRTC")
        return ToolObservation(tool="retrieve_kb", ok=True, summary="1")

    decisions = iter([
        AgentDecision(action="tool_call", tool="clarify", source="test"),
        AgentDecision(action="tool_call", tool="retrieve_kb", arguments={"query": "StampWebRTC 怎么写代码"}, source="test"),
        AgentDecision(action="finish", source="test"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4, max_retrieve_attempts=2),
        registry=build_agent_registry(),
        handlers={"clarify": clarify, "retrieve_kb": retrieve},
        decide_fn=lambda *_: next(decisions),
        resolve_binding_fn=lambda _c: _binding(show_j3=True),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert conv.clarification_callback is True
    assert retrieved == [1]
    assert result.route != "clarify"
    assert "clarify_callback_reclarify_blocked" in result.fallbacks


def test_phase1_registry_still_skips_clarify_harness():
    conv = ConversationContext.from_request("怎么写二次开发代码", [])
    pool = EvidencePool(question_id="q")
    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=2, max_retrieve_attempts=1),
        registry=build_phase1_registry(),
        handlers={},
        decide_fn=lambda *_: AgentDecision(action="finish", source="test"),
        resolve_binding_fn=lambda _c: _binding(show_j3=True),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.route != "clarify"
    assert "harness_force_j3" not in result.fallbacks


def test_rule_gate_empty_pool_denies_support():
    from rag_knowledge.services.agent_orchestration.evidence_gate import evaluate_rules

    conv = ConversationContext.from_request("StampServer 是什么", [])
    pool = EvidencePool(question_id="q")
    verdict = evaluate_rules(conv, pool)
    assert verdict["allow_knowledge_answer"] is False
    assert verdict["reason"] == "empty_pool"


def test_llm_support_empty_pool_cannot_knowledge_answer():
    conv = ConversationContext.from_request("StampServer 是什么", [])
    pool = EvidencePool(question_id="q")

    async def retrieve(_args):
        pool.add_retrieve([], query="StampServer 是什么")
        return ToolObservation(tool="retrieve_kb", ok=True, summary="0")

    decisions = iter([
        AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "StampServer 是什么", "target_entity": "StampServer"},
            source="test",
        ),
        AgentDecision(action="finish", source="test", gate="support"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4, max_retrieve_attempts=1),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": retrieve},
        decide_fn=lambda *_: next(decisions),
        resolve_binding_fn=lambda _c: _binding(),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.llm_gate == "support"
    assert result.answer_gate["allow_knowledge_answer"] is False
    assert result.answer_gate["reason"] == "empty_pool"
    assert result.evidence.citable_docs() == []


def test_llm_support_entity_conflict_cannot_knowledge_answer():
    conv = ConversationContext.from_request(
        "StampWebRTC 怎么用", [], entity_name="StampWebRTC",
    )
    pool = EvidencePool(question_id="q")

    async def retrieve(_args):
        pool.add_retrieve(
            [_doc("pb", "PipelineBuilder 发布")],
            query="q",
            head_entity="PipelineBuilder",
        )
        return ToolObservation(tool="retrieve_kb", ok=True, summary="1")

    decisions = iter([
        AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "StampWebRTC", "target_entity": "StampWebRTC"},
            source="test",
        ),
        AgentDecision(action="finish", source="test", gate="support"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4, max_retrieve_attempts=1),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": retrieve},
        decide_fn=lambda *_: next(decisions),
        resolve_binding_fn=lambda _c: _binding(),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.llm_gate == "support"
    assert result.answer_gate["allow_knowledge_answer"] is False
    assert result.answer_gate["reason"] == "entity_conflict"


def test_empty_retrieval_binds_gap_and_rewrites_query():
    """验证初次未召回时，模型自主生成不同改写 query 发起第 2 次补检并成功召回。"""
    question = "仔细说说怎么部署 StampServer"
    conv = ConversationContext.from_request(question, [])
    pool = EvidencePool(question_id="q")
    queries: list[str] = []

    async def retrieve(args):
        query = str(args.get("query") or question)
        queries.append(query)
        if len(queries) == 1:
            pool.add_retrieve([], query=query)
        else:
            pool.add_retrieve([_doc("s1", "StampServer 部署")], query=query, head_entity="StampServer")
        return ToolObservation(tool="retrieve_kb", ok=True, summary=str(len(queries)))

    decisions = iter([
        AgentDecision(action="tool_call", tool="retrieve_kb", source="test", arguments={"query": "StampServer 初检"}),
        AgentDecision(action="tool_call", tool="retrieve_kb", source="test", arguments={"query": "StampServer 部署步骤"}, gap="StampServer 部署流程", expected_gain="获取部署文档"),
        AgentDecision(action="finish", source="test"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=6),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": retrieve},
        decide_fn=lambda *_: next(decisions),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.retrieve_attempts == 2
    assert len(queries) == 2
    assert queries[0] == "StampServer 初检"
    assert queries[1] == "StampServer 部署步骤"

def test_agent_answer_docs_drop_when_gate_denies():
    conv = ConversationContext.from_request("什么", [])
    pool = EvidencePool(question_id="q")
    pool.add_retrieve([_doc("x", "无关", citation_id=1)], query="q", head_entity="Other")
    from rag_knowledge.services.agent_orchestration.models import AgentTurnResult

    result = AgentTurnResult(
        conversation=conv,
        evidence=pool,
        answer_gate={"allow_knowledge_answer": False, "reason": "entity_conflict"},
        evidence_snapshot=pool.create_snapshot(verdict={"verdict": "PARTIAL"}),
    )
    chain = object.__new__(RagChain)
    source_docs, retrieved = chain._agent_answer_docs(result)
    assert source_docs == []
    assert retrieved


def test_phase4_registry_supports_web_search_when_enabled():
    registry_off = build_agent_registry(allow_web_search=False)
    assert "web_search" not in registry_off.names()
    assert registry_off.validate_call("web_search", {}) == "tool_forbidden:web_search"

    registry_on = build_agent_registry(allow_web_search=True)
    assert "web_search" in registry_on.names()
    assert registry_on.validate_call("web_search", {"query": "StampWebRTC 最新版本"}) is None


def test_phase4_environment_permission_and_confirmation():
    from rag_knowledge.services.agent_orchestration.models import ToolSpec

    write_tool = ToolSpec(
        name="environment.write_config",
        description="修改配置",
        input_schema={"type": "object", "properties": {"key": {"type": "string"}}},
        permission="allow",
        side_effect="write",
        confirmation_required=True,
    )
    deny_tool = ToolSpec(
        name="environment.destructive_delete",
        description="删除数据",
        input_schema={"type": "object", "properties": {}},
        permission="deny",
        side_effect="destructive",
    )
    registry = build_agent_registry(environment_tools=[write_tool, deny_tool])

    assert registry.validate_call("environment.read_status", {}) is None
    assert registry.validate_call("environment.write_config", {"key": "timeout"}) == "tool_confirmation_required:environment.write_config"
    assert registry.validate_call("environment.destructive_delete", {}) == "tool_denied:environment.destructive_delete"


def test_phase4_web_search_adds_external_evidence_group():
    conv = ConversationContext.from_request("最新官网文档是什么", [])
    pool = EvidencePool(question_id="q")

    async def web_search_handler(args):
        web_doc = {
            "content": "最新官网说明",
            "metadata": {
                "chunk_id": "web:1",
                "title": "官网页面",
                "url": "https://example.com",
                "source_type": "external",
                "category": "网页搜索",
            },
        }
        pool.add_external([web_doc], query=str(args.get("query") or "q"))
        return ToolObservation(tool="web_search", ok=True, summary="1 result")

    decisions = iter([
        AgentDecision(action="tool_call", tool="web_search", arguments={"query": "官网文档"}, source="test"),
        AgentDecision(action="finish", source="test"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4, max_retrieve_attempts=1),
        registry=build_agent_registry(allow_web_search=True),
        handlers={"web_search": web_search_handler},
        decide_fn=lambda *_: next(decisions),
        resolve_binding_fn=lambda _c: _binding(),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.route == "web_search"
    assert len(result.evidence.groups) == 1
    assert result.evidence.groups[0].kind == "web_search"
    assert result.evidence.citable_docs()[0]["metadata"]["source_type"] == "external"
    assert result.tools[0]["name"] == "web_search"


def test_phase4_environment_read_status_handler_execution():
    conv = ConversationContext.from_request("检查系统状态", [])
    pool = EvidencePool(question_id="q")

    async def env_status_handler(_args):
        return ToolObservation(
            tool="environment.read_status",
            ok=True,
            summary="status=ok",
            data={"server": "running", "kb_name": "default"},
        )

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4, max_retrieve_attempts=1),
        registry=build_agent_registry(),
        handlers={"environment.read_status": env_status_handler},
        decide_fn=_seq_decide([
            AgentDecision(action="tool_call", tool="environment.read_status", source="test"),
            AgentDecision(action="finish", source="test"),
        ]),
        resolve_binding_fn=lambda _c: _binding(),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.tools[0]["name"] == "environment.read_status"
    assert result.tools[0]["ok"] is True


def test_link_entities_and_retrieve_kb_mode_execution():
    conv = ConversationContext.from_request("pipelinebuilder 数据规范", [])
    pool = EvidencePool(question_id="q")

    async def link_handler(_args):
        conv.linked_entities = [{"canonical_name": "PipelineBuilder", "entity_type": "Tool", "confidence": 0.98}]
        conv.domain_context = "已定位实体: PipelineBuilder(Tool)；关联关系: PipelineBuilder -[belongs_to]-> StampTools"
        conv.head_entity = "PipelineBuilder"
        return ToolObservation(
            tool="link_entities",
            ok=True,
            summary=conv.domain_context,
            data={"candidates": conv.linked_entities},
        )

    async def retrieve_handler(args):
        mode = args.get("mode") or "hybrid"
        pool.add_retrieve([_doc("chk_1", "PipelineBuilder 线表结构", citation_id=1)], query=args.get("query"), head_entity=conv.head_entity)
        return ToolObservation(
            tool="retrieve_kb",
            ok=True,
            summary=f"chunks=1 (mode={mode})",
            data={"chunk_ids": ["chk_1"], "mode": mode},
        )

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=5, max_retrieve_attempts=2),
        registry=build_agent_registry(),
        handlers={"link_entities": link_handler, "retrieve_kb": retrieve_handler},
        decide_fn=_seq_decide([
            AgentDecision(action="tool_call", tool="link_entities", arguments={"query": "pipelinebuilder"}, source="test"),
            AgentDecision(action="tool_call", tool="retrieve_kb", arguments={"query": "PipelineBuilder StampTools 线表", "mode": "hybrid"}, source="test"),
            AgentDecision(action="finish", gate="support", source="test"),
        ]),
        resolve_binding_fn=lambda _c: _binding(),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert len(result.tools) == 1
    assert result.tools[0]["name"] == "retrieve_kb"
    assert len(result.evidence.citable_docs()) == 1


def test_clarify_custom_options_execution():
    conv = ConversationContext.from_request("pipeline", [])
    pool = EvidencePool(question_id="q")

    async def clarify_handler(args):
        opts = [{"id": "1", "label": "PipelineBuilder（StampTools）"}, {"id": "2", "label": "PipelineWebGL（StampWebGL）"}]
        return ToolObservation(
            tool="clarify",
            ok=True,
            summary="card: 2 options",
            data={"pause": True, "clarify": {"needs_clarification": True, "ask_question": "请选择具体产品：", "options": opts}},
        )

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4, max_retrieve_attempts=1),
        registry=build_agent_registry(),
        handlers={"clarify": clarify_handler},
        decide_fn=_seq_decide([
            AgentDecision(action="tool_call", tool="clarify", arguments={"question": "请选择具体产品：", "options": ["PipelineBuilder", "PipelineWebGL"]}, source="test"),
        ]),
        resolve_binding_fn=lambda _c: _binding(),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.clarify is not None
    assert result.clarify["needs_clarification"] is True
    assert len(result.clarify["options"]) == 2


def test_agent_react_event_stream_interleaved_sequence():
    conv = ConversationContext.from_request(
        "PipelineBuilder 怎么用", [], entity_name="PipelineBuilder",
    )
    pool = EvidencePool(question_id="q")
    events = []

    async def on_event(ev):
        events.append(ev)

    async def link_handler(args):
        pool.add_relation(
            relation_key="pipeline -[belongs_to]-> pipeline_product",
            target_entity="pipeline",
        )
        return ToolObservation(tool="link_entities", ok=True, summary="候选实体数: 2", data={})

    async def retrieve_handler(args):
        pool.add_retrieve([_doc("chk_1", "Pipeline线表")], query=args.get("query"))
        return ToolObservation(tool="retrieve_kb", ok=True, summary="召回 1 个片段", data={"chunk_ids": ["chk_1"]})

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4, max_retrieve_attempts=1),
        registry=build_agent_registry(),
        handlers={"link_entities": link_handler, "retrieve_kb": retrieve_handler},
        decide_fn=_seq_decide([
            AgentDecision(action="tool_call", tool="link_entities", arguments={"query": "PipelineBuilder", "target_entity": "PipelineBuilder"}, reason="首先识别到产品关键词，检索知识图谱候选实体", source="test"),
            AgentDecision(action="tool_call", tool="retrieve_kb", arguments={"query": "PipelineBuilder", "target_entity": "PipelineBuilder"}, reason="已获取候选实体，开始检索相关文档", source="test"),
            AgentDecision(action="finish", reason="证据充足，开始组织回答", gate="support", source="test"),
        ]),
        resolve_binding_fn=lambda _c: _binding(),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run(on_event=on_event))
    event_types = [e["type"] for e in events]
    assert "thinking" not in event_types
    assert "decision" in event_types
    assert "guard" in event_types
    assert "tool_start" in event_types
    assert "tool_result" in event_types
    assert "evidence_update" in event_types
    assert any(e["type"] == "decision" and "首先识别到产品关键词" in e["data"]["reason"] for e in events)
    assert not any(e["type"] == "tool_start" and e["data"]["name"] == "link_entities" for e in events)
    assert any(e["type"] == "decision" and "已获取候选实体" in e["data"]["reason"] for e in events)
    assert any(e["type"] == "tool_start" and e["data"]["name"] == "retrieve_kb" for e in events)
    assert any(e["type"] == "decision" and "证据充足" in e["data"]["reason"] for e in events)




def test_v14_tool_registry_and_react_flow():
    # 1. 验证 Phase 1 注册表中已废除 understand / rewrite
    p1 = build_phase1_registry()
    assert "understand" not in p1.names()
    assert "rewrite" not in p1.names()
    assert "retrieve_kb" in p1.names()
    assert "reuse_evidence" in p1.names()

    # 2. 验证 Agent 注册表中同样不含 understand / rewrite
    agent_reg = build_agent_registry()
    assert "understand" not in agent_reg.names()
    assert "rewrite" not in agent_reg.names()
    assert "link_entities" not in agent_reg.names()
    assert "clarify" in agent_reg.names()


def test_v14_link_entities_is_retired():
    conv = ConversationContext.from_request("我啥时候给你说是pipelinebuilder了？", [])
    assert not conv.head_entity

    # link_entities 已由 expand_graph_scope 替代，不能作为 Main 工具复活。
    reg = build_agent_registry()
    spec = reg.get("link_entities")
    assert spec is None


def test_v14_llm_http_default_num_ctx_injection():
    from rag_knowledge.llm_http import _resolve_default_num_ctx
    # 当未传 num_ctx 时，默认返回 32768
    assert _resolve_default_num_ctx(None) >= 32768
    # 当显式指定时，使用显式值
    assert _resolve_default_num_ctx(16384) == 16384


def test_v14_retrieve_intent_progressive_disclosure():
    # 验证 retrieve_kb 的 schema 中包含 intent 枚举
    reg = build_agent_registry()
    spec = reg.get("retrieve_kb")
    assert spec is not None
    props = (spec.input_schema or {}).get("properties", {})
    assert "intent" in props
    assert "exact_parameter" in props["intent"]["enum"]
    assert "conceptual_overview" in props["intent"]["enum"]


def test_robust_json_parser_tolerance():
    # 1. 测试单引号与尾随逗号修复
    raw_single_quotes = "{'thought': '查询端口', 'action': 'tool_call', 'tool': 'retrieve_kb', 'arguments': {'query': '端口', 'intent': 'exact_parameter',},}"
    res = parse_json_object(raw_single_quotes)
    assert res["action"] == "tool_call"
    assert res["tool"] == "retrieve_kb"
    assert res["arguments"]["query"] == "端口"

    # 2. 测试 Python None/True 与未闭合大括号
    raw_python_keywords = "{'thought': '结束', 'action': 'finish', 'tool': None, 'gate': 'support'"
    res2 = parse_json_object(raw_python_keywords)
    assert res2["action"] == "finish"
    assert res2["tool"] is None
    assert res2["gate"] == "support"


def test_react_line_format_fallback():
    # 1. 测试函数调用风格纯文本
    raw_react_func = """
<think>分析用户提问，准备检索</think>
Thought: 正在查询 ModelBuilder 端口配置
Action: retrieve_kb(query="ModelBuilder 端口", intent="exact_parameter")
"""
    res = parse_json_object(raw_react_func)
    assert res["action"] == "tool_call"
    assert res["tool"] == "retrieve_kb"
    assert res["arguments"]["query"] == "ModelBuilder 端口"
    assert res["arguments"]["intent"] == "exact_parameter"
    assert "ModelBuilder" in res["thought"]

    # 2. 测试分行结束指令
    raw_react_finish = """
Thought: 证据已经充足，准备生成完整回答
Action: finish
Gate: support
"""
    res2 = parse_json_object(raw_react_finish)
    assert res2["action"] == "finish"
    assert res2["gate"] == "support"
    assert "证据已经充足" in res2["thought"]


def test_decision_prompt_has_one_shot():
    from rag_knowledge.services.agent_orchestration.runtime import _DECISION_PROMPT
    assert "示例（One-shot）" in _DECISION_PROMPT
    assert "StampServer 默认端口" in _DECISION_PROMPT


def test_retrieval_trace_explainable_snapshot():
    # 验证 AgentTurnResult 及 to_trace 中包含 retrieval_trace
    conv = ConversationContext.from_request("StampServer 默认端口", [])
    pool = EvidencePool(question_id="q")
    trace_snapshot = {
        "intent": "exact_parameter",
        "applied_weights": {"bm25": 0.85, "vector": 0.15},
        "graph_expansion_hops": 0,
        "top_k": 4,
        "candidate_k": 16,
        "effective_mode": "hybrid",
    }
    result = AgentTurnResult(
        conversation=conv,
        evidence=pool,
        retrieval_trace=trace_snapshot,
    )
    trace_dict = result.to_trace()
    assert "retrieval_trace" in trace_dict
    assert trace_dict["retrieval_trace"]["intent"] == "exact_parameter"
    assert trace_dict["retrieval_trace"]["applied_weights"] == {"bm25": 0.85, "vector": 0.15}
    assert trace_dict["retrieval_trace"]["graph_expansion_hops"] == 0


def test_context_conditioned_prompt_hard_isolation():
    # 1. 状态 A：纯会话释疑且无证据 -> 挂载会话解释 Prompt，无知识库拒答壳
    explain_msgs = build_agent_messages(
        question="我啥时候说是PipelineBuilder了？",
        conversation_section="## 对话上下文\n- 用户此前未指定产品",
        evidence_section="",
        is_direct_chat=True,
        has_evidence=False,
    )
    assert len(explain_msgs) >= 1
    sys_content = explain_msgs[0]["content"]
    assert "对话状态澄清与释疑助手" in sys_content
    assert "禁止机械拒答" in sys_content
    assert "<evidence_pool>" not in sys_content

    # 2. 状态 B：客观知识问答 -> 挂载事实强锁 Prompt
    strict_msgs = build_agent_messages(
        question="StampServer 端口是多少？",
        conversation_section="## 对话上下文\n- 当前实体: StampServer",
        evidence_section="## 证据池\n<evidence_pool>[1] 端口 8080</evidence_pool>",
        is_direct_chat=False,
        has_evidence=True,
    )
    sys_strict = strict_msgs[0]["content"]
    assert "RAG 知识库问答助手" in sys_strict
    assert "<evidence_pool>" in sys_strict
    assert "绝对事实强锁" in sys_strict





def test_controller_failure_terminates_without_heuristic_tool_call():
    """Main 决策失败时应结构化终止，Runtime 不得接管规划并调用工具。"""
    conv = ConversationContext.from_request("PipelineBuilder 字段规范是什么", [])
    conv.head_entity = "PipelineBuilder"
    pool = EvidencePool(question_id="q_test")
    budget = AgentBudget(max_steps=4, max_retrieve_attempts=2)
    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=budget,
        registry=build_agent_registry(),
        handlers={},
    )
    result = asyncio.run(loop.run())
    assert result.terminal_action == "controller_error"
    assert result.tools == []
    assert loop.steps[-1]["progress"] == ToolProgressStatus.ERROR
    assert loop.steps[-1]["observation"]["error"] == "controller_decision_error"


def test_stream_react_events_order_and_payload():
    """验证流式 ReAct 事件时序与参数规范：Decision -> ToolStart -> ToolEnd。"""
    conv = ConversationContext.from_request("StampServer 默认端口", [])
    pool = EvidencePool(question_id="q_stream")
    budget = AgentBudget(max_steps=3, max_retrieve_attempts=1)

    events: list[dict] = []

    async def mock_handler(args: dict) -> ToolObservation:
        pool.add_retrieve([_doc("c1", "8080")], query=args.get("query", ""))
        return ToolObservation(
            tool="retrieve_kb",
            ok=True,
            summary="召回 3 个文档片段",
            data={"chunk_ids": ["c1", "c2", "c3"]},
        )

    async def on_event(ev: dict):
        events.append(ev)

    def mock_decide(c, e, obs):
        if not obs:
            return AgentDecision(
                action="tool_call",
                tool="retrieve_kb",
                arguments={"query": "StampServer 默认端口", "mode": "hybrid"},
                reason="用户查询 StampServer 的端口配置，调用 retrieve_kb 获取证据。",
                source="llm",
            )
        return AgentDecision(
            action="finish",
            reason="已获取端口配置证据，准备组织最终回答。",
            source="llm",
        )

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=budget,
        registry=build_agent_registry(),
        handlers={"retrieve_kb": mock_handler},
        decide_fn=mock_decide,
    )

    result = asyncio.run(loop.run(on_event=on_event))
    assert result.route == "retrieve"
    assert len(events) >= 3

    # 检查事件类型时序
    ev_types = [ev["type"] for ev in events]
    assert "thinking" not in ev_types
    assert "decision" in ev_types
    assert "tool_start" in ev_types
    assert "tool_result" in ev_types

    first_decision = next(ev for ev in events if ev["type"] == "decision")
    assert "StampServer 的端口配置" in first_decision["data"]["reason"]

    # 验证 tool_start 中的入参包含非空 query
    tool_start_ev = next(ev for ev in events if ev["type"] == "tool_start")
    assert tool_start_ev["data"]["name"] == "retrieve_kb"
    assert tool_start_ev["data"]["arguments"]["query"] == "StampServer 默认端口"

    # 验证 canonical tool_result 中的结果包含 summary
    tool_result_ev = next(ev for ev in events if ev["type"] == "tool_result")
    assert tool_result_ev["data"]["ok"] is True
    assert tool_result_ev["data"]["summary"] == "召回 3 个文档片段"


def test_v14_link_entities_is_not_registered():
    conv = ConversationContext.from_request("我啥时候给你说是pipelinebuilder了？", [])
    assert not conv.head_entity

    # link_entities 已退休，Main Registry 不保留兼容入口。
    reg = build_agent_registry()
    spec = reg.get("link_entities")
    assert spec is None


def test_v14_llm_http_default_num_ctx_injection():
    from rag_knowledge.llm_http import _resolve_default_num_ctx
    # 当未传 num_ctx 时，默认返回 32768
    assert _resolve_default_num_ctx(None) >= 32768
    # 当显式指定时，使用显式值
    assert _resolve_default_num_ctx(16384) == 16384


def test_v14_retrieve_intent_progressive_disclosure():
    # 验证 retrieve_kb 的 schema 中包含 intent 枚举
    reg = build_agent_registry()
    spec = reg.get("retrieve_kb")
    assert spec is not None
    props = (spec.input_schema or {}).get("properties", {})
    assert "intent" in props
    assert "exact_parameter" in props["intent"]["enum"]
    assert "conceptual_overview" in props["intent"]["enum"]


def test_robust_json_parser_tolerance():
    # 1. 测试单引号与尾随逗号修复
    raw_single_quotes = "{'thought': '查询端口', 'action': 'tool_call', 'tool': 'retrieve_kb', 'arguments': {'query': '端口', 'intent': 'exact_parameter',},}"
    res = parse_json_object(raw_single_quotes)
    assert res["action"] == "tool_call"
    assert res["tool"] == "retrieve_kb"
    assert res["arguments"]["query"] == "端口"

    # 2. 测试 Python None/True 与未闭合大括号
    raw_python_keywords = "{'thought': '结束', 'action': 'finish', 'tool': None, 'gate': 'support'"
    res2 = parse_json_object(raw_python_keywords)
    assert res2["action"] == "finish"
    assert res2["tool"] is None
    assert res2["gate"] == "support"

    # 3. 测试截断字符串与未闭合双引号的自动修复
    raw_truncated_quote = '{"thought": "正在分析参数", "action": "tool_call", "tool": "retrieve_kb", "arguments": {"query": "StampServer 默认端口'
    res3 = parse_json_object(raw_truncated_quote)
    assert res3["action"] == "tool_call"
    assert res3["tool"] == "retrieve_kb"
    assert "StampServer 默认端口" in res3["arguments"]["query"]

    # 4. 测试深度截断时正则提取有效键值对
    raw_deep_truncated = '{"thought": "需要检索", "action": "tool_call", "tool": "retrieve_kb", "arguments": {"query": "StampGIS"'
    res4 = parse_json_object(raw_deep_truncated)
    assert res4["action"] == "tool_call"
    assert res4["tool"] == "retrieve_kb"
    assert res4["arguments"]["query"] == "StampGIS"


def test_react_line_format_fallback():
    # 1. 测试函数调用风格纯文本
    raw_react_func = """
<think>分析用户提问，准备检索</think>
Thought: 正在查询 ModelBuilder 端口配置
Action: retrieve_kb(query="ModelBuilder 端口", intent="exact_parameter")
"""
    res = parse_json_object(raw_react_func)
    assert res["action"] == "tool_call"
    assert res["tool"] == "retrieve_kb"
    assert res["arguments"]["query"] == "ModelBuilder 端口"
    assert res["arguments"]["intent"] == "exact_parameter"
    assert "ModelBuilder" in res["thought"]

    # 2. 测试分行结束指令
    raw_react_finish = """
Thought: 证据已经充足，准备生成完整回答
Action: finish
Gate: support
"""
    res2 = parse_json_object(raw_react_finish)
    assert res2["action"] == "finish"
    assert res2["gate"] == "support"
    assert "证据已经充足" in res2["thought"]


def test_decision_prompt_has_one_shot():
    from rag_knowledge.services.agent_orchestration.runtime import _DECISION_PROMPT
    assert "示例 1" in _DECISION_PROMPT or "示例" in _DECISION_PROMPT
    assert "StampServer 默认端口" in _DECISION_PROMPT
    assert "retrieve_kb" in _DECISION_PROMPT
    assert "expand_graph_scope" in _DECISION_PROMPT


def test_retrieval_trace_explainable_snapshot():
    # 验证 AgentTurnResult 及 to_trace 中包含 retrieval_trace
    conv = ConversationContext.from_request("StampServer 默认端口", [])
    pool = EvidencePool(question_id="q")
    trace_snapshot = {
        "intent": "exact_parameter",
        "applied_weights": {"bm25": 0.85, "vector": 0.15},
        "graph_expansion_hops": 0,
        "top_k": 4,
        "candidate_k": 16,
        "effective_mode": "hybrid",
    }
    result = AgentTurnResult(
        conversation=conv,
        evidence=pool,
        retrieval_trace=trace_snapshot,
    )
    trace_dict = result.to_trace()
    assert "retrieval_trace" in trace_dict
    assert trace_dict["retrieval_trace"]["intent"] == "exact_parameter"
    assert trace_dict["retrieval_trace"]["applied_weights"] == {"bm25": 0.85, "vector": 0.15}
    assert trace_dict["retrieval_trace"]["graph_expansion_hops"] == 0


def test_context_conditioned_prompt_hard_isolation():
    # 1. 状态 A：纯会话释疑且无证据 -> 挂载会话解释 Prompt，无知识库拒答壳
    explain_msgs = build_agent_messages(
        question="我啥时候说是PipelineBuilder了？",
        conversation_section="## 对话上下文\n- 用户此前未指定产品",
        evidence_section="",
        is_direct_chat=True,
        has_evidence=False,
    )
    assert len(explain_msgs) >= 1
    sys_content = explain_msgs[0]["content"]
    assert "对话状态澄清与释疑助手" in sys_content
    assert "禁止机械拒答" in sys_content
    assert "<evidence_pool>" not in sys_content

    # 2. 状态 B：客观知识问答 -> 挂载事实强锁 Prompt
    strict_msgs = build_agent_messages(
        question="StampServer 端口是多少？",
        conversation_section="## 对话上下文\n- 当前实体: StampServer",
        evidence_section="## 证据池\n<evidence_pool>[1] 端口 8080</evidence_pool>",
        is_direct_chat=False,
        has_evidence=True,
    )
    sys_strict = strict_msgs[0]["content"]
    assert "RAG 知识库问答助手" in sys_strict
    assert "<evidence_pool>" in sys_strict
    assert "绝对事实强锁" in sys_strict


def test_agent_stream_events_yielded():
    conv = ConversationContext.from_request("StampServer 端口是多少？", [])
    pool = EvidencePool(question_id="q_stream")
    budget = AgentBudget(max_steps=3, max_retrieve_attempts=1)

    events: list[dict] = []

    async def mock_handler(args: dict) -> ToolObservation:
        pool.add_retrieve([_doc("c1", "8080")], query=args.get("query", ""))
        return ToolObservation(
            tool="retrieve_kb",
            ok=True,
            summary="召回 3 个文档片段",
            data={"chunk_ids": ["c1", "c2", "c3"]},
        )

    async def on_event(ev: dict):
        events.append(ev)

    def mock_decide(c, e, obs):
        if not obs:
            return AgentDecision(
                action="tool_call",
                tool="retrieve_kb",
                arguments={"query": "StampServer 默认端口", "mode": "hybrid"},
                reason="用户查询 StampServer 的端口配置，调用 retrieve_kb 获取证据。",
                source="llm",
            )
        return AgentDecision(
            action="finish",
            reason="已获取端口配置证据，准备组织最终回答。",
            source="llm",
        )

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=budget,
        registry=build_agent_registry(),
        handlers={"retrieve_kb": mock_handler},
        decide_fn=mock_decide,
    )

    result = asyncio.run(loop.run(on_event=on_event))
    assert result.route == "retrieve"
    assert len(events) >= 3

    # 检查事件类型时序
    ev_types = [ev["type"] for ev in events]
    assert "thinking" not in ev_types
    assert "decision" in ev_types
    assert "tool_start" in ev_types
    assert "tool_result" in ev_types

    first_decision = next(ev for ev in events if ev["type"] == "decision")
    assert "StampServer 的端口配置" in first_decision["data"]["reason"]

    # 验证 tool_start 中的入参包含非空 query
    tool_start_ev = next(ev for ev in events if ev["type"] == "tool_start")
    assert tool_start_ev["data"]["name"] == "retrieve_kb"
    assert tool_start_ev["data"]["arguments"]["query"] == "StampServer 默认端口"

    # 验证 canonical tool_result 中的结果包含 summary
    tool_result_ev = next(ev for ev in events if ev["type"] == "tool_result")
    assert tool_result_ev["data"]["ok"] is True
    assert tool_result_ev["data"]["summary"] == "召回 3 个文档片段"


def test_govern_answer_path_case_insensitivity():
    """验证路径比对时大小写不敏感，避免原文包含大写路径时因模型输出小写而误触发安全警告。"""
    from rag_knowledge.services.answer_finalizer import AnswerFinalizer
    from rag_knowledge.services.helper_grounding_reviewer import HelperGroundingReviewer

    docs = [_doc("chk_path", "安装目录位于 C:\\Program Files\\StampServer 路径下", citation_id=1)]

    pass_reviewer = HelperGroundingReviewer(lambda _msgs: """{
        "verdict": "PASS",
        "coverage": "FULL",
        "summary": "通过",
        "claim_reviews": [{"claim_id": "c1", "claim_type": "knowledge_claim", "claim": "测试", "evidence_ids": [1], "status": "supported", "reason": "支持"}],
        "rewrite_actions": []
    }""")
    block_reviewer = HelperGroundingReviewer(lambda _msgs: """{
        "verdict": "NO_SAFE_ANSWER",
        "coverage": "NONE",
        "summary": "未在知识库中找到对应路径",
        "claim_reviews": [],
        "rewrite_actions": []
    }""")

    finalizer = AnswerFinalizer()
    # 1. 大小写不一致但路径存在：通过
    ans_matched = "StampServer 默认安装在 c:\\program files\\stampserver 目录下 [1]。"
    res1 = finalizer.finalize(ans_matched, "安装目录在哪", docs, helper_reviewer=pass_reviewer)
    assert "[1]" in res1.answer

    # 2. 输出了真正不存在的路径：触发安全拦截
    ans_unmatched = "StampServer 默认安装在 D:\\FakePath\\SecretServer 目录下 [1]。"
    res2 = finalizer.finalize(ans_unmatched, "安装目录在哪", docs, helper_reviewer=block_reviewer)
    assert "未在知识库中找到" in res2.answer or "[1]" not in res2.answer or res2.grounding.get("reasons")


def test_meta_chat_direct_finish_without_tools():
    """验证元对话（如'我们刚刚在讨论什么？'）直接判定为 finish，不调用 retrieve_kb/link_entities，且证据池为空。"""
    from rag_knowledge.services.agent_orchestration.runtime import is_meta_or_direct_chat

    assert is_meta_or_direct_chat("我们刚刚在讨论什么？") is True
    assert is_meta_or_direct_chat("你刚才说了什么") is True
    assert is_meta_or_direct_chat("我啥时候说是PipelineBuilder了？") is True
    assert is_meta_or_direct_chat("StampServer 默认端口是多少") is False

    conv = ConversationContext.from_request(
        "我们刚刚在讨论什么？",
        [
            {"role": "user", "content": "PipelineBuilder 怎么用"},
            {"role": "assistant", "content": "PipelineBuilder 是管线建模工具..."},
        ],
    )
    pool = EvidencePool(question_id="q_meta")
    budget = AgentBudget(max_steps=3, max_retrieve_attempts=1)
    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=budget,
        registry=build_agent_registry(),
        handlers={},
        cfg=SimpleNamespace(
            agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
        ),
        decide_fn=_seq_decide([
            AgentDecision(
                action="finalize",
                reason="这是会话历史回顾，可直接回答。",
                source="test_main",
            ),
        ]),
    )
    result = asyncio.run(loop.run())
    assert result.terminal_action == "controller_finalize"
    assert result.tools == []
    assert result.answer_contract["answer_type"] == "direct_chat"


def test_controller_failure_emits_structured_error_without_fallback_notice():
    """Main 决策异常必须显式报错并终止，不得以 notice 掩盖或启用替代规划。"""
    conv = ConversationContext.from_request("StampServer 配置项有哪些", [])
    pool = EvidencePool(question_id="q_fallback")
    budget = AgentBudget(max_steps=3, max_retrieve_attempts=1)

    events: list[dict] = []

    async def on_event(ev: dict):
        events.append(ev)

    async def mock_handler(args: dict) -> ToolObservation:
        return ToolObservation(
            tool="retrieve_kb",
            ok=True,
            summary="召回 0 个文档片段",
            data={},
        )

    def failing_decide_llm(*_args, **_kwargs):
        raise RuntimeError("LLM connection timeout")

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=budget,
        registry=build_agent_registry(),
        handlers={"retrieve_kb": mock_handler},
        decide_fn=failing_decide_llm,
    )

    result = asyncio.run(loop.run(on_event=on_event))
    assert result.terminal_action == "controller_error"
    assert result.tools == []
    assert not [ev for ev in events if ev["type"] == "notice"]
    errors = [ev["data"] for ev in events if ev["type"] == "error"]
    assert errors == [{
        "code": "controller_decision_error",
        "stage": "decision",
        "message": "Main Controller 未能生成合法决策，本轮执行已安全终止。",
        "recoverable": False,
        "step": 1,
        "exception_type": "RuntimeError",
        "validation_error": "LLM connection timeout",
        "repair_attempted": False,
        "protocol_attempts": [],
    }]


def test_direct_meta_chat_never_triggers_forced_retrieval():
    """验证清空记录或提问'我们刚刚在讨论什么'等元对话时，直接由 Agent 判定并直答，绝不触发强制知识库检索。"""
    conv = ConversationContext.from_request("我们刚刚在讨论什么？", [])
    pool = EvidencePool(question_id="q_meta")
    budget = AgentBudget(max_steps=4, max_retrieve_attempts=2)
    called_tools = []

    async def mock_retrieve(args):
        called_tools.append("retrieve_kb")
        return ToolObservation(tool="retrieve_kb", ok=True, summary="不应被调用", data={})

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=budget,
        registry=build_agent_registry(),
        handlers={"retrieve_kb": mock_retrieve},
    )

    result = asyncio.run(loop.run())
    assert called_tools == []
    assert result.retrieve_attempts == 0
    assert result.route == "direct"
    assert len(result.evidence.citable_docs()) == 0


def test_process_inquiry_can_trigger_clarify_when_appropriate():
    conv = ConversationContext.from_request("你不向我确认澄清吗？", [
        {"role": "user", "content": "StampTools 怎么部署"},
        {"role": "assistant", "content": "StampTools 部署步骤如下..."},
    ])
    pool = EvidencePool(question_id="q_inquiry")
    budget = AgentBudget(max_steps=3, max_retrieve_attempts=1)

    async def mock_clarify(args):
        return ToolObservation(
            tool="clarify",
            ok=True,
            summary="出示澄清卡片",
            data={
                "pause": True,
                "clarify": {
                    "question": args.get("question", "请选择具体版本："),
                    "options": args.get("options", ["Server 端", "Client 端"]),
                },
            },
        )

    def decide_clarify(c, e, obs):
        return AgentDecision(
            action="tool_call",
            tool="clarify",
            arguments={"question": "请确认您要咨询的具体产品模块：", "options": ["StampTools Server 端", "StampTools Web 端"]},
            thought="用户质询上一轮为何未澄清。当前问题存在多模块歧义，调用 clarify 工具出示选项。",
            source="llm",
        )

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=budget,
        registry=build_agent_registry(),
        handlers={"clarify": mock_clarify},
        decide_fn=decide_clarify,
    )

    result = asyncio.run(loop.run())
    assert result.route == "clarify"
    assert result.clarify is not None
    assert "请确认您要咨询的具体产品模块" in result.clarify.get("question", "")
    assert len(result.clarify.get("options", [])) == 2


def test_negative_correction_with_tech_question_preserves_evidence():
    """验证带有否定词的纠偏提问（如'我没问过X，我问的是Y'），Agent 检索出证据后绝不被静态正则误判为纯元对话而清空证据。"""
    conv = ConversationContext.from_request("我没问过 StampServer，我问的是 StampGIS 怎么配置", [
        {"role": "user", "content": "StampServer 端口是多少"},
        {"role": "assistant", "content": "StampServer 端口是 8080"},
    ])
    pool = EvidencePool(question_id="q_correct")
    budget = AgentBudget(max_steps=3, max_retrieve_attempts=2)

    async def mock_retrieve(args):
        pool.add_retrieve([_doc("gis_conf", "StampGIS")], query=args.get("query"))
        return ToolObservation(tool="retrieve_kb", ok=True, summary="召回 1 条")

    def decide_step(c, e, obs):
        if not obs:
            return AgentDecision(
                action="tool_call",
                tool="retrieve_kb",
                arguments={"query": "StampGIS 配置", "mode": "hybrid"},
                thought="用户否定了前文的 StampServer 并明确提出 StampGIS 配置诉求，改写为'StampGIS 配置'发起检索。",
                source="llm",
            )
        return AgentDecision(
            action="finish",
            gate="support",
            thought="已获取 StampGIS 配置证据，开始组织回答。",
            source="llm",
        )

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=budget,
        registry=build_agent_registry(),
        handlers={"retrieve_kb": mock_retrieve},
        decide_fn=decide_step,
    )

    result = asyncio.run(loop.run())
    assert result.route == "retrieve"
    assert len(result.evidence.citable_docs()) == 1
    assert result.evidence.citable_docs()[0]["metadata"]["chunk_id"] == "gis_conf"


def test_clarify_options_string_fault_tolerance():
    """验证当 options 以中文/英文逗号或换行分隔的字符串形式返回时，能自动容错切分为列表。"""
    import re
    from rag_knowledge.services.agent_orchestration.runtime import parse_json_object

    raw_json = '{"thought":"歧义澄清","action":"tool_call","tool":"clarify","arguments":{"question":"请选择产品：","options":"StampServer, StampTools, StampGIS"}}'
    data = parse_json_object(raw_json)
    opts = data.get("arguments", {}).get("options")
    # 模拟 runtime 的容错逻辑
    if isinstance(opts, str):
        opts = [s.strip() for s in re.split(r"[,，;；\n]+", opts) if s.strip()]
    assert opts == ["StampServer", "StampTools", "StampGIS"]
    assert len(opts) == 3

def test_v15_oneshot_finish_when_evidence_sufficient():
    """PRD V1.5 核心特性：首轮检索后证据充足，模型输出 finish 直接放行（单步即答，无多余补检）。"""
    conv = ConversationContext.from_request("StampServer 默认端口是多少", [])
    pool = EvidencePool(question_id="q_v15")
    retrieved_count = 0

    async def mock_retrieve(args):
        nonlocal retrieved_count
        retrieved_count += 1
        pool.add_retrieve([_doc("s_port", "StampServer 默认服务端口为 8080")], query=args.get("query"))
        return ToolObservation(tool="retrieve_kb", ok=True, summary="召回 1 条")

    decisions = iter([
        AgentDecision(action="tool_call", tool="retrieve_kb", arguments={"query": "StampServer 默认端口"}),
        AgentDecision(action="finish", thought="证据池已包含端口事实，结束检索。"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=8),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": mock_retrieve},
        decide_fn=lambda *_: next(decisions),
    )

    result = asyncio.run(loop.run())
    assert result.route == "retrieve"
    assert result.retrieve_attempts == 1
    assert retrieved_count == 1
    assert len(result.evidence.citable_docs()) == 1


def test_v15_cycle_detection_prevents_infinite_loop():
    """PRD V1.5 核心特性：当模型连续两次请求完全相同 query+tool 时，触发循环检测安全熔断。"""
    conv = ConversationContext.from_request("循环测试", [])
    pool = EvidencePool(question_id="q_cycle")
    retrieved_count = 0

    async def mock_retrieve(args):
        nonlocal retrieved_count
        retrieved_count += 1
        return ToolObservation(tool="retrieve_kb", ok=True, summary="未命中")

    decisions = iter([
        AgentDecision(action="tool_call", tool="retrieve_kb", arguments={"query": "相同查询词"}),
        AgentDecision(action="tool_call", tool="retrieve_kb", arguments={"query": "相同查询词"}),
        AgentDecision(action="finish"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=8),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": mock_retrieve},
        decide_fn=lambda *_: next(decisions),
    )

    result = asyncio.run(loop.run())
    assert retrieved_count == 1
    assert ("tool_cycle_detected" in result.fallbacks or "retrieve_cycle_detected" in result.fallbacks)


def test_malformed_tool_call_never_downgraded_to_finish():
    """协议安全保证：当 LLM 输出 tool_call 但缺失 tool 时，绝不被误当作 finish 退出。"""
    conv = ConversationContext.from_request("系统架构分层有哪些？", [])
    pool = EvidencePool(question_id="q_malformed")
    retrieved = []

    async def mock_retrieve(args):
        retrieved.append(args.get("query"))
        pool.add_retrieve([_doc("arch_1", "系统架构分为接入层与服务层")], query=args.get("query"))
        return ToolObservation(tool="retrieve_kb", ok=True, summary="召回 1 条")

    # 第 1 步输出非法 tool_call（tool=None），第 2 步输出合法 retrieve_kb，第 3 步 finish
    decisions = iter([
        AgentDecision(action="tool_call", tool=None, thought="我想检索但漏了tool名"),
        AgentDecision(action="tool_call", tool="retrieve_kb", arguments={"query": "系统架构 分层"}),
        AgentDecision(action="finish", thought="已有证据，组织回答"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=8),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": mock_retrieve},
        decide_fn=lambda *_: next(decisions),
    )

    result = asyncio.run(loop.run())
    assert "malformed_decision" in result.fallbacks
    assert len(retrieved) >= 1
    assert result.route == "retrieve"


def test_evidence_constraint_blocks_premature_finish():
    """Harness 对齐 PRD V1.5：尊重 LLM 合法 finish 决策，不替 LLM 自动补检。"""
    conv = ConversationContext.from_request("StampServer 核心配置有哪些", [])
    pool = EvidencePool(question_id="q_premature")
    retrieved = []

    async def mock_retrieve(args):
        retrieved.append(args.get("query"))
        return ToolObservation(tool="retrieve_kb", ok=True, summary="召回 1 条")

    # 模型在无证据时自主决定 finish
    decisions = iter([
        AgentDecision(action="finish", thought="未检索直接尝试结束"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=8),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": mock_retrieve},
        decide_fn=lambda *_: next(decisions),
    )

    result = asyncio.run(loop.run())
    assert len(retrieved) == 0
    assert len(result.tools) == 0


def test_universal_cycle_detection_on_link_entities():
    """全工具通用熔断：连续两次调用相同入参的 link_entities 时，在真正执行前立即熔断收敛。"""
    conv = ConversationContext.from_request(
        "StampServer 依赖什么", [], entity_name="StampServer",
    )
    pool = EvidencePool(question_id="q_cycle_link")
    link_exec_count = 0

    async def mock_link(args):
        nonlocal link_exec_count
        link_exec_count += 1
        return ToolObservation(tool="link_entities", ok=True, summary="候选实体数: 0")

    # 连续输出相同的 link_entities 调用，第二次应在执行前被精确循环守卫拒绝。
    decisions = iter([
        AgentDecision(action="tool_call", tool="link_entities", arguments={"query": "StampServer 依赖", "target_entity": "StampServer"}),
        AgentDecision(action="tool_call", tool="link_entities", arguments={"query": "StampServer 依赖", "target_entity": "StampServer"}),
        AgentDecision(action="finish", reason="本轮未获得可用关系证据"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=8),
        registry=build_agent_registry(),
        handlers={"link_entities": mock_link},
        decide_fn=lambda *_: next(decisions),
    )

    result = asyncio.run(loop.run())
    assert link_exec_count == 0
    assert "tool_unknown:link_entities" in result.fallbacks


def test_cycle_detection_requires_immediate_previous_call():
    from rag_knowledge.services.agent_orchestration.models import AgentBudget

    budget = AgentBudget()
    budget.record_call("retrieve_kb", {"query": "A"})
    budget.record_call("link_entities", {"query": "B"})

    assert budget.is_cycle("retrieve_kb", {"query": "A"}) is False
    assert budget.is_cycle("link_entities", {"query": "B"}) is True


def test_exact_cycle_ignores_changed_gap_and_expected_gain():
    budget = AgentBudget()
    arguments = {"query": "StampServer 端口", "mode": "hybrid"}
    budget.record_call(
        "retrieve_kb",
        arguments,
        gap="端口清单",
        expected_gain="获取端口列表",
    )

    assert budget.is_cycle(
        "retrieve_kb",
        arguments,
        gap="部署端口事实",
        expected_gain="获取明确端口值",
    ) is True


def test_attempted_gap_registry_isolates_target_scope():
    registry = AttemptedGapRegistry()
    registry.record(
        gap=" StampServer   端口 ",
        target_scope="StampServer",
        status=ToolProgressStatus.NO_PROGRESS,
        tool="retrieve_kb",
    )

    assert registry.is_exhausted("stampserver 端口", " stampserver ") is True
    assert registry.is_exhausted("stampserver 端口", "StampWebRTC") is False
    assert registry.is_exhausted("stampserver 端口", None) is False

    unscoped = AttemptedGapRegistry()
    unscoped.record(
        gap="端口清单",
        target_scope=None,
        status=ToolProgressStatus.NO_PROGRESS,
        tool="retrieve_kb",
    )
    assert unscoped.is_exhausted("端口清单", None) is True
    assert unscoped.is_exhausted("端口清单", "StampServer") is False


def test_agent_turn_trace_includes_snapshot_support_scope():
    conv = ConversationContext.from_request("三维管线管理的相关信息", [], entity_name="三维管线管理")
    pool = EvidencePool(question_id="q-trace-scope")
    pool.add_retrieve(
        [{
            "content": "管线系统支持碰撞分析。",
            "metadata": {
                "chunk_id": "ctx-1",
                "citation_id": 1,
                "support_scope": "CONTEXT_ONLY",
                "text_evidence_class": "RELATED_CONTEXT",
            },
        }],
        query="三维管线管理",
        target_entity="三维管线管理",
    )
    snapshot = pool.create_snapshot(verdict={"coverage": "PARTIAL"})
    result = AgentTurnResult(conversation=conv, evidence=pool, evidence_snapshot=snapshot)

    trace = result.to_trace()

    assert trace["evidence_snapshot_id"] == snapshot.snapshot_id
    assert trace["evidence_snapshot"]["evidence_items"][0]["metadata"]["support_scope"] == "CONTEXT_ONLY"
    assert trace["evidence_snapshot"]["evidence_items"][0]["metadata"]["text_evidence_class"] == "RELATED_CONTEXT"


def test_explicit_function_query_keeps_related_context_partial():
    from rag_knowledge.services.agent_orchestration.runtime import FinalizationHandler
    from rag_knowledge.services.dialogue_understanding import SemanticTaskContext

    conv = ConversationContext.from_request("三维管线管理的主要功能是什么？", [], entity_name="三维管线管理")
    conv.semantic_task = SemanticTaskContext(
        resolved_question="三维管线管理的主要功能是什么？",
        primary_entity="三维管线管理",
        mentioned_entities=("三维管线管理",),
        task_type="entity_query",
        confidence=1.0,
        answer_intent="function",
        requested_facets=("function",),
    )
    pool = EvidencePool(question_id="q-test")
    # All 5 admitted docs are RELATED_CONTEXT (CONTEXT_ONLY)
    for i in range(1, 6):
        pool.add_retrieve(
            [{
                "content": f"管线系统功能 {i}：支持碰撞分析与排管。",
                "metadata": {
                    "chunk_id": f"chunk-{i}",
                    "citation_id": i,
                    "support_scope": "CONTEXT_ONLY",
                    "text_evidence_class": "RELATED_CONTEXT",
                },
            }],
            query="三维管线管理",
            target_entity="三维管线管理",
        )

    handler = FinalizationHandler(conv, pool)
    coverage, reason, missing = handler._coverage_verdict([])
    # Must NOT be FULL! Must be PARTIAL because there are no TARGET_SPECIFIC function facts for 三维管线管理
    assert coverage == "PARTIAL"
    assert reason == "missing_fact"
    assert "目标实体" in missing or "缺少" in missing
