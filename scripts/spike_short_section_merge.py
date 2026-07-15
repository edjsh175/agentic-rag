#!/usr/bin/env python3
"""Offline short-section merge spike against watch_directory manuals (no Chroma writes)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.services.section_chunk_merge import (
    documents_to_merge_units,
    fact_window_coverage,
    length_stats,
    MergeUnit,
)
from rag_knowledge.services.unstructured_loader import UnstructuredChapterLoader

logger = logging.getLogger(__name__)

OUT_DIR = ROOT / "docs/3_待办清单/chunk-foundation-parallel-prep"
GOLD = OUT_DIR / "multi_chunk_qa_gold_v2.json"

DEFAULT_DOCS = [
    "StampServer用户手册_Rocky9 .docx",
    "StampTools用户手册.docx",
    "StampWebRTC用户手册.docx",
]


def _find_docx(watch_dir: Path, name: str) -> Path | None:
    direct = watch_dir / "word" / name
    if direct.exists():
        return direct
    matches = list(watch_dir.rglob(name))
    return matches[0] if matches else None


def _as_unmerged(docs: list[Document]) -> list:
    units = []
    for i, doc in enumerate(docs):
        meta = doc.metadata or {}
        doc_key = str(
            meta.get("source_snapshot_hash")
            or meta.get("source_document_id")
            or meta.get("source")
            or ""
        )
        units.append(
            MergeUnit(
                source=str(meta.get("source") or ""),
                section_path=str(meta.get("section_path") or ""),
                content_markdown=(doc.page_content or "").strip(),
                content_type=str(meta.get("content_type") or "text"),
                document_key=doc_key,
                source_document_id=str(meta.get("source_document_id") or doc_key[:32]),
                source_snapshot_hash=str(meta.get("source_snapshot_hash") or ""),
                merged_from_orders=[int(meta.get("element_order") or i)],
                source_element_ids=list(meta.get("source_element_ids") or []),
                source_raw_block_ids=list(meta.get("source_raw_block_ids") or []),
                chunk_index_global=i,
            )
        )
    return units


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    cfg = Config()
    watch_dir = args.watch_dir or Path(cfg.watch_dir)
    loader = UnstructuredChapterLoader()
    per_doc = []
    all_before = []
    all_after = []

    for name in DEFAULT_DOCS:
        path = _find_docx(watch_dir, name)
        if path is None:
            logger.warning("missing doc: %s under %s", name, watch_dir)
            continue
        docs = loader.load(str(path))
        before_units = _as_unmerged(docs)
        after_units = documents_to_merge_units(docs)
        before_stats = length_stats(before_units)
        after_stats = length_stats(after_units)
        per_doc.append(
            {
                "source": name,
                "path": str(path),
                "before": before_stats,
                "after": after_stats,
            }
        )
        all_before.extend(before_units)
        all_after.extend(after_units)
        logger.info(
            "%s before_count=%s after_count=%s lt200 %.2f -> %.2f",
            name,
            before_stats["count"],
            after_stats["count"],
            before_stats["lt_200_rate"],
            after_stats["lt_200_rate"],
        )

    gold_checks = []
    if GOLD.exists():
        gold = json.loads(GOLD.read_text(encoding="utf-8"))
        focus_ids = {"mq-002", "mq-003", "mq-005"}
        for item in gold:
            if item.get("id") not in focus_ids:
                continue
            coverage = fact_window_coverage(all_after, list(item.get("required_facts") or []))
            gold_checks.append({"id": item["id"], "question": item["question"], **coverage})

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "watch_directory": str(watch_dir),
        "documents": per_doc,
        "overall_before": length_stats(all_before),
        "overall_after": length_stats(all_after),
        "gold_fact_window_checks": gold_checks,
        "notes": (
            "Offline spike only. Soft-max 1200, target min 300, no hard cross L1/L2 boundary. "
            "Does not write Chroma."
        ),
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "round0c_merge_spike_report.json"
    md_path = args.out_dir / "round0c_merge_spike_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_lines = [
        "# Round 0C Merge Spike Report",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- overall before count/median/lt200: "
        f"{report['overall_before']['count']} / {report['overall_before']['median']} / "
        f"{report['overall_before']['lt_200_rate']:.2%}",
        f"- overall after count/median/lt200: "
        f"{report['overall_after']['count']} / {report['overall_after']['median']} / "
        f"{report['overall_after']['lt_200_rate']:.2%}",
        "",
        "## Per document",
        "",
    ]
    for row in per_doc:
        md_lines.append(
            f"- **{row['source']}**: lt200 {row['before']['lt_200_rate']:.2%} → "
            f"{row['after']['lt_200_rate']:.2%} "
            f"(count {row['before']['count']} → {row['after']['count']})"
        )
    md_lines.extend(["", "## Gold fact-window checks", ""])
    for row in gold_checks:
        md_lines.append(
            f"- `{row['id']}` best_hits={row['best_unit_fact_hits']}/{row['required_count']} "
            f"all_in_one={row['all_facts_in_one_unit']}"
        )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps({"overall_before": report["overall_before"], "overall_after": report["overall_after"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
