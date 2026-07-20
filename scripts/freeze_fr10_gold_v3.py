#!/usr/bin/env python3
"""Freeze FR-10 gold v3 only after 45 human-reviewed Section-anchor migrations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集"
V2 = BASE / "multi_chunk_qa_gold_v2.json"
LEDGER = BASE / "section_anchor_migration_v3.json"
V3 = BASE / "multi_chunk_qa_gold_v3.json"
MANIFEST = BASE / "multi_chunk_qa_gold_v3.manifest.json"


def main() -> int:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    rows = ledger.get("items") or []
    if ledger.get("expected_migration_count") != 45 or len(rows) != 45:
        raise SystemExit("ledger must contain exactly 45 reviewed migrations")
    if not ledger.get("corpus_snapshot_hash"):
        raise SystemExit("ledger requires the reviewed corpus_snapshot_hash")
    if any(row.get("review_status") != "approved" for row in rows):
        raise SystemExit("all 45 migrations require manual review_status=approved")
    gold = json.loads(V2.read_text(encoding="utf-8"))
    by_id = {str(item.get("id")): item for item in gold}
    for row in rows:
        item = by_id.get(str(row.get("id")))
        if not item:
            raise SystemExit(f"unknown gold id: {row.get('id')}")
        item["evidence_anchors"] = [row["new_anchor"]]
    payload = json.dumps(gold, ensure_ascii=False, indent=2) + "\n"
    V3.write_text(payload, encoding="utf-8")
    MANIFEST.write_text(json.dumps({
        "gold_version": "v3", "parent_gold": V2.name,
        "migration_ledger": LEDGER.name, "migration_count": 45,
        "corpus_snapshot_hash": ledger["corpus_snapshot_hash"],
        "gold_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "review_requirement": "all entries manually approved",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
