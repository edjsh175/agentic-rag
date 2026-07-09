from rag_knowledge.services.graph_extraction import (
    ConfigBlockExtractor,
    GraphBuilder,
    GraphCandidateApplier,
    GraphQualityService,
    GraphSpecialRuleRestorer,
    SectionPathExtractor,
    TableFieldExtractor,
)
from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB


def chunk(chunk_id="c1", content="正文", **metadata):
    return {"chunk_id": chunk_id, "content": content, "metadata": metadata}


def test_section_path_extractor_builds_pipeline_main_graph():
    result = SectionPathExtractor().extract(
        chunk(
            content="| 字段名 | 说明 |\n|---|---|\n| 管点编号 | 唯一编号 |",
            source="StampTools用户手册.docx",
            doc_category="StampTools",
            section_path="PipelineBuilder > 数据规范 > 管线点表 > 点数据结构",
            content_type="table",
        )
    )

    assert result.entity("StampTools").entity_type == "Product"
    assert result.entity("PipelineBuilder").entity_type == "Tool"
    assert result.entity("管线点表").entity_type == "DataTable"
    assert result.has_relation("PipelineBuilder", "belongs_to", "StampTools")
    assert result.has_relation("PipelineBuilder", "has_table", "管线点表")
    assert any(link.entity_name == "管线点表" and link.chunk_id == "c1" for link in result.links)
    assert any(link.entity_name == "StampTools用户手册.docx" and link.chunk_id == "c1" for link in result.links)
    section = next(item for item in result.entities if item.entity_type == "Section")
    assert any(link.entity_name == section.name and link.chunk_id == "c1" for link in result.links)


def test_section_path_extractor_does_not_guess_unknown_tool():
    result = SectionPathExtractor().extract(
        chunk(
            source="manual.docx",
            doc_category="StampTools",
            section_path="神秘构建器 > 使用说明",
        )
    )

    assert result.entity("神秘构建器") is None


def test_table_field_extractor_uses_scoped_fields_and_explicit_properties():
    section_result = SectionPathExtractor().extract(
        chunk(
            source="manual.docx",
            doc_category="StampTools",
            section_path="PipelineBuilder > 数据规范 > 管线点表",
            content_type="table",
        )
    )
    result = TableFieldExtractor().extract(
        chunk(
            content=(
                "| 字段名 | 说明 | 单位 |\n|---|---|---|\n"
                "| 管点编号 | 唯一识别码，必要字段 | |\n"
                "| 地面高程 | 地表高程 | 米 |"
            ),
            source="manual.docx",
            doc_category="StampTools",
            section_path="PipelineBuilder > 数据规范 > 管线点表",
            content_type="table",
            table_id="table-1",
        ),
        section_result,
    )

    fields = {field.field_name: field for field in result.fields}
    assert fields["管点编号"].scoped_name == "管线点表.管点编号"
    assert fields["管点编号"].required is True
    assert fields["地面高程"].unit == "米"


def test_table_field_extractor_reports_missing_table_context():
    result = TableFieldExtractor().extract(
        chunk(content="| 字段名 | 说明 |\n|---|---|\n| 编号 | 说明 |", content_type="table"),
        None,
    )

    assert not result.fields
    assert any(item.code == "missing_table_context" for item in result.diagnostics)


def test_config_block_extractor_links_config_to_nearest_service():
    section_result = SectionPathExtractor().extract(
        chunk(
            source="server.docx",
            doc_category="StampServer",
            section_path="管线发布服务 > 服务配置",
        )
    )
    result = ConfigBlockExtractor().extract(
        chunk(
            content="```apache\nPipelinePublishConfig /data/stampmanager/server.xml\n```",
            source="server.docx",
            doc_category="StampServer",
            section_path="管线发布服务 > 服务配置",
            content_type="code",
            language="apache",
        ),
        section_result,
    )

    config = result.entity("PipelinePublishConfig")
    assert config.entity_type == "ConfigItem"
    assert config.properties["path"] == "/data/stampmanager/server.xml"
    assert result.has_relation("管线发布服务", "uses_config", "PipelinePublishConfig")


def test_config_block_extractor_skips_config_without_owner():
    result = ConfigBlockExtractor().extract(
        chunk(content="UnknownConfig /tmp/a.xml", content_type="code"),
        None,
    )

    assert result.entity("UnknownConfig") is None
    assert any(item.code == "missing_config_owner" for item in result.diagnostics)


def test_config_block_extractor_accepts_strict_directive_in_docx_text_chunk():
    section_result = SectionPathExtractor().extract(
        chunk(doc_category="StampServer", section_path="Stamp服务部署 > 管线发布服务")
    )
    result = ConfigBlockExtractor().extract(
        chunk(
            content=(
                "Apache服务配置\n<IfModule se_pipeline_publish_module>\n"
                "PipelinePublishConfig /data/stampmanager/server.xml\n</IfModule>"
            ),
            doc_category="StampServer",
            section_path="Stamp服务部署 > 管线发布服务",
            content_type="text",
        ),
        section_result,
    )

    assert result.entity("PipelinePublishConfig") is not None


def test_v3_staging_batch_deduplicates_candidates(isolated_storage, monkeypatch):
    isolated_storage(db_name="graph-v3.db", data_dir_name="graph-v3-data", chroma_name="graph-v3-chroma")
    db = RelationalDB()

    batch_id = db.create_extraction_batch("incremental", {"chunk_ids": ["c1"]}, "snapshot-1")
    payload = {"name": "PipelineBuilder", "entity_type": "Tool"}
    first = db.add_extraction_candidate(batch_id, "entity", "fp-1", payload, "c1", "PipelineBuilder")
    second = db.add_extraction_candidate(batch_id, "entity", "fp-1", payload, "c1", "PipelineBuilder")

    assert first == second
    assert db.get_schema_version() == 3
    assert len(db.list_extraction_candidates(batch_id)) == 1


def test_batch_can_only_be_approved_after_candidates_are_reviewed(isolated_storage):
    isolated_storage(db_name="review.db", data_dir_name="review-data", chroma_name="review-chroma")
    db = RelationalDB()
    batch_id = db.create_extraction_batch("full", {}, "snapshot")
    candidate_id = db.add_extraction_candidate(
        batch_id, "entity", "fp", {"name": "StampTools", "entity_type": "Product"}, "c1", "StampTools"
    )

    try:
        db.set_extraction_batch_status(batch_id, "approved")
        assert False, "pending candidate should block approval"
    except ValueError as exc:
        assert "pending" in str(exc)

    db.review_extraction_candidates(batch_id, [candidate_id], "approved")
    db.set_extraction_batch_status(batch_id, "approved")
    assert db.get_extraction_batch(batch_id)["status"] == "approved"


def make_db(isolated_storage, name="builder.db", data_dir_name="graph-data", chroma_name="graph-chroma"):
    isolated_storage(db_name=name, data_dir_name=data_dir_name, chroma_name=chroma_name)
    return RelationalDB()


def test_graph_builder_stages_all_candidate_kinds(isolated_storage):
    db = make_db(isolated_storage, name="builder.db", data_dir_name="builder-data", chroma_name="builder-chroma")
    chunks = [
        chunk(
            content=(
                "| 字段名 | 说明 |\n|---|---|\n"
                "| 管点编号 | 唯一识别码，必要字段 |"
            ),
            source="StampTools用户手册.docx",
            doc_category="StampTools",
            section_path="PipelineBuilder > 数据规范 > 管线点表",
            content_type="table",
            table_id="table-1",
        ),
        chunk(
            chunk_id="c2",
            content="PipelinePublishConfig /data/stampmanager/server.xml",
            source="StampServer用户手册.docx",
            doc_category="StampServer",
            section_path="管线发布服务 > 服务配置",
            content_type="code",
        ),
    ]

    result = GraphBuilder(db=db, chunk_source=lambda: chunks).build_full()
    candidates = db.list_extraction_candidates(result.batch_id)

    assert {item["candidate_kind"] for item in candidates} >= {"entity", "relation", "field", "link"}
    assert result.stats["chunks"] == 2


def test_build_full_reuses_snapshot_unless_force_rebuild(isolated_storage):
    db = make_db(isolated_storage, name="reuse.db", data_dir_name="reuse-data", chroma_name="reuse-chroma")
    chunks = [chunk(source="manual.docx", doc_category="StampTools", section_path="DOMBuilder")]
    builder = GraphBuilder(db=db, chunk_source=lambda: chunks)

    first = builder.build_full()
    reused = builder.build_full()
    rebuilt = builder.build_full(force_rebuild=True)

    assert reused.batch_id == first.batch_id
    assert rebuilt.batch_id != first.batch_id
    assert db.get_extraction_batch(first.batch_id)["status"] == "superseded"
    assert db.get_extraction_batch(rebuilt.batch_id) is not None


def test_incremental_build_reports_missing_chunk_ids(isolated_storage):
    db = make_db(isolated_storage, name="incremental.db", data_dir_name="incremental-data", chroma_name="incremental-chroma")
    builder = GraphBuilder(db=db, chunk_source=lambda: [chunk(chunk_id="c1")])

    result = builder.build_incremental(["c1", "missing"])
    diagnostics = db.list_extraction_candidates(result.batch_id, "rejected")

    assert result.stats["requested_chunks"] == 2
    assert result.stats["matched_chunks"] == 1
    assert result.stats["missing_chunks"] == ["missing"]
    assert any(item["payload"]["code"] == "missing_chunk" for item in diagnostics)


def test_entity_candidates_are_semantically_deduplicated_and_keep_evidence(isolated_storage):
    db = make_db(isolated_storage, name="dedupe.db", data_dir_name="dedupe-data", chroma_name="dedupe-chroma")
    chunks = [
        chunk(chunk_id="c1", source="manual.docx", doc_category="StampTools", section_path="PipelineBuilder > 工程设置"),
        chunk(chunk_id="c2", source="manual.docx", doc_category="StampTools", section_path="PipelineBuilder > 数据设置"),
    ]

    batch = GraphBuilder(db=db, chunk_source=lambda: chunks).build_full()
    pipeline_entities = [
        item for item in db.list_extraction_candidates(batch.batch_id)
        if item["candidate_kind"] == "entity" and item["payload"]["name"] == "PipelineBuilder"
    ]

    assert len(pipeline_entities) == 1
    assert {item["source_chunk_id"] for item in pipeline_entities[0]["payload"]["evidences"]} == {"c1", "c2"}


def test_approved_batch_applies_entities_relations_fields_and_links_atomically(isolated_storage):
    db = make_db(isolated_storage, name="apply.db", data_dir_name="apply-data", chroma_name="apply-chroma")
    chunks = [chunk(
        content="| 字段名 | 说明 |\n|---|---|\n| 管点编号 | 唯一识别码，必要字段 |",
        source="StampTools用户手册.docx",
        doc_category="StampTools",
        section_path="PipelineBuilder > 数据规范 > 管线点表",
        content_type="table",
    )]
    batch = GraphBuilder(db=db, chunk_source=lambda: chunks).build_full()
    ids = [item["id"] for item in db.list_extraction_candidates(batch.batch_id, "pending")]
    db.review_extraction_candidates(batch.batch_id, ids, "approved")
    db.set_extraction_batch_status(batch.batch_id, "approved")

    GraphCandidateApplier(db).apply(batch.batch_id)

    pipeline = db.get_entity_by_name("PipelineBuilder")
    table = db.get_entity_by_name("管线点表")
    field = db.get_entity_by_name("管线点表.管点编号")
    assert pipeline and table and field
    assert field["doc_category"] == "StampTools"
    assert db.get_relation_by_details(pipeline["id"], table["id"], "has_table")
    assert db.get_link_by_entity_chunk(field["id"], "c1")
    assert any(item["alias"] == "管线发布工具" for item in db.list_aliases(pipeline["id"]))
    assert db.get_extraction_batch(batch.batch_id)["status"] == "applied"


def test_special_rule_restorer_recovers_different_from_and_alias(isolated_storage):
    db = make_db(isolated_storage, name="special.db", data_dir_name="special-data", chroma_name="special-chroma")
    pipeline = db.create_entity("PipelineBuilder", "Tool", "StampTools")
    db.create_entity("管线发布服务", "Service", "StampServer")
    db.create_entity("PipelinePublishConfig", "ConfigItem", "StampServer")

    with db._get_conn() as conn:
        GraphSpecialRuleRestorer(db).apply(conn)

    aliases = {item["alias"] for item in db.list_aliases(pipeline)}
    related = {
        (item["source_name"], item["relation_type"], item["target_name"])
        for item in db.list_relations(entity_id=pipeline, review_status="approved")
    }

    assert "管线发布工具" in aliases
    assert ("PipelineBuilder", "different_from", "管线发布服务") in related
    assert ("PipelineBuilder", "different_from", "PipelinePublishConfig") in related


def test_special_rule_restorer_is_idempotent(isolated_storage):
    db = make_db(isolated_storage, name="special-idempotent.db", data_dir_name="special-idem-data", chroma_name="special-idem-chroma")
    db.create_entity("PipelineBuilder", "Tool", "StampTools")
    db.create_entity("管线发布服务", "Service", "StampServer")
    db.create_entity("PipelinePublishConfig", "ConfigItem", "StampServer")

    with db._get_conn() as conn:
        restorer = GraphSpecialRuleRestorer(db)
        restorer.apply(conn)
        restorer.apply(conn)

    aliases = [item for item in db.list_aliases() if item["alias"] == "管线发布工具"]
    relations = [item for item in db.list_relations(review_status="approved") if item["relation_type"] == "different_from"]
    assert len(aliases) == 1
    assert len(relations) == 2


def test_apply_rolls_back_whole_batch_on_type_conflict(isolated_storage):
    db = make_db(isolated_storage, name="conflict.db", data_dir_name="conflict-data", chroma_name="conflict-chroma")
    db.create_entity("PipelineBuilder", "Product")
    batch_id = db.create_extraction_batch("incremental", {}, "snapshot")
    candidate_id = db.add_extraction_candidate(
        batch_id, "entity", "conflict", {"name": "PipelineBuilder", "entity_type": "Tool", "doc_category": "StampTools"}
    )
    second_id = db.add_extraction_candidate(
        batch_id, "entity", "new", {"name": "DOMBuilder", "entity_type": "Tool", "doc_category": "StampTools"}
    )
    db.review_extraction_candidates(batch_id, [candidate_id, second_id], "approved")
    db.set_extraction_batch_status(batch_id, "approved")

    try:
        GraphCandidateApplier(db).apply(batch_id)
        assert False, "type conflict must fail"
    except ValueError as exc:
        assert "type conflict" in str(exc)

    assert db.get_entity_by_name("DOMBuilder") is None
    assert db.get_extraction_batch(batch_id)["status"] == "failed"


def test_quality_service_validates_golden_graph(isolated_storage):
    db = make_db(isolated_storage, name="quality.db", data_dir_name="quality-data", chroma_name="quality-chroma")
    report = GraphQualityService(db).inspect_graph()
    assert "missing_golden_entity:PipelineBuilder" in report.errors


def test_partial_graph_quality_skips_golden_gate(isolated_storage):
    db = make_db(isolated_storage, name="partial-quality.db", data_dir_name="partial-quality-data", chroma_name="partial-quality-chroma")

    report = GraphQualityService(db).inspect_graph(profile="partial")

    assert report.ok
    assert not any(error.startswith("missing_golden_") for error in report.errors)


def test_batch_quality_rejects_approved_entity_without_approved_evidence_link(isolated_storage):
    db = make_db(isolated_storage, name="batch-quality.db", data_dir_name="batch-quality-data", chroma_name="batch-quality-chroma")
    batch_id = db.create_extraction_batch("incremental", {}, "snapshot")
    entity_id = db.add_extraction_candidate(
        batch_id,
        "entity",
        "entity-fp",
        {"name": "DOMBuilder", "entity_type": "Tool", "doc_category": "StampTools"},
        "c1",
        "DOMBuilder",
    )
    link_id = db.add_extraction_candidate(
        batch_id,
        "link",
        "link-fp",
        {"entity_name": "DOMBuilder", "chunk_id": "c1", "link_type": "evidence"},
        "c1",
        "DOMBuilder",
    )
    db.review_extraction_candidates(batch_id, [entity_id], "approved")
    db.review_extraction_candidates(batch_id, [link_id], "rejected", "bad evidence")
    db.set_extraction_batch_status(batch_id, "approved")

    report = GraphQualityService(db).inspect_batch(batch_id)

    assert "missing_evidence:DOMBuilder" in report.errors
    try:
        GraphCandidateApplier(db).apply(batch_id)
        assert False, "missing evidence must block apply"
    except ValueError as exc:
        assert "missing_evidence:DOMBuilder" in str(exc)
    assert db.get_entity_by_name("DOMBuilder") is None
    assert db.get_extraction_batch(batch_id)["status"] == "failed"


def test_graph_build_cli_exports_reviewable_batch(isolated_storage, tmp_path):
    import json
    import run_graph_build

    db = make_db(isolated_storage, name="cli-export.db", data_dir_name="cli-export-data", chroma_name="cli-export-chroma")
    batch_id = db.create_extraction_batch("incremental", {}, "snapshot")
    db.add_extraction_candidate(
        batch_id, "entity", "fp", {"name": "DOMBuilder", "entity_type": "Tool"}, "c1", "DOMBuilder"
    )
    output = tmp_path / "batch.json"

    exit_code = run_graph_build.main(["export", "--batch", batch_id, "--output", str(output)], db=db)

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["batch"]["id"] == batch_id
    assert payload["candidates"][0]["payload"]["name"] == "DOMBuilder"


def test_graph_build_cli_quality_returns_nonzero_for_failed_gate(isolated_storage):
    import run_graph_build

    db = make_db(isolated_storage, name="cli-quality.db", data_dir_name="cli-quality-data", chroma_name="cli-quality-chroma")
    assert run_graph_build.main(["quality", "--graph"], db=db) == 1
    assert run_graph_build.main(["quality", "--graph", "--profile", "partial"], db=db) == 0


def test_graph_build_cli_review_reports_invalid_candidate_ids(isolated_storage, capsys):
    import json
    import run_graph_build

    db = make_db(isolated_storage, name="cli-review.db", data_dir_name="cli-review-data", chroma_name="cli-review-chroma")
    batch_id = db.create_extraction_batch("incremental", {}, "snapshot")
    candidate_id = db.add_extraction_candidate(
        batch_id, "entity", "fp", {"name": "DOMBuilder", "entity_type": "Tool"}
    )

    exit_code = run_graph_build.main(
        ["review", "--batch", batch_id, "--approve", candidate_id, "missing-id"], db=db
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["requested"] == 2
    assert payload["updated"] == 1
    assert payload["missing_or_not_pending"] == 1
