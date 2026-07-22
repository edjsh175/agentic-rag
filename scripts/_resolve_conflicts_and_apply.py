"""Resolve 2 LLM entity type conflicts in favor of confirmed product backbone, then re-apply batch."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_graph_build
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_governance import resolve_db_path

BATCH = "a1c75bec-09ef-4420-be9a-e229add62c7b"
CLEANUP = json.loads((ROOT / "data" / "backbone_cleanup_apply_out.json").read_text(encoding="utf-8"))
CONFLICTS = json.loads((ROOT / "data" / "backbone_type_conflicts.json").read_text(encoding="utf-8"))["conflicts"]


def main() -> int:
    db = RelationalDB()
    db_path = str(resolve_db_path())
    backup_path = CLEANUP["backup_path"]
    actions = []

    with db._get_conn() as conn:
        for c in CONFLICTS:
            row = conn.execute(
                "SELECT id, name, entity_type, created_by FROM entities WHERE name = ?",
                (c["name"],),
            ).fetchone()
            if not row:
                continue
            if row["entity_type"] != c["wanted_type"]:
                conn.execute(
                    "UPDATE entities SET entity_type = ?, updated_at = ? WHERE id = ?",
                    (c["wanted_type"], db._now(), row["id"]),
                )
                actions.append({
                    "action": "update_entity_type",
                    "name": c["name"],
                    "from": row["entity_type"],
                    "to": c["wanted_type"],
                    "created_by": row["created_by"],
                })

        # Drop LLM has_procedure → BIM模型 which becomes illegal after Module type.
        for row in conn.execute(
            """
            SELECT r.id, s.name AS source_name, r.relation_type, t.name AS target_name, r.created_by
            FROM relations r
            JOIN entities s ON s.id = r.source_entity_id
            JOIN entities t ON t.id = r.target_entity_id
            WHERE t.name = ? AND r.relation_type = 'has_procedure' AND r.created_by = 'llm:schema_extractor'
            """,
            ("BIM模型",),
        ).fetchall():
            conn.execute("DELETE FROM relations WHERE id = ?", (row["id"],))
            actions.append({
                "action": "delete_illegal_llm_relation",
                "id": row["id"],
                "source": row["source_name"],
                "relation_type": row["relation_type"],
                "target": row["target_name"],
            })

        # Reset failed batch to approved for re-apply.
        batch = conn.execute("SELECT status, error_text FROM extraction_batches WHERE id = ?", (BATCH,)).fetchone()
        actions.append({"action": "batch_status_before", "status": batch["status"], "error": batch["error_text"]})
        conn.execute(
            "UPDATE extraction_batches SET status = 'approved', error_text = '' WHERE id = ?",
            (BATCH,),
        )

    report_path = ROOT / "data" / "backbone_type_conflict_resolution.json"
    report_path.write_text(json.dumps({"actions": actions}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("resolved", len(actions), "report", report_path)

    code = run_graph_build.main(
        [
            "apply",
            "--batch",
            BATCH,
            "--confirm-db-path",
            db_path,
            "--confirm-batch",
            BATCH,
            "--confirm-backup",
            backup_path,
        ],
        db=db,
    )
    if code != 0:
        raise RuntimeError(f"apply exited {code}")

    # verify
    formal = json.loads((ROOT / "data" / "product_relation_backbone.json").read_text(encoding="utf-8"))
    seed_rels = [r for r in db.list_relations() if r.get("created_by") == "seed:product_backbone"]
    seed_keys = {(r["source_name"], r["relation_type"], r["target_name"]) for r in seed_rels}
    formal_keys = {
        (item["source"], item["relation_type"], item["target"])
        for item in formal.get("relations") or []
    }
    missing = sorted(formal_keys - seed_keys)
    obsolete = sorted(seed_keys - formal_keys)
    summary = {
        "batch_id": BATCH,
        "backup_path": backup_path,
        "resolution_report": str(report_path),
        "seed_relation_count": len(seed_rels),
        "formal_relation_count": len(formal_keys),
        "missing_formal_edge_count": len(missing),
        "obsolete_seed_edge_count": len(obsolete),
        "missing": [{"source": s, "relation_type": rt, "target": t} for s, rt, t in missing[:30]],
        "obsolete": [{"source": s, "relation_type": rt, "target": t} for s, rt, t in obsolete[:30]],
        "conflict_actions": actions,
    }
    out = ROOT / "data" / "backbone_phase2_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "seed_relation_count": summary["seed_relation_count"],
        "missing": summary["missing_formal_edge_count"],
        "obsolete": summary["obsolete_seed_edge_count"],
        "batch_status": (db.get_extraction_batch(BATCH) or {}).get("status"),
    }, ensure_ascii=False))
    return 0 if not missing and not obsolete else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        (ROOT / "data" / "backbone_phase2_error.txt").write_text(str(exc), encoding="utf-8")
        print("ERROR", exc)
        raise SystemExit(1)
