#!/usr/bin/env python3
"""Compare GraphRAG retrieval A/B reports and write Round-4 markdown."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
EVAL = ROOT / "scripts" / "run_graph_rag_eval.py"
DEFAULT_DATASET = ROOT / "data" / "eval_graph_rag_dataset.json"


def _run_side(enabled: str, allowlist: str, output: Path, dataset: Path) -> dict:
    env = dict(os.environ)
    env["GRAPH_RETRIEVAL_ENABLED"] = enabled
    # Keep off baseline clean: disable allowlist injection when graph is off.
    env["GRAPH_RETRIEVAL_ANCHOR_GRAPH_CHUNK_ENABLED"] = allowlist
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        str(PYTHON),
        str(EVAL),
        "--dataset",
        str(dataset),
        "--output",
        str(output),
        "--label",
        f"graph={enabled};allowlist={allowlist}",
    ]
    print("running:", " ".join(cmd), f"ENABLED={enabled} ALLOWLIST={allowlist}")
    subprocess.run(cmd, cwd=str(ROOT), env=env, check=True)
    return json.loads(output.read_text(encoding="utf-8"))


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return b - a


def _pct(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x:.2%}"


def _pp(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x * 100:+.2f}pp"


def compare(off: dict, on: dict) -> dict:
    intents = sorted(set(off.get("by_intent", {})) | set(on.get("by_intent", {})))
    intent_rows = {}
    improved = []
    for intent in intents:
        o = (off.get("by_intent") or {}).get(intent) or {}
        n = (on.get("by_intent") or {}).get(intent) or {}
        row = {
            "n": n.get("n") or o.get("n") or 0,
            "baseline_recall@3": o.get("recall@3"),
            "graph_recall@3": n.get("recall@3"),
            "delta_recall@3": _delta(o.get("recall@3"), n.get("recall@3")),
            "baseline_mrr": o.get("mrr"),
            "graph_mrr": n.get("mrr"),
            "delta_mrr": _delta(o.get("mrr"), n.get("mrr")),
        }
        # Pass criterion: +5pp on Recall@3 or MRR
        lift = (row["delta_recall@3"] or 0) >= 0.05 or (row["delta_mrr"] or 0) >= 0.05
        row["improved"] = bool(lift)
        if lift:
            improved.append(intent)
        intent_rows[intent] = row

    obs = on.get("graph_observability") or {}
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall": {
            "baseline_recall@3": (off.get("overall") or {}).get("recall@3"),
            "graph_recall@3": (on.get("overall") or {}).get("recall@3"),
            "delta_recall@3": _delta(
                (off.get("overall") or {}).get("recall@3"),
                (on.get("overall") or {}).get("recall@3"),
            ),
            "baseline_mrr": (off.get("overall") or {}).get("mrr"),
            "graph_mrr": (on.get("overall") or {}).get("mrr"),
            "delta_mrr": _delta(
                (off.get("overall") or {}).get("mrr"),
                (on.get("overall") or {}).get("mrr"),
            ),
            "baseline_hit@3": (off.get("overall") or {}).get("hit@3"),
            "graph_hit@3": (on.get("overall") or {}).get("hit@3"),
        },
        "by_intent": intent_rows,
        "improved_intents": improved,
        "graph_observability": obs,
        "pass_criteria": {
            "at_least_2_intents_improved_5pp": len(improved) >= 2,
            "graph_fallback_rate_lt_40": (obs.get("graph_fallback_rate") or 1) < 0.40,
        },
    }
    payload["round4_retrieval_pass"] = bool(
        payload["pass_criteria"]["at_least_2_intents_improved_5pp"]
        and payload["pass_criteria"]["graph_fallback_rate_lt_40"]
    )
    return payload


def to_markdown(cmp: dict, off_path: Path, on_path: Path) -> str:
    lines = [
        "# GraphRAG A/B 验收报告（第 4 轮）",
        "",
        f"- **记录日期**：{datetime.now().strftime('%Y-%m-%d')}",
        f"- **Baseline**：`{off_path.as_posix()}`",
        f"- **Graph-on**：`{on_path.as_posix()}`",
        f"- **生成时间**：`{cmp['generated_at']}`",
        "",
        "## 1. 结论摘要",
        "",
        "| 项 | 结论 |",
        "|------|------|",
        f"| 检索 A/B 是否满足「≥2 类意图 +5pp」且 fallback<40% | **{'PASS' if cmp['round4_retrieval_pass'] else 'FAIL'}** |",
        f"| 提升意图 | {', '.join(cmp['improved_intents']) or '无'} |",
        f"| graph_fallback_rate | {_pct((cmp.get('graph_observability') or {}).get('graph_fallback_rate'))} |",
        f"| linked_entity_hit_rate | {_pct((cmp.get('graph_observability') or {}).get('linked_entity_hit_rate'))} |",
        f"| graph chunk 进入 top5 比例 | {_pct((cmp.get('graph_observability') or {}).get('graph_chunk_in_topk5_rate'))} |",
        "",
        "## 2. 总体指标",
        "",
        "| 指标 | baseline | graph-on | Δ |",
        "|------|---:|---:|---:|",
        f"| Recall@3 | {_pct(cmp['overall']['baseline_recall@3'])} | {_pct(cmp['overall']['graph_recall@3'])} | {_pp(cmp['overall']['delta_recall@3'])} |",
        f"| MRR | {cmp['overall']['baseline_mrr']:.4f} | {cmp['overall']['graph_mrr']:.4f} | {_pp(cmp['overall']['delta_mrr'])} |",
        f"| Hit@3 | {_pct(cmp['overall']['baseline_hit@3'])} | {_pct(cmp['overall']['graph_hit@3'])} | |",
        "",
        "## 3. 分意图",
        "",
        "| intent | n | baseline R@3 | graph R@3 | Δ | baseline MRR | graph MRR | Δ | 提升? |",
        "|------|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for intent, row in sorted((cmp.get("by_intent") or {}).items()):
        lines.append(
            f"| {intent} | {row['n']} | {_pct(row['baseline_recall@3'])} | {_pct(row['graph_recall@3'])} | "
            f"{_pp(row['delta_recall@3'])} | {row['baseline_mrr']:.4f} | {row['graph_mrr']:.4f} | "
            f"{_pp(row['delta_mrr'])} | {'Y' if row['improved'] else 'N'} |"
        )
    lines.extend(
        [
            "",
            "## 4. 判定说明",
            "",
            "- A 组：`GRAPH_RETRIEVAL_ENABLED=false` 且 `ANCHOR_GRAPH_CHUNK_ENABLED=false`（避免 allowlist 污染对照）。",
            "- B 组：`GRAPH_RETRIEVAL_ENABLED=true`（读正式库扩召回 + fuse）。",
            "- 指标口径：`retrieve_for_evaluation` 生产检索路径；金标为实体 `entity_chunk_links` 回填的 `relevant_chunk_ids`。",
            "- 本报告为**检索实效**验收；生产是否默认开启仍须结合证据债 / allowlist / 人工抽检。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--baseline", type=Path, default=ROOT / "data" / "eval_graph_rag_baseline.json")
    parser.add_argument("--treatment", type=Path, default=ROOT / "data" / "eval_graph_rag_with_graph.json")
    parser.add_argument(
        "--output-json",
        type=Path,
        default=ROOT / "data" / "eval_graph_rag_ab_compare.json",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=ROOT
        / "docs"
        / "3_待办清单"
        / "知识图谱语义抽取"
        / "已完成-第4轮-GraphRAG实效验收"
        / "GraphRAG_A_B验收报告.md",
    )
    parser.add_argument("--skip-run", action="store_true")
    args = parser.parse_args()

    if not args.skip_run:
        off = _run_side("false", "false", args.baseline, args.dataset)
        on = _run_side("true", "false", args.treatment, args.dataset)
    else:
        off = json.loads(args.baseline.read_text(encoding="utf-8"))
        on = json.loads(args.treatment.read_text(encoding="utf-8"))

    cmp = compare(off, on)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(cmp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(to_markdown(cmp, args.baseline, args.treatment), encoding="utf-8")
    print(json.dumps({"output_json": str(args.output_json), "output_md": str(args.output_md), "pass": cmp["round4_retrieval_pass"], "improved": cmp["improved_intents"]}, ensure_ascii=False, indent=2))
    return 0 if cmp["round4_retrieval_pass"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"eval failed: {exc}", file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
