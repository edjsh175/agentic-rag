"""R6c: human-scoped recovery of possible_duplicate diagnostic Procedure/Step + has_step."""
from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from rag_knowledge.models.graph_schema import normalize_entity_name, validate_relation
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.entity_resolution import EntityResolutionService
from rag_knowledge.services.graph_extraction.pipeline import GraphCandidateApplier, GraphQualityService
from rag_knowledge.services.graph_governance import assert_write_confirmation, resolve_db_path

SOURCE_BATCHES = [
    "7c848f88-a994-4162-8dc9-87af94df3214",
    "a6309a3d-6bab-484c-b919-74d121dc399a",
]
ENTITY_MIN_CONF = 0.85
REL_MIN_CONF = 0.80


class _Cand:
    def __init__(self, name: str, entity_type: str):
        self.name = name
        self.entity_type = entity_type


def _fp(kind: str, payload: dict) -> str:
    if kind == "entity":
        body = {"k": "entity", "t": payload.get("entity_type"), "n": payload.get("name")}
    else:
        body = {
            "k": "relation",
            "rt": payload.get("relation_type"),
            "s": payload.get("source_name"),
            "t": payload.get("target_name"),
        }
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:32]
    return f"r6c:{digest}"


def collect(db: RelationalDB) -> tuple[list[dict], list[dict], dict]:
    name_type: dict[str, str] = {}
    with db._get_conn() as conn:
        for row in conn.execute("SELECT name, entity_type FROM entities"):
            name_type[normalize_entity_name(row["name"])] = row["entity_type"]

    existing_rel: set[tuple[str, str]] = set()
    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT e1.name AS s, e2.name AS t "
            "FROM relations rel "
            "JOIN entities e1 ON e1.id = rel.source_entity_id "
            "JOIN entities e2 ON e2.id = rel.target_entity_id "
            "WHERE rel.relation_type = ?",
            ("has_step",),
        ).fetchall()
        for row in rows:
            existing_rel.add(
                (normalize_entity_name(row["s"]), normalize_entity_name(row["t"]))
            )

    resolver = EntityResolutionService(db)
    entity_items: list[dict] = []
    skip = Counter()
    seen_names: set[str] = set()
    diag_codes: Counter = Counter()

    for batch_id in SOURCE_BATCHES:
        for c in db.list_extraction_candidates(batch_id, "rejected"):
            if c["candidate_kind"] != "entity":
                continue
            p = dict(c.get("payload") or {})
            if not str(p.get("created_by") or "").startswith("llm:"):
                continue
            if p.get("resolution_action") != "diagnostic":
                continue
            et = p.get("entity_type")
            if et not in {"Procedure", "Step"}:
                continue
            conf = float(p.get("confidence") or 0)
            if conf < ENTITY_MIN_CONF:
                skip["entity_low_conf"] += 1
                continue
            name = normalize_entity_name(p.get("name") or "")
            if not name:
                continue
            if name in name_type:
                skip["entity_exact_exists"] += 1
                continue
            evidence = str(p.get("evidence_text") or c.get("evidence_text") or "")
            if not evidence and not p.get("evidences"):
                skip["entity_no_evidence"] += 1
                continue

            resolution = resolver.resolve(_Cand(p.get("name") or "", et))
            codes = [d.code for d in resolution.diagnostics]
            for code in codes:
                diag_codes[code] += 1
            # Only lift soft substring warnings; never type_conflict / alias reuse here.
            if resolution.action != "diagnostic" or "type_conflict" in codes:
                skip["entity_not_possible_duplicate"] += 1
                continue
            if "possible_duplicate" not in codes:
                skip["entity_unknown_diagnostic"] += 1
                continue
            if name in seen_names:
                skip["entity_dup"] += 1
                continue
            seen_names.add(name)

            # Stage as explicit new leaf for recovery batch.
            staged = dict(p)
            staged["resolution_action"] = "new"
            staged["resolved_entity_id"] = ""
            staged["properties"] = dict(staged.get("properties") or {})
            staged["properties"]["r6c_recovered_from_diagnostic"] = True
            staged["properties"]["possible_duplicate_hints"] = [
                d.message for d in resolution.diagnostics
            ]

            entity_items.append(
                {
                    "kind": "entity",
                    "payload": staged,
                    "source_chunk_id": c.get("source_chunk_id") or "",
                    "evidence_text": evidence,
                    "source_batch": batch_id,
                    "source_candidate_id": c["id"],
                    "diag_messages": [d.message for d in resolution.diagnostics],
                }
            )

    sim = dict(name_type)
    for item in entity_items:
        sim[normalize_entity_name(item["payload"].get("name") or "")] = item["payload"].get(
            "entity_type"
        )

    rel_items: list[dict] = []
    seen_rel: set[tuple[str, str]] = set()
    for batch_id in SOURCE_BATCHES:
        for c in db.list_extraction_candidates(batch_id, "rejected"):
            if c["candidate_kind"] != "relation":
                continue
            p = dict(c.get("payload") or {})
            if not str(p.get("created_by") or "").startswith("llm:"):
                continue
            if p.get("relation_type") != "has_step":
                continue
            conf = float(p.get("confidence") or 0)
            if conf < REL_MIN_CONF:
                skip["rel_low_conf"] += 1
                continue
            src = normalize_entity_name(p.get("source_name") or "")
            tgt = normalize_entity_name(p.get("target_name") or "")
            st, tt = sim.get(src), sim.get(tgt)
            if not st or not tt:
                skip["rel_missing_endpoint"] += 1
                continue
            ok, _ = validate_relation(st, "has_step", tt)
            if not ok:
                skip[f"rel_illegal_{st}->{tt}"] += 1
                continue
            key = (src, tgt)
            if key in existing_rel or key in seen_rel:
                skip["rel_exists_or_dup"] += 1
                continue
            evidence = str(p.get("evidence_text") or c.get("evidence_text") or "")
            if not evidence and not p.get("evidences"):
                skip["rel_no_evidence"] += 1
                continue
            seen_rel.add(key)
            rel_items.append(
                {
                    "kind": "relation",
                    "payload": p,
                    "source_chunk_id": c.get("source_chunk_id") or "",
                    "evidence_text": evidence,
                    "source_batch": batch_id,
                    "source_candidate_id": c["id"],
                }
            )

    summary = {
        "entity_count": len(entity_items),
        "rel_count": len(rel_items),
        "entity_by_type": dict(
            Counter(i["payload"].get("entity_type") for i in entity_items)
        ),
        "diag_codes": dict(diag_codes),
        "skip": dict(skip),
        "entities": [
            {
                "name": i["payload"].get("name"),
                "type": i["payload"].get("entity_type"),
                "conf": i["payload"].get("confidence"),
                "hints": i.get("diag_messages") or [],
            }
            for i in entity_items
        ],
        "relations": [
            {
                "src": i["payload"].get("source_name"),
                "tgt": i["payload"].get("target_name"),
                "conf": i["payload"].get("confidence"),
            }
            for i in rel_items
        ],
    }
    Path("data/llm_backfill_r6c_diagnostic_has_step.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "entity_count",
                    "rel_count",
                    "entity_by_type",
                    "diag_codes",
                    "skip",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("relations", summary["relations"][:30], "... total", len(summary["relations"]))
    return entity_items, rel_items, summary


def main() -> None:
    db = RelationalDB()
    entity_items, rel_items, summary = collect(db)
    if not rel_items:
        raise SystemExit("no has_step unlocked; abort without writing entities-only")

    db_path = Path(resolve_db_path())
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = Path("data/backups") / f"rag_relational_pre_r6c_diagnostic_has_step_{ts}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, backup)
    print("backup", backup)

    bid = db.create_extraction_batch(
        "llm_diagnostic_has_step_recovery",
        {
            "note": "R6c lift possible_duplicate Procedure/Step then has_step",
            "source_batches": SOURCE_BATCHES,
            "entity_min_conf": ENTITY_MIN_CONF,
            "rel_min_conf": REL_MIN_CONF,
            "entity_count": len(entity_items),
            "rel_count": len(rel_items),
        },
        "r6c",
    )
    approve_ids: list[str] = []
    for item in entity_items + rel_items:
        cid = db.add_extraction_candidate(
            bid,
            item["kind"],
            _fp(item["kind"], item["payload"]),
            item["payload"],
            item["source_chunk_id"],
            item["evidence_text"],
        )
        approve_ids.append(cid)

    db.review_extraction_candidates(
        bid, approve_ids, "approved", "r6c recover possible_duplicate + has_step"
    )
    db.set_extraction_batch_status(bid, "approved")
    pre = GraphQualityService(db).inspect_batch(bid)
    print("batch", bid, "approved", len(approve_ids), "preflight", pre.ok, pre.errors)
    if not pre.ok:
        raise SystemExit(f"preflight failed: {pre.errors}")

    assert_write_confirmation(
        db_path=db_path,
        confirm_db_path=str(db_path),
        confirm_batch=bid,
        batch_id=bid,
        confirm_backup=str(backup).replace("\\", "/"),
        require_backup=True,
    )
    audit = GraphCandidateApplier(db).apply(
        bid, operator="cli", backup_path=str(backup).replace("\\", "/")
    )
    out = {"batch_id": bid, "backup": str(backup), "summary": summary, "audit": audit}
    Path("data/llm_backfill_r6c_diagnostic_has_step_apply.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
