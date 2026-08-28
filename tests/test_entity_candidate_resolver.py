"""Tests for EntityCandidateResolver and Identity Resolution (PRD 2026-08-27)."""
from __future__ import annotations

import pytest

from rag_knowledge.services.agent_orchestration.models import ConversationContext
from rag_knowledge.services.dialogue_understanding import SemanticTaskContext
from rag_knowledge.services.entity_candidate_resolver import (
    EntityCandidate,
    EntityCandidateResolver,
    EntityRegistry,
    IdentityResolution,
    ClarificationSnapshotStore,
    get_entity_candidate_resolver,
)
from rag_knowledge.services.identity_scope import IdentityScopeResolver
from rag_knowledge.services.query_clarification import (
    ClarificationOption,
    QueryClarificationService,
    candidate_to_option,
    merge_clarification_candidates,
)


def _custom_constraints() -> dict:
    return {
        "entity_type_by_name": {
            "StampServer": "Product",
            "StampTools": "Product",
            "StampWebGL": "Product",
            "StampWebRTC": "Product",
            "StampNodeServer": "Product",
            "PipelineWebGL": "Product",
            "PipelineBuilder": "Tool",
            "PipelineWebRTC": "Product",
            "管线发布服务": "Service",
            "管线更新服务": "Service",
            "se_pipeline.so": "Service",  # non-entity, should be excluded
        },
        "canonical_by_alias": {
            "StampServer": "StampServer",
            "StampGIS Server": "StampServer",
            "StampTools": "StampTools",
            "StampWebGL": "StampWebGL",
            "StampWebRTC": "StampWebRTC",
            "StampNodeServer": "StampNodeServer",
            "PipelineWebGL": "PipelineWebGL",
            "Pipeline WebGL": "PipelineWebGL",
            "管线 WebGL": "PipelineWebGL",
            "PipelineBuilder": "PipelineBuilder",
            "PipelineWebRTC": "PipelineWebRTC",
            "管线 WebRTC": "PipelineWebRTC",
            "管线发布服务": "管线发布服务",
            "管线更新服务": "管线更新服务",
            "se_pipeline.so": "se_pipeline.so",
        },
        "doc_category_by_name": {
            "StampServer": "StampServer",
            "StampTools": "StampTools",
            "StampWebGL": "StampWebGL",
            "StampWebRTC": "StampWebRTC",
            "StampNodeServer": "StampServer",
            "PipelineWebGL": "StampWebGL",
            "PipelineBuilder": "StampTools",
            "PipelineWebRTC": "StampWebRTC",
            "管线发布服务": "StampServer",
            "管线更新服务": "StampServer",
        },
        "belongs_to": {
            "PipelineBuilder": {"StampTools"},
            "PipelineWebGL": {"StampWebGL"},
            "PipelineWebRTC": {"StampWebRTC"},
            "管线发布服务": {"StampServer"},
            "管线更新服务": {"StampServer"},
        },
        "different_from": [
            ("PipelineWebGL", "StampWebGL"),
            ("PipelineWebRTC", "StampWebRTC"),
        ],
        "relations": [
            {"source": "PipelineBuilder", "relation_type": "belongs_to", "target": "StampTools"},
            {"source": "PipelineWebGL", "relation_type": "different_from", "target": "StampWebGL"},
        ],
    }


def _family_root_constraints() -> dict:
    """Registry fixture for bare family surfaces and concrete implementations."""
    constraints = _custom_constraints()
    constraints["entity_type_by_name"].update({"WebGL": "Product", "WebRTC": "Product"})
    constraints["canonical_by_alias"].update({"WebGL": "WebGL", "WebRTC": "WebRTC"})
    return constraints


@pytest.fixture
def resolver() -> EntityCandidateResolver:
    return EntityCandidateResolver(constraints=_custom_constraints())


# -----------------------------------------------------------------------------
# A. Exact Entity Tests
# -----------------------------------------------------------------------------

def test_exact_canonical_match_stampserver(resolver: EntityCandidateResolver):
    res = resolver.resolve_identity("StampServer")
    assert res.status == "confirmed"
    assert res.confirmed_entity_name == "StampServer"
    assert res.confidence == 1.0
    assert len(res.candidates) >= 1
    assert res.candidates[0].canonical_name == "StampServer"


def test_exact_canonical_match_pipelinewebgl(resolver: EntityCandidateResolver):
    res = resolver.resolve_identity("PipelineWebGL")
    assert res.status == "confirmed"
    assert res.confirmed_entity_name == "PipelineWebGL"
    assert res.confidence == 1.0
    assert res.candidates[0].canonical_name == "PipelineWebGL"


def test_alias_match_resolves_to_canonical(resolver: EntityCandidateResolver):
    res = resolver.resolve_identity("Pipeline WebGL")
    assert res.status == "confirmed"
    assert res.confirmed_entity_name == "PipelineWebGL"
    assert "alias" in res.candidates[0].match_sources


# -----------------------------------------------------------------------------
# B. Vague English Surface Terms
# -----------------------------------------------------------------------------

def test_vague_surface_pipeline_ambiguous(resolver: EntityCandidateResolver):
    res = resolver.resolve_identity("pipeline")
    assert res.status == "ambiguous"
    assert res.confirmed_entity_name is None
    c_names = [c.canonical_name for c in res.candidates]
    assert "PipelineWebGL" in c_names
    assert "PipelineBuilder" in c_names
    assert "PipelineWebRTC" in c_names
    assert "se_pipeline.so" not in c_names


def test_vague_surface_server_recalls_candidates(resolver: EntityCandidateResolver):
    res = resolver.resolve_identity("server")
    assert len(res.candidates) >= 2
    c_names = [c.canonical_name for c in res.candidates]
    assert "StampServer" in c_names
    assert "StampNodeServer" in c_names


def test_vague_surface_builder_recalls_builder(resolver: EntityCandidateResolver):
    res = resolver.resolve_identity("builder")
    c_names = [c.canonical_name for c in res.candidates]
    assert "PipelineBuilder" in c_names


@pytest.mark.parametrize(
    ("surface", "expected_root", "expected_implementations"),
    [
        ("WebGL", "WebGL", {"StampWebGL", "PipelineWebGL"}),
        ("WebRTC", "WebRTC", {"StampWebRTC", "PipelineWebRTC"}),
    ],
)
def test_bare_family_surface_uses_generic_identity_ranking(
    surface: str,
    expected_root: str,
    expected_implementations: set[str],
):
    """Bare surfaces retain their generic recall after family-specific logic is removed."""
    result = EntityCandidateResolver(constraints=_family_root_constraints()).resolve_identity(surface)

    assert result.status == "confirmed"
    assert result.confirmed_entity_name == expected_root
    candidate_names = [candidate.canonical_name for candidate in result.candidates]
    assert candidate_names[0] == expected_root
    assert expected_implementations <= set(candidate_names)


# -----------------------------------------------------------------------------
# C. Vague Chinese Terms
# -----------------------------------------------------------------------------

def test_vague_chinese_guanxian(resolver: EntityCandidateResolver):
    res = resolver.resolve_identity("管线")
    assert res.status == "ambiguous"
    c_names = [c.canonical_name for c in res.candidates]
    assert "PipelineWebGL" in c_names
    assert "PipelineWebRTC" in c_names


def test_vague_chinese_service(resolver: EntityCandidateResolver):
    res = resolver.resolve_identity("管线发布服务")
    assert res.status == "confirmed"
    assert res.confirmed_entity_name == "管线发布服务"


# -----------------------------------------------------------------------------
# D. Spelling Typos / Fuzzy Match
# -----------------------------------------------------------------------------

def test_spelling_typo_pipelien(resolver: EntityCandidateResolver):
    res = resolver.resolve_identity("pipelien")
    assert len(res.candidates) >= 1
    c_names = [c.canonical_name for c in res.candidates]
    assert any("Pipeline" in name for name in c_names)


def test_spelling_typo_stampsever(resolver: EntityCandidateResolver):
    res = resolver.resolve_identity("StampSever")
    assert len(res.candidates) >= 1
    assert res.candidates[0].canonical_name == "StampServer"


# -----------------------------------------------------------------------------
# E. Nonsense / Unresolved
# -----------------------------------------------------------------------------

def test_nonexistent_surface_unresolved(resolver: EntityCandidateResolver):
    res = resolver.resolve_identity("zyx987654321random")
    assert res.status == "unresolved"
    assert res.confirmed_entity_name is None


# -----------------------------------------------------------------------------
# F. different_from does not admit fake candidates
# -----------------------------------------------------------------------------

def test_different_from_does_not_admit_candidates(resolver: EntityCandidateResolver):
    res = resolver.resolve_identity("PipelineBuilder")
    c_names = [c.canonical_name for c in res.candidates]
    assert "PipelineBuilder" == c_names[0]
    assert "StampWebGL" not in c_names


# -----------------------------------------------------------------------------
# G. Top-K Capping & Fixed "以上都不是"
# -----------------------------------------------------------------------------

def test_clarification_options_capped_at_max_and_has_single_other(resolver: EntityCandidateResolver):
    candidates = resolver.discover_candidates("pipeline")
    system_opts = [candidate_to_option(c) for c in candidates]

    merged = merge_clarification_candidates(
        system_candidates=system_opts,
        include_other=True,
        max_options=4,
    )

    meaningful = [m for m in merged if m.source != "fixed_other"]
    assert len(meaningful) <= 4
    assert merged[-1].id == "other"
    assert merged[-1].label == "以上都不是"
    assert merged[-1].source == "fixed_other"


# -----------------------------------------------------------------------------
# H. P0-2: Option ID vs Entity ID Separation
# -----------------------------------------------------------------------------

def test_option_id_and_entity_id_separation(resolver: EntityCandidateResolver):
    candidates = resolver.discover_candidates("pipeline")
    system_opts = [candidate_to_option(c) for c in candidates]
    merged = merge_clarification_candidates(system_opts, include_other=True)

    # First option should have option_id = "a", and a valid ent_xxx entity_id
    opt0 = merged[0]
    assert opt0.id == "a"
    assert opt0.entity_id is not None
    assert opt0.entity_id.startswith("ent_")

    # Serialized dict should have option_id="a" and entity_id="ent_xxx"
    d0 = opt0.to_dict()
    assert d0["id"] == "a"
    assert d0["option_id"] == "a"
    assert d0["entity_id"] == opt0.entity_id
    assert d0["candidate_id"] == opt0.entity_id

    # 'other' option has entity_id = None
    opt_other = merged[-1]
    assert opt_other.id == "other"
    assert opt_other.entity_id is None
    assert opt_other.to_dict()["entity_id"] is None


# -----------------------------------------------------------------------------
# I. P0-1: Identity Resolution Precedes ControllerState
# -----------------------------------------------------------------------------

def test_identity_resolution_precedes_controller_state():
    constraints = _custom_constraints()
    scope = IdentityScopeResolver.resolve(
        None,
        question="pipeline 的配置方法是什么？",
        constraints=constraints,
    )

    assert scope.identity_status == "ambiguous_entity"
    assert len(scope.candidate_entities) >= 2
    assert scope.identity_resolution is not None
    assert scope.identity_resolution.status == "ambiguous"

    ctx = ConversationContext.from_request(
        "pipeline 的配置方法是什么？",
        history=None,
    )
    prompt = ctx.to_prompt()
    assert "当前主体候选（存在歧义）" in prompt
    assert "ambiguous" in prompt


# -----------------------------------------------------------------------------
# J. P0-3: Authoritative Snapshot Callback Closed Loop
# -----------------------------------------------------------------------------

def test_snapshot_creation_and_callback_validation(resolver: EntityCandidateResolver):
    resolution = resolver.resolve_identity("pipeline")
    snapshot = resolver.create_clarification_snapshot(resolution, max_options=4)

    assert snapshot.clarification_id.startswith("clar_")
    assert len(snapshot.display_candidates) <= 4

    valid_id = snapshot.display_candidates[0].entity_id
    validated_ent = resolver.validate_callback_selection(valid_id, snapshot_id=snapshot.clarification_id)
    assert validated_ent is not None
    assert validated_ent.canonical_name == snapshot.display_candidates[0].canonical_name

    # Fake ID rejected
    invalid_ent = resolver.validate_callback_selection("ent_fake123", snapshot_id=snapshot.clarification_id)
    assert invalid_ent is None

    if len(snapshot.candidates) > len(snapshot.display_candidates):
        hidden = snapshot.candidates[len(snapshot.display_candidates)]
        assert resolver.validate_callback_selection(
            hidden.entity_id,
            snapshot_id=snapshot.clarification_id,
        ) is None

    # Other returns None
    other_ent = resolver.validate_callback_selection("other", snapshot_id=snapshot.clarification_id)
    assert other_ent is None


def test_snapshot_store_expires_and_bounds_process_local_callbacks():
    now = [100.0]
    store = ClarificationSnapshotStore(ttl_seconds=5, max_entries=2, clock=lambda: now[0])
    resolver = EntityCandidateResolver(constraints=_custom_constraints(), snapshot_store=store)

    first = resolver.create_clarification_snapshot(resolver.resolve_identity("pipeline"))
    assert first.expires_at == 105.0
    assert resolver.get_snapshot(first.clarification_id) is first

    now[0] = 101.0
    second = resolver.create_clarification_snapshot(resolver.resolve_identity("pipeline"))
    now[0] = 102.0
    third = resolver.create_clarification_snapshot(resolver.resolve_identity("pipeline"))
    assert resolver.get_snapshot(first.clarification_id) is None
    assert resolver.get_snapshot(second.clarification_id) is second

    now[0] = 108.0
    assert resolver.get_snapshot(second.clarification_id) is None
    assert resolver.get_snapshot(third.clarification_id) is None


def test_authoritative_snapshot_callback_via_identity_scope():
    constraints = _custom_constraints()
    resolver = get_entity_candidate_resolver(constraints=constraints)
    resolution = resolver.resolve_identity("pipeline")
    snapshot = resolver.create_clarification_snapshot(resolution, max_options=4)

    # 1. Valid snapshot selection -> Locks confirmed_entity
    cand0 = snapshot.display_candidates[0]
    scope_valid = IdentityScopeResolver.resolve(
        None,
        clarification_selected=cand0.canonical_name,
        clarification_option_id="a",
        clarification_snapshot_id=snapshot.clarification_id,
        constraints=constraints,
    )
    assert scope_valid.identity_status == "confirmed_entity"
    assert scope_valid.confirmed_entity == cand0.canonical_name
    assert scope_valid.confirmed_entity_id == cand0.entity_id

    # 2. Tampered candidate selection not in snapshot -> Rejected as mismatch / unresolved
    scope_tampered = IdentityScopeResolver.resolve(
        None,
        clarification_selected="MadeUpEntity",
        clarification_option_id="z",
        clarification_snapshot_id=snapshot.clarification_id,
        constraints=constraints,
    )
    assert scope_tampered.identity_status in {"unresolved", "confirmed_topic"}
    assert scope_tampered.confirmed_entity is None


def test_callback_without_snapshot_fails_closed():
    constraints = _custom_constraints()
    resolver = get_entity_candidate_resolver(constraints=constraints)

    assert resolver.validate_callback_selection("PipelineWebGL") is None
    scope = IdentityScopeResolver.resolve(
        None,
        clarification_selected="PipelineWebGL",
        constraints=constraints,
    )
    assert scope.identity_status == "unresolved"
    assert scope.confirmed_entity is None
    assert scope.scope_reason == "clarification_snapshot_required"


# -----------------------------------------------------------------------------
# L. P0-1: entity_binding_required → not_required 四态贯通
# -----------------------------------------------------------------------------

def test_resolve_identity_not_required_skips_recall(resolver: EntityCandidateResolver):
    res = resolver.resolve_identity("pipeline", entity_binding_required=False)
    assert res.status == "not_required"
    assert res.candidates == ()
    assert res.confirmed_entity_name is None
    assert res.reason == "entity_binding_not_required"


def test_identity_scope_topic_task_resolves_to_not_required():
    constraints = _custom_constraints()
    semantic_task = SemanticTaskContext(
        resolved_question="系统架构分层有哪些？",
        primary_entity=None,
        mentioned_entities=(),
        task_type="unbound",
        confidence=1.0,
        entity_binding_required=False,
    )
    scope = IdentityScopeResolver.resolve(
        semantic_task,
        question="系统架构分层有哪些？",
        constraints=constraints,
    )
    assert scope.identity_status == "not_required"
    assert scope.primary_entity is None
    assert scope.confirmed_entity is None
    assert scope.scope_reason == "entity_binding_not_required"
    # 召回始终执行（歧义检测不依赖绑定标志）；此处召回为空
    assert scope.identity_resolution is not None
    assert scope.identity_resolution.status == "unresolved"
    assert scope.candidate_entities == ()


def test_not_required_does_not_inherit_previous_entity():
    """主题型问题不得继承上一轮主体（防漂移）。"""
    constraints = _custom_constraints()
    semantic_task = SemanticTaskContext(
        resolved_question="系统架构分层有哪些？",
        primary_entity=None,
        mentioned_entities=(),
        task_type="unbound",
        confidence=1.0,
        entity_binding_required=False,
    )
    scope = IdentityScopeResolver.resolve(
        semantic_task,
        question="系统架构分层有哪些？",
        previous_confirmed_entity="PipelineWebGL",
        constraints=constraints,
    )
    assert scope.identity_status == "not_required"
    assert scope.primary_entity is None
    assert scope.confirmed_entity is None


# -----------------------------------------------------------------------------
# K. QueryClarificationService Integration
# -----------------------------------------------------------------------------

def test_query_clarification_service_analyze(resolver: EntityCandidateResolver):
    svc = QueryClarificationService(
        enabled=True,
        llm_enabled=False,
        constraints=_custom_constraints(),
    )

    # Exact match -> needs_clarification is False
    res_exact = svc.analyze("StampServer")
    assert res_exact.needs_clarification is False

    # Ambiguous term -> needs_clarification is True
    res_ambiguous = svc.analyze("pipeline")
    assert res_ambiguous.needs_clarification is True
    assert res_ambiguous.clarification_snapshot_id
    snapshot = resolver.get_snapshot(res_ambiguous.clarification_snapshot_id)
    assert snapshot is not None
    option_entity_ids = [opt.entity_id for opt in res_ambiguous.options if opt.entity_id]
    assert option_entity_ids == [candidate.entity_id for candidate in snapshot.display_candidates]
    assert len(res_ambiguous.options) >= 2
    assert any("PipelineWebGL" in opt.label for opt in res_ambiguous.options)
    assert res_ambiguous.options[-1].label == "以上都不是"
