#!/usr/bin/env python3
"""Compare FR-10 v4 retrieval baseline with graph retrieval off vs on (separate subprocesses)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLD = (
    ROOT
    / "docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集/multi_chunk_qa_gold_v4.json"
)
DEFAULT_BASE_OUT = (
    ROOT
    / "docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集"
)
EVAL_SCRIPT = ROOT / "scripts/eval_multi_evidence_offline.py"
PYTHON = ROOT / "venv/Scripts/python.exe"


def _run_side(enabled: str, gold: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(**__import__("os").environ)
    env["GRAPH_RETRIEVAL_ENABLED"] = enabled
    cmd = [
        str(PYTHON),
        str(EVAL_SCRIPT),
        "--mode",
        "retrieval",
        "--production-path",
        "--gold",
        str(gold),
        "--out-dir",
        str(out_dir),
    ]
    print(f"running graph={enabled}: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(ROOT), env=env, check=True)
    report_path = out_dir / "fr10_baseline_report.json"
    return json.loads(report_path.read_text(encoding="utf-8"))


def _compare(off: dict, on: dict) -> dict:
    off_summary = off.get("summary") or {}
    on_summary = on.get("summary") or {}
    off_cats = off_summary.get("by_category") or {}
    on_cats = on_summary.get("by_category") or {}

    category_deltas = {}
    block_reasons = []
    for name, off_row in off_cats.items():
        on_row = on_cats.get(name) or {}
        delta = (on_row.get("pass_rate") or 0) - (off_row.get("pass_rate") or 0)
        category_deltas[name] = {
            "off_pass_rate": off_row.get("pass_rate"),
            "on_pass_rate": on_row.get("pass_rate"),
            "delta": delta,
        }
        if delta < 0:
            block_reasons.append(f"category {name} regressed: {delta:.2%}")

    overall_delta = (on_summary.get("pass_rate") or 0) - (off_summary.get("pass_rate") or 0)
    admission = "BLOCK_ADMISSION" if block_reasons else "PREVIEW_ONLY_NO_PRODUCTION_ON"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "off_summary": {
            "pass_rate": off_summary.get("pass_rate"),
            "mean_completeness": off_summary.get("mean_completeness"),
            "mean_evidence_recall": off_summary.get("mean_evidence_recall"),
        },
        "on_summary": {
            "pass_rate": on_summary.get("pass_rate"),
            "mean_completeness": on_summary.get("mean_completeness"),
            "mean_evidence_recall": on_summary.get("mean_evidence_recall"),
        },
        "overall_pass_rate_delta": overall_delta,
        "category_deltas": category_deltas,
        "block_reasons": block_reasons,
        "admission_decision": admission,
        "note": (
            "Even if on >= off, graph must not affect /query until Round 3 execute "
            "and formal Round 4 GraphRAG acceptance complete."
        ),
    }


def _to_markdown(payload: dict) -> str:
    lines = [
        "# Graph Retrieval A/B Preview",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- admission_decision: **{payload['admission_decision']}**",
        f"- overall pass_rate delta (on-off): **{payload['overall_pass_rate_delta']:.2%}**",
        "",
        "## Off baseline",
        "",
        f"- pass_rate: {payload['off_summary']['pass_rate']:.2%}",
        f"- mean_completeness: {payload['off_summary']['mean_completeness']:.2%}",
        f"- mean_evidence_recall: {payload['off_summary']['mean_evidence_recall']:.2%}",
        "",
        "## On preview",
        "",
        f"- pass_rate: {payload['on_summary']['pass_rate']:.2%}",
        f"- mean_completeness: {payload['on_summary']['mean_completeness']:.2%}",
        f"- mean_evidence_recall: {payload['on_summary']['mean_evidence_recall']:.2%}",
        "",
        "## Category deltas",
        "",
        "| category | off | on | delta |",
        "|---|---:|---:|---:|",
    ]
    for name, row in sorted(payload["category_deltas"].items()):
        lines.append(
            f"| {name} | {row['off_pass_rate']:.2%} | {row['on_pass_rate']:.2%} | {row['delta']:.2%} |"
        )
    if payload["block_reasons"]:
        lines.extend(["", "## Block reasons", ""])
        for reason in payload["block_reasons"]:
            lines.append(f"- {reason}")
    lines.extend(["", "## Note", "", payload["note"], ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--base-out-dir", type=Path, default=DEFAULT_BASE_OUT)
    parser.add_argument("--skip-run", action="store_true", help="Only compare existing reports")
    args = parser.parse_args()

    off_dir = args.base_out_dir / "fr10_live_2537_v4_graph_ab_off"
    on_dir = args.base_out_dir / "fr10_live_2537_v4_graph_ab_on"
    compare_dir = args.base_out_dir / "fr10_live_2537_v4_graph_ab_compare"

    if not args.skip_run:
        off = _run_side("false", args.gold, off_dir)
        on = _run_side("true", args.gold, on_dir)
    else:
        off = json.loads((off_dir / "fr10_baseline_report.json").read_text(encoding="utf-8"))
        on = json.loads((on_dir / "fr10_baseline_report.json").read_text(encoding="utf-8"))

    payload = _compare(off, on)
    compare_dir.mkdir(parents=True, exist_ok=True)
    json_path = compare_dir / "graph_ab_compare.json"
    md_path = compare_dir / "graph_ab_compare.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(_to_markdown(payload) + "\n", encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print(f"admission_decision={payload['admission_decision']}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"eval subprocess failed: {exc}", file=sys.stderr)
        sys.exit(exc.returncode or 1)
