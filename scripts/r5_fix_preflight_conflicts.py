"""R5: reject approved candidates that fail apply preflight, then reset batch to approved."""
from __future__ import annotations

from collections import defaultdict

from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_extraction.pipeline import GraphQualityService

BATCH = "a6309a3d-6bab-484c-b919-74d121dc399a"
PREFER = {
    "Procedure": 0,
    "Command": 1,
    "Step": 2,
    "ConfigItem": 3,
    "Error": 4,
    "Solution": 5,
    "EnvironmentComponent": 6,
}


def main() -> None:
    db = RelationalDB()
    reject_ids = [
        "4082ec5f-b9db-43c9-8545-3ca1e1a4b6c5",  # 编译发布 Step
        "1fc491a1-63bc-4984-841a-5d9656e32f27",  # 导出模型 Step
        "7afcd584-18c3-498e-849f-5d3ede9d01da",  # PipelineBuilder has_procedure 管线面表
    ]

    approved = db.list_extraction_candidates(BATCH, "approved")
    proc_names: set[str] = set()
    with db._get_conn() as conn:
        for row in conn.execute("SELECT name FROM entities WHERE entity_type='Procedure'"):
            proc_names.add(normalize_entity_name(row["name"]))
    for c in approved:
        p = c.get("payload") or {}
        if (
            c["candidate_kind"] == "entity"
            and p.get("entity_type") == "Procedure"
            and c["id"] not in reject_ids
        ):
            proc_names.add(normalize_entity_name(p.get("name") or ""))

    for c in approved:
        p = c.get("payload") or {}
        if c["candidate_kind"] == "relation" and p.get("relation_type") == "has_procedure":
            tgt = normalize_entity_name(p.get("target_name") or "")
            if tgt not in proc_names:
                reject_ids.append(c["id"])
                print("reject has_procedure", p.get("source_name"), "->", p.get("target_name"))

    by_name: dict[str, list] = defaultdict(list)
    for c in approved:
        if c["candidate_kind"] != "entity" or c["id"] in reject_ids:
            continue
        p = c.get("payload") or {}
        by_name[normalize_entity_name(p.get("name") or "")].append(c)

    for name, items in by_name.items():
        types = {(i.get("payload") or {}).get("entity_type") for i in items}
        if len(types) <= 1:
            continue
        items_sorted = sorted(
            items,
            key=lambda i: PREFER.get((i.get("payload") or {}).get("entity_type"), 99),
        )
        keep = items_sorted[0]
        for item in items_sorted[1:]:
            reject_ids.append(item["id"])
            print(
                "type-conflict keep",
                name,
                (keep.get("payload") or {}).get("entity_type"),
                "reject",
                (item.get("payload") or {}).get("entity_type"),
            )

    reject_ids = list(dict.fromkeys(reject_ids))
    print("reject_ids", len(reject_ids))

    with db._get_conn() as conn:
        now = db._now()
        for cid in reject_ids:
            conn.execute(
                "UPDATE extraction_candidates SET status='rejected', "
                "rejection_reason=?, reviewed_at=? "
                "WHERE batch_id=? AND id=? AND status='approved'",
                ("r5 preflight conflict/illegal", now, BATCH, cid),
            )
        conn.commit()

    approved2 = db.list_extraction_candidates(BATCH, "approved")
    print("approved remaining", len(approved2))
    pre = GraphQualityService(db).inspect_batch(BATCH)
    print("preflight ok", pre.ok)
    if pre.errors:
        print("errors", pre.errors)
    db.set_extraction_batch_status(BATCH, "approved")
    print("batch", (db.get_extraction_batch(BATCH) or {}).get("status"))


if __name__ == "__main__":
    main()
