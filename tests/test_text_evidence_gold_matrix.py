"""Deterministic Gold Matrix Test Runner for Text Evidence Admission Subsystem (PRD 2026-08-27).

JSON Fixture is the SINGLE SOURCE OF TRUTH: tests/fixtures/text_evidence_admission_gold_v1.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.documents import Document

from rag_knowledge.services.agent_candidate_pipeline import (
    CandidateProvenance,
    CandidateResult,
)
from rag_knowledge.services.agent_orchestration.graph_working_set import (
    GraphEntityState,
    GraphRelationCandidate,
    GraphWorkingSet,
)
from rag_knowledge.services.dialogue_understanding import SemanticTaskContext
from rag_knowledge.services.text_evidence_admission import (
    TextEvidenceAdmissionService,
    TextEvidenceQualification,
    resolve_entity_conflict,
    valid_text_qualification_protocol,
)

GOLD_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "text_evidence_admission_gold_v1.json"


def _load_gold_cases() -> list[dict[str, Any]]:
    with open(GOLD_FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases", [])


_GOLD_CASES = _load_gold_cases()


def _build_candidate(c_data: dict[str, Any], target_entity: str) -> CandidateResult:
    meta = dict(c_data.get("metadata", {}))
    meta.setdefault("chunk_id", c_data.get("chunk_id", "test_chunk_01"))
    meta.setdefault("document_entity", c_data.get("document_entity", ""))
    meta.setdefault("review_status", "approved")
    doc = Document(page_content=c_data.get("content", ""), metadata=meta)

    prov_list: list[CandidateProvenance] = []
    for p in c_data.get("provenance", []):
        prov_list.append(
            CandidateProvenance(
                generator=p.get("generator", "hybrid"),
                rank=p.get("rank", 1),
                graph_path=tuple(p.get("graph_path", ())),
                exact_lexical=p.get("exact_lexical", False),
                entity_link=p.get("entity_link", False),
                linked_entity=p.get("linked_entity"),
            )
        )

    return CandidateResult(
        document=doc,
        target_entity=target_entity,
        provenance=prov_list,
    )


def _build_working_set(ws_data: dict[str, Any] | None) -> GraphWorkingSet | None:
    if ws_data is None:
        return None
    ws = GraphWorkingSet()
    for root in ws_data.get("roots", []):
        ws.add_root(root, entity_id=f"root_{root}")
    for ent in ws_data.get("entities", []):
        origin_root = ent.get("origin_root") or (ws.exploration_roots[0] if ws.exploration_roots else "")
        ws.add_entity(
            GraphEntityState(
                ent.get("entity_id", ent.get("name")),
                ent.get("name"),
                depth_from_root=ent.get("depth", 1),
                origin_root=origin_root,
            )
        )
    for rel in ws_data.get("relations", []):
        ws.add_relation(
            GraphRelationCandidate(
                rel.get("relation_id", f"rel_{rel.get('source')}_{rel.get('target')}"),
                rel.get("source"),
                rel.get("target"),
                rel.get("relation_type", "different_from"),
            )
        )
    return ws


@pytest.mark.parametrize(
    "case",
    _GOLD_CASES,
    ids=[c["id"] for c in _GOLD_CASES],
)
def test_text_evidence_gold_matrix(case: dict[str, Any]):
    case_id = case["id"]
    test_type = case.get("test_type", "service_qualify")
    category = case.get("category", "")

    if test_type == "protocol_check":
        q_data = case["qualification"]
        qual = TextEvidenceQualification(
            verdict=q_data["verdict"],
            evidence_class=q_data["evidence_class"],
            support_scope=q_data["support_scope"],
            intent_relevance=q_data.get("intent_relevance", "HIGH"),
            reason_code=q_data.get("reason_code", "test"),
            reason=q_data.get("reason", "test"),
        )
        is_valid = valid_text_qualification_protocol(qual)
        expected_valid = case.get("expected_valid", True)
        assert is_valid == expected_valid, (
            f"[{case_id}] Protocol validity check failed: qualification={q_data}, "
            f"expected_valid={expected_valid}, actual_valid={is_valid}"
        )

    elif test_type == "conflict_check":
        target = case.get("target_entity", "")
        cand = _build_candidate(case["candidate"], target)
        ws = _build_working_set(case.get("working_set"))
        status, signals = resolve_entity_conflict(cand, target_entity=target, graph_working_set=ws)
        expected_status = case.get("expected_conflict_status", "NO_CONFLICT")
        assert status == expected_status, (
            f"[{case_id}] Conflict check mismatch: target='{target}', "
            f"expected_status='{expected_status}', actual_status='{status}', signals={signals}"
        )

    elif test_type == "service_qualify":
        target = case.get("target_entity", "")
        st_data = case.get("semantic_task")
        sem_task = None
        if st_data is not None:
            sem_task = SemanticTaskContext(
                resolved_question=st_data.get("resolved_question", ""),
                primary_entity=st_data.get("primary_entity"),
                mentioned_entities=tuple(st_data.get("mentioned_entities", ())),
                task_type=st_data.get("task_type", "single_entity_detail"),
                answer_intent=st_data.get("answer_intent", "general_qa"),
                confidence=1.0,
                entity_binding_required=st_data.get("entity_binding_required", True),
            )

        cand = _build_candidate(case["candidate"], target)
        ws = _build_working_set(case.get("working_set"))

        # Setup mock semantic admitter if provided
        sem_resp = case.get("semantic_admitter_response")
        sem_admitter = None
        if sem_resp is not None:
            def _mock_admitter(query: str, c: CandidateResult, pending: TextEvidenceQualification):
                return TextEvidenceQualification(
                    verdict=sem_resp["verdict"],
                    evidence_class=sem_resp["evidence_class"],
                    support_scope=sem_resp["support_scope"],
                    intent_relevance=sem_resp.get("intent_relevance", "HIGH"),
                    reason_code=sem_resp.get("reason_code", "mock_code"),
                    reason=sem_resp.get("reason", "mock_reason"),
                )
            sem_admitter = _mock_admitter

        svc = TextEvidenceAdmissionService()
        qual = svc.qualify(
            cand,
            semantic_task=sem_task,
            target_entity=target,
            graph_working_set=ws,
            semantic_admitter=sem_admitter,
        )

        expected = case.get("expected", {})

        if "verdict" in expected:
            assert qual.verdict == expected["verdict"], (
                f"[{case_id}] Verdict mismatch: target='{target}', "
                f"expected_verdict='{expected['verdict']}', actual_verdict='{qual.verdict}', "
                f"actual_reason_code='{qual.reason_code}', actual_reason='{qual.reason}'"
            )

        if "evidence_class" in expected:
            assert qual.evidence_class == expected["evidence_class"], (
                f"[{case_id}] EvidenceClass mismatch: target='{target}', "
                f"expected_class='{expected['evidence_class']}', actual_class='{qual.evidence_class}', "
                f"actual_reason='{qual.reason}'"
            )

        if "support_scope" in expected:
            assert qual.support_scope == expected["support_scope"], (
                f"[{case_id}] SupportScope mismatch: target='{target}', "
                f"expected_scope='{expected['support_scope']}', actual_scope='{qual.support_scope}'"
            )

        if "intent_relevance" in expected:
            assert qual.intent_relevance == expected["intent_relevance"], (
                f"[{case_id}] IntentRelevance mismatch: target='{target}', "
                f"expected_relevance='{expected['intent_relevance']}', actual_relevance='{qual.intent_relevance}'"
            )

        if "reason_code" in expected:
            assert qual.reason_code == expected["reason_code"], (
                f"[{case_id}] ReasonCode mismatch: target='{target}', "
                f"expected_code='{expected['reason_code']}', actual_code='{qual.reason_code}'"
            )
