import json

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from rag_knowledge.services.agent_orchestration.models import (
    AgentBudget,
    AgentDecision,
    ConversationContext,
    EvidencePool,
    ToolObservation,
    ToolSpec,
)
from rag_knowledge.services.agent_orchestration.runtime import AgentLoop, ToolRegistry
from rag_knowledge.services.conversation_context import UnderstandingResult
from rag_knowledge.services.exploration_grant import (
    ExplorationGrantResolver,
    GrantAuthorization,
)
from rag_knowledge.services.identity_scope import (
    IdentityScope,
    IdentityScopeResolver,
    is_canonical_backbone_entity,
)
from rag_knowledge.services.evidence_scope import BindingStrength
from rag_knowledge.services.query_clarification import (
    CandidateDTO,
    ClarificationFilter,
    ClarificationOption,
    QueryClarificationService,
    merge_clarification_candidates,
)
from rag_knowledge.services.entity_candidate_resolver import get_entity_candidate_resolver


def test_merge_clarification_candidates_only_uses_verified_candidate_set():
    system_seeds = [
        ClarificationOption(
            id="opt_1",
            label="PipelineWebGL",
            filter=ClarificationFilter(entity_name="PipelineWebGL"),
            source="canonical",
            canonical_name="PipelineWebGL",
            entity_type="module",
            binding_status="confirmed_entity",
            score=1.0,
        ),
        ClarificationOption(
            id="opt_2",
            label="PipelineBuilder",
            filter=ClarificationFilter(entity_name="PipelineBuilder"),
            source="canonical",
            canonical_name="PipelineBuilder",
            entity_type="module",
            binding_status="confirmed_entity",
            score=0.9,
        ),
    ]

    merged = merge_clarification_candidates(
        system_candidates=system_seeds,
        include_other=True,
    )

    labels = [m.label for m in merged]
    assert "PipelineWebGL" in labels
    assert "PipelineBuilder" in labels
    # The merger has no model-option input, so a hallucination cannot be admitted.
    assert "PipelineCloudRender" not in labels
    assert any("以上都不是" in m.label for m in merged)


def test_identity_scope_requires_snapshot_backed_entity_selection():
    constraints = {
        "entity_type_by_name": {
            "PipelineWebGL": "Module",
            "PipelineBuilder": "Module",
        },
        "canonical_by_alias": {
            "webgl pipeline": "PipelineWebGL",
        },
    }

    resolver = get_entity_candidate_resolver(constraints=constraints)
    snapshot = resolver.create_clarification_snapshot(
        resolver.resolve_identity("PipelineWebGL")
    )
    # 1. Snapshot-backed selection -> CONFIRMED_ENTITY
    scope_entity = IdentityScopeResolver.resolve(
        None,
        clarification_selected="PipelineWebGL",
        clarification_option_id="a",
        clarification_snapshot_id=snapshot.clarification_id,
        selected_candidate=snapshot.display_candidates[0].to_dict(),
        constraints=constraints,
    )
    assert scope_entity.identity_status == "confirmed_entity"
    assert scope_entity.primary_entity == "PipelineWebGL"
    assert scope_entity.confirmed_entity == "PipelineWebGL"
    assert scope_entity.confirmed_topic is None

    # 2. A label without a server snapshot must not bind a topic or entity.
    scope_topic = IdentityScopeResolver.resolve(
        None,
        clarification_selected="CloudRenderTopic",
        constraints=constraints,
    )
    assert scope_topic.identity_status == "unresolved"
    assert scope_topic.primary_entity is None
    assert scope_topic.confirmed_entity is None
    assert scope_topic.confirmed_topic == "CloudRenderTopic"

    # 3. 'Other' selected -> UNRESOLVED
    scope_unresolved = IdentityScopeResolver.resolve(
        None,
        clarification_selected="以上都不是",
        constraints=constraints,
    )
    assert scope_unresolved.identity_status == "unresolved"
    assert scope_unresolved.primary_entity is None
    assert scope_unresolved.confirmed_topic is None


def test_callback_metadata_cannot_spoof_a_canonical_entity():
    constraints = {
        "entity_type_by_name": {"PipelineWebGL": "Module"},
        "canonical_by_alias": {},
    }
    scope = IdentityScopeResolver.resolve(
        None,
        clarification_selected="PipelineMagicServer",
        selected_candidate={
            "id": "model_01",
            "label": "PipelineMagicServer",
            "source": "model_suggested",
            "binding_status": "unresolved",
            "canonical_name": "PipelineWebGL",
        },
        constraints=constraints,
    )
    assert scope.identity_status == "unresolved"
    assert scope.confirmed_entity is None
    assert scope.confirmed_topic == "PipelineMagicServer"


def _unbound_understanding(question: str) -> UnderstandingResult:
    return UnderstandingResult(
        mode="retrieve",
        user_utterance=question,
        resolved_question=question,
        semantic_task_context={
            "resolved_question": question,
            "primary_entity": None,
            "mentioned_entities": [],
            "task_type": "unbound",
            "confidence": 1.0,
        },
    )


def test_conversation_callback_binds_snapshot_entity_id():
    constraints = {
        "entity_type_by_name": {"PipelineWebGL": "Module"},
        "canonical_by_alias": {"webgl pipeline": "PipelineWebGL"},
    }
    with patch(
        "rag_knowledge.services.identity_scope.load_backbone_constraints",
        return_value=constraints,
    ):
        resolver = get_entity_candidate_resolver(constraints=constraints)
        snapshot = resolver.create_clarification_snapshot(
            resolver.resolve_identity("PipelineWebGL")
        )
        selected = snapshot.display_candidates[0]
        system = ConversationContext.from_request(
            "pipelien 是什么？",
            [],
            clarification_selected=selected.canonical_name,
            clarification_option_id="a",
            clarification_snapshot_id=snapshot.clarification_id,
            clarification_selected_candidate={
                "entity_id": selected.entity_id,
                "label": selected.canonical_name,
                "canonical_name": selected.canonical_name,
                "source": "verified",
                "binding_status": "canonical",
            },
            clarification_selection_kind="option",
            understanding=_unbound_understanding("pipelien 是什么？"),
        )
    assert system.clarification_callback is True
    assert system.identity_status == "confirmed_entity"
    assert system.confirmed_entity == "PipelineWebGL"
    assert system.confirmed_entity_id == selected.entity_id
    assert system.head_entity == "PipelineWebGL"
    assert system.selected_entity == "PipelineWebGL"


def test_clarification_callback_replaces_stale_ambiguous_retrieval_query():
    constraints = {
        "entity_type_by_name": {"三维管线管理": "Module"},
        "canonical_by_alias": {},
    }
    understanding = UnderstandingResult(
        mode="retrieve",
        user_utterance="pipeline",
        resolved_question="pipeline",
        retrieval_queries=[{"text": "pipeline", "kind": "original", "weight": 1.0}],
        semantic_task_context={
            "resolved_question": "pipeline",
            "primary_entity": None,
            "mentioned_entities": [],
            "task_type": "unbound",
            "confidence": 1.0,
        },
    )

    with patch(
        "rag_knowledge.services.identity_scope.load_backbone_constraints",
        return_value=constraints,
    ):
        resolver = get_entity_candidate_resolver(constraints=constraints)
        snapshot = resolver.create_clarification_snapshot(
            resolver.resolve_identity("三维管线管理")
        )
        selected = snapshot.display_candidates[0]
        conversation = ConversationContext.from_request(
            "pipeline",
            [],
            clarification_selected=selected.canonical_name,
            clarification_option_id="a",
            clarification_snapshot_id=snapshot.clarification_id,
            clarification_selected_candidate={
                "entity_id": selected.entity_id,
                "label": selected.canonical_name,
                "canonical_name": selected.canonical_name,
                "source": "verified",
                "binding_status": "canonical",
            },
            clarification_selection_kind="option",
            understanding=understanding,
        )

    assert conversation.resolved_question == "三维管线管理 的相关信息"
    assert conversation.understanding is not None
    assert [item["text"] for item in conversation.understanding.retrieval_queries] == [
        "三维管线管理 的相关信息"
    ]
    assert all(
        item["text"].casefold() != "pipeline"
        for item in conversation.understanding.retrieval_queries
    )


def test_conversation_callback_rejects_option_without_snapshot():
    constraints = {
        "entity_type_by_name": {"PipelineWebGL": "module"},
        "canonical_by_alias": {},
    }
    with patch(
        "rag_knowledge.services.identity_scope.load_backbone_constraints",
        return_value=constraints,
    ):
        conversation = ConversationContext.from_request(
            "pipelien 是什么？",
            [],
            clarification_selected="PipelineMagicServer",
            clarification_option_id="model_02",
            clarification_selected_candidate={
                "id": "model_02",
                "label": "PipelineMagicServer",
                "source": "model_suggested",
                "binding_status": "unresolved",
            },
            clarification_selection_kind="option",
            understanding=_unbound_understanding("pipelien 是什么？"),
        )

    assert conversation.identity_status == "unresolved"
    assert conversation.scope.scope_reason == "clarification_snapshot_required"
    assert conversation.confirmed_topic == "PipelineMagicServer"
    assert conversation.confirmed_entity is None
    assert conversation.head_entity is None
    assert conversation.selected_entity is None


def test_conversation_other_free_text_stays_unresolved_and_reenters_main():
    constraints = {
        "entity_type_by_name": {"PipelineWebGL": "module"},
        "canonical_by_alias": {},
    }
    with patch(
        "rag_knowledge.services.identity_scope.load_backbone_constraints",
        return_value=constraints,
    ):
        conversation = ConversationContext.from_request(
            "pipelien 是什么？",
            [],
            clarification_option_id="other",
            clarification_selected_candidate={
                "id": "other",
                "label": "以上都不是",
                "source": "fixed_other",
                "binding_status": "other",
            },
            clarification_selection_kind="free_text",
            clarification_free_text="我指的是内部的发布流水线",
            understanding=_unbound_understanding("pipelien 是什么？"),
        )

    assert conversation.clarification_callback is True
    assert conversation.identity_status == "unresolved"
    assert conversation.confirmed_entity is None
    assert conversation.confirmed_topic is None
    assert conversation.head_entity is None
    assert "内部的发布流水线" in conversation.user_question


def test_exploration_grant_records_unbound_and_unverified_targets_as_exploration():
    constraints = {
        "entity_type_by_name": {
            "PipelineWebGL": "module",
        },
    }

    # Case 1: Unresolved entity (e.g. typo 'pipelien')
    scope_unresolved = IdentityScope(
        scope_id="s1",
        primary_entity=None,
        binding_strength=BindingStrength.UNBOUND,
        forbidden_rebindings=frozenset(),
        scope_reason="stage1_unbound",
        identity_status="unresolved",
        raw_entity_mention="pipelien",
    )

    resolver_unresolved = ExplorationGrantResolver(
        semantic_task=None,
        identity_scope=scope_unresolved,
        constraints=constraints,
    )
    auth = resolver_unresolved.authorize("pipelien")
    assert auth.authorized
    assert auth.grant.source_type == "exploratory_query"
    auth_broad = resolver_unresolved.authorize(None)
    assert auth_broad.authorized
    assert auth_broad.grant.target_entities == ()

    # Case 2: Confirmed topic cannot grant entity exploration
    scope_topic = IdentityScope(
        scope_id="s2",
        primary_entity=None,
        binding_strength=BindingStrength.UNBOUND,
        forbidden_rebindings=frozenset(),
        scope_reason="clarification_topic",
        identity_status="confirmed_topic",
        confirmed_topic="CloudRenderTopic",
    )
    resolver_topic = ExplorationGrantResolver(
        semantic_task=None,
        identity_scope=scope_topic,
        constraints=constraints,
    )
    auth_entity = resolver_topic.authorize("SomeEntity")
    assert auth_entity.authorized
    assert auth_entity.grant.source_type == "exploratory_query"

    # Plain text topic retrieve (no target_entity) is authorized as topic grant
    auth_plain = resolver_topic.authorize(None)
    assert auth_plain.authorized
    assert auth_plain.grant.source_type == "confirmed_topic"


@pytest.mark.anyio
async def test_agent_loop_rejected_target_remains_observation_and_allows_new_exploration():
    conv = ConversationContext(
        user_question="pipelien 怎么配置",
        session=MagicMock(turns=[], focus=None, resolved_entity=None, last_sources=[]),
        head_entity="pipeline",
        identity_status="confirmed_entity",
    )
    evidence = EvidencePool(question_id="test_q1")
    budget = AgentBudget(max_steps=5, max_retrieve_attempts=2)
    registry = ToolRegistry()
    registry.register(ToolSpec(name="retrieve_kb", description="retrieval", input_schema={}))

    calls = []

    async def mock_retrieve(args):
        calls.append(args.get("target_entity"))
        if args.get("target_entity") == "pipelien":
            return ToolObservation(
                tool="retrieve_kb",
                ok=False,
                summary="探索目标未获得证据范围授权",
                error="exploration_not_authorized",
            )
        return ToolObservation(
            tool="retrieve_kb",
            ok=True,
            summary="已切换到候选范围继续探索",
        )

    loop = AgentLoop(
        conversation=conv,
        evidence=evidence,
        budget=budget,
        registry=registry,
        handlers={"retrieve_kb": mock_retrieve},
        decide_fn=lambda *args: AgentDecision(action="finalize"),
    )

    obs1 = await loop._execute("retrieve_kb", {"target_entity": "pipelien", "query": "pipelien"})
    assert not obs1.ok

    obs2 = await loop._execute("retrieve_kb", {"target_entity": "pipeline", "query": "pipeline 配置"})
    assert obs2.ok
    assert calls == ["pipelien", "pipeline"]


def _unbound_identity_loop(identity_status: str, semantic_task=None) -> AgentLoop:
    conv = ConversationContext(
        user_question="pipeline",
        session=MagicMock(turns=[], focus=None, resolved_entity=None, last_sources=[]),
        head_entity=None,
        identity_status=identity_status,
        semantic_task=semantic_task,
    )
    registry = ToolRegistry()
    for name in ("retrieve_kb", "reuse_evidence", "clarify"):
        registry.register(ToolSpec(name=name, description=name, input_schema={}))

    async def mock_ok(args):
        return ToolObservation(tool="retrieve_kb", ok=True, summary="executed")

    return AgentLoop(
        conversation=conv,
        evidence=EvidencePool(question_id="identity_guard_test"),
        budget=AgentBudget(max_steps=5, max_retrieve_attempts=2),
        registry=registry,
        handlers={"retrieve_kb": mock_ok, "reuse_evidence": mock_ok, "clarify": mock_ok},
        decide_fn=lambda *args: AgentDecision(action="finalize"),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("identity_status", ["ambiguous_entity", "unresolved"])
async def test_unbound_identity_allows_working_evidence_exploration(identity_status):
    loop = _unbound_identity_loop(identity_status)

    # 未绑定仍可先取回 Working Evidence，随后由 Agent 决定是否澄清。
    obs = await loop._execute("retrieve_kb", {"target_entity": None, "query": "管线相关信息"})
    assert obs.ok

    # 历史证据同样只是 Working 候选，不能据此形成未绑定 answer_subject。
    obs_reuse = await loop._execute("reuse_evidence", {})
    assert obs_reuse.ok

    # Prompt 暴露取证和澄清，交由 Main 基于 Observation 决策。
    state = json.loads(loop._controller_state_for_prompt())
    assert state["identity_status"] == identity_status
    assert "allowed_tools" not in state
    visible_tools = loop._available_tool_names()
    assert "retrieve_kb" in visible_tools
    assert "reuse_evidence" in visible_tools
    assert "clarify" in visible_tools


@pytest.mark.anyio
async def test_unbound_identity_allows_targeted_exploration_as_observation():
    loop = _unbound_identity_loop("ambiguous_entity")
    obs = await loop._execute(
        "retrieve_kb", {"target_entity": "PipelineWebGL", "query": "PipelineWebGL"}
    )
    assert obs.ok


@pytest.mark.anyio
async def test_topic_task_without_binding_requirement_keeps_null_target_retrieval():
    semantic_task = SimpleNamespace(entity_binding_required=False)
    loop = _unbound_identity_loop("unresolved", semantic_task=semantic_task)

    obs = await loop._execute(
        "retrieve_kb", {"target_entity": None, "query": "公司有哪些产品线"}
    )
    assert obs.ok

    state = json.loads(loop._controller_state_for_prompt())
    assert "allowed_tools" not in state
    assert "retrieve_kb" in loop._available_tool_names()


@pytest.mark.anyio
async def test_not_required_identity_enters_null_target_retrieval_without_clarify():
    semantic_task = SimpleNamespace(entity_binding_required=False)
    loop = _unbound_identity_loop("not_required", semantic_task=semantic_task)

    obs = await loop._execute(
        "retrieve_kb", {"target_entity": None, "query": "系统架构分层有哪些"}
    )
    assert obs.ok

    # 主题型任务仍允许 Main 在检索与澄清之间做策略选择
    state = json.loads(loop._controller_state_for_prompt())
    assert state["identity_status"] == "not_required"
    assert "allowed_tools" not in state
    visible_tools = loop._available_tool_names()
    assert "retrieve_kb" in visible_tools
    assert "clarify" in visible_tools


def test_identity_scope_multi_entity_confirmed_set():
    constraints = {
        "entity_type_by_name": {
            "StampServer": "product",
            "StampTools": "product",
        },
    }
    semantic_task = MagicMock(
        mentioned_entities=["StampServer", "StampTools"],
        primary_entity="StampServer",
    )
    scope = IdentityScopeResolver.resolve(
        semantic_task,
        constraints=constraints,
    )
    assert scope.identity_status == "confirmed_entity"
    assert scope.primary_entity == "StampServer"
    assert scope.confirmed_entities == ("StampServer", "StampTools")
    assert scope.confirmed_entity == "StampServer"
    assert "StampServer" in scope.root_entities
    assert "StampTools" in scope.root_entities


def test_exploration_grant_multi_entity_authorizations():
    constraints = {
        "entity_type_by_name": {
            "StampServer": "product",
            "StampTools": "product",
        },
    }
    scope = IdentityScope(
        scope_id="s_multi",
        primary_entity="StampServer",
        binding_strength=BindingStrength.EXPLICIT,
        forbidden_rebindings=frozenset(),
        scope_reason="user_explicit_mention",
        identity_status="confirmed_entity",
        confirmed_entity="StampServer",
        confirmed_entities=("StampServer", "StampTools"),
    )
    resolver = ExplorationGrantResolver(
        semantic_task=MagicMock(mentioned_entities=["StampServer", "StampTools"]),
        identity_scope=scope,
        constraints=constraints,
    )

    # 1. Authorize second entity individually
    auth2 = resolver.authorize("StampTools")
    assert auth2.authorized
    assert auth2.grant.target_entities == ("StampTools",)
    assert auth2.grant.source_type == "user_explicit_mention"

    # 2. Authorize both entities as list
    auth_list = resolver.authorize(["StampServer", "StampTools"])
    assert auth_list.authorized
    assert auth_list.grant.target_entities == ("StampServer", "StampTools")

    # 3. Authorize both entities as comma-separated string
    auth_str = resolver.authorize("StampServer, StampTools")
    assert auth_str.authorized
    assert auth_str.grant.target_entities == ("StampServer", "StampTools")

    # 4. Unknown targets remain valid exploration hypotheses, not identity bindings.
    auth_invalid = resolver.authorize(["StampServer", "UnknownTarget"])
    assert auth_invalid.authorized
    assert auth_invalid.grant.source_type == "exploratory_query"
