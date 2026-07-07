from rag_knowledge.services.graph_extraction import (
    ConfigBlockExtractor,
    SectionPathExtractor,
    TableFieldExtractor,
    GraphBuilder,
    GraphCandidateApplier,
    GraphQualityService,
)
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


def test_v3_staging_batch_deduplicates_candidates(tmp_path, monkeypatch):
    from rag_knowledge.config import Config

    db_path = tmp_path / "graph-v3.db"
    monkeypatch.setattr(Config(), "relational_db_path", db_path)
    RelationalDB._instance = None
    db = RelationalDB()

    batch_id = db.create_extraction_batch("incremental", {"chunk_ids": ["c1"]}, "snapshot-1")
    payload = {"name": "PipelineBuilder", "entity_type": "Tool"}
    first = db.add_extraction_candidate(batch_id, "entity", "fp-1", payload, "c1", "PipelineBuilder")
    second = db.add_extraction_candidate(batch_id, "entity", "fp-1", payload, "c1", "PipelineBuilder")

    assert first == second
    assert db.get_schema_version() == 3
    assert len(db.list_extraction_candidates(batch_id)) == 1


def test_batch_can_only_be_approved_after_candidates_are_reviewed(tmp_path, monkeypatch):
    from rag_knowledge.config import Config

    monkeypatch.setattr(Config(), "relational_db_path", tmp_path / "review.db")
    RelationalDB._instance = None
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


def make_db(tmp_path, monkeypatch, name="builder.db"):
    from rag_knowledge.config import Config

    monkeypatch.setattr(Config(), "relational_db_path", tmp_path / name)
    RelationalDB._instance = None
    return RelationalDB()


def test_graph_builder_stages_all_candidate_kinds(tmp_path, monkeypatch):
    db = make_db(tmp_path, monkeypatch)
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


def test_build_full_reuses_snapshot_unless_force_rebuild(tmp_path, monkeypatch):
    db = make_db(tmp_path, monkeypatch)
    chunks = [chunk(source="manual.docx", doc_category="StampTools", section_path="DOMBuilder")]
    builder = GraphBuilder(db=db, chunk_source=lambda: chunks)

    first = builder.build_full()
    reused = builder.build_full()
    rebuilt = builder.build_full(force_rebuild=True)

    assert reused.batch_id == first.batch_id
    assert rebuilt.batch_id != first.batch_id
    assert db.get_extraction_batch(first.batch_id)["status"] == "superseded"
    assert db.get_extraction_batch(rebuilt.batch_id) is not None


def test_incremental_build_reports_missing_chunk_ids(tmp_path, monkeypatch):
    db = make_db(tmp_path, monkeypatch)
    builder = GraphBuilder(db=db, chunk_source=lambda: [chunk(chunk_id="c1")])

    result = builder.build_incremental(["c1", "missing"])
    diagnostics = db.list_extraction_candidates(result.batch_id, "rejected")

    assert result.stats["requested_chunks"] == 2
    assert result.stats["matched_chunks"] == 1
    assert result.stats["missing_chunks"] == ["missing"]
    assert any(item["payload"]["code"] == "missing_chunk" for item in diagnostics)


def test_entity_candidates_are_semantically_deduplicated_and_keep_evidence(tmp_path, monkeypatch):
    db = make_db(tmp_path, monkeypatch)
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


def test_approved_batch_applies_entities_relations_fields_and_links_atomically(tmp_path, monkeypatch):
    db = make_db(tmp_path, monkeypatch)
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
    assert db.get_extraction_batch(batch.batch_id)["status"] == "applied"


def test_apply_rolls_back_whole_batch_on_type_conflict(tmp_path, monkeypatch):
    db = make_db(tmp_path, monkeypatch)
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


def test_quality_service_validates_golden_graph(tmp_path, monkeypatch):
    db = make_db(tmp_path, monkeypatch)
    report = GraphQualityService(db).inspect_graph()
    assert "missing_golden_entity:PipelineBuilder" in report.errors


def test_partial_graph_quality_skips_golden_gate(tmp_path, monkeypatch):
    db = make_db(tmp_path, monkeypatch)

    report = GraphQualityService(db).inspect_graph(profile="partial")

    assert report.ok
    assert not any(error.startswith("missing_golden_") for error in report.errors)


def test_batch_quality_rejects_approved_entity_without_approved_evidence_link(tmp_path, monkeypatch):
    db = make_db(tmp_path, monkeypatch)
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


def test_graph_build_cli_exports_reviewable_batch(tmp_path, monkeypatch):
    import json
    import run_graph_build

    db = make_db(tmp_path, monkeypatch)
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


def test_graph_build_cli_quality_returns_nonzero_for_failed_gate(tmp_path, monkeypatch):
    import run_graph_build

    db = make_db(tmp_path, monkeypatch)
    assert run_graph_build.main(["quality", "--graph"], db=db) == 1
    assert run_graph_build.main(["quality", "--graph", "--profile", "partial"], db=db) == 0


def test_graph_build_cli_review_reports_invalid_candidate_ids(tmp_path, monkeypatch, capsys):
    import json
    import run_graph_build

    db = make_db(tmp_path, monkeypatch)
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
