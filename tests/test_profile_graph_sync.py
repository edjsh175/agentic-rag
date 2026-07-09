import json
from pathlib import Path

from rag_knowledge.repository.relational_db import RelationalDB


def _write_profiles(path: Path) -> None:
    profiles = [
        {
            "id": "pipeline_point_table",
            "entity_aliases": ["管线点表", "点数据结构"],
            "intent_terms": [],
            "recall_terms": ["点数据结构", "管点编号", "地面高程", "字段名", "说明", "1"],
            "section_families": [
                ["PipelineBuilder > 数据规范 > 管线点表", "PipelineBuilder > 数据规范 > 点数据结构"]
            ],
            "preferred_sources": ["StampTools"],
            "fallback_sources": ["StampServer"],
            "sibling_penalty_groups": [
                ["管线点表", "点数据结构", "管线线表", "线表数据结构", "管线面表", "面表数据结构"]
            ],
            "candidate_min_k": None,
        }
    ]
    path.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")


def test_profile_sync_preview_extracts_aliases_relations_and_diagnostics(isolated_storage):
    _, _, _, data_dir = isolated_storage(
        db_name="profile-preview.db",
        data_dir_name="profile-preview-data",
        chroma_name="profile-preview-chroma",
    )
    _write_profiles(data_dir / "retrieval_intent_profiles.json")

    from rag_knowledge.services.profile_graph_sync import ProfileGraphSyncService

    preview = ProfileGraphSyncService().preview()

    assert len(preview.profiles) == 1
    profile = preview.profiles[0]
    assert profile.profile_id == "pipeline_point_table"
    assert any(item.name == "管线点表" and item.entity_type == "DataTable" for item in profile.entities)
    assert any(item.alias == "点数据结构" and item.entity_name == "管线点表" for item in profile.aliases)
    assert any(
        item.source_name == "PipelineBuilder"
        and item.relation_type == "has_table"
        and item.target_name == "管线点表"
        for item in profile.relations
    )
    assert any(
        item.source_name == "管线点表"
        and item.relation_type == "has_field"
        and item.target_name == "管点编号"
        for item in profile.relations
    )
    assert any(
        item.source_name == "DOMBuilder" and item.target_name == "StampTools"
        for item in profile.weak_relations
    ) is False
    assert any(item.code == "generic_recall_term" and item.term == "字段名" for item in profile.diagnostics)
    assert any(item.code == "generic_recall_term" and item.term == "说明" for item in profile.diagnostics)


def test_profile_sync_apply_creates_pending_batch_and_alias_candidates(isolated_storage):
    _, _, _, data_dir = isolated_storage(
        db_name="profile-apply.db",
        data_dir_name="profile-apply-data",
        chroma_name="profile-apply-chroma",
    )
    _write_profiles(data_dir / "retrieval_intent_profiles.json")
    db = RelationalDB()

    from rag_knowledge.services.profile_graph_sync import ProfileGraphSyncService

    result = ProfileGraphSyncService(db=db).build_batch()
    batch = db.get_extraction_batch(result.batch_id)
    candidates = db.list_extraction_candidates(result.batch_id)

    assert batch["status"] == "draft"
    assert {item["candidate_kind"] for item in candidates} >= {"entity", "alias", "relation", "diagnostic"}
    alias_candidate = next(item for item in candidates if item["candidate_kind"] == "alias")
    assert alias_candidate["status"] == "pending"
    assert alias_candidate["payload"]["entity_name"] == "管线点表"
    assert alias_candidate["payload"]["alias"] == "点数据结构"


def test_profile_sync_apply_can_preapprove_candidates_and_batch(isolated_storage):
    _, _, _, data_dir = isolated_storage(
        db_name="profile-approve.db",
        data_dir_name="profile-approve-data",
        chroma_name="profile-approve-chroma",
    )
    _write_profiles(data_dir / "retrieval_intent_profiles.json")
    db = RelationalDB()

    from rag_knowledge.services.profile_graph_sync import ProfileGraphSyncService

    result = ProfileGraphSyncService(db=db).build_batch(review_status="approved")
    batch = db.get_extraction_batch(result.batch_id)
    candidates = db.list_extraction_candidates(result.batch_id)

    assert batch["status"] == "approved"
    assert candidates
    assert all(item["status"] in {"approved", "rejected"} for item in candidates)


def test_profile_sync_apply_is_idempotent_after_graph_apply(isolated_storage):
    _, _, _, data_dir = isolated_storage(
        db_name="profile-idempotent.db",
        data_dir_name="profile-idempotent-data",
        chroma_name="profile-idempotent-chroma",
    )
    _write_profiles(data_dir / "retrieval_intent_profiles.json")
    db = RelationalDB()

    from rag_knowledge.services.graph_extraction import GraphCandidateApplier
    from rag_knowledge.services.profile_graph_sync import ProfileGraphSyncService

    first = ProfileGraphSyncService(db=db).build_batch(review_status="approved")
    GraphCandidateApplier(db).apply(first.batch_id)

    second = ProfileGraphSyncService(db=db).build_batch(review_status="approved")
    assert db.list_extraction_candidates(second.batch_id, "approved") == []

    table = db.get_entity_by_name("管线点表")
    aliases = [item for item in db.list_aliases(table["id"]) if item["alias"] == "点数据结构"]
    relations = [
        item
        for item in db.list_relations(entity_id=table["id"], review_status="approved")
        if item["relation_type"] == "different_from"
    ]

    assert len(aliases) == 1
    assert len(relations) == 2


def test_profile_sync_cli_supports_dry_run_and_apply(isolated_storage, capsys):
    _, _, _, data_dir = isolated_storage(
        db_name="profile-cli.db",
        data_dir_name="profile-cli-data",
        chroma_name="profile-cli-chroma",
    )
    _write_profiles(data_dir / "retrieval_intent_profiles.json")
    db = RelationalDB()

    import sync_profiles_to_graph

    dry_run_exit = sync_profiles_to_graph.main(["--dry-run", "--json"], db=db)
    dry_run_payload = json.loads(capsys.readouterr().out)
    apply_exit = sync_profiles_to_graph.main(["--apply", "--json"], db=db)
    apply_payload = json.loads(capsys.readouterr().out)

    assert dry_run_exit == 0
    assert dry_run_payload["mode"] == "dry-run"
    assert dry_run_payload["profiles"][0]["profile_id"] == "pipeline_point_table"
    assert apply_exit == 0
    assert apply_payload["mode"] == "apply"
    assert apply_payload["review_status"] == "pending"
    assert apply_payload["batch"]["status"] == "draft"
