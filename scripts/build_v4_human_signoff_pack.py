#!/usr/bin/env python3
"""Build human signoff checklist and pending ledger for FR-10 v4 retrieval gold."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集"
GOLD = BASE / "multi_chunk_qa_gold_v4.json"
LEDGER = BASE / "multi_chunk_qa_gold_v4.review_ledger.json"
FR10 = BASE / "fr10_live_2537_v4_production_path_post_conflict_planner/fr10_baseline_report.json"
CHECKLIST = BASE / "multi_chunk_qa_gold_v4.human_signoff_checklist.md"
SIGNOFF_LEDGER = BASE / "multi_chunk_qa_gold_v4.human_signoff_ledger.json"


def main() -> None:
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    codex = {item["id"]: item for item in json.loads(LEDGER.read_text(encoding="utf-8"))["items"]}
    fr10 = {row["id"]: row for row in json.loads(FR10.read_text(encoding="utf-8"))["results"]}

    items = []
    lines = [
        "# FR-10 v4 检索黄金集人工签核清单",
        "",
        f"- **状态**：`human_signoff_pending`",
        f"- **生成日期**：{date.today().isoformat()}",
        f"- **题量**：{len(gold)}",
        f"- **技术冻结**：`multi_chunk_qa_gold_v4.json` + Codex evidence review",
        f"- **基线报告**：`fr10_live_2537_v4_production_path_post_conflict_planner/fr10_baseline_report.json`",
        "",
        "签核人请在 `multi_chunk_qa_gold_v4.human_signoff_ledger.json` 填写 `decision` / `signer` / `signed_at`。",
        "",
        "| ID | 分类 | Codex 决定 | FR-10 通过 | 题干摘要 |",
        "|---|---|---|---|---|",
    ]

    for item in gold:
        qid = item["id"]
        codex_item = codex.get(qid, {})
        fr10_row = fr10.get(qid, {})
        passed = fr10_row.get("passed", False)
        question = item.get("question", "").replace("|", "\\|")
        if len(question) > 48:
            question = question[:45] + "..."
        lines.append(
            f"| {qid} | {item.get('category', '')} | {codex_item.get('decision', 'n/a')} | "
            f"{'是' if passed else '否'} | {question} |"
        )
        items.append(
            {
                "id": qid,
                "category": item.get("category"),
                "codex_decision": codex_item.get("decision"),
                "fr10_baseline_passed": passed,
                "decision": "pending",
                "signer": "",
                "signed_at": "",
                "notes": "",
            }
        )

    CHECKLIST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    SIGNOFF_LEDGER.write_text(
        json.dumps(
            {
                "signoff_version": "fr10-v4-human-signoff-v1",
                "status": "human_signoff_pending",
                "gold": "multi_chunk_qa_gold_v4.json",
                "technical_freeze": "multi_chunk_qa_gold_v4.manifest.json",
                "generated_at": date.today().isoformat(),
                "counts": {"pending": len(items), "confirmed": 0, "revise": 0, "withdraw": 0},
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {CHECKLIST}")
    print(f"wrote {SIGNOFF_LEDGER}")


if __name__ == "__main__":
    main()
