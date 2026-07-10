import json

import run_graph_build
from tests.test_graph_extraction import make_db


def test_rebuild_safe_dry_run_writes_report_without_changing_database(isolated_storage, tmp_path):
    db = make_db(isolated_storage, name="safe-rebuild.db", data_dir_name="safe-rebuild-data", chroma_name="safe-rebuild-chroma")
    entity_id = db.create_entity("Manual fact", "Tool", created_by="manual", review_status="approved")
    before = db.get_entity(entity_id)
    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"

    assert run_graph_build.main(["rebuild-safe", "--dry-run", "--output-json", str(json_path), "--output-md", str(md_path)], db=db) == 0
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["manual_fact_preserved"] is True
    assert payload["preserved_by_source"]["manual"] >= 1
    assert md_path.exists()
    assert db.get_entity(entity_id) == before
