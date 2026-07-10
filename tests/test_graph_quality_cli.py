import json

import run_graph_build
from tests.test_graph_extraction import make_db


def test_quality_llm_reports_candidate_diagnostics(isolated_storage, capsys):
    db = make_db(isolated_storage, name="llm-quality.db", data_dir_name="llm-quality-data", chroma_name="llm-quality-chroma")
    batch_id = db.create_extraction_batch("incremental", {}, "snapshot")
    db.add_extraction_candidate(batch_id, "entity", "fp-1", {
        "name": "Apache", "entity_type": "EnvironmentComponent", "confidence": 0.95,
        "evidence_text": "Apache", "source_chunk_id": "c1", "created_by": "llm:schema_extractor",
    }, "c1", "Apache")
    db.add_extraction_candidate(batch_id, "diagnostic", "fp-2", {
        "code": "type_conflict", "message": "Apache conflict", "chunk_id": "c1",
    }, "c1", "Apache conflict")
    db.review_extraction_candidates(batch_id, [db.list_extraction_candidates(batch_id)[0]["id"]], "approved")

    assert run_graph_build.main(["quality", "--batch", batch_id, "--llm"], db=db) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stats"]["total_llm_candidates"] == 1
    assert payload["stats"]["type_conflict_count"] == 1


def test_review_filters_approve_only_matching_type_and_confidence(isolated_storage, capsys):
    db = make_db(isolated_storage, name="review-filter.db", data_dir_name="review-filter-data", chroma_name="review-filter-chroma")
    batch_id = db.create_extraction_batch("incremental", {}, "snapshot")
    db.add_extraction_candidate(batch_id, "entity", "fp-1", {"name": "Apache", "entity_type": "EnvironmentComponent", "confidence": 0.95, "source_chunk_id": "c1", "evidence_text": "Apache"}, "c1", "Apache")
    db.add_extraction_candidate(batch_id, "entity", "fp-2", {"name": "DemoTool", "entity_type": "Tool", "confidence": 0.95, "source_chunk_id": "c1", "evidence_text": "DemoTool"}, "c1", "DemoTool")

    assert run_graph_build.main(["review", "--batch", batch_id, "--approve-type", "Tool", "--approve-confidence-above", "0.9"], db=db) == 0
    json.loads(capsys.readouterr().out)
    candidates = db.list_extraction_candidates(batch_id)
    assert [item["status"] for item in candidates] == ["pending", "approved"]


def test_review_reports_requested_selected_and_safety_rejected(isolated_storage, capsys):
    db = make_db(
        isolated_storage,
        name="review-safety.db",
        data_dir_name="review-safety-data",
        chroma_name="review-safety-chroma",
    )
    batch_id = db.create_extraction_batch("incremental", {}, "snapshot")
    safe_id = db.add_extraction_candidate(
        batch_id,
        "entity",
        "safe",
        {
            "name": "DemoTool",
            "entity_type": "Tool",
            "evidence_text": "DemoTool",
            "source_chunk_id": "c1",
        },
        "c1",
        "DemoTool",
    )
    unsafe_id = db.add_extraction_candidate(
        batch_id,
        "alias",
        "unsafe",
        {"entity_name": "DemoTool", "alias": "演示工具"},
    )

    assert run_graph_build.main(
        ["review", "--batch", batch_id, "--approve", safe_id, unsafe_id, "missing-id"],
        db=db,
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "requested": 3,
        "selected": 2,
        "rejected_by_safety": 1,
        "updated": 1,
        "missing_or_not_pending": 1,
        "status": "approved",
        "remaining_pending": 1,
    }
