#!/usr/bin/env python3
"""Repair product backbone ownership: layers are facets; catalog owns Tool/Service parents."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.backbone_ownership import (
    ARCHITECTURE_LAYER_NAMES,
    catalog_ownership_expectations,
    is_architecture_layer_name,
    repair_backbone_payload,
)
from rag_knowledge.services.domain_catalog import DomainCatalogLoader

ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "data" / "product_relation_backbone_preview.json"
FORMAL = ROOT / "data" / "product_relation_backbone.json"
BACKUP_DIR = ROOT / "data" / "archive" / "backups"


def _backup(path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"{path.stem}_pre_ownership_{stamp}{path.suffix}"
    shutil.copy2(path, dest)
    return dest


def repair_preview(*, write: bool) -> dict:
    raw = json.loads(PREVIEW.read_text(encoding="utf-8"))
    repaired, report = repair_backbone_payload(raw)
    summary = {
        "preview": str(PREVIEW),
        "write": write,
        **report.as_dict(),
        "relations_before": len(raw.get("relations") or []),
        "relations_after": len(repaired.get("relations") or []),
    }
    if write:
        backup = _backup(PREVIEW)
        PREVIEW.write_text(json.dumps(repaired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary["backup"] = str(backup)
    return summary


def _repair_live_conn(conn: sqlite3.Connection, *, write: bool) -> dict:
    expectations = catalog_ownership_expectations(DomainCatalogLoader())
    dropped: list[dict] = []
    ensured: list[dict] = []
    skipped: list[str] = []

    layer_rows = conn.execute(
        """
        SELECT r.id, s.name AS source_name, s.entity_type AS source_type,
               t.name AS target_name, r.created_by
        FROM relations r
        JOIN entities s ON s.id = r.source_entity_id
        JOIN entities t ON t.id = r.target_entity_id
        WHERE r.relation_type = 'belongs_to'
        """
    ).fetchall()
    for row in layer_rows:
        if not is_architecture_layer_name(row["target_name"]):
            continue
        if row["source_type"] not in {"Tool", "Service", "Product"}:
            continue
        dropped.append(
            {
                "id": row["id"],
                "source": row["source_name"],
                "target": row["target_name"],
                "created_by": row["created_by"],
            }
        )
        if write:
            conn.execute("DELETE FROM relations WHERE id = ?", (row["id"],))

    name_rows = {
        str(r["name"]): dict(r)
        for r in conn.execute(
            "SELECT id, name, entity_type FROM entities WHERE review_status = 'approved'"
        ).fetchall()
    }
    existing_edges = {
        (r["source_name"], r["target_name"])
        for r in conn.execute(
            """
            SELECT s.name AS source_name, t.name AS target_name
            FROM relations r
            JOIN entities s ON s.id = r.source_entity_id
            JOIN entities t ON t.id = r.target_entity_id
            WHERE r.relation_type = 'belongs_to' AND r.review_status = 'approved'
            """
        ).fetchall()
    }

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    for child, owner in sorted(expectations.items()):
        child_ent = name_rows.get(child)
        owner_ent = name_rows.get(owner)
        if not child_ent or not owner_ent:
            skipped.append(f"missing_endpoint:{child}->{owner}")
            continue
        if child_ent["entity_type"] not in {"Tool", "Service"}:
            skipped.append(f"type_mismatch_child:{child}:{child_ent['entity_type']}")
            continue
        if owner_ent["entity_type"] not in {"Product", "Tool", "Service"}:
            skipped.append(f"type_mismatch_owner:{child}->{owner}:{owner_ent['entity_type']}")
            continue
        if (child, owner) in existing_edges:
            continue
        ensured.append({"source": child, "target": owner})
        if write:
            conn.execute(
                "INSERT INTO relations (id, source_entity_id, target_entity_id, relation_type, "
                "properties_json, confidence, evidence_text, source_chunk_id, review_status, "
                "created_by, created_at) VALUES (?, ?, ?, 'belongs_to', '{}', 1.0, ?, '', "
                "'approved', 'seed:product_backbone', ?)",
                (
                    str(uuid.uuid4()),
                    child_ent["id"],
                    owner_ent["id"],
                    "ownership:catalog",
                    now,
                ),
            )
            existing_edges.add((child, owner))

    if write:
        conn.commit()

    return {
        "write": write,
        "architecture_layers": sorted(ARCHITECTURE_LAYER_NAMES),
        "dropped_layer_edges": dropped,
        "ensured_owner_edges": ensured,
        "skipped": skipped,
        "dropped_count": len(dropped),
        "ensured_count": len(ensured),
    }


def repair_live_db(*, write: bool, db_path: str | None = None) -> dict:
    if db_path:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            return _repair_live_conn(conn, write=write)
        finally:
            conn.close()
    db = RelationalDB()
    with db._get_conn() as conn:
        return _repair_live_conn(conn, write=write)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="repair product_relation_backbone_preview.json")
    parser.add_argument("--live-db", action="store_true", help="repair belongs_to edges in rag_relational.db")
    parser.add_argument("--db-path", default="", help="optional sqlite path for --live-db")
    parser.add_argument("--write", action="store_true", help="persist changes (default dry-run)")
    parser.add_argument("--json", action="store_true", help="print JSON summary")
    args = parser.parse_args()

    if not args.preview and not args.live_db:
        parser.error("specify --preview and/or --live-db")

    summary: dict = {}
    if args.preview:
        summary["preview"] = repair_preview(write=args.write)
    if args.live_db:
        summary["live_db"] = repair_live_db(
            write=args.write,
            db_path=args.db_path or None,
        )

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(summary)


if __name__ == "__main__":
    main()
