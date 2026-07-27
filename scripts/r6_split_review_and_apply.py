"""R6: conservative split review + apply for StampWebRTC/基础环境 LLM batch."""
from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_extraction.pipeline import GraphCandidateApplier, GraphQualityService
from rag_knowledge.services.graph_governance import assert_write_confirmation, resolve_db_path
from rag_knowledge.services.graph_governance import is_safe_review_candidate

BATCH = "7c848f88-a994-4162-8dc9-87af94df3214"
SAFE_ENTITY = {"Command", "Procedure", "Step", "EnvironmentComponent", "Error", "Solution"}
SAFE_REL = {"runs_command", "configured_by", "uses_config", "has_procedure", "has_step"}
PREFER = {
    "Procedure": 0,
    "Command": 1,
    "Step": 2,
    "ConfigItem": 3,
    "Error": 4,
    "Solution": 5,
    "EnvironmentComponent": 6,
}


def is_llm(c: dict) -> bool:
    p = c.get("payload") or {}
    return str(p.get("created_by") or "").startswith("llm:") or str(
        (p.get("properties") or {}).get("created_by") or ""
    ).startswith("llm:")


def main() -> None:
    db = RelationalDB()
    batch = db.get_extraction_batch(BATCH)
    if not batch:
        raise SystemExit(f"batch not found: {BATCH}")
    pending = db.list_extraction_candidates(BATCH, "pending")
    reasons: Counter = Counter()

    known: set[str] = set()
    with db._get_conn() as conn:
        for row in conn.execute("SELECT name FROM entities"):
            known.add(normalize_entity_name(row["name"]))

    entity_approve: list[dict] = []
    other_reject: list[str] = []
    rel_candidates: list[dict] = []

    for c in pending:
        p = c.get("payload") or {}
        if not is_llm(c):
            other_reject.append(c["id"])
            reasons["reject_non_llm"] += 1
            continue
        conf = float(p.get("confidence") or 0)
        kind = c["candidate_kind"]
        if kind == "entity":
            et = p.get("entity_type")
            ok_type = (et in SAFE_ENTITY and conf >= 0.85) or (et == "ConfigItem" and conf >= 0.90)
            if ok_type and is_safe_review_candidate(c, batch=batch):
                entity_approve.append(c)
                reasons[f"approve_entity_{et}"] += 1
            else:
                other_reject.append(c["id"])
                reasons[f"reject_entity_{et}"] += 1
        elif kind == "relation":
            rel_candidates.append(c)
        else:
            other_reject.append(c["id"])
            reasons[f"reject_kind_{kind}"] += 1

    # within-batch type conflicts: prefer Procedure over Step etc.
    by_name: dict[str, list] = defaultdict(list)
    for c in entity_approve:
        by_name[normalize_entity_name((c.get("payload") or {}).get("name") or "")].append(c)
    keep_ids: set[str] = set()
    for name, items in by_name.items():
        if name in known:
            # if formal type differs, drop all approve for that name
            with db._get_conn() as conn:
                row = conn.execute(
                    "SELECT entity_type FROM entities WHERE name = ?", (name,)
                ).fetchone()
            formal = row["entity_type"] if row else None
            for item in items:
                et = (item.get("payload") or {}).get("entity_type")
                if formal and et != formal:
                    other_reject.append(item["id"])
                    reasons["reject_conflict_formal"] += 1
                else:
                    keep_ids.add(item["id"])
            continue
        items_sorted = sorted(
            items,
            key=lambda i: PREFER.get((i.get("payload") or {}).get("entity_type"), 99),
        )
        keep_ids.add(items_sorted[0]["id"])
        for item in items_sorted[1:]:
            other_reject.append(item["id"])
            reasons["reject_conflict_batch"] += 1

    entity_approve = [c for c in entity_approve if c["id"] in keep_ids]
    approve_names = {
        normalize_entity_name((c.get("payload") or {}).get("name") or "") for c in entity_approve
    }

    rel_approve: list[dict] = []
    for c in rel_candidates:
        p = c.get("payload") or {}
        conf = float(p.get("confidence") or 0)
        rt = p.get("relation_type")
        src = normalize_entity_name(p.get("source_name") or "")
        tgt = normalize_entity_name(p.get("target_name") or "")
        endpoints_ok = (src in known or src in approve_names) and (
            tgt in known or tgt in approve_names
        )
        if rt in SAFE_REL and conf >= 0.85 and endpoints_ok and is_safe_review_candidate(
            c, batch=batch
        ):
            rel_approve.append(c)
            reasons[f"approve_rel_{rt}"] += 1
        else:
            other_reject.append(c["id"])
            reasons[f"reject_rel_{rt}"] += 1

    approve_ids = [c["id"] for c in entity_approve] + [c["id"] for c in rel_approve]
    other_reject = list(dict.fromkeys(other_reject))
    plan = {
        "batch_id": BATCH,
        "approve": len(approve_ids),
        "reject": len(other_reject),
        "entity_approve": len(entity_approve),
        "rel_approve": len(rel_approve),
        "breakdown": dict(reasons),
    }
    Path("data/llm_backfill_r6_review_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(plan, ensure_ascii=False, indent=2))

    upd_a = db.review_extraction_candidates(
        BATCH, approve_ids, "approved", "r6 conservative llm leaf approve"
    )
    upd_r = db.review_extraction_candidates(
        BATCH, other_reject, "rejected", "r6 defer/reject non-target"
    )
    remaining = db.list_extraction_candidates(BATCH, "pending")
    approved = db.list_extraction_candidates(BATCH, "approved")
    if remaining:
        raise SystemExit(f"still pending: {len(remaining)}")
    db.set_extraction_batch_status(BATCH, "approved" if approved else "rejected")
    print("updated_approve", upd_a, "updated_reject", upd_r, "approved", len(approved))

    pre = GraphQualityService(db).inspect_batch(BATCH)
    print("preflight", pre.ok, pre.errors)
    if not pre.ok:
        # drop conflicting approved via SQL then recheck
        # parse simple conflict names from errors
        reject_more: list[str] = []
        approved2 = db.list_extraction_candidates(BATCH, "approved")
        err_text = ";".join(pre.errors)
        for c in approved2:
            p = c.get("payload") or {}
            if c["candidate_kind"] == "entity":
                name = p.get("name") or ""
                if f"entity type conflict:{name}" in err_text or f"entity type conflict:{normalize_entity_name(name)}" in err_text:
                    reject_more.append(c["id"])
            elif c["candidate_kind"] == "relation":
                key = f"{p.get('source_name')}:{p.get('relation_type')}:{p.get('target_name')}"
                if f"illegal_relation:{key}" in err_text or "illegal_relation" in err_text:
                    # only reject if this exact triple appears
                    if f"illegal_relation:{p.get('source_name')}:{p.get('relation_type')}:{p.get('target_name')}" in err_text:
                        reject_more.append(c["id"])
        if reject_more:
            with db._get_conn() as conn:
                now = db._now()
                for cid in reject_more:
                    conn.execute(
                        "UPDATE extraction_candidates SET status='rejected', rejection_reason=?, reviewed_at=? "
                        "WHERE batch_id=? AND id=? AND status='approved'",
                        ("r6 preflight conflict/illegal", now, BATCH, cid),
                    )
                conn.commit()
            db.set_extraction_batch_status(BATCH, "approved")
            pre = GraphQualityService(db).inspect_batch(BATCH)
            print("preflight_after_fix", pre.ok, pre.errors)
        if not pre.ok:
            raise SystemExit(f"preflight failed: {pre.errors}")

    db_path = Path(resolve_db_path())
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = Path("data/backups") / f"rag_relational_pre_r6_apply_{ts}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, backup)
    print("backup", backup)

    assert_write_confirmation(
        db_path=db_path,
        confirm_db_path=str(db_path),
        confirm_batch=BATCH,
        batch_id=BATCH,
        confirm_backup=str(backup).replace("\\", "/"),
        require_backup=True,
    )
    audit = GraphCandidateApplier(db).apply(
        BATCH, operator="cli", backup_path=str(backup).replace("\\", "/")
    )
    summary = {"batch_id": BATCH, "plan": plan, "backup": str(backup), "audit": audit}
    Path("data/llm_backfill_r6_apply_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
