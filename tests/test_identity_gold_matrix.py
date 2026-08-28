"""Deterministic Gold Matrix Test Runner for Identity & Clarification Subsystem (PRD 2026-08-27).

JSON Fixture is the SINGLE SOURCE OF TRUTH: tests/fixtures/identity_resolution_gold_v1.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rag_knowledge.services.dialogue_understanding import SemanticTaskContext
from rag_knowledge.services.entity_candidate_resolver import (
    EntityCandidateResolver,
    get_entity_candidate_resolver,
)
from rag_knowledge.services.identity_scope import IdentityScopeResolver

GOLD_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "identity_resolution_gold_v1.json"


def _load_gold_cases() -> list[dict[str, Any]]:
    with open(GOLD_FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases", [])


_GOLD_CASES = _load_gold_cases()


def _custom_constraints() -> dict[str, Any]:
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
            "se_pipeline.so": "Service",  # non-entity library, excluded from allowed entity types
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


def _family_root_constraints() -> dict[str, Any]:
    constraints = _custom_constraints()
    constraints["entity_type_by_name"].update({"WebGL": "Product", "WebRTC": "Product"})
    constraints["canonical_by_alias"].update({"WebGL": "WebGL", "WebRTC": "WebRTC"})
    return constraints


@pytest.fixture
def default_resolver() -> EntityCandidateResolver:
    return EntityCandidateResolver(constraints=_custom_constraints())


@pytest.fixture
def family_root_resolver() -> EntityCandidateResolver:
    return EntityCandidateResolver(constraints=_family_root_constraints())


@pytest.mark.parametrize(
    "case",
    _GOLD_CASES,
    ids=[c["id"] for c in _GOLD_CASES],
)
def test_identity_gold_matrix(
    case: dict[str, Any],
    default_resolver: EntityCandidateResolver,
    family_root_resolver: EntityCandidateResolver,
):
    case_id = case["id"]
    category = case["category"]
    component = case.get("target_component", "resolver")
    query = case.get("query", "")
    expected = case.get("expected", {})
    profile = case.get("constraints_profile", "default")
    resolver = family_root_resolver if profile == "family_root" else default_resolver
    constraints = _family_root_constraints() if profile == "family_root" else _custom_constraints()

    if component == "resolver":
        res = resolver.resolve_identity(query)
        candidate_names = [c.canonical_name for c in res.candidates]

        if "status" in expected:
            assert res.status == expected["status"], (
                f"[{case_id}] Status mismatch: query='{query}', expected_status='{expected['status']}', actual_status='{res.status}'"
            )

        if "confirmed_entity" in expected:
            assert res.confirmed_entity_name == expected["confirmed_entity"], (
                f"[{case_id}] Confirmed entity mismatch: query='{query}', "
                f"expected_entity='{expected['confirmed_entity']}', actual_entity='{res.confirmed_entity_name}'"
            )

        if "top_candidate" in expected:
            top = candidate_names[0] if candidate_names else None
            assert top == expected["top_candidate"], (
                f"[{case_id}] Top candidate mismatch: query='{query}', "
                f"expected_top='{expected['top_candidate']}', actual_top='{top}'"
            )

        if "confidence_min" in expected and res.confidence is not None:
            assert res.confidence >= expected["confidence_min"], (
                f"[{case_id}] Confidence too low: query='{query}', "
                f"expected_min={expected['confidence_min']}, actual={res.confidence}"
            )

        if "candidates_count" in expected:
            assert len(res.candidates) == expected["candidates_count"], (
                f"[{case_id}] Candidate count mismatch: query='{query}', "
                f"expected_count={expected['candidates_count']}, actual_count={len(res.candidates)}"
            )

        for req in expected.get("must_include_candidates", []):
            assert req in candidate_names, (
                f"[{case_id}] Missing required candidate '{req}' in recall list: {candidate_names} for query='{query}'"
            )

        for exc in expected.get("must_exclude_candidates", []):
            assert exc not in candidate_names, (
                f"[{case_id}] Forbidden candidate '{exc}' present in recall list: {candidate_names} for query='{query}'"
            )

    elif component == "identity_scope":
        st_data = case.get("semantic_task", {})
        sem_task = SemanticTaskContext(
            resolved_question=st_data.get("resolved_question", query),
            primary_entity=st_data.get("primary_entity"),
            mentioned_entities=tuple(st_data.get("mentioned_entities", ())),
            task_type=st_data.get("task_type", "single_entity_detail"),
            confidence=1.0,
            entity_binding_required=st_data.get("entity_binding_required", True),
        )
        ctx = case.get("context", {})
        scope = IdentityScopeResolver.resolve(
            sem_task,
            question=query,
            previous_confirmed_entity=ctx.get("previous_confirmed_entity"),
            constraints=constraints,
        )

        if "identity_status" in expected:
            assert scope.identity_status == expected["identity_status"], (
                f"[{case_id}] IdentityScope status mismatch: query='{query}', "
                f"expected_status='{expected['identity_status']}', actual_status='{scope.identity_status}'"
            )

        if "confirmed_entity" in expected:
            assert scope.confirmed_entity == expected["confirmed_entity"], (
                f"[{case_id}] IdentityScope confirmed_entity mismatch: query='{query}', "
                f"expected_entity='{expected['confirmed_entity']}', actual_entity='{scope.confirmed_entity}'"
            )

        if "scope_reason" in expected:
            assert scope.scope_reason == expected["scope_reason"], (
                f"[{case_id}] IdentityScope scope_reason mismatch: query='{query}', "
                f"expected_reason='{expected['scope_reason']}', actual_reason='{scope.scope_reason}'"
            )

    elif component == "callback":
        action = case.get("callback_action", "")
        resolution = resolver.resolve_identity(query)
        snapshot = resolver.create_clarification_snapshot(resolution, max_options=4)

        if action == "select_other":
            val_result = resolver.validate_callback_selection("other", snapshot_id=snapshot.clarification_id)
            assert val_result is None, f"[{case_id}] validate_callback_selection('other') should return None"

            scope = IdentityScopeResolver.resolve(
                None,
                clarification_selected="other",
                clarification_snapshot_id=snapshot.clarification_id,
                constraints=constraints,
            )
            assert scope.identity_status == "unresolved"
            assert scope.confirmed_entity is None
            assert scope.scope_reason == "clarification_other"

        elif action == "select_valid_candidate":
            first_cand = snapshot.display_candidates[0]
            val_result = resolver.validate_callback_selection(first_cand.entity_id, snapshot_id=snapshot.clarification_id)
            assert val_result is not None, f"[{case_id}] validate_callback_selection should succeed for valid candidate"
            assert val_result.canonical_name == first_cand.canonical_name

            scope = IdentityScopeResolver.resolve(
                None,
                clarification_selected=first_cand.canonical_name,
                clarification_option_id="a",
                clarification_snapshot_id=snapshot.clarification_id,
                constraints=constraints,
            )
            assert scope.identity_status == "confirmed_entity"
            assert scope.confirmed_entity == first_cand.canonical_name
            assert scope.confirmed_entity_id == first_cand.entity_id
            assert scope.scope_reason == "clarification_confirmed_from_snapshot"

        elif action == "select_tampered_id":
            val_result = resolver.validate_callback_selection("ent_tampered_fake_999", snapshot_id=snapshot.clarification_id)
            assert val_result is None, f"[{case_id}] validate_callback_selection should reject tampered ID"

            scope = IdentityScopeResolver.resolve(
                None,
                clarification_selected="FakeEntity",
                clarification_option_id="z",
                clarification_snapshot_id=snapshot.clarification_id,
                constraints=constraints,
            )
            assert scope.identity_status == "unresolved"
            assert scope.confirmed_entity is None
            assert scope.scope_reason == "clarification_snapshot_mismatch"

        elif action == "select_without_snapshot":
            val_result = resolver.validate_callback_selection("PipelineWebGL", snapshot_id=None)
            assert val_result is None, f"[{case_id}] validate_callback_selection without snapshot must fail closed"

            scope = IdentityScopeResolver.resolve(
                None,
                clarification_selected="PipelineWebGL",
                constraints=constraints,
            )
            assert scope.identity_status == "unresolved"
            assert scope.confirmed_entity is None
            assert scope.scope_reason == "clarification_snapshot_required"
