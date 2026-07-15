#!/usr/bin/env python3
"""Offline FR-10 multi-evidence scorer (read-only; does not write Chroma)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_knowledge.evaluation.multi_evidence_metrics import score_answer, summarize_scores

logger = logging.getLogger(__name__)

DEFAULT_GOLD = ROOT / "docs/3_待办清单/chunk-foundation-parallel-prep/multi_chunk_qa_gold_v2.json"
DEFAULT_OUT_DIR = ROOT / "docs/3_待办清单/chunk-foundation-parallel-prep"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_reports(out_dir: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = str(payload.get("generated_at") or "baseline").replace(":", "").replace("+", "")
    json_path = out_dir / "fr10_baseline_report.json"
    md_path = out_dir / "fr10_baseline_report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(_to_markdown(payload), encoding="utf-8")
    logger.info("wrote %s and %s (%s)", json_path, md_path, stamp)
    return json_path, md_path


def _to_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# FR-10 Offline Baseline Report",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        f"- mode: `{payload.get('mode')}`",
        f"- gold: `{payload.get('gold_path')}`",
        f"- total: **{summary.get('total', 0)}**",
        f"- pass_rate: **{summary.get('pass_rate', 0):.2%}**",
        f"- mean_completeness: **{summary.get('mean_completeness', 0):.2%}**",
        f"- mean_evidence_recall: **{summary.get('mean_evidence_recall', 0):.2%}**",
        f"- forbidden_rate: **{summary.get('forbidden_rate', 0):.2%}**",
        "",
        "## Notes",
        "",
        str(payload.get("notes") or ""),
        "",
        "## By category",
        "",
        "| category | total | passed | pass_rate | mean_completeness |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, row in sorted((summary.get("by_category") or {}).items()):
        lines.append(
            f"| {name} | {row['total']} | {row['passed']} | {row['pass_rate']:.2%} | {row['mean_completeness']:.2%} |"
        )
    lines.extend(
        [
            "",
            "## By answerability",
            "",
            "| answerability | total | passed | pass_rate | mean_completeness |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for name, row in sorted((summary.get("by_answerability") or {}).items()):
        lines.append(
            f"| {name} | {row['total']} | {row['passed']} | {row['pass_rate']:.2%} | {row['mean_completeness']:.2%} |"
        )
    lines.extend(["", "## Fail sample (first 20)", ""])
    fails = [r for r in payload.get("results") or [] if not r.get("passed")][:20]
    if not fails:
        lines.append("_none_")
    else:
        for row in fails:
            lines.append(
                f"- `{row.get('id')}` [{row.get('category')}/{row.get('answerability')}] "
                f"completeness={row.get('completeness'):.2f} "
                f"checks={row.get('checks')}"
            )
    lines.append("")
    return "\n".join(lines)


def _sources_from_retrieve_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for doc in docs or []:
        meta = doc.get("metadata") or {}
        out.append(
            {
                "source": meta.get("source") or "",
                "section_path": meta.get("section_path") or "",
                "chunk_id": meta.get("chunk_id") or "",
                "content": doc.get("content") or "",
            }
        )
    return out


def _proxy_answer_from_sources(sources: list[dict[str, Any]]) -> str:
    parts = []
    for src in sources:
        body = str(src.get("content") or "").strip()
        if body:
            parts.append(body)
    return "\n".join(parts)


def run_rules_only(items: list[dict[str, Any]], answers: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        answer = answers.get(str(item.get("id")), "")
        rows.append(score_answer(item, answer, sources=None))
    return rows


def run_retrieval(items: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    from rag_knowledge.services.rag import RagChain

    rag = RagChain()
    rows: list[dict[str, Any]] = []
    selected = items if limit is None else items[:limit]
    for i, item in enumerate(selected):
        question = str(item.get("question") or "")
        try:
            docs, _context = rag._retrieve(question, review_status="approved")
            sources = _sources_from_retrieve_docs(docs)
            proxy = _proxy_answer_from_sources(sources)
            # Retrieval proxy does not emit refusal / conflict governance phrases.
            scored = score_answer(item, proxy, sources=sources)
            scored["mode_detail"] = "retrieval_proxy_answer"
            scored["retrieved_count"] = len(sources)
        except Exception as exc:  # pragma: no cover - live env dependent
            logger.warning("retrieve failed for %s: %s", item.get("id"), exc)
            scored = score_answer(item, "", sources=[])
            scored["error"] = str(exc)
        rows.append(scored)
        if (i + 1) % 10 == 0:
            logger.info("progress %d/%d", i + 1, len(selected))
    return rows


def run_qa(items: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    from rag_knowledge.services.rag import RagChain

    rag = RagChain()
    rows: list[dict[str, Any]] = []
    selected = items if limit is None else items[:limit]
    for i, item in enumerate(selected):
        question = str(item.get("question") or "")
        try:
            result = rag.query(question)
            answer = str((result or {}).get("answer") or "")
            source_docs = (result or {}).get("source_documents") or []
            sources = []
            for doc in source_docs:
                meta = getattr(doc, "metadata", None) or {}
                if isinstance(doc, dict):
                    meta = doc.get("metadata") or {}
                    content = doc.get("page_content") or doc.get("content") or ""
                else:
                    content = getattr(doc, "page_content", "") or ""
                sources.append(
                    {
                        "source": meta.get("source") or "",
                        "section_path": meta.get("section_path") or "",
                        "chunk_id": meta.get("chunk_id") or "",
                        "content": content,
                    }
                )
            scored = score_answer(item, answer, sources=sources)
            scored["mode_detail"] = "full_qa"
            scored["answer_preview"] = answer[:240]
        except Exception as exc:  # pragma: no cover
            logger.warning("qa failed for %s: %s", item.get("id"), exc)
            scored = score_answer(item, "", sources=[])
            scored["error"] = str(exc)
        rows.append(scored)
        if (i + 1) % 5 == 0:
            logger.info("progress %d/%d", i + 1, len(selected))
    return rows


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Offline FR-10 multi-evidence scorer")
    p.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument(
        "--mode",
        choices=("rules", "retrieval", "qa"),
        default="retrieval",
        help="rules=score provided answers; retrieval=chroma read-only proxy; qa=full RagChain.query",
    )
    p.add_argument("--answers-json", type=Path, default=None, help="id->answer map for --mode rules")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--empty-baseline", action="store_true", help="rules mode with empty answers (no Chroma)")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    items = _load_json(args.gold)
    if not isinstance(items, list):
        raise SystemExit("gold must be a JSON array")

    notes = (
        "Baseline on current production chunks. Conflict/completeness may be low until "
        "Round 0C–0F and FR-08 prompt wiring. Retrieval mode scores concatenated retrieved "
        "content as a proxy answer (not LLM generation)."
    )

    if args.mode == "rules" or args.empty_baseline:
        answers: dict[str, str] = {}
        if args.answers_json and args.answers_json.exists():
            answers = {str(k): str(v) for k, v in _load_json(args.answers_json).items()}
        selected = items if args.limit is None else items[: args.limit]
        rows = run_rules_only(selected, answers)
        mode = "rules"
        notes = "Rules-only scoring without live retrieval." + (
            " Empty answers baseline." if args.empty_baseline or not answers else ""
        )
    elif args.mode == "qa":
        rows = run_qa(items, args.limit)
        mode = "qa"
    else:
        rows = run_retrieval(items, args.limit)
        mode = "retrieval"

    summary = summarize_scores(rows)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "gold_path": str(args.gold),
        "notes": notes,
        "summary": summary,
        "results": rows,
    }
    _write_reports(args.out_dir, payload)
    print(json.dumps({"mode": mode, "summary": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
