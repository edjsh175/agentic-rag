#!/usr/bin/env python3
"""Read-only audit of approved relations missing chunk-level evidence (source_chunk_id)."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data/rag_relational.db"
DEFAULT_OUT_DIR = ROOT / "docs/3_待办清单/知识图谱语义抽取/准入准备"
BACKBONE_CREATED_BY = "seed:product_backbone"


def _resolve_db(path: Path) -> Path:
    if path.exists():
        return path
    from rag_knowledge.services.graph_governance import resolve_db_path

    return Path(resolve_db_path())


def _missing_chunk_id_clause() -> str:
    return "(source_chunk_id IS NULL OR TRIM(source_chunk_id) = '')"


def audit(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    total_approved = conn.execute(
        "SELECT COUNT(*) AS c FROM relations WHERE review_status = 'approved'"
    ).fetchone()["c"]
    missing_rows = conn.execute(
        f"""
        SELECT id, source_entity_id, target_entity_id, relation_type, confidence,
               evidence_text, source_chunk_id, review_status, created_by, created_at
        FROM relations
        WHERE review_status = 'approved' AND {_missing_chunk_id_clause()}
        ORDER BY created_by, relation_type, id
        """
    ).fetchall()
    conn.close()

    by_created_by: dict[str, int] = defaultdict(int)
    by_relation_type: dict[str, int] = defaultdict(int)
    by_created_by_type: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in missing_rows:
        created_by = row["created_by"] or ""
        rel_type = row["relation_type"] or ""
        by_created_by[created_by] += 1
        by_relation_type[rel_type] += 1
        by_created_by_type[created_by][rel_type] += 1

    backbone_count = by_created_by.get(BACKBONE_CREATED_BY, 0)
    non_backbone_count = len(missing_rows) - backbone_count

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "summary": {
            "approved_relations_total": total_approved,
            "missing_source_chunk_id_total": len(missing_rows),
            "backbone_seed_missing": backbone_count,
            "non_backbone_missing": non_backbone_count,
        },
        "by_created_by": dict(sorted(by_created_by.items(), key=lambda x: (-x[1], x[0]))),
        "by_relation_type": dict(sorted(by_relation_type.items(), key=lambda x: (-x[1], x[0]))),
        "by_created_by_and_relation_type": {
            k: dict(sorted(v.items(), key=lambda x: (-x[1], x[0])))
            for k, v in sorted(by_created_by_type.items())
        },
        "policy_3a": {
            "backbone_seed": (
                "seed:product_backbone may use document/seed-level evidence without source_chunk_id; "
                "default backbone_seed_policy (management boundary only, not answer-fusion candidate until allowlist confirms)."
            ),
            "non_backbone": (
                "Approved relations without source_chunk_id must be backfilled or excluded from retrieval candidates."
            ),
        },
        "relations_missing_chunk_id": [dict(row) for row in missing_rows],
    }


def _suggest_policy(row: dict) -> str:
    created_by = row.get("created_by") or ""
    has_chunk = bool((row.get("source_chunk_id") or "").strip())
    if has_chunk:
        return "pending_review"
    if created_by == BACKBONE_CREATED_BY:
        return "backbone_seed_policy"
    return "exclude"


def build_allowlist(report: dict, db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    with_chunk_total = conn.execute(
        """
        SELECT COUNT(*) AS c FROM relations
        WHERE review_status = 'approved'
          AND source_chunk_id IS NOT NULL AND TRIM(source_chunk_id) != ''
        """
    ).fetchone()["c"]
    conn.close()

    items = []
    for row in report["relations_missing_chunk_id"]:
        items.append(
            {
                "relation_id": row["id"],
                "relation_type": row["relation_type"],
                "created_by": row.get("created_by") or "",
                "has_source_chunk_id": False,
                "suggested_policy": _suggest_policy(row),
                "evidence_text_preview": (row.get("evidence_text") or "")[:120],
            }
        )

    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[item["suggested_policy"]] += 1
    counts["pending_review"] += with_chunk_total

    return {
        "generated_at": report["generated_at"],
        "status": "draft_pending_human",
        "policy_note": report["policy_3a"],
        "summary": {
            "approved_with_chunk_evidence": with_chunk_total,
            "approved_missing_chunk_evidence": len(items),
            "note": "Only relations missing source_chunk_id are listed; relations with chunk evidence default to pending_review.",
        },
        "counts_by_suggested_policy": dict(counts),
        "items": items,
    }


def _to_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# Relation Chunk Evidence Audit",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- db_path: `{report['db_path']}`",
        f"- approved relations: **{s['approved_relations_total']}**",
        f"- missing source_chunk_id: **{s['missing_source_chunk_id_total']}**",
        f"- backbone seed missing: **{s['backbone_seed_missing']}**",
        f"- non-backbone missing: **{s['non_backbone_missing']}**",
        "",
        "## Policy (3A)",
        "",
        f"- backbone: {report['policy_3a']['backbone_seed']}",
        f"- non-backbone: {report['policy_3a']['non_backbone']}",
        "",
        "## By created_by",
        "",
        "| created_by | count |",
        "|---|---:|",
    ]
    for name, count in report["by_created_by"].items():
        lines.append(f"| {name} | {count} |")
    lines.extend(["", "## By relation_type", "", "| relation_type | count |", "|---|---:|"])
    for name, count in report["by_relation_type"].items():
        lines.append(f"| {name} | {count} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--with-allowlist", action="store_true", default=True)
    args = parser.parse_args()

    db_path = _resolve_db(args.db)
    report = audit(db_path)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    json_path = args.out_dir / "relation_chunk_evidence_audit.json"
    md_path = args.out_dir / "relation_chunk_evidence_audit.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(_to_markdown(report) + "\n", encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")

    if args.with_allowlist:
        allowlist = build_allowlist(report, db_path)
        allowlist_path = args.out_dir / "graph_retrieval_relation_allowlist_draft.json"
        allowlist_path.write_text(json.dumps(allowlist, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {allowlist_path}")


if __name__ == "__main__":
    main()
