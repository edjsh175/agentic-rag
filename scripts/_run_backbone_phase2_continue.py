"""Phase 2 in-process: stage → split review → apply (cleanup already done)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_graph_build
import sync_product_backbone_to_graph
from rag_knowledge.services.graph_governance import resolve_db_path
SUMMARY = ROOT / "data" / "backbone_phase2_summary.json"
CLEANUP_OUT = ROOT / "data" / "backbone_cleanup_apply_out.json"


def main() -> int:
    db_path = str(resolve_db_path())
    cleanup = json.loads(CLEANUP_OUT.read_text(encoding="utf-8"))
    backup_path = cleanup["backup_path"]
    summary = {
        "db_path": db_path,
        "cleanup": {
            "backup_path": backup_path,
            "report_path": cleanup.get("report_path"),
            "to_delete_count": cleanup["diff"]["to_delete_count"],
            "to_keep_count": cleanup["diff"]["to_keep_count"],
            "to_add_count": cleanup["diff"]["to_add_count"],
            "deleted_relations": cleanup["cleanup"]["deleted_relations"],
            "deleted_entity_count": cleanup["cleanup"]["deleted_entity_count"],
        },
    }

    # stage
    code = sync_product_backbone_to_graph.main(
        ["--stage", "--confirm-db-path", db_path, "--json"]
    )
    if code != 0:
        raise RuntimeError(f"stage exited {code}")
    # re-read via service for batch id (CLI prints JSON to stdout; capture via rebuild)
    from rag_knowledge.repository.relational_db import RelationalDB
    from rag_knowledge.services.product_backbone_graph_sync import BATCH_MODE

    db = RelationalDB()
    batches = [b for b in db.list_extraction_batches() if b.get("mode") == BATCH_MODE]
    batches.sort(key=lambda b: b.get("created_at") or "", reverse=True)
    if not batches:
        raise RuntimeError("no product_backbone_seed batch found after stage")
    batch_id = batches[0]["id"]
    # Prefer pending/approved newest that is not applied
    for b in batches:
        if b.get("status") in {"pending", "approved", "extracted", "staged"} or True:
            # pick newest non-applied
            if b.get("status") != "applied":
                batch_id = b["id"]
                break
    summary["batch_id"] = batch_id
    summary["batch_status_before_review"] = (db.get_extraction_batch(batch_id) or {}).get("status")
    pending = db.list_extraction_candidates(batch_id, "pending")
    summary["pending_before_review"] = len(pending)
    print("stage_batch", batch_id, "pending", len(pending))

    for kind in ("entity", "alias", "relation"):
        code = run_graph_build.main(
            ["review", "--batch", batch_id, "--approve-kind", kind],
            db=db,
        )
        if code != 0:
            raise RuntimeError(f"review {kind} exited {code}")
        remaining = db.list_extraction_candidates(batch_id, "pending")
        summary[f"remaining_after_{kind}"] = len(remaining)
        print("review", kind, "remaining", len(remaining))

    remaining = db.list_extraction_candidates(batch_id, "pending")
    if remaining:
        raise RuntimeError(f"still pending after split review: {len(remaining)}")

    code = run_graph_build.main(
        [
            "apply",
            "--batch",
            batch_id,
            "--confirm-db-path",
            db_path,
            "--confirm-batch",
            batch_id,
            "--confirm-backup",
            backup_path,
        ],
        db=db,
    )
    if code != 0:
        raise RuntimeError(f"apply exited {code}")
    summary["apply_status"] = "applied"
    summary["batch_status_after_apply"] = (db.get_extraction_batch(batch_id) or {}).get("status")

    # verify formal edges present as seed
    formal = json.loads((ROOT / "data" / "product_relation_backbone.json").read_text(encoding="utf-8"))
    seed_rels = [
        r for r in db.list_relations()
        if r.get("created_by") == "seed:product_backbone"
    ]
    seed_keys = {(r["source_name"], r["relation_type"], r["target_name"]) for r in seed_rels}
    missing = []
    for item in formal.get("relations") or []:
        key = (item["source"], item["relation_type"], item["target"])
        if key not in seed_keys:
            missing.append(key)
    summary["seed_relation_count"] = len(seed_rels)
    summary["formal_relation_count"] = len(formal.get("relations") or [])
    summary["missing_formal_edges"] = [
        {"source": s, "relation_type": rt, "target": t} for s, rt, t in missing[:50]
    ]
    summary["missing_formal_edge_count"] = len(missing)

    # obsolete edges should be 0 relative to formal: any seed edge not in formal
    formal_keys = {
        (item["source"], item["relation_type"], item["target"])
        for item in formal.get("relations") or []
    }
    obsolete = [k for k in seed_keys if k not in formal_keys]
    summary["obsolete_seed_edge_count"] = len(obsolete)
    summary["obsolete_seed_edges"] = [
        {"source": s, "relation_type": rt, "target": t} for s, rt, t in obsolete[:50]
    ]

    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "batch_id": batch_id,
        "seed_relation_count": summary["seed_relation_count"],
        "missing_formal_edge_count": summary["missing_formal_edge_count"],
        "obsolete_seed_edge_count": summary["obsolete_seed_edge_count"],
    }, ensure_ascii=False))
    if missing or obsolete:
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        (ROOT / "data" / "backbone_phase2_error.txt").write_text(str(exc), encoding="utf-8")
        print("ERROR", exc)
        raise SystemExit(1)
