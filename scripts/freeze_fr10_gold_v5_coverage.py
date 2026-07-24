#!/usr/bin/env python3
"""Freeze v5 coverage gold from candidates after automated evidence verification.

Keeps only items whose anchors resolve on live chunks and whose required_facts
have direct phrase matches. Does not modify v4.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = ROOT / "docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集"
DEFAULT_CANDIDATE = BASE / "multi_chunk_qa_gold_v5_coverage_candidate.json"
DEFAULT_CANDIDATE_MANIFEST = BASE / "multi_chunk_qa_gold_v5_coverage.candidate.manifest.json"
DEFAULT_OUT = BASE / "multi_chunk_qa_gold_v5_coverage.json"
DEFAULT_MANIFEST = BASE / "multi_chunk_qa_gold_v5_coverage.manifest.json"
DEFAULT_LEDGER = BASE / "multi_chunk_qa_gold_v5_coverage.review_ledger.json"
DEFAULT_AUDIT = BASE / "multi_chunk_qa_gold_v5_coverage.audit.json"


def _source_matches(metadata: dict[str, Any], source: str) -> bool:
    expected = source.strip().replace("\\", "/")
    values = (metadata.get("source"), metadata.get("file_path"), metadata.get("file_name"))
    return any(
        (actual := str(value or "").strip().replace("\\", "/"))
        and (actual == expected or actual.endswith("/" + expected) or actual.endswith(expected))
        for value in values
    )


def _anchor_matches(metadata: dict[str, Any], anchor: dict[str, Any]) -> bool:
    if not _source_matches(metadata, str(anchor.get("source") or "")):
        return False
    section_id = str(anchor.get("section_id") or "").strip()
    if section_id and str(metadata.get("section_id") or "").strip() != section_id:
        return False
    section = str(anchor.get("section_path_contains") or "").strip()
    return not section or section in str(metadata.get("section_path") or "")


def _alternatives(fact: str) -> list[str]:
    return [part.strip() for part in fact.replace(" OR ", " 或 ").replace(" or ", " 或 ").split("或") if part.strip()]


def _fact_direct(fact: str, texts: list[str]) -> bool:
    for alt in _alternatives(fact):
        needle = alt.casefold()
        if any(needle in text.casefold() for text in texts):
            return True
    return False


def _load_chunks() -> list[dict[str, Any]]:
    from rag_knowledge.repository.vector_store import VectorStore

    raw = VectorStore().get_chunk_stats_source()
    return [
        {"id": str(chunk_id), "document": document or "", "metadata": dict(metadata or {})}
        for chunk_id, document, metadata in zip(
            raw.get("ids") or [], raw.get("documents") or [], raw.get("metadatas") or []
        )
    ]


def verify_item(item: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    anchors = item.get("evidence_anchors") or []
    matched: list[dict[str, Any]] = []
    for anchor in anchors:
        matched.extend(chunk for chunk in chunks if _anchor_matches(chunk["metadata"], anchor))
    texts = [str(chunk.get("document") or "") for chunk in matched]
    facts = [str(f) for f in (item.get("required_facts") or [])]
    missing_facts = [fact for fact in facts if not _fact_direct(fact, texts)]
    if not matched:
        decision = "rejected"
        reason = "anchor_not_found"
    elif not facts:
        decision = "rejected"
        reason = "missing_required_facts"
    elif missing_facts:
        decision = "rejected"
        reason = "facts_not_in_anchor"
    else:
        decision = "approved"
        reason = "anchor_and_facts_verified"
    return {
        "id": item.get("id"),
        "decision": decision,
        "reason": reason,
        "matched_chunk_count": len(matched),
        "missing_facts": missing_facts,
        "question": item.get("question"),
        "category": item.get("category"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze verified v5 coverage gold items")
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--candidate-manifest", type=Path, default=DEFAULT_CANDIDATE_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--min-approved", type=int, default=80)
    args = parser.parse_args(argv)

    items = json.loads(args.candidate.read_text(encoding="utf-8"))
    cand_manifest = json.loads(args.candidate_manifest.read_text(encoding="utf-8"))
    chunks = _load_chunks()

    ledger_rows = [verify_item(item, chunks) for item in items]
    approved_ids = {row["id"] for row in ledger_rows if row["decision"] == "approved"}
    frozen: list[dict[str, Any]] = []
    for item in items:
        if item.get("id") not in approved_ids:
            continue
        row = dict(item)
        row["review_status"] = "approved"
        row["review_basis"] = "v5 automated evidence verification (anchor + required_facts direct match)"
        row.pop("slot_id", None)
        frozen.append(row)

    payload = json.dumps(frozen, ensure_ascii=False, indent=2) + "\n"
    args.out.write_text(payload, encoding="utf-8")

    counts = {
        "approved": sum(1 for r in ledger_rows if r["decision"] == "approved"),
        "rejected": sum(1 for r in ledger_rows if r["decision"] == "rejected"),
    }
    ledger = {
        "ledger_version": "v5-coverage-review-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate": args.candidate.name,
        "counts": counts,
        "items": ledger_rows,
        "note": "Automated verification only; optional human signoff can refine later without changing v4.",
    }
    args.ledger.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.audit.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cat_counts: dict[str, int] = {}
    for item in frozen:
        cat = str(item.get("category") or "")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    manifest = {
        "gold_version": "v5-coverage",
        "status": "frozen_for_coverage_tuning",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parent_candidate": args.candidate.name,
        "candidate_manifest": args.candidate_manifest.name,
        "corpus_snapshot_hash": cand_manifest.get("corpus_snapshot_hash"),
        "gold_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "review_ledger": args.ledger.name,
        "review_counts": counts,
        "retrieval_question_count": len(frozen),
        "category_counts": cat_counts,
        "scope": "rewrite_and_filter_tuning",
        "relation_to_v4": "independent; v4 FR-10 baseline unchanged",
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "frozen_count": len(frozen),
                "counts": counts,
                "category_counts": cat_counts,
                "out": str(args.out),
                "manifest": str(args.manifest),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if len(frozen) < args.min_approved:
        print(f"ERROR: frozen_count={len(frozen)} < min_approved={args.min_approved}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
