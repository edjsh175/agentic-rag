from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from rag_knowledge.config import Config
from rag_knowledge.services.agent_orchestration.models import (
    AgentBudget,
    AgentDecision,
    AnswerGenerationContext,
    ConversationContext,
    EvidencePool,
    ToolObservation,
)
from rag_knowledge.services.agent_orchestration.evidence_gate import EvidenceGap
from rag_knowledge.services.conversation_context import UnderstandingResult
from rag_knowledge.services.agent_orchestration.runtime import (
    AgentLoop,
    FinalizationHandler,
    build_agent_registry,
    build_answer_generation_messages,
    normalize_decision_payload,
    parse_react_line_format,
)
from rag_knowledge.services.model_routing import ModelRoutePolicy
from rag_knowledge.services.query_contextualizer import QueryContextualizer, RetrievalQuery
from rag_knowledge.services.query_planner import QueryPlanner


def _doc(chunk_id: str, content: str = "StampServer 的端口是 8080") -> dict:
    return {
        "content": content,
        "metadata": {
            "chunk_id": chunk_id,
            "file_name": "server.md",
            "document_entity": "StampServer",
        },
    }


def test_evidence_gap_is_observation_without_recovery_instruction():
    gap = EvidenceGap(gap_type="missing_fact", missing="StampServer 端口")

    assert gap.to_dict() == {
        "gap_type": "missing_fact",
        "missing": "StampServer 端口",
    }
    assert not hasattr(gap, "recovery_strategy")
    assert not hasattr(gap, "query")


def test_model_route_policy_defaults_keep_agent_on_main():
    cfg = SimpleNamespace()
    policy = ModelRoutePolicy(cfg)
    assert policy.agent_controller_role() == "llm"
    assert policy.agent_answer_role() == "llm"
    assert policy.linear_preprocess_role() == "helper_llm"


def test_agent_controller_uses_main_and_normalizes_legacy_finish(isolated_storage):
    isolated_storage()
    Config._instance = None
    cfg = Config()
    conv = ConversationContext.from_request("StampServer 是什么", [])
    pool = EvidencePool(question_id="q")
    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=AgentBudget(max_steps=2),
        registry=build_agent_registry(),
        handlers={},
        cfg=cfg,
        tool_timeout=0,
    )
    with patch(
        "rag_knowledge.llm_http.chat_role",
        return_value='{"action":"finalize","tool":null,"arguments":{}}',
    ) as mocked:
        decision = loop._decide_via_llm()
    assert decision.action == "finalize"
    assert mocked.call_args.args[1] == "llm"
    assert mocked.call_args.kwargs["stage"] == "agent_controller"


def test_finalize_rejection_returns_observation_then_controller_retrieves():
    conv = ConversationContext.from_request("StampServer 的端口是多少", [])
    pool = EvidencePool(question_id="q")
    decisions = iter(
        [
            AgentDecision(action="finalize"),
            AgentDecision(
                action="tool_call",
                tool="retrieve_kb",
                arguments={"query": "StampServer 端口"},
            ),
            AgentDecision(action="finalize", focus_evidence_ids=("c1",)),
        ]
    )

    async def retrieve(args):
        pool.add_retrieve([_doc("c1")], query=args["query"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="ok")

    events = []

    async def on_event(event):
        events.append(event)

    result = asyncio.run(
        AgentLoop(
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
        ).run(on_event=on_event)
    )
    assert result.terminal_action == "controller_finalize"
    assert result.finalization_attempts == 2
    assert result.finalization_rejections == 1
    assert result.evidence_snapshot is not None
    assert result.evidence.citable_docs() == []
    assert result.answer_context is not None
    rejected = next(event for event in events if event["type"] == "finalization_rejected")
    assert "next_action" not in rejected["data"]
    assert not any(event["type"] == "thinking" for event in events)


def test_rejected_finalize_observation_contains_no_recovery_action():
    conv = ConversationContext.from_request("StampServer 的端口是多少", [])
    pool = EvidencePool(question_id="q")
    observed_finalize = []

    def decide(_conversation, _evidence, observations):
        if not observations:
            return AgentDecision(action="finalize")
        latest = observations[-1]
        if latest.get("tool") == "finalize":
            observed_finalize.append(latest)
            return AgentDecision(
                action="tool_call",
                tool="retrieve_kb",
                arguments={"query": "StampServer 端口"},
                gap="StampServer 端口配置",
                expected_gain="获取端口数值",
            )
        return AgentDecision(action="finalize")

    async def retrieve(args):
        pool.add_retrieve([_doc("c1")], query=args["query"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="ok")

    result = asyncio.run(
        AgentLoop(
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
        ).run()
    )

    assert result.finalization_rejections == 1
    assert result.retrieve_attempts == 1
    assert result.terminal_action == "controller_finalize"
    assert len(observed_finalize) == 1
    assert "next_action" not in observed_finalize[0]
    assert "next_action" not in observed_finalize[0]["data"]
    assert any(
        step.get("controller", {}).get("tool") == "retrieve_kb"
        for step in result.agent_steps
    )


def test_retrieve_with_no_new_chunks_freezes_answer_snapshot():
    conv = ConversationContext.from_request("StampServer 的端口是多少", [])
    pool = EvidencePool(question_id="q")
    decisions = iter([
        AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "StampServer 端口 1"},
        ),
        AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "StampServer 端口 2"},
            gap="未覆盖端口",
            expected_gain="补充端口信息",
        ),
        AgentDecision(action="finalize"),
    ])

    async def retrieve(args):
        if "端口 1" in args["query"]:
            pool.add_retrieve([_doc("c1")], query=args["query"])
        else:
            pool.add_retrieve([], query=args["query"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="ok")

    events = []

    async def on_event(event):
        events.append(event)

    result = asyncio.run(
        AgentLoop(
            conversation=conv,
            evidence=pool,
            budget=AgentBudget(max_steps=4),
            registry=build_agent_registry(),
            handlers={"retrieve_kb": retrieve},
            cfg=SimpleNamespace(
                agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
            ),
            decide_fn=lambda *_: next(decisions),
            tool_timeout=0,
        ).run(on_event=on_event)
    )

    assert result.terminal_action == "controller_finalize"
    assert result.evidence_snapshot is not None
    assert "retrieve_no_new_evidence" in result.fallbacks


def test_retrieve_no_new_evidence_does_not_auto_query_graph_for_fact_question():
    conv = ConversationContext.from_request("PipelineWebGL", [])
    pool = EvidencePool(question_id="q")
    decisions = iter([
        AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "PipelineWebGL 产品关系 1"},
        ),
        AgentDecision(
            action="tool_call",
            tool="retrieve_kb",
            arguments={"query": "PipelineWebGL 产品关系 2"},
            gap="未覆盖产品关系",
            expected_gain="补充关系",
        ),
        AgentDecision(action="finalize"),
    ])

    async def retrieve(args):
        doc = _doc("c1", "PipelineWebGL 的三维管线功能")
        doc["metadata"]["document_entity"] = "PipelineWebGL"
        pool.add_retrieve(
            [doc],
            query=args["query"],
            head_entity="PipelineWebGL",
            target_entity="PipelineWebGL",
        )
        return ToolObservation(tool="retrieve_kb", ok=True, summary="ok")

    async def link(_args):
        pool.add_relation(
            relation_key="PipelineWebGL -[belongs_to]-> WebGL",
            target_entity="PipelineWebGL",
            provenance=[{
                "source_entity": "PipelineWebGL",
                "target_entity": "WebGL",
                "relation_type": "belongs_to",
                "source_ref": "relation:r1",
            }],
        )
        return ToolObservation(tool="link_entities", ok=True, summary="relation=1")

    result = asyncio.run(
        AgentLoop(
            conversation=conv,
            evidence=pool,
            budget=AgentBudget(max_steps=4),
            registry=build_agent_registry(),
            handlers={"retrieve_kb": retrieve, "link_entities": link},
            cfg=SimpleNamespace(
                agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
            ),
            decide_fn=lambda *_: next(decisions),
            tool_timeout=0,
        ).run()
    )

    assert result.terminal_action == "controller_finalize"
    assert not any(group.kind == "relation" for group in result.evidence.groups)
    assert not any(tool["name"] == "link_entities" for tool in result.tools)


def test_missing_relation_recovers_with_graph_relation_retrieval():
    conv = ConversationContext.from_request(
        "ModelBuilder 和 UEModelBuilder 有什么区别？", []
    )
    pool = EvidencePool(question_id="q")
    for entity, chunk_id in (("ModelBuilder", "model"), ("UEModelBuilder", "ue")):
        doc = _doc(chunk_id, f"{entity} 的说明")
        doc["metadata"]["document_entity"] = entity
        pool.add_retrieve([doc], query=entity, target_entity=entity)
    decisions = iter([
        AgentDecision(action="finalize"),
        AgentDecision(
            action="tool_call",
            tool="link_entities",
            arguments={"query": "ModelBuilder 和 UEModelBuilder 区别", "target_entity": "ModelBuilder"},
            thought="缺少实体关系证据，调用 link_entities 探索图谱",
        ),
        AgentDecision(action="finalize"),
    ])

    async def link(_args):
        pool.add_relation(
            relation_key="ModelBuilder -[different_from]-> UEModelBuilder",
            target_entity="ModelBuilder",
        )
        return ToolObservation(tool="link_entities", ok=True, summary="relation=1")

    result = asyncio.run(
        AgentLoop(
            conversation=conv,
            evidence=pool,
            budget=AgentBudget(max_steps=4),
            registry=build_agent_registry(),
            handlers={"link_entities": link},
            cfg=SimpleNamespace(
                agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
            ),
            decide_fn=lambda *_: next(decisions),
            tool_timeout=0,
        ).run()
    )

    assert result.terminal_action == "controller_finalize"
    assert any(tool["name"] == "link_entities" for tool in result.tools)
    graph_call = next(tool for tool in result.tools if tool["name"] == "link_entities")
    assert graph_call["data"] == {}


def test_duplicate_retrieve_denial_returns_to_controller_for_finalization():
    conv = ConversationContext.from_request("StampServer 的端口是多少", [])
    pool = EvidencePool(question_id="q")
    decisions = iter(
        [
            AgentDecision(
                action="tool_call",
                tool="retrieve_kb",
                arguments={"query": "StampServer 端口", "mode": "hybrid"},
            ),
            AgentDecision(
                action="tool_call",
                tool="retrieve_kb",
                arguments={"query": "StampServer 端口", "mode": "hybrid"},
            ),
            AgentDecision(action="finalize"),
        ]
    )

    async def retrieve(args):
        pool.add_retrieve([_doc("c1")], query=args["query"])
        return ToolObservation(tool="retrieve_kb", ok=True, summary="ok")

    result = asyncio.run(
        AgentLoop(
            conversation=conv,
            evidence=pool,
            budget=AgentBudget(max_steps=4),
            registry=build_agent_registry(),
            handlers={"retrieve_kb": retrieve},
            cfg=SimpleNamespace(
                agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
            ),
            decide_fn=lambda *_: next(decisions),
            tool_timeout=0,
        ).run()
    )

    assert len(result.tools) == 1
    assert "tool_cycle_detected" in result.fallbacks
    denied_step = result.agent_steps[1]
    assert denied_step["guard"] == {
        "allowed": False,
        "reason": "tool_cycle_detected",
    }
    assert denied_step["observation"]["status"] == "DENIED"
    assert result.terminal_action == "controller_finalize"


def test_controller_finish_is_normalized_before_the_answer_stage():
    conv = ConversationContext.from_request("StampServer 的端口是多少", [])
    pool = EvidencePool(question_id="q")
    pool.add_retrieve([_doc("c1")], query="StampServer 端口")
    events = []

    async def on_event(event):
        events.append(event)

    result = asyncio.run(
        AgentLoop(
            conversation=conv,
            evidence=pool,
            budget=AgentBudget(max_steps=2),
            registry=build_agent_registry(),
            handlers={},
            cfg=SimpleNamespace(
                agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
            ),
            decide_fn=lambda *_: AgentDecision(action="finish", source="llm"),
            tool_timeout=0,
        ).run(on_event=on_event)
    )
    assert result.terminal_action == "controller_finalize"
    assert result.evidence_snapshot is not None
    assert result.answer_context is not None
    assert result.evidence.citable_docs() == []
    event_types = [event["type"] for event in events]
    assert "finalization_requested" in event_types
    assert "evidence_snapshot_created" in event_types
    assert event_types.index("finalization_requested") < event_types.index("evidence_snapshot_created")
    trace = result.to_trace()
    assert trace["terminal_action"] == "controller_finalize"
    assert trace["evidence_snapshot_version"] == result.evidence_snapshot.evidence_version


def test_direct_chat_finalize_does_not_consume_budget_on_empty_evidence():
    conv = ConversationContext.from_request("我们刚才聊了什么？", [])
    pool = EvidencePool(question_id="q")
    events = []

    async def on_event(event):
        events.append(event)

    result = asyncio.run(
        AgentLoop(
            conversation=conv,
            evidence=pool,
            budget=AgentBudget(max_steps=3),
            registry=build_agent_registry(),
            handlers={},
            cfg=SimpleNamespace(
                agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
            ),
            decide_fn=lambda *_: AgentDecision(action="finish", source="llm"),
            tool_timeout=0,
        ).run(on_event=on_event)
    )

    assert result.terminal_action == "controller_finalize"
    assert result.budget["steps_used"] == 1
    assert result.finalization_attempts == 1
    assert result.finalization_rejections == 0
    assert result.evidence_snapshot is None
    assert result.answer_contract == {
        "answer_type": "direct_chat",
        "evidence_required": False,
        "answer_mode": "full",
    }
    assert result.answer_gate["answer_type"] == "direct_chat"
    assert result.answer_gate["reason"] == "evidence_not_required"
    assert result.route == "direct"
    event_types = [event["type"] for event in events]
    assert event_types.count("finalization_requested") == 1
    assert "finalization_rejected" not in event_types
    assert "evidence_snapshot_created" not in event_types


def test_finalize_rejects_multi_entity_gap_before_answer_stage():
    conv = ConversationContext.from_request(
        "ModelBuilder 和 UEModelBuilder 有什么区别？", []
    )
    pool = EvidencePool(question_id="q")
    model_doc = _doc("c1", "ModelBuilder 的说明")
    model_doc["metadata"]["document_entity"] = "ModelBuilder"
    pool.add_retrieve(
        [model_doc],
        query="ModelBuilder",
        target_entity="ModelBuilder",
    )
    result = asyncio.run(
        AgentLoop(
            conversation=conv,
            evidence=pool,
            budget=AgentBudget(max_steps=1),
            registry=build_agent_registry(),
            handlers={},
            cfg=SimpleNamespace(
                agent_orchestration=SimpleNamespace(terminal_finalization_v2=True),
            ),
            decide_fn=lambda *_: AgentDecision(action="finalize"),
            tool_timeout=0,
        ).run()
    )
    assert result.finalization_rejections == 1
    assert result.terminal_action == "step_budget_exhausted"
    assert result.evidence_snapshot is None
    assert result.answer_context is None
    assert result.answer_contract == {
        "answer_type": "knowledge",
        "evidence_required": True,
        "answer_mode": "full",
    }


def test_finalization_separates_admissibility_from_overview_coverage():
    conv = ConversationContext.from_request("PipelineWebGL 是什么", [])
    pool = EvidencePool(question_id="q")
    doc = _doc("c1", "PipelineWebGL 支持场景浏览设置。")
    doc["metadata"]["document_entity"] = "PipelineWebGL"
    pool.add_retrieve([doc], query="PipelineWebGL")

    result = FinalizationHandler(conv, pool).evaluate()

    verdict = result["evidence_verdict"]
    assert verdict["admissibility"] == "VALID"
    assert verdict["coverage"] == "PARTIAL"
    assert verdict["can_answer"] is False
    assert result["status"] == "finalization_rejected"
    assert result["reason"] == "missing_fact"
    assert "next_action" not in result
    assert "recovery_strategy" not in result


def test_agent_snapshot_bypasses_generation_pack():
    from types import SimpleNamespace

    from rag_knowledge.services.rag import RagChain

    chain = object.__new__(RagChain)
    docs = [{"content": "fact", "metadata": {"citation_id": 1}}]
    packed = chain._pack_agent_answer_context(
        SimpleNamespace(answer_context=SimpleNamespace(documents=lambda: docs)),
        docs,
        "[1] fact",
        [{"role": "user", "content": "history"}],
        "question",
    )

    assert packed.source_docs == docs
    assert packed.source_docs is not docs
    assert "[1]" in packed.context
    assert "fact" in packed.context
    assert packed.decision.reason == "frozen_evidence_snapshot"
    assert packed.decision.removed_chunks == 0


def test_agent_evidence_snapshot_without_answer_context_still_bypasses_pack():
    from rag_knowledge.services.rag import RagChain

    pool = EvidencePool(question_id="q")
    pool.add_retrieve([_doc("c1", "完整事实")], query="q")
    snapshot = pool.create_snapshot(verdict={"verdict": "SUFFICIENT"})
    chain = object.__new__(RagChain)
    chain._pack_for_generation = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("frozen snapshot must not be repacked")
    )

    packed = chain._pack_agent_answer_context(
        SimpleNamespace(answer_context=None, evidence_snapshot=snapshot),
        [],
        "",
        [],
        "q",
    )

    assert packed.source_docs == snapshot.documents()
    assert packed.decision.reason == "frozen_evidence_snapshot"


def test_agent_answer_docs_fail_closed_without_frozen_snapshot():
    from rag_knowledge.services.rag import RagChain

    chain = object.__new__(RagChain)
    result = SimpleNamespace(
        answer_context=None,
        evidence_snapshot=None,
        answer_gate={"allow_knowledge_answer": True},
        evidence=SimpleNamespace(citable_docs=lambda: [_doc("live")]),
    )

    assert chain._agent_answer_docs(result) == ([], [])


def test_grounded_retry_includes_candidate_v1_and_complete_review_payload():
    from rag_knowledge.services.rag import RagChain

    captured = {}

    class _Llm:
        def invoke(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(content="Candidate V2")

    chain = object.__new__(RagChain)
    chain._build_llm = lambda _model: _Llm()
    original_messages = [
        {"role": "system", "content": "Frozen Evidence Snapshot: 完整事实"},
        {"role": "user", "content": "原问题"},
    ]
    review_payload = {
        "verdict": "REVISE",
        "coverage": "PARTIAL",
        "claim_reviews": [
            {
                "claim_id": "c2",
                "status": "unsupported",
                "evidence_ids": [],
            }
        ],
        "rewrite_actions": [
            {
                "claim_id": "c2",
                "action": "rewrite_to_supported_scope_or_remove",
                "instruction": "缩回证据支持范围",
            }
        ],
    }
    review = SimpleNamespace(
        rewrite_actions=review_payload["rewrite_actions"],
        to_dict=lambda: review_payload,
    )

    answer = chain._retry_grounded_candidate(
        "main-model",
        original_messages,
        "Candidate V1 原文",
        [{"content": "完整事实", "metadata": {"citation_id": 1}}],
        review,
    )

    contents = [message.content for message in captured["messages"]]
    assert answer == "Candidate V2"
    assert contents[:2] == [item["content"] for item in original_messages]
    assert contents[2] == "Candidate V1 原文"
    assert '"claim_id": "c2"' in contents[3]
    assert '"instruction": "缩回证据支持范围"' in contents[3]
    assert original_messages == [
        {"role": "system", "content": "Frozen Evidence Snapshot: 完整事实"},
        {"role": "user", "content": "原问题"},
    ]


def test_evidence_snapshot_and_answer_context_are_immutable():
    pool = EvidencePool(question_id="q")
    pool.add_retrieve([_doc("c1")], query="q")
    snapshot = pool.create_snapshot(verdict={"verdict": "SUFFICIENT"})
    context = AnswerGenerationContext.from_snapshot(
        original_question="q",
        resolved_question="q",
        conversation_context="历史仅用于指代",
        snapshot=snapshot,
    )
    assert snapshot.documents()[0]["metadata"]["chunk_id"] == "c1"
    assert context.documents()[0]["metadata"]["chunk_id"] == "c1"
    assert isinstance(snapshot.evidence_items, tuple)
    assert isinstance(context.evidence_items, tuple)
    assert snapshot.evidence_version == pool.evidence_version
    assert context.evidence_version == snapshot.evidence_version
    pool.add_retrieve([_doc("c2")], query="later")
    assert [item["metadata"]["chunk_id"] for item in snapshot.documents()] == ["c1"]


def test_understanding_result_deep_freezes_nested_payloads():
    result = UnderstandingResult(
        retrieval_queries=[{"text": "q", "alternatives": ["a"]}],
        filters={"entities": ["StampServer"]},
    )
    for mutate in (
        lambda: result.retrieval_queries.append({"text": "new"}),
        lambda: result.retrieval_queries[0]["alternatives"].append("b"),
        lambda: result.filters["entities"].append("Other"),
    ):
        try:
            mutate()
        except TypeError:
            continue
        raise AssertionError("UnderstandingResult nested payload must be immutable")


def test_answer_prompt_excludes_tools_and_raw_agent_trace():
    pool = EvidencePool(question_id="q")
    pool.add_retrieve([_doc("c1")], query="q")
    snapshot = pool.create_snapshot(verdict={"verdict": "SUFFICIENT"})
    context = AnswerGenerationContext.from_snapshot(
        original_question="q",
        resolved_question="q",
        conversation_context="当前实体为 StampServer",
        snapshot=snapshot,
    )
    system, user = [item["content"] for item in build_answer_generation_messages(context)]
    assert "retrieve_kb" not in system
    assert "tool schema" not in system.lower()
    assert "Thought" not in user
    assert "Observation" not in user
    assert "<evidence_snapshot>" in user


def test_answer_prompt_uses_snapshot_citations_and_excludes_history_facts():
    pool = EvidencePool(question_id="q")
    pool.add_relation(
        relation_key="PipelineWebGL -[belongs_to]-> WebGL",
        target_entity="PipelineWebGL",
        provenance=[{
            "source_ref": "relation:r1",
            "relation_type": "belongs_to",
        }],
    )
    pool.add_retrieve([_doc("c1", "PipelineWebGL 支持三维管线查询")], query="pipeline")
    snapshot = pool.create_snapshot(verdict={"verdict": "SUFFICIENT"})
    context = AnswerGenerationContext.from_snapshot(
        original_question="pipeline",
        resolved_question="PipelineWebGL pipeline",
        conversation_context=(
            "- 当前主体身份: PipelineWebGL\n"
            "- 图谱关联背景: PipelineWebGL 属于 WebGL [14]\n"
            "- 近期对话历史:\n  助手: 旧答案 [22]"
        ),
        snapshot=snapshot,
    )

    system, user = [item["content"] for item in build_answer_generation_messages(context)]
    assert "本轮合法引用编号只有：[1], [2]" in system
    assert "[14]" not in user
    assert "[22]" not in user
    assert "<graph_relations>" in user
    assert "PipelineWebGL -[belongs_to]-> WebGL" in user


def test_linear_preprocess_calls_helper_and_agent_mode_preserves_controller_query(isolated_storage):
    isolated_storage()
    Config._instance = None
    cfg = Config()
    planner = QueryPlanner(cfg)
    planner._classify_via_llm = lambda question, **kwargs: (
        "config",
        0.95,
    )
    plan = planner.plan(
        "StampServer 端口",
        [RetrievalQuery("StampServer 端口", "controller", 1.0)],
        mode="agent",
        controller_intent="exact_parameter",
    )
    assert plan.planner_role == "deterministic"
    assert plan.queries[0].text == "StampServer 端口"
    assert plan.intent == "exact_parameter"

    overview = planner.plan(
        "PipelineWebGL pipeline",
        [RetrievalQuery("PipelineWebGL pipeline", "controller", 1.0)],
        mode="agent",
        controller_intent="conceptual_overview",
    )
    assert overview.planner_role == "deterministic"
    assert overview.queries[0].text == "PipelineWebGL pipeline"
    assert any(
        query.kind == "agent_intent" and "概述" in query.text
        for query in overview.queries
    )

    contextualizer = QueryContextualizer(cfg)
    with patch(
        "rag_knowledge.llm_http.chat_role",
        return_value='{"standalone_query":"q","search_queries":["q"],"is_context_dependent":false,"confidence":1}',
    ) as mocked:
        contextualizer._contextualize_via_llm("q", "", "", "")
    assert mocked.call_args.args[1] == "helper_llm"
    assert mocked.call_args.kwargs["stage"] == "common_stage1"


def test_react_parser_accepts_finalize_control_action():
    parsed = parse_react_line_format("Thought: 证据足够\nAction: finalize")
    assert parsed is not None
    assert parsed["action"] == "finalize"


def test_legacy_finish_normalizes_to_finalize_without_accepting_a_tool():
    normalized = normalize_decision_payload({"action": "finish", "tool": None})
    assert normalized["action"] == "finalize"
    assert normalized["tool"] is None

    try:
        normalize_decision_payload({"action": "finalize", "tool": "retrieve_kb"})
    except ValueError as exc:
        assert "cannot carry tool" in str(exc)
    else:
        raise AssertionError("finalize with a tool must be rejected")


def test_finalization_rollout_flag_can_fall_back_to_compatibility_path():
    conv = ConversationContext.from_request("StampServer 是什么", [])
    pool = EvidencePool(question_id="q")
    cfg = SimpleNamespace(
        agent_orchestration=SimpleNamespace(terminal_finalization_v2=False),
    )
    result = asyncio.run(
        AgentLoop(
            conversation=conv,
            evidence=pool,
            budget=AgentBudget(max_steps=1),
            registry=build_agent_registry(),
            handlers={},
            cfg=cfg,
            decide_fn=lambda *_: AgentDecision(action="finalize"),
            tool_timeout=0,
        ).run()
    )
    assert result.terminal_action == "finish_compat"
    assert result.evidence_snapshot is None
