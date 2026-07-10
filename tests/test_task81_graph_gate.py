# -*- coding: utf-8 -*-
"""Tests for Task 8.1 graph gate validator."""
from __future__ import annotations

import json
from pathlib import Path

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.task81_graph_gate import Task81GraphGateValidator
from tests.fixtures.pipeline_graph_facts_production import seed_partial_pipeline_graph, seed_production_pipeline_graph


def _write_all_profiles(data_dir: Path) -> None:
    path = data_dir / "migrations" / "retrieval_intent_profiles_v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        path.read_text(encoding="utf-8") if path.exists() else "[]",
        encoding="utf-8",
    )
    profiles = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "migrations" / "retrieval_intent_profiles_v1.json").read_text(
            encoding="utf-8"
        )
    )
    path.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")


def test_gate_pass_on_production_graph(isolated_storage):
    _, _, _, data_dir = isolated_storage(
        db_name="gate-pass.db",
        data_dir_name="gate-pass-data",
        chroma_name="gate-pass-chroma",
    )
    _write_all_profiles(data_dir)
    db = RelationalDB()
    seed_production_pipeline_graph(db)
    report = Task81GraphGateValidator(db).validate(include_global_quality=False)
    assert report.verdict == "PASS"
    assert not report.issues


def test_gate_needs_apply_on_partial_graph(isolated_storage):
    _, _, _, data_dir = isolated_storage(
        db_name="gate-needs.db",
        data_dir_name="gate-needs-data",
        chroma_name="gate-needs-chroma",
    )
    _write_all_profiles(data_dir)
    db = RelationalDB()
    seed_partial_pipeline_graph(db)
    report = Task81GraphGateValidator(db).validate(include_global_quality=False)
    assert report.verdict == "NEEDS_APPLY"
    assert report.preview_summary["pipeline_face_table"]["entities"] > 0


def test_gate_blocked_on_pending_field(isolated_storage):
    _, _, _, data_dir = isolated_storage(
        db_name="gate-blocked.db",
        data_dir_name="gate-blocked-data",
        chroma_name="gate-blocked-chroma",
    )
    _write_all_profiles(data_dir)
    db = RelationalDB()
    seed_partial_pipeline_graph(db)
    db.create_entity("管线面表.管面编号", "Field", review_status="pending")
    report = Task81GraphGateValidator(db).validate(include_global_quality=False)
    assert report.verdict == "BLOCKED"
