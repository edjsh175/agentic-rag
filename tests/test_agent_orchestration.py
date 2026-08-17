"""Phase 1：对话 Agent Runtime / EvidencePool / 工具白名单 / 开关回退。"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from rag_knowledge.config import Config
from rag_knowledge.services.agent_orchestration.models import (
    AgentBudget,
    AgentDecision,
    ConversationContext,
    EvidencePool,
    ToolObservation,
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
    assert registry.validate_call("retrieve_kb", {}) is None


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
        return AgentDecision(action="tool_call", tool="retrieve_kb", source="test")

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
        AgentDecision(action="tool_call", tool="retrieve_kb", source="test"),
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
    assert {"type": "token", "data": NO_KNOWLEDGE_ANSWER} in events
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


def test_phase2_registry_allows_clarify_and_link_not_answer():
    registry = build_agent_registry()
    assert PHASE2_TOOL_NAMES <= registry.names()
    assert registry.validate_call("clarify", {}) is None
    assert registry.validate_call("link_entities", {}) is None
    assert registry.validate_call("answer", {}) == "tool_forbidden:answer"
    assert registry.validate_call("web_search", {}) == "tool_forbidden:web_search"


def test_harness_forces_j3_when_model_finishes():
    conv = ConversationContext.from_request("怎么写二次开发代码", [])
    pool = EvidencePool(question_id="q")

    async def clarify(_args):
        return _card_observation()

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4, max_retrieve_attempts=2),
        registry=build_agent_registry(),
        handlers={"clarify": clarify},
        decide_fn=lambda *_: AgentDecision(action="finish", source="test"),
        resolve_binding_fn=lambda _c: _binding(show_j3=True),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.route == "clarify"
    assert result.clarify["needs_clarification"] is True
    assert result.tools[0]["name"] == "clarify"
    assert "harness_force_j3" in result.fallbacks


def test_harness_blocks_named_family_clarify():
    conv = ConversationContext.from_request("StampWebRTC 二次开发怎么写", [])
    pool = EvidencePool(question_id="q")
    retrieved = []

    async def clarify(_args):
        raise AssertionError("clarify must not run for named legal anchor")

    async def retrieve(_args):
        retrieved.append(1)
        pool.add_retrieve([_doc("rtc", "StampUtil")], query="q")
        return ToolObservation(tool="retrieve_kb", ok=True, summary="1")

    decisions = iter([
        AgentDecision(action="tool_call", tool="clarify", source="test"),
        AgentDecision(action="finish", source="test"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4, max_retrieve_attempts=2),
        registry=build_agent_registry(),
        handlers={"clarify": clarify, "retrieve_kb": retrieve},
        decide_fn=lambda *_: next(decisions),
        resolve_binding_fn=lambda _c: _binding(skip_generic=True),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.route != "clarify"
    assert result.clarify is None
    assert retrieved == [1]
    assert "harness_block_named_family" in result.fallbacks


def test_link_entities_does_not_write_evidence_pool():
    conv = ConversationContext.from_request("StampServer 是什么", [])
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
            AgentDecision(action="tool_call", tool="link_entities", source="test"),
            AgentDecision(action="finish", source="test"),
        ]),
        resolve_binding_fn=lambda _c: _binding(),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert pool.citable_docs() == []
    assert pool.groups == []
    assert result.entity_link["candidate_count"] == 1
    assert result.entity_link["names"] == ["StampServer"]


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
        pool.add_retrieve([_doc("rtc", "StampUtil")], query="q")
        return ToolObservation(tool="retrieve_kb", ok=True, summary="1")

    decisions = iter([
        AgentDecision(action="tool_call", tool="clarify", source="test"),
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
    assert "harness_block_callback_reclarify" in result.fallbacks


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


def test_rewrite_query_keeps_confirmed_entity():
    from rag_knowledge.services.agent_orchestration.evidence_gate import rewrite_query

    out = rewrite_query(
        "broaden_semantics",
        "请详细说明怎么配置",
        head_entity="StampWebRTC",
    )
    assert "StampWebRTC" in out


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
        AgentDecision(action="tool_call", tool="retrieve_kb", source="test"),
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
        AgentDecision(action="tool_call", tool="retrieve_kb", source="test"),
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
    question = "请详细说明怎么配置 StampServer"
    conv = ConversationContext.from_request(question, [])
    pool = EvidencePool(question_id="q")
    queries: list[str] = []

    async def retrieve(args):
        query = str(args.get("query") or question)
        queries.append(query)
        if len(queries) == 1:
            pool.add_retrieve([], query=query)
        else:
            pool.add_retrieve([_doc("s1", "StampServer 配置")], query=query, head_entity="StampServer")
        return ToolObservation(tool="retrieve_kb", ok=True, summary=str(len(queries)))

    decisions = iter([
        AgentDecision(action="tool_call", tool="retrieve_kb", source="test"),
        AgentDecision(action="tool_call", tool="retrieve_kb", source="test", arguments={"query": question}),
        AgentDecision(action="finish", source="test"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=6, max_retrieve_attempts=2),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": retrieve},
        decide_fn=lambda *_: next(decisions),
        resolve_binding_fn=lambda _c: _binding(),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.retrieve_attempts == 2
    assert queries[1] != queries[0]
    assert result.evidence_gap
    assert result.evidence_gap[0]["gap_type"] == "empty_retrieval"
    assert result.evidence_gap[0]["recovery_strategy"]
    groups = [g for g in result.evidence.groups if g.kind == "retrieve"]
    assert groups[1].gap_type == "empty_retrieval"
    assert result.retrieve_improvement == 1
    assert result.answer_gate["allow_knowledge_answer"] is True


def test_agent_answer_docs_drop_when_gate_denies():
    conv = ConversationContext.from_request("什么", [])
    pool = EvidencePool(question_id="q")
    pool.add_retrieve([_doc("x", "无关", citation_id=1)], query="q", head_entity="Other")
    from rag_knowledge.services.agent_orchestration.models import AgentTurnResult

    result = AgentTurnResult(
        conversation=conv,
        evidence=pool,
        answer_gate={"allow_knowledge_answer": False, "reason": "entity_conflict"},
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
    assert len(result.tools) == 2
    assert result.tools[0]["name"] == "link_entities"
    assert result.tools[1]["name"] == "retrieve_kb"
    assert "PipelineBuilder" in conv.head_entity
    assert "StampTools" in conv.domain_context
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


