"""Tests for RelationRecoveryService / recover-relations CLI."""
from __future__ import annotations

import json

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.relation_recovery import (
    RelationRecoveryService,
    is_generic_entity_name,
)
import run_graph_build


def make_db(isolated_storage, name="recover.db", data_dir_name="recover-data", chroma_name="recover-chroma"):
    isolated_storage(db_name=name, data_dir_name=data_dir_name, chroma_name=chroma_name)
    return RelationalDB()


def _add_entity(db: RelationalDB, name: str, entity_type: str) -> str:
    with db._get_conn() as conn:
        eid = db._uid()
        now = db._now()
        conn.execute(
            "INSERT INTO entities (id, name, canonical_name, entity_type, properties_json, "
            "doc_category, confidence, review_status, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '{}', '', 1.0, 'approved', 'test', ?, ?)",
            (eid, name, name, entity_type, now, now),
        )
        return eid


def _rejected_entity(batch_id: str, db: RelationalDB, *, name: str, et: str, conf: float, **extra) -> str:
    payload = {
        "name": name,
        "entity_type": et,
        "confidence": conf,
        "created_by": "llm:schema_extractor",
        "evidence_text": f"evidence for {name}",
        "evidences": [{"evidence_text": f"evidence for {name}", "source_chunk_id": "chk_1"}],
        "source_chunk_id": "chk_1",
        **extra,
    }
    cid = db.add_extraction_candidate(
        batch_id, "entity", f"ent:{name}:{et}", payload, "chk_1", payload["evidence_text"]
    )
    db.review_extraction_candidates(batch_id, [cid], "rejected", "test reject")
    return cid


def _rejected_rel(
    batch_id: str,
    db: RelationalDB,
    *,
    src: str,
    rt: str,
    tgt: str,
    conf: float,
) -> str:
    payload = {
        "source_name": src,
        "relation_type": rt,
        "target_name": tgt,
        "confidence": conf,
        "created_by": "llm:schema_extractor",
        "evidence_text": f"{src} {rt} {tgt}",
        "evidences": [{"evidence_text": f"{src} {rt} {tgt}", "source_chunk_id": "chk_1"}],
        "source_chunk_id": "chk_1",
    }
    cid = db.add_extraction_candidate(
        batch_id, "relation", f"rel:{src}:{rt}:{tgt}", payload, "chk_1", payload["evidence_text"]
    )
    db.review_extraction_candidates(batch_id, [cid], "rejected", "test reject")
    return cid


def test_generic_entity_name_blocklist():
    assert is_generic_entity_name("编辑")
    assert is_generic_entity_name("设置")
    assert not is_generic_entity_name("飞行路径")


def test_recover_has_step_when_endpoints_ready(isolated_storage):
    db = make_db(isolated_storage)
    _add_entity(db, "模型压平", "Procedure")
    src_batch = db.create_extraction_batch("full", {"include_llm": True}, "snap")
    _rejected_entity(src_batch, db, name="压平设置界面", et="Step", conf=0.9)
    _rejected_rel(src_batch, db, src="模型压平", rt="has_step", tgt="压平设置界面", conf=0.9)
    # missing endpoint should not unlock
    _rejected_rel(src_batch, db, src="不存在流程", rt="has_step", tgt="压平设置界面", conf=0.9)
    # generic name blocked
    _rejected_entity(src_batch, db, name="编辑", et="Procedure", conf=0.9)

    plan = RelationRecoveryService(db).plan(
        source_batches=[src_batch],
        relation_types=["has_step"],
        entity_min_conf=0.80,
        rel_min_conf=0.80,
    )
    assert plan.summary["entity_count"] == 1
    assert plan.summary["rel_count"] == 1
    assert plan.relations[0].payload["target_name"] == "压平设置界面"
    assert plan.summary["skip"].get("entity_generic", 0) >= 1
    assert plan.summary["skip"].get("rel_missing_endpoint", 0) >= 1


def test_recover_skips_type_conflict_diagnostic(isolated_storage):
    db = make_db(isolated_storage, name="diag.db", data_dir_name="diag-data", chroma_name="diag-chroma")
    _add_entity(db, "工程设置长路径Section", "Section")  # substring collision source
    src_batch = db.create_extraction_batch("full", {"include_llm": True}, "snap")
    # exact name type conflict in formal DB
    _add_entity(db, "冲突名", "Tool")
    _rejected_entity(
        src_batch,
        db,
        name="冲突名",
        et="Procedure",
        conf=0.9,
        resolution_action="diagnostic",
    )
    # possible_duplicate via substring — only lifted with flag
    _rejected_entity(
        src_batch,
        db,
        name="工程设置",
        et="Procedure",
        conf=0.9,
        resolution_action="diagnostic",
    )

    base = RelationRecoveryService(db).plan(
        source_batches=[src_batch],
        relation_types=["has_step"],
        include_possible_duplicate=False,
    )
    assert base.summary["entity_count"] == 0

    lifted = RelationRecoveryService(db).plan(
        source_batches=[src_batch],
        relation_types=["has_procedure", "has_step"],
        include_possible_duplicate=True,
    )
    names = {e.payload["name"] for e in lifted.entities}
    assert "工程设置" in names
    assert "冲突名" not in names


def test_recover_relations_cli_dry_run_and_stage(isolated_storage, tmp_path, capsys):
    db = make_db(isolated_storage, name="cli.db", data_dir_name="cli-data", chroma_name="cli-chroma")
    _add_entity(db, "数据管理", "Procedure")
    src_batch = db.create_extraction_batch("full", {"include_llm": True}, "snap")
    _rejected_entity(src_batch, db, name="添加目录", et="Step", conf=0.85)
    _rejected_rel(src_batch, db, src="数据管理", rt="has_step", tgt="添加目录", conf=0.85)

    out = tmp_path / "plan.json"
    rc = run_graph_build.main(
        [
            "recover-relations",
            "--source-batch",
            src_batch,
            "--relation-type",
            "has_step",
            "--output-json",
            str(out),
        ],
        db=db,
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry_run"
    assert payload["rel_count"] == 1
    assert out.is_file()

    rc2 = run_graph_build.main(
        [
            "recover-relations",
            "--source-batch",
            src_batch,
            "--relation-type",
            "has_step",
            "--output-json",
            str(out),
            "--stage",
        ],
        db=db,
    )
    assert rc2 == 0
    staged = json.loads(capsys.readouterr().out)
    assert staged["status"] == "staged"
    batch = db.get_extraction_batch(staged["batch_id"])
    assert batch["status"] == "approved"
    approved = db.list_extraction_candidates(staged["batch_id"], "approved")
    assert len(approved) == 2
