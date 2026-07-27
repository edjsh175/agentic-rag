#!/usr/bin/env python3
"""Summarize A/B/C graph noise control eval runs."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def hit3(r: dict) -> bool:
    rel = set(r["relevant_chunk_ids"])
    top = set(r["retrieved_chunk_ids"][:3])
    return bool(rel & top)


def noise_top5(r: dict) -> list[str]:
    rel = set(r["relevant_chunk_ids"])
    g = set(r.get("graph_chunk_ids") or [])
    top5 = set(r["retrieved_chunk_ids"][:5])
    return sorted(top5 & g - rel)


def summarize(name: str, data: dict, regress_from: dict | None = None) -> tuple:
    o = data["overall"]
    obs = data["graph_observability"]
    hits = sum(hit3(r) for r in data["results"])
    regress = []
    if regress_from:
        for ra, rb in zip(regress_from["results"], data["results"]):
            if hit3(ra) and not hit3(rb):
                regress.append((ra["id"], ra["question"]))
    print(f"=== {name}")
    print(f"  R@3 {o['recall@3']:.4f} MRR {o['mrr']:.4f} Hit@3 {hits}/20")
    print(f"  fallback {obs.get('graph_fallback_rate')} link_hit {obs.get('linked_entity_hit_rate')}")
    if regress:
        print(f"  regressions {len(regress)}: {regress}")
    return o, hits, regress


def main() -> None:
    a = load("data/eval_graph_rag_ctrl_A_off.json")
    b = load("data/eval_graph_rag_ctrl_B_full.json")
    c = load("data/eval_graph_rag_ctrl_C_filter.json")

    oa, ha, reg_ab = summarize("A off", a)
    ob, hb, _ = summarize("B full no filter", b, a)
    oc, hc, reg_bc = summarize("C filter on", c, b)

    print("--- deltas ---")
    print(f"B vs A R@3 {ob['recall@3']-oa['recall@3']:+.4f} MRR {ob['mrr']-oa['mrr']:+.4f} Hit@3 {hb-ha:+d}")
    print(f"C vs A R@3 {oc['recall@3']-oa['recall@3']:+.4f} MRR {oc['mrr']-oa['mrr']:+.4f} Hit@3 {hc-ha:+d}")
    print(f"C vs B R@3 {oc['recall@3']-ob['recall@3']:+.4f} MRR {oc['mrr']-ob['mrr']:+.4f} Hit@3 {hc-hb:+d}")

    am = {r["id"]: r for r in a["results"]}
    bm = {r["id"]: r for r in b["results"]}
    cm = {r["id"]: r for r in c["results"]}
    print("--- key cases ---")
    for i in ["graphrag-005", "graphrag-008", "graphrag-001"]:
        print(i)
        for label, m in [("A", am), ("B", bm), ("C", cm)]:
            r = m[i]
            print(
                f"  {label} hit3={hit3(r)} r@3={r['metrics']['recall@3']:.3f} "
                f"mrr={r['metrics']['mrr']:.3f} fb={r.get('graph_fallback_reason')} "
                f"noise={noise_top5(r)[:2]}"
            )

    out = {
        "A": {"recall@3": oa["recall@3"], "mrr": oa["mrr"], "hit@3": ha},
        "B": {"recall@3": ob["recall@3"], "mrr": ob["mrr"], "hit@3": hb},
        "C": {"recall@3": oc["recall@3"], "mrr": oc["mrr"], "hit@3": hc},
        "regress_B_from_A": [x[0] for x in reg_ab],
        "regress_C_from_B": [x[0] for x in reg_bc],
    }
    (ROOT / "data/eval_graph_rag_ctrl_summary.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
