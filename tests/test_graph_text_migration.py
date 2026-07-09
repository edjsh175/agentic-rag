from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_text_migration import GraphTextMigration


def make_db(isolated_storage, name="migration.db", data_dir_name="migration-data", chroma_name="migration-chroma"):
    isolated_storage(db_name=name, data_dir_name=data_dir_name, chroma_name=chroma_name)
    return RelationalDB()


def test_migration_normalizes_graph_entities_aliases_and_fields(isolated_storage):
    db = make_db(isolated_storage, name="migration.db", data_dir_name="migration-data", chroma_name="migration-chroma")
    pipeline_id = db.create_entity("PipelineBuilder", "Tool", "StampTools")
    old_service_id = db.create_entity("绠＄嚎鍙戝竷鏈嶅姟", "Service", "StampServer")
    old_table_id = db.create_entity("绠＄嚎鐐硅〃", "DataTable", "StampTools")
    old_field_entity_id = db.create_entity("绠＄嚎鐐硅〃.绠＄偣缂栧彿", "Field", "StampTools")

    db.create_relation(pipeline_id, old_service_id, "uses_config")
    db.create_relation(pipeline_id, old_table_id, "has_table")
    db.create_relation(old_table_id, old_field_entity_id, "has_field")
    db.create_alias(pipeline_id, "绠＄嚎鍙戝竷宸ュ叿", review_status="approved")

    with db._get_conn() as conn:
        conn.execute(
            "INSERT INTO fields (id, table_entity_id, field_name, description, required, unit, value_range, source_chunk_id, created_at, field_entity_id) "
            "VALUES (?, ?, ?, '', 1, '', '', 'c1', ?, ?)",
            (db._uid(), old_table_id, "绠＄偣缂栧彿", db._now(), old_field_entity_id),
        )

    stats = GraphTextMigration(db).apply()

    assert stats["entities_renamed"] >= 3
    assert stats["aliases_renamed"] == 1
    assert stats["fields_renamed"] == 1
    assert db.get_entity_by_name("管线发布服务") is not None
    assert db.get_entity_by_name("管线点表") is not None
    assert db.get_entity_by_name("管线点表.管点编号") is not None
    assert any(item["alias"] == "管线发布工具" for item in db.list_aliases(pipeline_id))

    with db._get_conn() as conn:
        field_row = dict(
            conn.execute("SELECT * FROM fields WHERE field_name = ?", ("管点编号",)).fetchone()
        )
    assert field_row["table_entity_id"] == db.get_entity_by_name("管线点表")["id"]

    relations = {
        (item["source_name"], item["relation_type"], item["target_name"])
        for item in db.list_relations(review_status="approved")
    }
    assert ("PipelineBuilder", "has_table", "管线点表") in relations
    assert ("管线点表", "has_field", "管线点表.管点编号") in relations


def test_migration_merges_duplicate_entities_when_correct_name_already_exists(isolated_storage):
    db = make_db(isolated_storage, "merge.db", "merge-data", "merge-chroma")
    pipeline_id = db.create_entity("PipelineBuilder", "Tool", "StampTools")
    correct_service_id = db.create_entity("管线发布服务", "Service", "StampServer")
    old_service_id = db.create_entity("绠＄嚎鍙戝竷鏈嶅姟", "Service", "StampServer")
    db.create_relation(pipeline_id, old_service_id, "different_from")
    db.create_link(old_service_id, "chunk-1", "evidence")

    stats = GraphTextMigration(db).apply()

    assert stats["entities_merged"] == 1
    assert db.get_entity(old_service_id) is None
    relations = db.list_relations(entity_id=correct_service_id, review_status="approved")
    assert any(item["target_name"] == "管线发布服务" for item in relations)
    assert db.get_link_by_entity_chunk(correct_service_id, "chunk-1") is not None


def test_migration_is_idempotent(isolated_storage):
    db = make_db(isolated_storage, "idempotent.db", "idempotent-data", "idempotent-chroma")
    db.create_entity("绠＄嚎鍙戝竷鏈嶅姟", "Service", "StampServer")

    migration = GraphTextMigration(db)
    first = migration.apply()
    second = migration.apply()

    assert first["entities_renamed"] == 1
    assert second["entities_renamed"] == 0
    assert second["entities_merged"] == 0
    assert second["aliases_renamed"] == 0
    assert second["fields_renamed"] == 0


def test_cli_repair_text_reports_fix_counts(isolated_storage, capsys):
    import json
    import run_graph_build

    db = make_db(isolated_storage, "cli-migration.db", "cli-migration-data", "cli-migration-chroma")
    db.create_entity("绠＄嚎鍙戝竷鏈嶅姟", "Service", "StampServer")

    exit_code = run_graph_build.main(["repair-text"], db=db)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["entities_renamed"] == 1
    assert payload["ok"] is True
