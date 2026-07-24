#!/usr/bin/env python3
"""Classify v5 coverage baseline failures for rewrite/filter tuning."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集"
DEFAULT_REPORT = BASE / "fr10_live_2537_v5_coverage_baseline" / "fr10_baseline_report.json"
DEFAULT_OUT_JSON = BASE / "fr10_live_2537_v5_coverage_baseline" / "v5_failure_taxonomy.json"
DEFAULT_OUT_MD = BASE / "fr10_live_2537_v5_coverage_baseline" / "v5_failure_taxonomy.md"


def _classify(row: dict[str, Any]) -> str:
    if row.get("passed"):
        return "pass"
    diagnostics = row.get("anchor_stage_diagnostics") or row.get("diagnostics") or []
    drop_reasons = {str(d.get("drop_reason") or "") for d in diagnostics if isinstance(d, dict)}
    if "not_retrieved" in drop_reasons:
        return "not_retrieved"
    if "rerank_drop" in drop_reasons:
        return "rerank_drop"
    if "quality_filter" in drop_reasons:
        return "quality_filter"
    if "final_top_k_trim" in drop_reasons:
        return "final_top_k_trim"
    completeness = float(row.get("completeness") or 0)
    evidence = float(row.get("evidence_recall") or row.get("mean_evidence_recall") or 0)
    if evidence <= 0 and completeness <= 0:
        return "no_evidence"
    if evidence < 1.0:
        return "partial_evidence"
    return "other_fail"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    rows = report.get("results") or []
    classified = []
    for row in rows:
        bucket = _classify(row)
        classified.append(
            {
                "id": row.get("id"),
                "category": row.get("category"),
                "passed": bool(row.get("passed")),
                "bucket": bucket,
                "completeness": row.get("completeness"),
                "evidence_recall": row.get("evidence_recall"),
                "question": row.get("question"),
            }
        )
    counts = Counter(item["bucket"] for item in classified)
    payload = {
        "report": str(args.report),
        "summary": report.get("summary"),
        "failure_counts": dict(sorted(counts.items())),
        "items": classified,
        "tuning_hints": {
            "not_retrieved": "改写 query / soft_match 别名 / Hybrid 召回",
            "rerank_drop": "reranker 开关或候选宽度",
            "quality_filter": "retrieval_quality 阈值",
            "final_top_k_trim": "top_k / candidate_k",
            "partial_evidence": "多章节题或 required_facts 过宽",
            "no_evidence": "锚点与检索路径严重偏离",
        },
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# v5 覆盖集失败分类",
        "",
        f"- report: `{args.report}`",
        f"- total: {payload.get('summary', {}).get('total')}",
        f"- pass_rate: {payload.get('summary', {}).get('pass_rate')}",
        "",
        "## 分桶计数",
        "",
        "| bucket | count |",
        "|---|---:|",
    ]
    for name, count in sorted(counts.items()):
        lines.append(f"| {name} | {count} |")
    lines.extend(["", "## 失败题", "", "| id | category | bucket | evidence_recall | question |", "|---|---|---|---:|---|"])
    for item in classified:
        if item["bucket"] == "pass":
            continue
        q = str(item.get("question") or "").replace("|", "/")
        lines.append(
            f"| {item['id']} | {item['category']} | {item['bucket']} | {item.get('evidence_recall')} | {q} |"
        )
    args.out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"failure_counts": payload["failure_counts"], "summary": payload["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
