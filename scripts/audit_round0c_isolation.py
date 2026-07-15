#!/usr/bin/env python3
"""Round 0C isolation audit: identity uniqueness + merge diagnostics (no Chroma writes)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_knowledge.config import Config
from rag_knowledge.services.loader import FileLoader
from rag_knowledge.services.section_chunk_merge import (
    apply_technical_manual_merge,
    explain_merge_flush_reasons,
    length_stats,
    reassign_chunk_adjacency,
    section_id_for,
)
from rag_knowledge.services.unstructured_loader import UnstructuredChapterLoader

logger = logging.getLogger(__name__)

DEFAULT_DOCS = [
    "StampServer用户手册_Rocky9 .docx",
    "StampTools用户手册.docx",
    "StampWebRTC用户手册.docx",
]

OUT_DIR = (
    ROOT
    / "docs"
    / "3_待办清单"
    / "chunk基石治理"
    / "chunk-foundation-round0c-isolation"
)

# Flush reasons that are expected bookkeeping, not skip diagnostics.
_NON_SKIP_FLUSH_REASONS = frozenset(
    {"flush_eof", "flush_before_atomic", "atomic_keep"}
)


def _find_docx(watch_dir: Path, name: str) -> Path | None:
    direct = watch_dir / "word" / name
    if direct.exists():
        return direct
    matches = list(watch_dir.rglob(name))
    return matches[0] if matches else None


def _dup_report(values: list[str], label: str) -> dict:
    counts = Counter(values)
    dupes = {k: v for k, v in counts.items() if v > 1}
    return {
        "field": label,
        "total": len(values),
        "unique": len(counts),
        "duplicate_value_count": len(dupes),
        "duplicate_occurrence_sum": sum(v for v in dupes.values()),
        "sample_duplicates": sorted(dupes.items(), key=lambda x: -x[1])[:10],
    }


def _is_skip_flush_reason(reason: str) -> bool:
    if reason in _NON_SKIP_FLUSH_REASONS:
        return False
    if reason.startswith("merge_"):
        return False
    return True


def _doc_lengths_from_documents(docs) -> dict:
    class _U:
        def __init__(self, text: str):
            self.char_len = len(text or "")

    return length_stats([_U(d.page_content or "") for d in docs])


def _prd_text_lengths_from_documents(docs) -> dict:
    return _doc_lengths_from_documents(
        [
            d
            for d in docs
            if str((d.metadata or {}).get("content_type") or "text") == "text"
            and not bool((d.metadata or {}).get("table_context"))
        ]
    )


def _prepare_final_chunks(elements, chunk_loader: FileLoader):
    merged = apply_technical_manual_merge(elements)
    split = chunk_loader._split_documents_preserving_blocks(merged)
    marked = chunk_loader._mark_table_context_chunks(split)
    filtered = chunk_loader._post_process_chunks(marked)
    return reassign_chunk_adjacency(filtered)


def _source_key(meta: dict, fallback: str) -> str:
    return str(
        meta.get("source_document_id")
        or meta.get("source_snapshot_hash")
        or fallback
    )


def _length_gate_reasons(overall: dict) -> list[str]:
    checks = (
        ("lt100", "after_lt_100_rate", 0.05),
        ("lt200", "after_lt_200_rate", 0.15),
        ("gt1200", "after_gt_1200_rate", 0.05),
    )
    return [
        f"{label} after={float(overall.get(key) or 0):.1%} > {limit:.0%} PRD gate"
        for label, key, limit in checks
        if float(overall.get(key) or 0) > limit
    ]


def _go_no_go_report(measured_reasons: list[str]) -> dict:
    return {
        "chunk_foundation_gate_passed": not measured_reasons,
        "enter_0g": False,
        "reasons": measured_reasons or ["all measured chunk-foundation gates passed"],
        "remaining_0g_requirements": [
            "FR-10 overall/category gates and thresholds are not frozen or verified",
            "0E/OCR inclusion scope is not reviewed",
            "Go checklist is not frozen",
        ],
    }


def _source_section_lineage_report(docs) -> dict[str, int]:
    report = {
        "missing_source_section_paths": 0,
        "missing_source_section_ids": 0,
        "mismatched_source_section_pairs": 0,
        "invalid_source_section_ids": 0,
        "missing_source_section_titles": 0,
    }
    for doc in docs:
        meta = doc.metadata or {}
        if meta.get("content_type") == "heading":
            continue
        paths = [str(value) for value in meta.get("source_section_paths") or []]
        section_ids = [str(value) for value in meta.get("source_section_ids") or []]
        if not paths:
            report["missing_source_section_paths"] += 1
        if not section_ids:
            report["missing_source_section_ids"] += 1
        if len(paths) != len(section_ids):
            report["mismatched_source_section_pairs"] += 1

        document_key = str(
            meta.get("source_snapshot_hash")
            or meta.get("source_document_id")
            or meta.get("source")
            or "unknown_document"
        )
        for path, section_id in zip(paths, section_ids):
            if section_id != section_id_for(document_key, path):
                report["invalid_source_section_ids"] += 1

        path_parts = [
            tuple(part.strip() for part in path.split(">") if part.strip())
            for path in paths
        ]
        l2_keys = {parts[:2] for parts in path_parts if len(parts) >= 2}
        if len(l2_keys) > 1:
            searchable = f"{doc.page_content or ''}\n{meta.get('searchable_text') or ''}"
            report["missing_source_section_titles"] += sum(
                1 for path in paths if path not in searchable
            )
    return report


def _cross_document_section_collisions(
    section_source_pairs: list[tuple[str, str]],
) -> dict:
    """section_id shared within one document is OK; collide across different sources."""
    by_section: dict[str, set[str]] = defaultdict(set)
    for section_id, source in section_source_pairs:
        if not section_id:
            continue
        by_section[section_id].add(source)

    collisions = {
        sid: sorted(sources)
        for sid, sources in by_section.items()
        if len(sources) > 1
    }
    return {
        "field": "cross_document_section_id_collision",
        "collision_value_count": len(collisions),
        "sample_collisions": sorted(
            ((sid, srcs) for sid, srcs in collisions.items()),
            key=lambda x: -len(x[1]),
        )[:10],
    }


def audit_one(
    name: str,
    path: Path,
    loader: UnstructuredChapterLoader,
    chunk_loader: FileLoader,
) -> dict:
    elements = loader.load(str(path))
    before_all_stats = _doc_lengths_from_documents(elements)
    before_stats = _prd_text_lengths_from_documents(elements)

    final = _prepare_final_chunks(elements, chunk_loader)
    after_all_stats = _doc_lengths_from_documents(final)
    after_stats = _prd_text_lengths_from_documents(final)

    flush_events = explain_merge_flush_reasons(elements)
    flush_reason_counts = Counter(str(ev.get("reason") or "") for ev in flush_events)
    skip_reason_counts = {
        k: v for k, v in flush_reason_counts.items() if _is_skip_flush_reason(k)
    }
    sample_skip_events = [
        ev for ev in flush_events if _is_skip_flush_reason(str(ev.get("reason") or ""))
    ][:20]
    merge_like = {
        k: flush_reason_counts[k]
        for k in flush_reason_counts
        if k.startswith("merge_")
    }
    section_lineage = _source_section_lineage_report(final)

    section_source_pairs: list[tuple[str, str]] = []
    source_document_ids: list[str] = []
    for d in final:
        meta = d.metadata or {}
        section_id = str(meta.get("section_id") or "")
        section_source_pairs.append((section_id, _source_key(meta, name)))
        source_document_ids.append(str(meta.get("source_document_id") or ""))

    return {
        "source": name,
        "path": str(path),
        "element_count": len(elements),
        "final_chunk_count": len(final),
        "before_all": before_all_stats,
        "after_all": after_all_stats,
        "before": before_stats,
        "after": after_stats,
        "flush_reason_counts": dict(flush_reason_counts),
        "skip_reason_counts": dict(skip_reason_counts),
        "merge_opportunity_counts": merge_like,
        "sample_skip_events": sample_skip_events,
        "lineage": {
            "final_chunks": len(final),
            "missing_source_element_ids": sum(
                1 for d in final if not (d.metadata or {}).get("source_element_ids")
            ),
            "missing_source_raw_block_ids": sum(
                1
                for d in final
                if not (d.metadata or {}).get("source_raw_block_ids")
                and (d.metadata or {}).get("content_type") != "heading"
            ),
            "missing_chunk_uid": sum(
                1
                for d in final
                if not str((d.metadata or {}).get("chunk_uid") or "").startswith("chk_")
            ),
            "missing_section_id": sum(
                1
                for d in final
                if not str((d.metadata or {}).get("section_id") or "").startswith("sec_")
            ),
            **section_lineage,
        },
        "section_ids": [str((d.metadata or {}).get("section_id") or "") for d in final],
        "chunk_uids": [str((d.metadata or {}).get("chunk_uid") or "") for d in final],
        "source_document_ids": source_document_ids,
        "section_source_pairs": section_source_pairs,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument(
        "--docs",
        nargs="*",
        default=DEFAULT_DOCS,
        help="DOCX filenames under watch_directory",
    )
    args = parser.parse_args(argv)

    cfg = Config()
    watch_dir = args.watch_dir or Path(cfg.watch_dir)
    loader = UnstructuredChapterLoader()
    chunk_loader = FileLoader()

    per_doc = []
    all_section_ids: list[str] = []
    all_chunk_uids: list[str] = []
    all_source_document_ids: list[str] = []
    all_section_source_pairs: list[tuple[str, str]] = []

    for name in args.docs:
        path = _find_docx(watch_dir, name)
        if path is None:
            logger.warning("missing doc: %s under %s", name, watch_dir)
            continue
        row = audit_one(name, path, loader, chunk_loader)
        logger.info(
            "%s elements=%s final=%s lt200 %.1f%% -> %.1f%%",
            name,
            row["element_count"],
            row["final_chunk_count"],
            100 * row["before"]["lt_200_rate"],
            100 * row["after"]["lt_200_rate"],
        )
        all_section_ids.extend(row["section_ids"])
        all_chunk_uids.extend(row["chunk_uids"])
        all_source_document_ids.extend(row["source_document_ids"])
        all_section_source_pairs.extend(row["section_source_pairs"])
        per_doc.append(row)

    overall_before = {"count": 0, "lt_100": 0, "lt_200": 0, "gt_1200": 0}
    overall_after = {"count": 0, "lt_100": 0, "lt_200": 0, "gt_1200": 0}
    for row in per_doc:
        b, a = row["before"], row["after"]
        overall_before["count"] += b["count"]
        overall_after["count"] += a["count"]
        overall_before["lt_100"] += int(round(b["lt_100_rate"] * b["count"]))
        overall_before["lt_200"] += int(round(b["lt_200_rate"] * b["count"]))
        overall_after["lt_100"] += int(round(a["lt_100_rate"] * a["count"]))
        overall_after["lt_200"] += int(round(a["lt_200_rate"] * a["count"]))
        overall_after["gt_1200"] += int(round(a["gt_1200_rate"] * a["count"]))
        overall_before["gt_1200"] += int(round(b["gt_1200_rate"] * b["count"]))

    def _rate(n: int, d: int) -> float:
        return (n / d) if d else 0.0

    cross_doc = _cross_document_section_collisions(all_section_source_pairs)
    identity = {
        "section_id_within_doc_sharing_ok": True,
        "section_id": _dup_report(all_section_ids, "section_id"),
        "cross_document_section_id_collision": cross_doc,
        "chunk_uid": _dup_report(all_chunk_uids, "chunk_uid"),
        "source_document_id": _dup_report(all_source_document_ids, "source_document_id"),
        "section_source_pair_count": len(all_section_source_pairs),
    }
    broken_links = 0
    for name in args.docs:
        path = _find_docx(watch_dir, name)
        if path is None:
            continue
        elements = loader.load(str(path))
        final = _prepare_final_chunks(elements, chunk_loader)
        local_uids = {(x.metadata or {}).get("chunk_uid") for x in final}
        for d in final:
            meta = d.metadata or {}
            prev_id = meta.get("prev_chunk_id")
            next_id = meta.get("next_chunk_id")
            if prev_id and prev_id not in local_uids:
                broken_links += 1
            if next_id and next_id not in local_uids:
                broken_links += 1

    identity["broken_prev_next_links"] = broken_links

    webrtc = next((r for r in per_doc if "WebRTC" in r["source"]), None)

    doc_reports = []
    for row in per_doc:
        slim = {
            k: v
            for k, v in row.items()
            if k
            not in {
                "section_ids",
                "chunk_uids",
                "source_document_ids",
                "section_source_pairs",
            }
        }
        slim["sample_skip_events"] = row.get("sample_skip_events", [])[:15]
        doc_reports.append(slim)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "watch_directory": str(watch_dir),
        "notes": (
            "Isolation only: UnstructuredChapterLoader + technical_manual_merge + "
            "reassign_chunk_adjacency. Does not write chroma_db or call /rebuild. "
            "section_id may be shared by multiple chunks in the same document; "
            "go/no-go uses cross-document section_id collisions."
        ),
        "identity": identity,
        "overall": {
            "scope": (
                "content_type=text; heading/table/code/embedded_image and explicit "
                "same-section table_context excluded"
            ),
            "before_all_chunk_count": sum(row["before_all"]["count"] for row in per_doc),
            "after_all_chunk_count": sum(row["after_all"]["count"] for row in per_doc),
            "before_count": overall_before["count"],
            "after_count": overall_after["count"],
            "before_lt_100_rate": _rate(overall_before["lt_100"], overall_before["count"]),
            "before_lt_200_rate": _rate(overall_before["lt_200"], overall_before["count"]),
            "before_gt_1200_rate": _rate(overall_before["gt_1200"], overall_before["count"]),
            "after_lt_100_rate": _rate(overall_after["lt_100"], overall_after["count"]),
            "after_lt_200_rate": _rate(overall_after["lt_200"], overall_after["count"]),
            "after_gt_1200_rate": _rate(overall_after["gt_1200"], overall_after["count"]),
            "prd_lt_100_gate": 0.05,
            "prd_lt_200_gate": 0.15,
            "prd_gt_1200_gate": 0.05,
            "meets_prd_lt_100": _rate(overall_after["lt_100"], overall_after["count"]) <= 0.05,
            "meets_prd_lt_200": _rate(overall_after["lt_200"], overall_after["count"]) <= 0.15,
            "meets_prd_gt_1200": _rate(overall_after["gt_1200"], overall_after["count"]) <= 0.05,
            "gate_note": (
                "Do not freeze interim thresholds from this run alone; "
                "section 13 requires Go checklist freeze after review."
            ),
        },
        "documents": doc_reports,
        "webrtc_diagnosis": None
        if webrtc is None
        else {
            "source": webrtc["source"],
            "element_count": webrtc["element_count"],
            "final_chunk_count": webrtc["final_chunk_count"],
            "lt_200_before": webrtc["before"]["lt_200_rate"],
            "lt_200_after": webrtc["after"]["lt_200_rate"],
            "flush_reason_counts": webrtc["flush_reason_counts"],
            "skip_reason_counts": webrtc["skip_reason_counts"],
            "merge_opportunity_counts": webrtc["merge_opportunity_counts"],
            "sample_skip_events": webrtc.get("sample_skip_events", [])[:20],
        },
    }

    reasons = []
    if cross_doc["collision_value_count"]:
        reasons.append(
            f"cross-document section_id collisions: {cross_doc['collision_value_count']} values"
        )
    if identity["chunk_uid"]["duplicate_value_count"]:
        reasons.append(
            f"chunk_uid collisions: {identity['chunk_uid']['duplicate_value_count']} values"
        )
    if broken_links:
        reasons.append(f"broken prev/next links: {broken_links}")
    section_lineage_fields = (
        "missing_source_section_paths",
        "missing_source_section_ids",
        "mismatched_source_section_pairs",
        "invalid_source_section_ids",
        "missing_source_section_titles",
    )
    for field in section_lineage_fields:
        count = sum(int(row["lineage"].get(field) or 0) for row in per_doc)
        if count:
            reasons.append(f"source section lineage {field}: {count}")
    reasons.extend(_length_gate_reasons(report["overall"]))
    report["go_no_go"] = _go_no_go_report(reasons)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "round0c_isolation_audit.json"
    md_path = args.out_dir / "round0c_isolation_audit.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    md = [
        "# Round 0C Isolation Audit",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- watch_directory: `{report['watch_directory']}`",
        f"- chunk_foundation_gate_passed: **{report['go_no_go']['chunk_foundation_gate_passed']}**",
        f"- enter_0g: **{report['go_no_go']['enter_0g']}**",
        "",
        "## PRD ordinary-text length",
        "",
        f"- scope: `{report['overall']['scope']}`",
        f"- all chunks before/after: `{report['overall']['before_all_chunk_count']} -> {report['overall']['after_all_chunk_count']}`",
        "",
        "| | count | lt100 | lt200 | gt1200 |",
        "|---|---:|---:|---:|---:|",
        f"| before | {report['overall']['before_count']} | {report['overall']['before_lt_100_rate']:.1%} | {report['overall']['before_lt_200_rate']:.1%} | {report['overall']['before_gt_1200_rate']:.1%} |",
        f"| after | {report['overall']['after_count']} | {report['overall']['after_lt_100_rate']:.1%} | {report['overall']['after_lt_200_rate']:.1%} | {report['overall']['after_gt_1200_rate']:.1%} |",
        "| PRD gate | - | <=5% | <=15% | <=5% |",
        "",
        "## Identity",
        "",
        "- within-doc section_id sharing: **OK** (not a go/no-go failure)",
        f"- cross-document section_id collisions: **{cross_doc['collision_value_count']}**",
        f"- section_id raw duplicates (includes within-doc): {identity['section_id']['duplicate_value_count']} "
        f"(occurrences {identity['section_id']['duplicate_occurrence_sum']})",
        f"- chunk_uid duplicates: **{identity['chunk_uid']['duplicate_value_count']}** "
        f"(occurrences {identity['chunk_uid']['duplicate_occurrence_sum']})",
        f"- source_document_id unique values: {identity['source_document_id']['unique']} / "
        f"{identity['source_document_id']['total']} chunks",
        f"- broken prev/next: **{broken_links}**",
        "",
    ]
    if cross_doc["sample_collisions"]:
        md.extend(
            [
                "Sample cross-document section_id collisions:",
                "",
                "```json",
                json.dumps(cross_doc["sample_collisions"][:5], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )

    md.extend(
        [
            "## Per document",
            "",
        ]
    )
    for row in per_doc:
        md.extend(
            [
                f"### {row['source']}",
                "",
                f"- elements -> final: `{row['element_count']} -> {row['final_chunk_count']}`",
                f"- ordinary-text lt200: `{row['before']['lt_200_rate']:.1%} -> {row['after']['lt_200_rate']:.1%}`",
                f"- median chars: `{row['before']['median']} -> {row['after']['median']}`",
                f"- flush_reason_counts: `{json.dumps(row['flush_reason_counts'], ensure_ascii=False)}`",
                f"- skip_reason_counts: `{json.dumps(row['skip_reason_counts'], ensure_ascii=False)}`",
                f"- merge opportunities (flush replay): `{json.dumps(row['merge_opportunity_counts'], ensure_ascii=False)}`",
                f"- lineage missing element/raw (non-heading): "
                f"`{row['lineage']['missing_source_element_ids']}` / "
                f"`{row['lineage']['missing_source_raw_block_ids']}`",
                f"- source section lineage errors: "
                f"paths=`{row['lineage']['missing_source_section_paths']}`, "
                f"ids=`{row['lineage']['missing_source_section_ids']}`, "
                f"pairs=`{row['lineage']['mismatched_source_section_pairs']}`, "
                f"invalid_ids=`{row['lineage']['invalid_source_section_ids']}`, "
                f"titles=`{row['lineage']['missing_source_section_titles']}`",
                "",
            ]
        )

    if report["webrtc_diagnosis"]:
        w = report["webrtc_diagnosis"]
        md.extend(
            [
                "## WebRTC diagnosis",
                "",
                f"- elements -> final: `{w['element_count']} -> {w['final_chunk_count']}`",
                f"- lt200: `{w['lt_200_before']:.1%} -> {w['lt_200_after']:.1%}`",
                f"- flush_reason_counts: `{json.dumps(w['flush_reason_counts'], ensure_ascii=False)}`",
                f"- skip_reason_counts: `{json.dumps(w['skip_reason_counts'], ensure_ascii=False)}`",
                f"- merge_opportunity_counts: `{json.dumps(w['merge_opportunity_counts'], ensure_ascii=False)}`",
                "",
                "Sample skip flush events:",
                "",
                "```json",
                json.dumps(w["sample_skip_events"][:10], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )

    md.extend(
        [
            "## Go / No-Go",
            "",
            *[f"- {r}" for r in report["go_no_go"]["reasons"]],
            "",
            "## Remaining Round 0G requirements",
            "",
            *[
                f"- {r}"
                for r in report["go_no_go"]["remaining_0g_requirements"]
            ],
            "",
        ]
    )
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    logger.info("wrote %s", json_path)
    logger.info("wrote %s", md_path)
    logger.info(
        "chunk_foundation_gate_passed=%s enter_0g=%s reasons=%s",
        report["go_no_go"]["chunk_foundation_gate_passed"],
        report["go_no_go"]["enter_0g"],
        report["go_no_go"]["reasons"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
