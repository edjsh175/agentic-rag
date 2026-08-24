import pytest
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


def test_merge_clarification_candidates_dedup_and_unresolved():
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

    model_suggestions = [
        {"label": "PipelineWebGL", "rationale": "WebGL pipeline"},
        {"label": "PipelineCloudRender", "rationale": "Cloud render pipeline"},
    ]

    merged = merge_clarification_candidates(
        system_candidates=system_seeds,
        model_suggested_options=model_suggestions,
        include_other=True,
    )

    labels = [m.label for m in merged]
    assert "PipelineWebGL" in labels
    assert "PipelineBuilder" in labels
    assert "PipelineCloudRender" in labels
    assert any("以上都不是" in m.label for m in merged)

    cloud_opt = next(m for m in merged if m.label == "PipelineCloudRender")
    assert cloud_opt.binding_status == "unresolved"
    assert cloud_opt.source == "model_suggested"
    assert cloud_opt.filter.entity_name is None


def test_identity_scope_resolution_confirmed_entity_and_topic():
    constraints = {
        "entity_type_by_name": {
            "PipelineWebGL": "module",
            "PipelineBuilder": "module",
        },
        "canonical_by_alias": {
            "webgl pipeline": "PipelineWebGL",
        },
    }

    # 1. Canonical selection -> CONFIRMED_ENTITY
    scope_entity = IdentityScopeResolver.resolve(
        None,
        clarification_selected="PipelineWebGL",
        constraints=constraints,
    )
    assert scope_entity.identity_status == "confirmed_entity"
    assert scope_entity.primary_entity == "PipelineWebGL"
    assert scope_entity.confirmed_entity == "PipelineWebGL"
    assert scope_entity.confirmed_topic is None

    # 2. Novel topic selection not in backbone -> CONFIRMED_TOPIC
    scope_topic = IdentityScopeResolver.resolve(
        None,
        clarification_selected="CloudRenderTopic",
        constraints=constraints,
    )
    assert scope_topic.identity_status == "confirmed_topic"
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
        "entity_type_by_name": {"PipelineWebGL": "module"},
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
    assert scope.identity_status == "confirmed_topic"
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


def test_conversation_callback_binds_system_and_resolved_model_candidates():
    constraints = {
        "entity_type_by_name": {"PipelineWebGL": "module"},
        "canonical_by_alias": {"webgl pipeline": "PipelineWebGL"},
    }
    with patch(
        "rag_knowledge.services.identity_scope.load_backbone_constraints",
        return_value=constraints,
    ):
        system = ConversationContext.from_request(
            "pipelien 是什么？",
            [],
            clarification_selected="PipelineWebGL",
            clarification_option_id="cand_01",
            clarification_selected_candidate={
                "id": "cand_01",
                "label": "PipelineWebGL",
                "canonical_name": "PipelineWebGL",
                "source": "backbone",
                "binding_status": "canonical",
            },
            clarification_selection_kind="option",
            understanding=_unbound_understanding("pipelien 是什么？"),
        )
        model = ConversationContext.from_request(
            "pipelien 是什么？",
            [],
            clarification_selected="webgl pipeline",
            clarification_option_id="model_01",
            clarification_selected_candidate={
                "id": "model_01",
                "label": "webgl pipeline",
                "canonical_name": "PipelineWebGL",
                "source": "model_suggested",
                "binding_status": "unresolved",
            },
            clarification_selection_kind="option",
            understanding=_unbound_understanding("pipelien 是什么？"),
        )

    for conversation in (system, model):
        assert conversation.clarification_callback is True
        assert conversation.identity_status == "confirmed_entity"
        assert conversation.confirmed_entity == "PipelineWebGL"
        assert conversation.head_entity == "PipelineWebGL"
        assert conversation.selected_entity == "PipelineWebGL"


def test_conversation_callback_keeps_unknown_model_candidate_as_topic():
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

    assert conversation.identity_status == "confirmed_topic"
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


def test_exploration_grant_failsafe_denies_unauthorized_and_topic():
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
    assert not auth.authorized
    assert auth.rejection_reason in {"target_not_authorized", "identity_not_confirmed"}
    auth_broad = resolver_unresolved.authorize(None)
    assert not auth_broad.authorized
    assert auth_broad.rejection_reason == "identity_not_confirmed"

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
    assert not auth_entity.authorized
    assert auth_entity.rejection_reason in {"confirmed_topic_cannot_grant_entity", "target_not_authorized"}

    # Plain text topic retrieve (no target_entity) is authorized as topic grant
    auth_plain = resolver_topic.authorize(None)
    assert auth_plain.authorized
    assert auth_plain.grant.source_type == "confirmed_topic"


@pytest.mark.anyio
async def test_agent_loop_rejected_target_memory_and_no_drop_target_recovery():
    conv = ConversationContext(
        user_question="pipelien 怎么配置",
        session=MagicMock(turns=[], focus=None, resolved_entity=None, last_sources=[]),
        head_entity=None,
        identity_status="unresolved",
        raw_entity_mention="pipelien",
    )
    evidence = EvidencePool(question_id="test_q1")
    budget = AgentBudget(max_steps=5, max_retrieve_attempts=2)
    registry = ToolRegistry()
    registry.register(ToolSpec(name="retrieve_kb", description="retrieval", input_schema={}))

    async def mock_retrieve(args):
        return ToolObservation(
            tool="retrieve_kb",
            ok=False,
            summary="探索目标未获得证据范围授权",
            error="exploration_not_authorized",
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
    assert ("pipelien", "retrieve_kb") in loop._rejected_targets

    obs2 = await loop._execute("retrieve_kb", {"target_entity": "pipelien", "query": "pipelien"})
    assert not obs2.ok
    assert obs2.error == "target_already_rejected"

    broad = await loop._execute("retrieve_kb", {"target_entity": None, "query": "pipelien"})
    assert not broad.ok
    assert broad.error == "broadening_after_target_rejection"


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

    # 4. Deny if any target in list is invalid
    auth_invalid = resolver.authorize(["StampServer", "UnknownTarget"])
    assert not auth_invalid.authorized
    assert auth_invalid.rejection_reason == "target_not_authorized"
