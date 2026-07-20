#!/usr/bin/env python3
"""Freeze FR-10 gold v3.2 from approved v3.1 question corrections."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集"
V31 = BASE / "multi_chunk_qa_gold_v3_1.json"
LEDGER = BASE / "question_corrections_v3_2.json"
V32 = BASE / "multi_chunk_qa_gold_v3_2.json"
MANIFEST = BASE / "multi_chunk_qa_gold_v3_2.manifest.json"


def main() -> int:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    rows = ledger.get("items") or []
    if ledger.get("expected_correction_count") != 1 or len(rows) != 1:
        raise SystemExit("ledger must contain exactly one approved correction")
    if not ledger.get("corpus_snapshot_hash") or rows[0].get("review_status") != "approved":
        raise SystemExit("ledger requires corpus snapshot and approved correction")
    gold = json.loads(V31.read_text(encoding="utf-8"))
    item = next((entry for entry in gold if entry.get("id") == rows[0].get("id")), None)
    if item is None or item.get("question") != rows[0].get("old_question"):
        raise SystemExit("parent question does not match correction ledger")
    item["question"] = rows[0]["new_question"]
    payload = json.dumps(gold, ensure_ascii=False, indent=2) + "\n"
    V32.write_text(payload, encoding="utf-8")
    MANIFEST.write_text(json.dumps({
        "gold_version": "v3.2", "parent_gold": V31.name,
        "correction_ledger": LEDGER.name, "correction_count": 1,
        "corpus_snapshot_hash": ledger["corpus_snapshot_hash"],
        "gold_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "review_requirement": "manually approved correction",
        "reviewed_corrections": rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
