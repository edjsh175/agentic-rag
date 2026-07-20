#!/usr/bin/env python3
"""Create a read-only evidence-review ledger for the 90 FR-10 v4 retrieval candidates."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BASE = ROOT / "docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集"
DEFAULT_GOLD = BASE / "multi_chunk_qa_gold_v4_retrieval_candidate.json"
DEFAULT_OUT = BASE / "multi_chunk_qa_gold_v4_retrieval_candidate.audit.json"


def _source_matches(metadata: dict[str, Any], source: str) -> bool:
    expected = source.strip().replace("\\", "/")
    values = (metadata.get("source"), metadata.get("file_path"), metadata.get("file_name"))
    return any(
        (actual := str(value or "").strip().replace("\\", "/"))
        and (actual == expected or actual.endswith(f"/{expected}"))
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


def _excerpt(text: str, needle: str) -> str:
    index = text.casefold().find(needle.casefold())
    if index < 0:
        return ""
    return text[max(0, index - 160) : index + len(needle) + 320]


def _chunk_view(chunk: dict[str, Any], *, limit: int = 1200) -> dict[str, Any]:
    metadata = chunk["metadata"]
    return {
        "chunk_id": chunk["id"],
        "section_id": metadata.get("section_id") or "",
        "section_path": metadata.get("section_path") or "",
        "content_excerpt": str(chunk["document"] or "")[:limit],
    }


def _fact_check(fact: str, anchor_chunks: list[dict[str, Any]]) -> dict[str, Any]:
    alternatives = _alternatives(fact)
    hits = []
    for alternative in alternatives:
        for chunk in anchor_chunks:
            text = str(chunk["document"] or "")
            excerpt = _excerpt(text, alternative)
            if excerpt:
                hits.append({"alternative": alternative, "chunk_id": chunk["id"], "evidence_excerpt": excerpt})
                break
    return {
        "required_fact": fact,
        "accepted_alternatives": alternatives,
        "direct_match": bool(hits),
        "hits": hits,
    }


def audit_item(item: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    anchors = item.get("evidence_anchors") or []
    anchor_results = []
    all_anchor_chunks: dict[str, dict[str, Any]] = {}
    for anchor in anchors:
        matches = [chunk for chunk in chunks if _anchor_matches(chunk["metadata"], anchor)]
        for chunk in matches:
            all_anchor_chunks[chunk["id"]] = chunk
        source_matches = [chunk for chunk in chunks if _source_matches(chunk["metadata"], str(anchor.get("source") or ""))]
        anchor_results.append(
            {
                "anchor": anchor,
                "matched_chunk_count": len(matches),
                "source_chunk_count": len(source_matches),
                "matched_chunks": [_chunk_view(chunk) for chunk in matches[:8]],
            }
        )
    fact_checks = [_fact_check(str(fact), list(all_anchor_chunks.values())) for fact in item.get("required_facts") or []]
    anchor_missing = any(row["matched_chunk_count"] == 0 for row in anchor_results)
    facts_not_direct = [row["required_fact"] for row in fact_checks if not row["direct_match"]]
    if anchor_missing:
        review_state = "needs_anchor_repair"
    elif facts_not_direct:
        review_state = "needs_fact_review"
    elif item.get("review_status") == "approved":
        review_state = "verified_after_review"
    else:
        review_state = "pending_manual_review"
    return {
        "id": item["id"],
        "category": item["category"],
        "question": item["question"],
        "ground_truth": item["ground_truth"],
        "required_facts": item.get("required_facts") or [],
        "review_state": review_state,
        "manual_review_status": "unreviewed",
        "anchor_results": anchor_results,
        "fact_checks": fact_checks,
        "facts_not_directly_found": facts_not_direct,
        "reviewer_decision": "",
        "reviewer_rationale": "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only v4 retrieval-candidate evidence audit")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--expected-chunks", type=int, default=2537)
    parser.add_argument("--expected-items", type=int, default=90)
    args = parser.parse_args(argv)

    from rag_knowledge.repository.vector_store import VectorStore

    items = json.loads(args.gold.read_text(encoding="utf-8"))
    if len(items) != args.expected_items or any(item.get("evaluation_scope") != "fr10_retrieval" for item in items):
        raise SystemExit(f"gold must contain {args.expected_items} FR-10 retrieval items")
    collection = VectorStore().get_chroma()._collection
    raw = collection.get(include=["documents", "metadatas"])
    chunks = [
        {"id": str(chunk_id), "document": document or "", "metadata": dict(metadata or {})}
        for chunk_id, document, metadata in zip(raw.get("ids") or [], raw.get("documents") or [], raw.get("metadatas") or [])
    ]
    if len(chunks) != args.expected_chunks:
        raise SystemExit(f"expected {args.expected_chunks} live chunks, found {len(chunks)}")

    rows = [audit_item(item, chunks) for item in items]
    summary = Counter(row["review_state"] for row in rows)
    payload = {
        "audit_version": "fr10-v4-retrieval-candidate-audit-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gold": args.gold.name,
        "collection_chunk_count": len(chunks),
        "read_only": True,
        "summary": dict(sorted(summary.items())),
        "freeze_rule": "Every item remains unreviewed until a human records an approved or rejected reviewer decision. Direct phrase matches are evidence aids, not correctness decisions.",
        "items": rows,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
