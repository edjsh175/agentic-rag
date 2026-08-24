#!/usr/bin/env python3
"""Freeze FR-10 gold v3 only after 55 human-reviewed Section-anchor migrations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs/04_已完成归档/01_文档解析与切块/已完成-第0B轮-并行准备与预研/评测基线与黄金集"
V2 = BASE / "multi_chunk_qa_gold_v2.json"
LEDGER = BASE / "section_anchor_migration_v3.json"
V3 = BASE / "multi_chunk_qa_gold_v3.json"
MANIFEST = BASE / "multi_chunk_qa_gold_v3.manifest.json"
EXPECTED_MIGRATIONS = 55


def main() -> int:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    rows = ledger.get("items") or []
    if ledger.get("expected_migration_count") != EXPECTED_MIGRATIONS or len(rows) != EXPECTED_MIGRATIONS:
        raise SystemExit(f"ledger must contain exactly {EXPECTED_MIGRATIONS} reviewed migrations")
    if not ledger.get("corpus_snapshot_hash"):
        raise SystemExit("ledger requires the reviewed corpus_snapshot_hash")
    if any(row.get("review_status") != "approved" for row in rows):
        raise SystemExit("all 55 migrations require manual review_status=approved")
    gold = json.loads(V2.read_text(encoding="utf-8"))
    by_id = {str(item.get("id")): item for item in gold}
    reviewed_records = []
    for row in rows:
        item = by_id.get(str(row.get("id")))
        if not item:
            raise SystemExit(f"unknown gold id: {row.get('id')}")
        if not row.get("new_anchor"):
            raise SystemExit(f"missing new_anchor for {row.get('id')}")
        old_anchors = item.get("evidence_anchors") or []
        item["evidence_anchors"] = list(row.get("new_anchors") or [row["new_anchor"]])
        reviewed_records.append({
            "id": item["id"],
            "question": item.get("question"),
            "old_anchors": old_anchors,
            "new_anchors": item["evidence_anchors"],
            "verification_chunk_id": row.get("verification_chunk_id"),
            "rationale": row.get("rationale"),
            "reviewer": row.get("reviewer"),
            "reviewed_at": row.get("reviewed_at"),
        })
    payload = json.dumps(gold, ensure_ascii=False, indent=2) + "\n"
    V3.write_text(payload, encoding="utf-8")
    MANIFEST.write_text(json.dumps({
        "gold_version": "v3", "parent_gold": V2.name,
        "migration_ledger": LEDGER.name, "migration_count": EXPECTED_MIGRATIONS,
        "corpus_snapshot_hash": ledger["corpus_snapshot_hash"],
        "gold_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "review_requirement": "all entries manually approved",
        "reviewed_migrations": reviewed_records,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
