"""Tests for co-occurrence long-tail relation proposals."""
from __future__ import annotations

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.relation_cooccur import (
    CooccurRelationService,
    DirectedOption,
    LLMCooccurRelationArbiter,
    legal_directed_options,
)
import run_graph_build


def make_db(isolated_storage, name="cooccur.db", data_dir_name="cooccur-data", chroma_name="cooccur-chroma"):
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


def _link(db: RelationalDB, entity_id: str, chunk_id: str, evidence: str = "") -> None:
    db.create_link(
        entity_id,
        chunk_id,
        link_type="mentions",
        evidence_text=evidence or f"evidence {entity_id}",
        source="test",
    )


def _add_rel(db: RelationalDB, src_id: str, rt: str, tgt_id: str) -> None:
    with db._get_conn() as conn:
        rid = db._uid()
        now = db._now()
        conn.execute(
            "INSERT INTO relations (id, source_entity_id, target_entity_id, relation_type, "
            "properties_json, confidence, evidence_text, source_chunk_id, review_status, "
            "created_by, created_at) VALUES (?, ?, ?, ?, '{}', 1.0, 'e', 'c', 'approved', 'test', ?)",
            (rid, src_id, tgt_id, rt, now),
        )


def test_legal_directed_options_has_step_unique():
    opts = legal_directed_options(
        "模型压平", "Procedure", "压平设置", "Step", ["has_step", "runs_command"]
    )
    assert len(opts) == 1
    assert opts[0].relation_type == "has_step"
    assert opts[0].source_name == "模型压平"


def test_schema_unique_cooccur_proposes(isolated_storage):
    db = make_db(isolated_storage)
    proc = _add_entity(db, "模型压平", "Procedure")
    step = _add_entity(db, "压平设置", "Step")
    _link(db, proc, "chk_1", "模型压平包含压平设置步骤")
    _link(db, step, "chk_1", "模型压平包含压平设置步骤")

    plan = CooccurRelationService(db).plan(
        relation_types=["has_step"],
        chunk_ids=["chk_1"],
        include_llm=False,
    )
    assert plan.summary["rel_count"] == 1
    rel = plan.relations[0].payload
    assert rel["source_name"] == "模型压平"
    assert rel["relation_type"] == "has_step"
    assert rel["target_name"] == "压平设置"
    assert rel["created_by"] == "cooccur:schema"


def test_skips_when_pair_already_linked(isolated_storage):
    db = make_db(isolated_storage, name="linked.db", data_dir_name="linked-data", chroma_name="linked-chroma")
    proc = _add_entity(db, "模型压平", "Procedure")
    step = _add_entity(db, "压平设置", "Step")
    _add_rel(db, proc, "has_step", step)
    _link(db, proc, "chk_1")
    _link(db, step, "chk_1")

    plan = CooccurRelationService(db).plan(relation_types=["has_step"], chunk_ids=["chk_1"])
    assert plan.summary["rel_count"] == 0
    assert plan.summary["skip"].get("pair_already_linked", 0) >= 1


def test_multi_option_needs_llm_without_flag(isolated_storage):
    db = make_db(isolated_storage, name="multi.db", data_dir_name="multi-data", chroma_name="multi-chroma")
    # Procedure belongs_to Tool AND Tool has_procedure Procedure — two directed options.
    tool = _add_entity(db, "PipelineBuilder", "Tool")
    proc = _add_entity(db, "材质映射", "Procedure")
    _link(db, tool, "chk_2", "PipelineBuilder 材质映射流程")
    _link(db, proc, "chk_2", "PipelineBuilder 材质映射流程")

    plan = CooccurRelationService(db).plan(
        relation_types=["belongs_to", "has_procedure"],
        chunk_ids=["chk_2"],
        include_llm=False,
    )
    assert plan.summary["rel_count"] == 0
    assert plan.summary["skip"].get("multi_option_needs_llm", 0) >= 1


def test_llm_arbiter_accepts_option(isolated_storage):
    db = make_db(isolated_storage, name="llm.db", data_dir_name="llm-data", chroma_name="llm-chroma")
    tool = _add_entity(db, "PipelineBuilder", "Tool")
    proc = _add_entity(db, "材质映射", "Procedure")
    _link(db, tool, "chk_3", "PipelineBuilder 材质映射流程")
    _link(db, proc, "chk_3", "PipelineBuilder 材质映射流程")

    class FakeArbiter:
        def arbitrate(self, **kwargs):
            opts = kwargs["options"]
            pick = next(o for o in opts if o.relation_type == "has_procedure")
            return ("accept", pick, 0.91)

    plan = CooccurRelationService(db, arbiter=FakeArbiter()).plan(
        relation_types=["belongs_to", "has_procedure"],
        chunk_ids=["chk_3"],
        include_llm=True,
    )
    assert plan.summary["rel_count"] == 1
    assert plan.relations[0].payload["relation_type"] == "has_procedure"
    assert plan.relations[0].payload["created_by"] == "cooccur:llm"


def test_stage_pending_by_default(isolated_storage):
    db = make_db(isolated_storage, name="stage.db", data_dir_name="stage-data", chroma_name="stage-chroma")
    proc = _add_entity(db, "模型压平", "Procedure")
    step = _add_entity(db, "压平设置", "Step")
    _link(db, proc, "chk_1", "模型压平包含压平设置步骤")
    _link(db, step, "chk_1", "模型压平包含压平设置步骤")
    service = CooccurRelationService(db)
    plan = service.plan(relation_types=["has_step"], chunk_ids=["chk_1"])
    batch_id = service.stage(plan)
    cands = db.list_extraction_candidates(batch_id)
    assert len(cands) == 1
    assert cands[0]["status"] == "pending"


def test_cli_dry_run(isolated_storage, tmp_path):
    db = make_db(isolated_storage, name="cli.db", data_dir_name="cli-data", chroma_name="cli-chroma")
    proc = _add_entity(db, "模型压平", "Procedure")
    step = _add_entity(db, "压平设置", "Step")
    _link(db, proc, "chk_1", "模型压平包含压平设置步骤")
    _link(db, step, "chk_1", "模型压平包含压平设置步骤")
    out = tmp_path / "cooccur.json"
    code = run_graph_build.main(
        [
            "propose-cooccur-relations",
            "--chunk-id",
            "chk_1",
            "--relation-type",
            "has_step",
            "--output-json",
            str(out),
        ],
        db=db,
    )
    assert code == 0
    assert out.exists()


def test_llm_arbiter_parses_accept():
    class FakeLLM:
        def invoke(self, prompt):
            return '{"verdict":"accept","option_index":1,"confidence":0.9}'

    opts = [
        DirectedOption("A", "belongs_to", "B", "Procedure", "Tool"),
        DirectedOption("B", "has_procedure", "A", "Tool", "Procedure"),
    ]
    verdict, pick, conf = LLMCooccurRelationArbiter(FakeLLM()).arbitrate(
        name_a="A",
        type_a="Procedure",
        name_b="B",
        type_b="Tool",
        evidence_text="e",
        options=opts,
    )
    assert verdict == "accept"
    assert pick == opts[1]
    assert conf == 0.9
