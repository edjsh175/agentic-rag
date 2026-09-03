from __future__ import annotations

import asyncio

import pytest

from rag_knowledge.services.agent_orchestration.models import (
    AgentBudget,
    AgentDecision,
    ConversationContext,
    EvidencePool,
)
from rag_knowledge.services.agent_orchestration.runtime import (
    AgentLoop,
    ComposeAnswerHandler,
    build_agent_registry,
    normalize_decision_payload,
)


def test_compose_answer_is_main_tool_and_reviewer_is_not_registered():
    registry = build_agent_registry()

    assert "compose_answer" in registry.names()
    assert "reviewer" not in registry.names()
    assert "grounding_reviewer" not in registry.names()


def test_controller_protocol_rejects_finalize_and_answer_type():
    with pytest.raises(ValueError, match="finalize is retired"):
        normalize_decision_payload({"action": "finalize"})
    with pytest.raises(ValueError, match="answer_type is retired"):
        AgentLoop(
            conversation=ConversationContext.from_request("你好", []),
            evidence=EvidencePool(question_id="protocol"),
            budget=AgentBudget(max_steps=1),
            registry=build_agent_registry(),
            handlers={},
        )._decision_from_raw(
            '{"action":"tool_call","tool":"compose_answer",'
            '"answer_type":"direct_chat","arguments":{}}'
        )


def test_compose_answer_freezes_empty_snapshot_without_preemptive_rejection():
    conversation = ConversationContext.from_request("给我起三个标题", [])
    pool = EvidencePool(question_id="empty-snapshot")

    result = ComposeAnswerHandler(conversation, pool).compose()

    assert result["status"] == "accepted"
    assert result["evidence_snapshot"] is not None
    assert result["evidence_snapshot"].documents() == []
    assert result["answer_contract"] == {"answer_mode": "full"}


def test_main_compose_answer_creates_answer_context_on_zero_evidence():
    conversation = ConversationContext.from_request("你好", [])
    pool = EvidencePool(question_id="main-compose")
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)
    loop = AgentLoop(
        conversation=conversation,
        evidence=pool,
        budget=AgentBudget(max_steps=1),
        registry=build_agent_registry(),
        handlers={},
        decide_fn=lambda *_: AgentDecision(
            action="tool_call",
            tool="compose_answer",
            arguments={"answer_mode": "full"},
        ),
    )

    result = asyncio.run(loop.run(on_event=on_event))

    assert result.terminal_action == "controller_compose_answer"
    assert result.answer_context is not None
    assert [d for d in result.answer_context.documents() if (d.get("metadata") or {}).get("citable", True) is not False] == []
    assert [event["type"] for event in events if event["type"] == "tool_start"]
    assert any(
        event["data"].get("name") == "compose_answer"
        for event in events
        if event["type"] == "tool_result"
    )


def test_strong_immediate_context_uses_direct_candidate_then_publication_gate():
    loop = AgentLoop(
        conversation=ConversationContext.from_request("你刚才为什么反问我？", []),
        evidence=EvidencePool(question_id="direct-candidate"),
        budget=AgentBudget(max_steps=1),
        registry=build_agent_registry(),
        handlers={},
        decide_fn=lambda *_: AgentDecision(
            action="direct_candidate",
            candidate="我刚才反问，是因为当前表达需要澄清。",
        ),
    )

    result = asyncio.run(loop.run())

    assert result.terminal_action == "controller_direct_candidate"
    assert result.direct_candidate == "我刚才反问，是因为当前表达需要澄清。"
    assert result.answer_context is None
    assert result.evidence_snapshot is not None
    assert result.evidence_snapshot.citable_documents() == []
    assert len(result.evidence_snapshot.documents()) > 0
    assert normalize_decision_payload({
        "action": "direct_candidate",
        "candidate": result.direct_candidate,
        "arguments": {},
    })["action"] == "direct_candidate"


@pytest.mark.parametrize(
    "question",
    [
        "如何使用pipelienbuilder",
        "那 PipelineBuilder 怎么部署？",
        "它还有哪些参数？",
    ],
)
def test_gold_formal_questions_end_with_compose_answer(question: str):
    loop = AgentLoop(
        conversation=ConversationContext.from_request(question, []),
        evidence=EvidencePool(question_id=f"gold-formal-{question}"),
        budget=AgentBudget(max_steps=1),
        registry=build_agent_registry(),
        handlers={},
        decide_fn=lambda *_: AgentDecision(
            action="tool_call",
            tool="compose_answer",
            arguments={"answer_mode": "full"},
        ),
    )

    result = asyncio.run(loop.run())

    assert result.terminal_action == "controller_compose_answer"
    assert result.answer_context is not None


@pytest.mark.parametrize(
    "question",
    [
        "你刚才为什么反问我？",
        "我什么时候说过 PipelineWebGL？",
        "刚才到底有没有真的弹出澄清卡？",
    ],
)
def test_gold_meta_conversation_can_only_end_as_pending_direct_candidate(question: str):
    loop = AgentLoop(
        conversation=ConversationContext.from_request(question, []),
        evidence=EvidencePool(question_id=f"gold-meta-{question}"),
        budget=AgentBudget(max_steps=1),
        registry=build_agent_registry(),
        handlers={},
        decide_fn=lambda *_: AgentDecision(
            action="direct_candidate",
            candidate="这是基于当前会话状态生成的待审答复。",
        ),
    )

    result = asyncio.run(loop.run())

    assert result.terminal_action == "controller_direct_candidate"
    assert result.direct_candidate
    assert result.answer_context is None


def test_reviewer_finding_is_visible_to_main_without_creating_a_gap_contract():
    finding = {
        "affected_claim_ids": ["c1"],
        "rewrite_actions": [{"claim_id": "c1", "action": "correct_to_evidence"}],
    }
    loop = AgentLoop(
        conversation=ConversationContext.from_request("你刚才为什么反问我？", []),
        evidence=EvidencePool(question_id="reviewer-finding"),
        budget=AgentBudget(max_steps=1),
        registry=build_agent_registry(),
        handlers={},
        initial_observations=[{
            "tool": "reviewer_finding",
            "ok": False,
            "status": "REWRITE",
            "summary": "请基于即时上下文修正 Candidate。",
            "data": finding,
        }],
    )

    assert loop.gap_contract is None
    assert "reviewer_finding" in loop._observation_history_for_prompt()
    assert "correct_to_evidence" in loop._observation_history_for_prompt()


def test_reviewer_feedback_remains_the_only_gap_contract_source():
    loop = AgentLoop(
        conversation=ConversationContext.from_request("端口是多少？", []),
        evidence=EvidencePool(question_id="reviewer-feedback"),
        budget=AgentBudget(max_steps=1),
        registry=build_agent_registry(),
        handlers={},
        initial_observations=[{
            "tool": "reviewer_feedback",
            "ok": False,
            "status": "RETRIEVE",
            "summary": "缺少端口证据。",
            "data": {
                "gap_id": "port",
                "affected_claim_ids": ["c1"],
                "missing_fact": "默认端口",
            },
        }],
    )

    assert loop.gap_contract is not None
    assert loop.gap_contract["gap_id"] == "port"
