"""Read-only draft export of extraction exemplar candidates from the formal graph.

Output is a DRAFT JSON for human curation. Do NOT copy it into
data/extraction_exemplars/ without review.

Usage:
  .\\venv\\Scripts\\python.exe scripts/export_extraction_exemplar_candidates.py
  .\\venv\\Scripts\\python.exe scripts/export_extraction_exemplar_candidates.py --product StampTools --limit 20
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "rag_relational.db"
CREATED_BY_ALLOW = (
    "rule:chapter_leaf",
    "rule:server_leaf",
    "rule:phase_b",
    "manual",
    "admin",
    "llm:schema_extractor",
    "llm:leak_salvage",
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Export draft extraction exemplars (read-only)")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--product", default="StampTools", help="Filter by doc_category / product name hint")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument(
        "--output",
        default=str(ROOT / "data" / "_exemplar_candidates_draft.json"),
        help="Draft output path (not under extraction_exemplars/)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db)
    if not db_path.is_file():
        raise SystemExit(f"database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    placeholders = ",".join("?" for _ in CREATED_BY_ALLOW)
    rows = conn.execute(
        f"""
        SELECT e.id, e.name, e.entity_type, e.doc_category, e.created_by
        FROM entities e
        WHERE e.created_by IN ({placeholders})
          AND e.entity_type IN ('Procedure', 'Format', 'Command')
          AND (
            e.doc_category = ?
            OR e.doc_category LIKE ?
            OR e.name LIKE ?
          )
        ORDER BY e.entity_type, e.name
        LIMIT ?
        """,
        (*CREATED_BY_ALLOW, args.product, f"%{args.product}%", f"%{args.product}%", args.limit),
    ).fetchall()

    drafts = []
    for row in rows:
        rels = conn.execute(
            """
            SELECT s.name AS source_name, r.relation_type, t.name AS target_name
            FROM relations r
            JOIN entities s ON s.id = r.source_entity_id
            JOIN entities t ON t.id = r.target_entity_id
            WHERE s.id = ? OR t.id = ?
            LIMIT 12
            """,
            (row["id"], row["id"]),
        ).fetchall()
        link = conn.execute(
            """
            SELECT chunk_id, section_path, evidence_text
            FROM entity_chunk_links
            WHERE entity_id = ?
            LIMIT 1
            """,
            (row["id"],),
        ).fetchone()
        drafts.append(
            {
                "id": f"draft-{row['entity_type'].lower()}-{row['name'][:40]}",
                "scenario": "draft_from_formal_db",
                "doc_category": row["doc_category"] or args.product,
                "section_path": (link["section_path"] if link else "") or "",
                "content_excerpt": (link["evidence_text"] if link else "") or row["name"],
                "good": {
                    "entities": [
                        {"name": row["name"], "entity_type": row["entity_type"]}
                    ],
                    "relations": [
                        {
                            "source_name": r["source_name"],
                            "relation_type": r["relation_type"],
                            "target_name": r["target_name"],
                        }
                        for r in rels
                    ],
                },
                "bad": [
                    "DRAFT — review before promoting to data/extraction_exemplars/",
                    "不要把 GUI 字段升成 ConfigItem",
                ],
                "_meta": {
                    "entity_id": row["id"],
                    "created_by": row["created_by"],
                    "chunk_id": (link["chunk_id"] if link else None),
                },
            }
        )

    payload = {
        "pack_id": f"draft_{args.product.lower()}",
        "doc_categories": [args.product],
        "purpose": "DRAFT only — human curation required before production use",
        "exemplars": drafts,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), "count": len(drafts)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
