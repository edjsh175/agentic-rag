#!/usr/bin/env python3
"""Diff and delete obsolete seed:product_backbone facts before re-staging the new backbone."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_governance import resolve_db_path
from rag_knowledge.services.product_backbone_graph_sync import SEED_CREATED_BY

ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "data" / "product_relation_backbone.json"
BACKUP_DIR = ROOT / "data" / "backups"
REPORT_DIR = ROOT / "data"


def _load_formal_edges() -> tuple[set[str], set[tuple[str, str, str]]]:
    data = json.loads(FORMAL.read_text(encoding="utf-8"))
    entities = {str(e["name"]).strip() for e in data.get("entities") or [] if e.get("name")}
    edges: set[tuple[str, str, str]] = set()
    for item in data.get("relations") or []:
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        rel = str(item.get("relation_type") or "").strip()
        if source and target and rel:
            edges.add((source, rel, target))
    return entities, edges


def _seed_relations(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT r.id, r.relation_type, r.source_entity_id, r.target_entity_id,
               s.name AS source_name, t.name AS target_name,
               s.created_by AS source_created_by, t.created_by AS target_created_by
        FROM relations r
        JOIN entities s ON s.id = r.source_entity_id
        JOIN entities t ON t.id = r.target_entity_id
        WHERE r.created_by = ?
        """,
        (SEED_CREATED_BY,),
    ).fetchall()
    return [dict(r) for r in rows]


def _seed_entities(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, entity_type, created_by FROM entities WHERE created_by = ?",
        (SEED_CREATED_BY,),
    ).fetchall()
    return [dict(r) for r in rows]


def build_diff(conn: sqlite3.Connection) -> dict:
    formal_entities, formal_edges = _load_formal_edges()
    old_rels = _seed_relations(conn)
    old_edges = {(r["source_name"], r["relation_type"], r["target_name"]): r for r in old_rels}

    to_delete = []
    to_keep = []
    for key, row in old_edges.items():
        if key in formal_edges:
            to_keep.append({"id": row["id"], "source": key[0], "relation_type": key[1], "target": key[2]})
        else:
            to_delete.append({"id": row["id"], "source": key[0], "relation_type": key[1], "target": key[2]})

    to_add = sorted(
        [
            {"source": s, "relation_type": rt, "target": t}
            for (s, rt, t) in formal_edges
            if (s, rt, t) not in old_edges
        ],
        key=lambda x: (x["source"], x["relation_type"], x["target"]),
    )

    return {
        "formal_entity_count": len(formal_entities),
        "formal_relation_count": len(formal_edges),
        "old_seed_relation_count": len(old_rels),
        "old_seed_entity_count": len(_seed_entities(conn)),
        "to_delete": sorted(to_delete, key=lambda x: (x["source"], x["relation_type"], x["target"])),
        "to_keep": sorted(to_keep, key=lambda x: (x["source"], x["relation_type"], x["target"])),
        "to_add": to_add,
        "formal_entities": sorted(formal_entities),
    }


def cleanup(conn: sqlite3.Connection, diff: dict, *, apply: bool) -> dict:
    deleted_relation_ids = [item["id"] for item in diff["to_delete"]]
    formal_entities = set(diff["formal_entities"])

    deleted_entities: list[dict] = []
    if apply and deleted_relation_ids:
        conn.executemany("DELETE FROM relations WHERE id = ?", [(rid,) for rid in deleted_relation_ids])

    # After relation deletes, find seed-only entities not in formal set and unreferenced.
    seed_entities = _seed_entities(conn)
    referenced: set[str] = set()
    for row in conn.execute("SELECT source_entity_id, target_entity_id FROM relations").fetchall():
        referenced.add(row["source_entity_id"])
        referenced.add(row["target_entity_id"])

    candidates = []
    for ent in seed_entities:
        if ent["name"] in formal_entities:
            continue
        if ent["id"] in referenced:
            continue
        # Never delete non-seed creators (already filtered), but double-check.
        if ent["created_by"] != SEED_CREATED_BY:
            continue
        candidates.append(ent)

    if apply:
        for ent in candidates:
            conn.execute("DELETE FROM aliases WHERE entity_id = ?", (ent["id"],))
            conn.execute("DELETE FROM entity_chunk_links WHERE entity_id = ?", (ent["id"],))
            conn.execute("DELETE FROM entities WHERE id = ?", (ent["id"],))
            deleted_entities.append({"id": ent["id"], "name": ent["name"], "entity_type": ent["entity_type"]})
    else:
        deleted_entities = [
            {"id": e["id"], "name": e["name"], "entity_type": e["entity_type"]} for e in candidates
        ]

    return {
        "deleted_relations": len(deleted_relation_ids),
        "deleted_relation_ids": deleted_relation_ids,
        "deleted_entities": deleted_entities,
        "deleted_entity_count": len(deleted_entities),
        "applied": apply,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Execute deletes in a transaction")
    parser.add_argument("--backup", action="store_true", help="Copy DB before apply")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    db_path = Path(resolve_db_path())
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = None
    if args.backup or args.apply:
        backup_path = BACKUP_DIR / f"rag_relational_pre_backbone_replace_{stamp}.db"
        shutil.copy2(db_path, backup_path)

    # Use raw connection for transactional cleanup; RelationalDB still validates path.
    _ = RelationalDB()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        diff = build_diff(conn)
        result = cleanup(conn, diff, apply=args.apply)
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()

    report = {
        "db_path": str(db_path),
        "backup_path": str(backup_path) if backup_path else None,
        "diff": {
            "formal_entity_count": diff["formal_entity_count"],
            "formal_relation_count": diff["formal_relation_count"],
            "old_seed_relation_count": diff["old_seed_relation_count"],
            "old_seed_entity_count": diff["old_seed_entity_count"],
            "to_delete_count": len(diff["to_delete"]),
            "to_keep_count": len(diff["to_keep"]),
            "to_add_count": len(diff["to_add"]),
            "to_delete": diff["to_delete"],
            "to_keep": diff["to_keep"],
            "to_add": diff["to_add"],
        },
        "cleanup": result,
    }
    report_path = REPORT_DIR / f"backbone_replace_diff_{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(report_path)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"backup={backup_path} delete_rels={result['deleted_relations']} "
            f"delete_ents={result['deleted_entity_count']} keep={len(diff['to_keep'])} "
            f"add={len(diff['to_add'])} applied={args.apply} report={report_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
