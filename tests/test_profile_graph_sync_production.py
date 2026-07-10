# -*- coding: utf-8 -*-
"""Production-shaped profile sync tests."""
from __future__ import annotations

import json
from pathlib import Path

from rag_knowledge.models.graph_schema import make_field_entity_name
from rag_knowledge.repository.relational_db import RelationalDB
from tests.fixtures.pipeline_graph_facts_production import (
    DOC_SOURCE,
    POINT_CANONICAL_PATH,
    seed_partial_pipeline_graph,
    seed_production_pipeline_graph,
)


def _profiles_path(data_dir: Path) -> Path:
    src = Path(__file__).resolve().parents[1] / "data" / "migrations" / "retrieval_intent_profiles_v1.json"
    dst = data_dir / "migrations" / "retrieval_intent_profiles_v1.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def test_production_preview_idempotent(isolated_storage):
    _, _, _, data_dir = isolated_storage(
        db_name="prod-sync-idem.db",
        data_dir_name="prod-sync-idem-data",
        chroma_name="prod-sync-idem-chroma",
    )
    _profiles_path(data_dir)
    db = RelationalDB()
    seed_production_pipeline_graph(db)

    from rag_knowledge.services.profile_graph_sync import ProfileGraphSyncService

    preview = ProfileGraphSyncService(db=db).preview("pipeline_point_table")
    profile = preview.profiles[0]
    assert profile.entities == []
    assert profile.aliases == []
    assert profile.relations == []


def test_partial_preview_only_gaps(isolated_storage):
    _, _, _, data_dir = isolated_storage(
        db_name="prod-sync-gap.db",
        data_dir_name="prod-sync-gap-data",
        chroma_name="prod-sync-gap-chroma",
    )
    _profiles_path(data_dir)
    db = RelationalDB()
    seed_partial_pipeline_graph(db)

    from rag_knowledge.services.profile_graph_sync import ProfileGraphSyncService

    preview = ProfileGraphSyncService(db=db).preview("pipeline_face_table")
    profile = preview.profiles[0]
    assert any(item.name == "管线面表" for item in profile.entities)
    scoped = make_field_entity_name("管线面表", "管面编号")
    assert any(item.name == scoped for item in profile.entities)
    assert any(
        item.relation_type == "has_field" and item.target_name == scoped for item in profile.relations
    )
    assert not any(item.entity_type == "Section" and "::" not in item.name for item in profile.entities)


def test_partial_point_table_requests_alias_and_siblings(isolated_storage):
    _, _, _, data_dir = isolated_storage(
        db_name="prod-sync-point.db",
        data_dir_name="prod-sync-point-data",
        chroma_name="prod-sync-point-chroma",
    )
    _profiles_path(data_dir)
    db = RelationalDB()
    seed_partial_pipeline_graph(db)

    from rag_knowledge.services.profile_graph_sync import ProfileGraphSyncService

    preview = ProfileGraphSyncService(db=db).preview("pipeline_point_table")
    profile = preview.profiles[0]
    assert any(item.alias == "点数据结构" for item in profile.aliases)
    assert any(item.relation_type == "different_from" for item in profile.relations)
    assert not any(
        item.entity_type == "Section" and item.name == POINT_CANONICAL_PATH for item in profile.entities
    )
