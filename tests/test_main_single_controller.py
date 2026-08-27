"""Main Single Controller & Agent 收敛执行 PRD 核心单元测试集。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from rag_knowledge.services.agent_orchestration.models import (
    AgentBudget,
    AgentDecision,
    AttemptedGapRegistry,
    ConversationContext,
    EvidenceDelta,
    EvidencePool,
    ToolObservation,
    ToolProgressStatus,
)
from rag_knowledge.services.agent_orchestration.runtime import (
    AgentLoop,
    build_agent_registry,
)


def _doc(chunk_id: str, content: str = "doc content") -> dict:
    return {
        "content": content,
        "metadata": {
            "chunk_id": chunk_id,
            "document_entity": "StampServer",
            "citation_id": 1,
            "file_name": f"{chunk_id}.md",
            "page_label": "无页码",
            "category": "text",
            "source_type": "knowledge_base",
        },
    }


def test_toolcall_source_is_100_percent_controller():
    """验证所有被执行的工具调用 100% 来自 Main Controller，绝无 Harness 越权发起的动作。"""
    conv = ConversationContext.from_request("StampServer 的端口是多少", [])
    pool = EvidencePool(question_id="q")

    async def retrieve(args):
        pool.add_retrieve([_doc("c1", "StampServer 的端口是 8080")], query=args["query"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="ok")

    decisions = iter([
        AgentDecision(action="tool_call", tool="retrieve_kb", arguments={"query": "StampServer 端口"}, source="llm"),
        AgentDecision(action="finalize", focus_evidence_ids=("c1",), source="llm"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": retrieve},
        cfg=SimpleNamespace(
            agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
        ),
        decide_fn=lambda *_: next(decisions),
        resolve_binding_fn=lambda _c: SimpleNamespace(anchor_entity="StampServer", show_j3=False, is_strong=True),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.terminal_action == "controller_finalize"
    for step in result.agent_steps:
        if step.get("controller", {}).get("action") == "tool_call":
            assert step["controller"]["role"] == "llm"
            assert "harness" not in step or step.get("harness") is None


def test_controller_evidence_summary_exposes_precise_partial_coverage():
    """Main 必须看到 Gate 同源的当前覆盖状态，才能一次选择正确的 partial/full。"""
    conv = ConversationContext.from_request("StampServer 是什么", [])
    conv.head_entity = "StampServer"
    pool = EvidencePool(question_id="q")
    pool.add_retrieve(
        [_doc("c1", "StampServer 默认管理端口是 8080")],
        query="StampServer 端口",
        head_entity="StampServer",
        target_entity="StampServer",
    )
    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4, max_retrieve_attempts=2),
        registry=build_agent_registry(),
        handlers={},
        cfg=SimpleNamespace(
            agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
        ),
        decide_fn=lambda *_: AgentDecision(action="finalize", source="llm"),
        tool_timeout=0,
    )

    summary = loop._evidence_summary()

    assert 'current_evidence_state={"coverage":"PARTIAL"' in summary
    assert '"missing_facts":[' in summary
    assert '"missing_facts":[]' not in summary
    assert '"evidence_count":1' in summary
    assert '"evidence_version":1' in summary


def test_first_step_zero_docs_marked_as_no_progress():
    """验证首轮检索 0 -> 0 docs 时，正确判定为 NO_PROGRESS 而不是 PROGRESS。"""
    conv = ConversationContext.from_request("StampServer 未知功能", [])
    pool = EvidencePool(question_id="q")

    async def retrieve(args):
        # 首轮空召回
        pool.add_retrieve([], query=args["query"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="empty")

    decisions = iter([
        AgentDecision(action="tool_call", tool="retrieve_kb", arguments={"query": "StampServer 未知功能"}, source="llm"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=1),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": retrieve},
        cfg=SimpleNamespace(
            agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
        ),
        decide_fn=lambda *_: next(decisions),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert len(result.tools) == 1
    tool_rec = result.tools[0]
    assert tool_rec["status"] == ToolProgressStatus.NO_PROGRESS
    delta = tool_rec["evidence_delta"]
    assert delta["new_chunks"] == 0
    assert delta["new_entities"] == 0
    assert delta["new_relations"] == 0
    assert delta["status"] == ToolProgressStatus.NO_PROGRESS
    assert loop.continuous_no_progress_count == 1


def test_second_retrieval_missing_gap_denied_by_harness():
    """验证第 2 次 retrieve_kb 若未携带 gap/expected_gain，Harness 拒绝执行并返回 DENIED。"""
    conv = ConversationContext.from_request("StampServer 部署与端口", [])
    conv.head_entity = "StampServer"
    pool = EvidencePool(question_id="q")

    async def retrieve(args):
        doc = _doc("c1", "StampServer 部署")
        doc["metadata"]["document_entity"] = "StampServer"
        pool.add_retrieve([doc], query=args["query"], head_entity="StampServer", target_entity="StampServer")
        return ToolObservation(tool="retrieve_kb", ok=True, summary="ok")

    decisions = iter([
        AgentDecision(action="tool_call", tool="retrieve_kb", arguments={"query": "StampServer 部署"}, source="llm"),
        # 第二轮未提供 gap 与 expected_gain
        AgentDecision(action="tool_call", tool="retrieve_kb", arguments={"query": "StampServer 端口"}, source="llm"),
        # 第三轮纠正，提供 gap
        AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "StampServer 端口"},
            gap="StampServer 端口配置",
            expected_gain="获取默认管理端口",
            source="llm",
        ),
        AgentDecision(action="finalize", source="llm"),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=5),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": retrieve},
        cfg=SimpleNamespace(
            agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
        ),
        decide_fn=lambda *_: next(decisions),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert "missing_retrieval_gap" in result.fallbacks
    # 第 2 步被拦截
    step2 = result.agent_steps[1]
    assert step2["guard"]["allowed"] is False
    assert step2["guard"]["reason"] == "missing_retrieval_gap"
    assert step2["progress"] == ToolProgressStatus.DENIED
    assert step2["evidence_delta"] == {
        "new_chunks": 0,
        "new_entities": 0,
        "new_relations": 0,
        "evidence_version_before": 1,
        "evidence_version_after": 1,
        "status": ToolProgressStatus.DENIED,
    }
    assert loop.continuous_no_progress_count == 1
    # 实际执行了 2 次工具（第 1 步与第 3 步）
    assert len(result.tools) == 2


def test_exhausted_gap_denied_by_harness():
    """验证针对相同 target 重复探索已无增量的 Gap 时被 Harness 拦截。"""
    conv = ConversationContext.from_request("StampServer 架构", [])
    conv.head_entity = "StampServer"
    pool = EvidencePool(question_id="q")

    async def retrieve(args):
        # 第一次返回空
        pool.add_retrieve([], query=args["query"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="empty")

    decisions = iter([
        # 第 1 步：尝试 gap A，未召回（NO_PROGRESS）
        AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "StampServer 架构 1", "target_entity": "StampServer"},
            gap="StampServer 架构图",
            expected_gain="架构文档",
            source="llm",
        ),
        # 第 2 步：再次尝试相同的已耗尽 gap A，应被 Harness 拦截
        AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "StampServer 架构 2", "target_entity": "StampServer"},
            gap="StampServer 架构图",
            expected_gain="架构文档",
            source="llm",
        ),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=2),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": retrieve},
        cfg=SimpleNamespace(
            agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
        ),
        decide_fn=lambda *_: next(decisions),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert "exhausted_gap" in result.fallbacks


def test_two_consecutive_no_progress_triggers_fuse_and_blocks_further_exploration():
    """验证连续 2 次探索工具返回 NO_PROGRESS 时，触发 exploration_fuse_open 熔断。"""
    conv = ConversationContext.from_request("未知实体未知问题", [])
    pool = EvidencePool(question_id="q")

    async def retrieve(args):
        pool.add_retrieve([], query=args["query"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="empty")

    decisions = iter([
        AgentDecision(action="tool_call", tool="retrieve_kb", arguments={"query": "q1"}, source="llm"),
        AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "q2"},
            gap="第一个未覆盖缺口",
            expected_gain="验证第二个独立检索目标",
            source="llm",
        ),
        # 第三轮再次尝试探索工具，应被熔断拒绝
        AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "q3"},
            gap="新缺口",
            expected_gain="新增益",
            source="llm",
        ),
    ])

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=3),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": retrieve},
        cfg=SimpleNamespace(
            agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
        ),
        decide_fn=lambda *_: next(decisions),
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert loop._exploration_fuse_open is True
    assert "exploration_fuse_open" in result.fallbacks


def test_duplicate_graph_relation_is_no_progress_not_a_new_chunk():
    conv = ConversationContext.from_request(
        "StampServer 依赖什么？", [], entity_name="StampServer",
    )
    pool = EvidencePool(question_id="q")

    async def expand_graph_scope(_args):
        pool.add_relation(
            relation_key="StampServer -[depends_on]-> Redis",
            target_entity="StampServer",
            admission_verdict="PASS",
        )
        return ToolObservation(tool="expand_graph_scope", ok=True, summary="relation")

    decisions = iter([
        AgentDecision(
            action="tool_call",
            tool="expand_graph_scope",
            arguments={"start_entities": ["StampServer"], "additional_hops": 1, "direction": "both"},
            source="llm",
        ),
        AgentDecision(
            action="tool_call",
            tool="expand_graph_scope",
            arguments={"start_entities": ["StampServer"], "additional_hops": 1, "direction": "both"},
            source="llm",
        ),
    ])
    result = asyncio.run(AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=2),
        registry=build_agent_registry(),
        handlers={"expand_graph_scope": expand_graph_scope},
        cfg=SimpleNamespace(
            agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
        ),
        decide_fn=lambda *_: next(decisions),
        tool_timeout=0,
    ).run())

    assert result.tools[0]["evidence_delta"]["new_relations"] == 1
    assert result.tools[0]["evidence_delta"]["new_chunks"] == 0
    assert len(result.tools) == 1
    assert "tool_cycle_detected" in result.fallbacks


def test_budget_exhaustion_never_creates_partial_snapshot():
    """Budget/fuse may stop exploration, but only Main may authorize PARTIAL finalization."""
    conv = ConversationContext.from_request("StampServer 部署与配置", [])
    conv.head_entity = "StampServer"
    pool = EvidencePool(question_id="q")

    async def retrieve(args):
        doc = _doc("c1", "StampServer 部署说明")
        doc["metadata"]["document_entity"] = "StampServer"
        pool.add_retrieve(
            [doc],
            query=args["query"],
            head_entity="StampServer",
            target_entity="StampServer",
        )
        return ToolObservation(tool="retrieve_kb", ok=True, summary="ok")

    decisions = iter([
        AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "StampServer 部署"},
            source="llm",
        ),
    ])

    result = asyncio.run(AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=1),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": retrieve},
        cfg=SimpleNamespace(
            agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
        ),
        decide_fn=lambda *_: next(decisions),
        tool_timeout=0,
    ).run())

    assert result.terminal_action == "step_budget_exhausted"
    assert result.evidence_snapshot is None
    assert result.answer_context is None


def test_budget_exhaustion_never_creates_partial_snapshot_without_main_finalize():
    """Budget/Fuse can stop exploration, but only Main may authorize PARTIAL answer publication."""
    conv = ConversationContext.from_request("StampServer 部署与端口", [])
    conv.head_entity = "StampServer"
    pool = EvidencePool(question_id="q")

    async def retrieve(args):
        pool.add_retrieve(
            [_doc("c1", "StampServer 部署说明")],
            query=args["query"],
            head_entity="StampServer",
            target_entity="StampServer",
        )
        return ToolObservation(tool="retrieve_kb", ok=True, summary="ok")

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=1),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": retrieve},
        cfg=SimpleNamespace(
            agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
        ),
        decide_fn=lambda *_: AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "StampServer 部署"},
            source="llm",
        ),
        tool_timeout=0,
    )

    result = asyncio.run(loop.run())

    assert result.terminal_action == "step_budget_exhausted"
    assert result.evidence_snapshot is None
    assert result.answer_context is None
    assert result.answer_contract == {}


def test_finalization_rejected_observation_loop_closure():
    """验证 Finalization 门禁拒绝后，作为 ToolObservation 回传给 Main，由 Main 自主决策补检并成功闭环。"""
    conv = ConversationContext.from_request("StampServer 部署与配置", [])
    conv.head_entity = "StampServer"
    pool = EvidencePool(question_id="q")

    async def retrieve(args):
        doc = _doc("c1", "StampServer 完整配置手册")
        doc["metadata"]["document_entity"] = "StampServer"
        pool.add_retrieve([doc], query=args["query"], head_entity="StampServer", target_entity="StampServer")
        return ToolObservation(tool="retrieve_kb", ok=True, summary="ok")

    observed_tools: list[str] = []

    def decide(_conv, _pool, observations):
        for obs in observations:
            observed_tools.append(obs.get("tool"))
        if not observations:
            # 第一轮直接尝试 finalize（无证据将被拒）
            return AgentDecision(action="finalize", source="llm")
        last_obs = observations[-1]
        if last_obs.get("tool") == "finalize" and not last_obs.get("ok"):
            # Main 控制器看到 Finalize 门禁拒绝及 Observation 中的缺口，自主决定调用 retrieve_kb 补检
            return AgentDecision(
                action="tool_call",
                tool="retrieve_kb",
                arguments={"query": "StampServer 完整配置手册"},
                gap="StampServer 配置事实",
                expected_gain="获取完整配置手册",
                source="llm",
            )
        return AgentDecision(
            action="finalize",
            arguments={"answer_mode": "partial"},
            source="llm",
        )

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=4),
        registry=build_agent_registry(),
        handlers={"retrieve_kb": retrieve},
        cfg=SimpleNamespace(
            agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
        ),
        decide_fn=decide,
        tool_timeout=0,
    )
    result = asyncio.run(loop.run())
    assert result.finalization_rejections == 1
    assert result.terminal_action == "controller_finalize"
    assert "finalize" in observed_tools
    assert result.evidence_snapshot is not None
