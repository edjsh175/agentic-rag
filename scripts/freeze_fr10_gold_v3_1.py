#!/usr/bin/env python3
"""Freeze FR-10 gold v3.1 from approved v3 evidence-anchor corrections."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集"
V3 = BASE / "multi_chunk_qa_gold_v3.json"
LEDGER = BASE / "section_anchor_corrections_v3_1.json"
V31 = BASE / "multi_chunk_qa_gold_v3_1.json"
MANIFEST = BASE / "multi_chunk_qa_gold_v3_1.manifest.json"
EXPECTED_CORRECTIONS = 8


def main() -> int:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    rows = ledger.get("items") or []
    if ledger.get("expected_correction_count") != EXPECTED_CORRECTIONS or len(rows) != EXPECTED_CORRECTIONS:
        raise SystemExit(f"ledger must contain exactly {EXPECTED_CORRECTIONS} approved corrections")
    if not ledger.get("corpus_snapshot_hash") or any(row.get("review_status") != "approved" for row in rows):
        raise SystemExit("ledger requires corpus snapshot and approved rows")
    gold = json.loads(V3.read_text(encoding="utf-8"))
    by_id = {str(item.get("id")): item for item in gold}
    reviewed = []
    for row in rows:
        item = by_id.get(str(row.get("id")))
        anchors = row.get("new_anchors") or []
        if item is None or not anchors:
            raise SystemExit(f"invalid correction: {row.get('id')}")
        old_anchors = item.get("evidence_anchors") or []
        item["evidence_anchors"] = anchors
        reviewed.append({
            "id": item["id"], "question": item.get("question"),
            "old_anchors": old_anchors, "new_anchors": anchors,
            "verification_chunk_id": row.get("verification_chunk_id"),
            "rationale": row.get("rationale"), "reviewer": row.get("reviewer"),
            "reviewed_at": row.get("reviewed_at"),
        })
    payload = json.dumps(gold, ensure_ascii=False, indent=2) + "\n"
    V31.write_text(payload, encoding="utf-8")
    MANIFEST.write_text(json.dumps({
        "gold_version": "v3.1", "parent_gold": V3.name,
        "correction_ledger": LEDGER.name, "correction_count": EXPECTED_CORRECTIONS,
        "corpus_snapshot_hash": ledger["corpus_snapshot_hash"],
        "gold_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "review_requirement": "all entries manually approved",
        "reviewed_corrections": reviewed,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
