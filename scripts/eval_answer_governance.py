"""Run the answer-governance candidate set through the admin QA debug endpoint."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen


CONFLICT_SIGNAL_RE = re.compile(r"冲突|不一致|核对原文|不得静默|多个(?:值|取值|来源)")
REFUSAL_SIGNAL_RE = re.compile(r"未查询到|无法提供|证据不足|无法给出|无法稳定恢复|无有效业务内容")


def _citations(answer: str) -> int:
    return len(set(re.findall(r"\[(\d+)\]", answer or "")))


def _explicit_values(item: dict) -> list[str]:
    return [fact for fact in item.get("required_facts", []) if re.fullmatch(r"\d{2,5}", fact)]


def evaluate_item(item: dict, answer: str) -> dict:
    """Score only deterministic governance behavior; retain literal misses for review."""
    answer = (answer or "").strip()
    forbidden_hits = [fact for fact in item.get("forbidden_claims", []) if fact and fact in answer]
    literal_missing = [fact for fact in item.get("required_facts", []) if fact and fact not in answer]
    explicit_missing = [value for value in _explicit_values(item) if value not in answer]
    answerability = item.get("answerability")
    if answerability == "conflict":
        behavior_pass = bool(CONFLICT_SIGNAL_RE.search(answer)) and not explicit_missing
    elif answerability == "none":
        behavior_pass = bool(REFUSAL_SIGNAL_RE.search(answer))
    else:
        raise ValueError(f"unsupported answerability: {answerability!r}")
    return {
        "id": item["id"],
        "category": item.get("category"),
        "answerability": answerability,
        "behavior_pass": behavior_pass and not forbidden_hits,
        "forbidden_hits": forbidden_hits,
        "explicit_value_missing": explicit_missing,
        "literal_required_fact_missing": literal_missing,
        "citation_count": _citations(answer),
        "answer": answer,
    }


def ask(endpoint: str, question: str, timeout: int) -> dict:
    payload = json.dumps({"question": question, "thinking": False}).encode("utf-8")
    request = Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # nosec B310: local endpoint is explicit CLI input
        return json.loads(response.read().decode("utf-8"))


def build_report(items: list[dict], results: list[dict]) -> dict:
    passed = sum(result["behavior_pass"] for result in results)
    return {
        "scope": "answer_governance",
        "mode": "production_admin_qa_debug",
        "total": len(items),
        "completed": len(results),
        "remaining": len(items) - len(results),
        "behavior_passed": passed,
        "behavior_pass_rate": passed / len(results) if results else 0.0,
        "results": results,
        "interpretation": (
            "behavior_pass evaluates only deterministic refusal/conflict behavior, "
            "explicit numeric values, and forbidden claims. "
            "literal_required_fact_missing is a review aid, not a semantic answer-quality gate."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:10605/admin/qa-debug")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--rerun", action="append", default=[])
    args = parser.parse_args()

    items = json.loads(args.gold.read_text(encoding="utf-8"))
    results = []
    if args.resume and args.out.exists():
        results = json.loads(args.out.read_text(encoding="utf-8")).get("results", [])
    if args.rerun:
        rerun_ids = set(args.rerun)
        results = [result for result in results if result["id"] not in rerun_ids]
    completed_ids = {result["id"] for result in results}
    pending = [item for item in items if item["id"] not in completed_ids]
    if args.limit is not None:
        pending = pending[:args.limit]
    for item in pending:
        response = ask(args.endpoint, item["question"], args.timeout)
        result = evaluate_item(item, response.get("answer", ""))
        result["evidence_chain"] = response.get("evidence_chain", {})
        results.append(result)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(build_report(items, results), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"{item['id']}: {'PASS' if result['behavior_pass'] else 'FAIL'}", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(build_report(items, results), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
