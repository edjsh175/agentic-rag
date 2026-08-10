# -*- coding: utf-8 -*-
"""Run StampTools smoke quiz against live /query and score by focus keywords."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUIZ = ROOT / "data" / "stamptools_smoke_quiz_v1.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _focus_hits(text: str, focus: list[str]) -> list[str]:
    blob = text or ""
    return [term for term in focus if term and term in blob]


def _score_item(kind: str, focus: list[str], answer: str, sources: list[dict]) -> dict[str, Any]:
    source_blob = "\n".join(
        str(s.get("content") or s.get("preview") or "")
        + "\n"
        + str(s.get("file_name") or s.get("source") or "")
        + "\n"
        + str(s.get("section_title") or "")
        for s in sources
    )
    combined = f"{answer}\n{source_blob}"
    hits = _focus_hits(combined, focus)
    miss = [t for t in focus if t not in hits]
    no_hit = "当前知识库中未查询到相关内容" in (answer or "")
    # Structure: must hit primary tool-ish token when present, and >=40% focus (min 1).
    need = max(1, int(round(len(focus) * 0.4)))
    if kind == "structure":
        primary = focus[0] if focus else ""
        primary_ok = (not primary) or (primary in hits)
        passed = (not no_hit) and primary_ok and len(hits) >= need
    else:
        passed = (not no_hit) and len(hits) >= need
    return {
        "passed": passed,
        "no_kb_hit": no_hit,
        "focus_hits": hits,
        "focus_miss": miss,
        "focus_hit_count": len(hits),
        "focus_need": need,
    }


def _source_summaries(sources: list[dict], limit: int = 4) -> list[dict[str, Any]]:
    out = []
    for s in sources[:limit]:
        content = str(s.get("content") or "")
        out.append(
            {
                "file_name": s.get("file_name") or s.get("source"),
                "section_title": s.get("section_title"),
                "chunk_id": s.get("chunk_id") or s.get("id"),
                "preview": content[:180],
            }
        )
    return out


def run_quiz(
    *,
    base_url: str,
    quiz_path: Path,
    out_path: Path,
    timeout: float,
    sleep_s: float,
) -> dict[str, Any]:
    quiz = json.loads(quiz_path.read_text(encoding="utf-8"))
    questions = list(quiz.get("questions") or [])
    results: list[dict[str, Any]] = []
    client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    health = client.get("/health")
    health.raise_for_status()

    for idx, item in enumerate(questions, start=1):
        qid = item["id"]
        question = item["question"]
        kind = item["kind"]
        focus = list(item.get("expect_focus") or [])
        print(f"[{idx}/{len(questions)}] {qid} {kind}: {question}", flush=True)
        t0 = time.perf_counter()
        err = None
        payload = {
            "question": question,
            "doc_category": "StampTools",
            "allow_general_knowledge": False,
            "pipeline_events": False,
            "web_search": False,
        }
        try:
            resp = client.post("/query", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            data = {"answer": "", "source_documents": []}
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        answer = str(data.get("answer") or "")
        sources = list(data.get("source_documents") or [])
        score = _score_item(kind, focus, answer, sources) if not err else {
            "passed": False,
            "no_kb_hit": True,
            "focus_hits": [],
            "focus_miss": focus,
            "focus_hit_count": 0,
            "focus_need": 1,
            "error": err,
        }
        row = {
            "id": qid,
            "kind": kind,
            "question": question,
            "expect_focus": focus,
            "elapsed_ms": elapsed_ms,
            "answer": answer,
            "sources": _source_summaries(sources),
            "score": score,
            "notes": item.get("notes"),
        }
        results.append(row)
        print(
            f"  -> passed={score['passed']} hits={score['focus_hits']} ({elapsed_ms}ms)",
            flush=True,
        )
        if sleep_s > 0 and idx < len(questions):
            time.sleep(sleep_s)

    structure = [r for r in results if r["kind"] == "structure"]
    detail = [r for r in results if r["kind"] == "detail"]
    s_pass = sum(1 for r in structure if r["score"]["passed"])
    d_pass = sum(1 for r in detail if r["score"]["passed"])
    bar = quiz.get("pass_bar") or {}
    # Default bar from quiz file narrative: 4/6 each
    s_need = int(bar.get("structure_min", 4))
    d_need = int(bar.get("detail_min", 4))
    phase_ok = s_pass >= s_need and d_pass >= d_need

    report = {
        "quiz": quiz.get("name"),
        "ran_at": _utc_now(),
        "base_url": base_url,
        "health": health.json() if health.headers.get("content-type", "").startswith("application/json") else health.text,
        "summary": {
            "structure_passed": s_pass,
            "structure_total": len(structure),
            "detail_passed": d_pass,
            "detail_total": len(detail),
            "structure_need": s_need,
            "detail_need": d_need,
            "phase_ok": phase_ok,
        },
        "failed_ids": [r["id"] for r in results if not r["score"]["passed"]],
        "results": results,
        "next_actions": [],
    }
    if not phase_ok:
        failed_structure = [r for r in structure if not r["score"]["passed"]]
        failed_detail = [r for r in detail if not r["score"]["passed"]]
        if failed_structure:
            report["next_actions"].append(
                "结构题失败：按失败工具定向补核心 Procedure（仍禁止 GUI ConfigItem/逐步 Step）"
            )
        if failed_detail:
            report["next_actions"].append(
                "细节题失败：优先查切块/检索命中，不先加图谱节点"
            )
    else:
        report["next_actions"].append(
            "本阶段烟测达标：冻结全量重抽；后续按失败个案（若有）小修，验收改题集而非 coverage"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)
    print(f"wrote {out_path}", flush=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="StampTools GraphRAG smoke quiz runner")
    parser.add_argument("--base-url", default="http://127.0.0.1:10605")
    parser.add_argument("--quiz", type=Path, default=DEFAULT_QUIZ)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "stamptools_smoke_quiz_v1_results.json",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--sleep", type=float, default=0.5)
    args = parser.parse_args()
    report = run_quiz(
        base_url=args.base_url,
        quiz_path=args.quiz,
        out_path=args.out,
        timeout=args.timeout,
        sleep_s=args.sleep,
    )
    return 0 if report["summary"]["phase_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
