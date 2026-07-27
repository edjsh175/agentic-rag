#!/usr/bin/env python3
"""Round-4 GraphRAG retrieval A/B runner (production path, no answer LLM)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_knowledge.evaluation.metrics import compute_all, compute_batch
from rag_knowledge.services.rag import RagChain

DEFAULT_DATASET = ROOT / "data" / "eval_graph_rag_dataset.json"


def _chunk_ids(docs: list[dict]) -> list[str]:
    out: list[str] = []
    for doc in docs:
        meta = doc.get("metadata") or {}
        cid = meta.get("chunk_id") or meta.get("id")
        if cid:
            out.append(str(cid))
    return out


def _run_one(rag: RagChain, item: dict) -> dict:
    q = item["question"]
    kb = item.get("kb_name")
    cat = item.get("doc_category")
    relevant = set(item.get("relevant_chunk_ids") or [])
    expected = [str(x) for x in (item.get("linked_entity_names") or [])]

    queries = rag._build_retrieval_query_specs(q, None)
    plan = rag._plan_retrieval(q, queries, force_rerank=False)
    plan, gctx, gdocs = rag._prepare_graph_plan(
        q, plan, kb_name=kb, doc_category=cat, review_status="approved"
    )
    linked = [e.canonical_name for e in (plan.linked_entities or ())]
    fallback = plan.graph_fallback_reason
    if fallback is None and gctx is not None:
        fallback = gctx.fallback_reason
    graph_chunk_ids = list(getattr(plan, "graph_chunk_ids", ()) or ())
    if not graph_chunk_ids and gctx is not None:
        graph_chunk_ids = list(gctx.chunk_ids or [])

    docs, _ = rag.retrieve_for_evaluation(
        q, kb_name=kb, doc_category=cat, review_status="approved"
    )
    retrieved = _chunk_ids(docs)
    metrics = compute_all(retrieved, relevant, ks=[3, 5])
    expected_norm = {x.strip() for x in expected if x.strip()}
    linked_norm = {x.strip() for x in linked if x.strip()}
    link_hit = bool(expected_norm) and expected_norm.issubset(linked_norm)
    graph_in_topk = bool(set(retrieved[:5]) & set(graph_chunk_ids)) if graph_chunk_ids else False
    return {
        "id": item.get("id"),
        "question": q,
        "intent": item.get("intent"),
        "relevant_chunk_ids": sorted(relevant),
        "retrieved_chunk_ids": retrieved[:10],
        "metrics": metrics,
        "linked_entities": linked,
        "expected_entities": expected,
        "linked_entity_hit": link_hit,
        "graph_fallback_reason": fallback,
        "graph_chunk_ids": graph_chunk_ids[:20],
        "graph_chunk_in_topk5": graph_in_topk,
        "channels": [
            (d.get("metadata") or {}).get("retrieval_channel")
            for d in docs[:5]
            if (d.get("metadata") or {}).get("retrieval_channel")
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    items = payload.get("items") or payload
    if not isinstance(items, list):
        raise SystemExit("dataset must contain items[]")

    rag = RagChain()
    results = [_run_one(rag, item) for item in items]

    all_retrieved = [r["retrieved_chunk_ids"] for r in results]
    all_relevant = [set(r["relevant_chunk_ids"]) for r in results]
    overall = compute_batch(all_retrieved, all_relevant, ks=[3, 5])

    by_intent: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_intent[str(r.get("intent") or "unknown")].append(r)

    intent_metrics = {}
    for intent, rows in sorted(by_intent.items()):
        intent_metrics[intent] = compute_batch(
            [x["retrieved_chunk_ids"] for x in rows],
            [set(x["relevant_chunk_ids"]) for x in rows],
            ks=[3, 5],
        )
        intent_metrics[intent]["n"] = len(rows)

    n = len(results) or 1
    fallback_rate = sum(1 for r in results if r.get("graph_fallback_reason")) / n
    link_hit_rate = sum(1 for r in results if r.get("linked_entity_hit")) / n
    graph_chunk_hit_rate = sum(1 for r in results if r.get("graph_chunk_in_topk5")) / n

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": args.label
        or f"graph_enabled={os.environ.get('GRAPH_RETRIEVAL_ENABLED', 'ini')}",
        "env": {
            "GRAPH_RETRIEVAL_ENABLED": os.environ.get("GRAPH_RETRIEVAL_ENABLED"),
            "GRAPH_RETRIEVAL_ANCHOR_GRAPH_CHUNK_ENABLED": os.environ.get(
                "GRAPH_RETRIEVAL_ANCHOR_GRAPH_CHUNK_ENABLED"
            ),
            "GRAPH_RETRIEVAL_QUERY_REWRITE_ENABLED": os.environ.get(
                "GRAPH_RETRIEVAL_QUERY_REWRITE_ENABLED"
            ),
        },
        "dataset": str(args.dataset),
        "n": len(results),
        "overall": overall,
        "by_intent": intent_metrics,
        "graph_observability": {
            "linked_entity_hit_rate": link_hit_rate,
            "graph_fallback_rate": fallback_rate,
            "graph_chunk_in_topk5_rate": graph_chunk_hit_rate,
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "n": len(results),
                "overall": overall,
                "graph_observability": report["graph_observability"],
                "by_intent": {k: {"n": v["n"], "recall@3": v.get("recall@3"), "mrr": v.get("mrr")} for k, v in intent_metrics.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
