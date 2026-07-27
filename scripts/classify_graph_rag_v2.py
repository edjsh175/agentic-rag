#!/usr/bin/env python3
"""Classify GraphRAG A/B item deltas for v2 expand / postfix reports."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _hit3(retrieved: list[str], relevant: set[str]) -> bool:
    return bool(set(retrieved[:3]) & relevant)


def classify_status(a: dict, b: dict) -> str:
    relevant = set(a.get("relevant_chunk_ids") or [])
    a_hit = _hit3(a.get("retrieved_chunk_ids") or [], relevant)
    b_hit = _hit3(b.get("retrieved_chunk_ids") or [], relevant)
    am, bm = a.get("metrics") or {}, b.get("metrics") or {}
    a_r3, b_r3 = am.get("recall@3") or 0, bm.get("recall@3") or 0
    a_mrr, b_mrr = am.get("mrr") or 0, bm.get("mrr") or 0
    if not a_hit and b_hit:
        return "gain"
    if a_hit and not b_hit:
        return "regress"
    if not a_hit and not b_hit:
        return "still_miss"
    if b_r3 > a_r3 + 1e-9 or b_mrr > a_mrr + 1e-9:
        return "gain"
    if b_r3 < a_r3 - 1e-9 or b_mrr < a_mrr - 1e-9:
        return "regress"
    return "stable_hit"


def vuln_class(row: dict, a: dict, b: dict) -> str | None:
    if row["status"] not in {"regress", "still_miss"}:
        return None
    expected = set(row.get("expected_entities") or [])
    linked = set(row.get("linked_entities") or [])
    fb = row.get("graph_fallback_reason")
    if fb == "graph_evidence_filtered":
        return "图证据过滤"
    if fb == "no_linked_entity":
        return "未链上实体"
    if expected and linked and not (expected & linked):
        return "改写偏锚/实体链偏"
    if expected and linked and expected <= linked and not row.get("b_hit@3"):
        return "金标过窄/过宽"
    if row["status"] == "regress":
        return "改写偏锚/实体链偏"
    return "邻域挤占"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "data" / "eval_graph_rag_dataset_v2.json")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--treatment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    ds = json.loads(args.dataset.read_text(encoding="utf-8"))
    by_seed = {x["id"]: x for x in ds.get("items") or []}
    off = json.loads(args.baseline.read_text(encoding="utf-8"))
    on = json.loads(args.treatment.read_text(encoding="utf-8"))
    a_by = {r["id"]: r for r in off.get("results") or []}
    b_by = {r["id"]: r for r in on.get("results") or []}

    rows = []
    buckets: dict[str, list[str]] = {
        "gain": [],
        "regress": [],
        "still_miss": [],
        "stable_hit": [],
    }
    by_cat: Counter[str] = Counter()
    vuln_counts: Counter[str] = Counter()

    for qid in sorted(set(a_by) | set(b_by)):
        a, b = a_by[qid], b_by[qid]
        seed = by_seed.get(qid) or {}
        relevant = set(a.get("relevant_chunk_ids") or [])
        status = classify_status(a, b)
        am, bm = a.get("metrics") or {}, b.get("metrics") or {}
        row = {
            "id": qid,
            "question": b.get("question") or a.get("question"),
            "intent": b.get("intent") or seed.get("intent"),
            "doc_category": b.get("doc_category") or seed.get("doc_category"),
            "status": status,
            "a_hit@3": _hit3(a.get("retrieved_chunk_ids") or [], relevant),
            "b_hit@3": _hit3(b.get("retrieved_chunk_ids") or [], relevant),
            "delta_recall@3": (bm.get("recall@3") or 0) - (am.get("recall@3") or 0),
            "delta_mrr": (bm.get("mrr") or 0) - (am.get("mrr") or 0),
            "linked_entities": b.get("linked_entities") or [],
            "expected_entities": b.get("expected_entities") or seed.get("linked_entity_names") or [],
            "linked_entity_hit": bool(
                set(b.get("linked_entities") or [])
                & set(b.get("expected_entities") or seed.get("linked_entity_names") or [])
            ),
            "graph_fallback_reason": b.get("graph_fallback_reason"),
            "graph_chunk_in_topk5": bool(
                set(b.get("graph_chunk_ids") or []) & set((b.get("retrieved_chunk_ids") or [])[:5])
            ),
        }
        row["vuln_class"] = vuln_class(row, a, b)
        rows.append(row)
        buckets[status].append(qid)
        by_cat[row.get("doc_category") or "?"] += 1
        if row["vuln_class"]:
            vuln_counts[row["vuln_class"]] += 1

    out = {
        **buckets,
        "by_category": dict(by_cat),
        "vuln_counts": dict(vuln_counts),
        "rows": rows,
        "summary": {k: len(v) for k, v in buckets.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": out["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
