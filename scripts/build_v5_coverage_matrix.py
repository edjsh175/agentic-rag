#!/usr/bin/env python3
"""Build v5 coverage matrix: live text sources × L1/L2 section_path slots.

Read-only against Chroma. Does not mutate the frozen v4 gold set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = ROOT / "docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集"
DEFAULT_JSON = BASE / "v5_coverage_matrix.json"
DEFAULT_MD = BASE / "v5_coverage_matrix.md"

MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4", ".avi", ".mov", ".mkv"}
MIN_CHARS = 80
TOC_HINTS = ("目录", "contents", "table of contents")
# Prefer more slots for large product manuals.
PRIORITY_SOURCE_SUBSTR = (
    "StampServer",
    "StampTools",
    "StampWebGL",
    "StampWebRTC",
    "PipelineBuilder",
    "PipelineSystem",
    "StampManager",
    "UEModelBuilder",
    "ModelBuilder",
    "TerrainBuilder",
)


def _section_key(section_path: str, depth: int = 2) -> str:
    parts = [p.strip() for p in str(section_path or "").split(">") if p.strip()]
    if not parts:
        return ""
    return " > ".join(parts[:depth])


def _is_media(source: str) -> bool:
    return Path(str(source or "")).suffix.lower() in MEDIA_EXTS


def _is_noise(text: str, section_path: str) -> bool:
    body = (text or "").strip()
    if len(body) < MIN_CHARS:
        return True
    low = body.casefold()
    sec = (section_path or "").casefold()
    if any(h in sec for h in TOC_HINTS) and len(body) < 400:
        return True
    # Pure link / hyperlink junk
    if body.count("HYPERLINK") >= 3:
        return True
    if low.startswith("#") and "\n" not in body and len(body) < 120:
        return True
    return False


def _source_priority(source: str) -> int:
    name = Path(str(source or "")).name
    for i, token in enumerate(PRIORITY_SOURCE_SUBSTR):
        if token.casefold() in name.casefold():
            return i
    return 100


def _load_chunks() -> tuple[list[dict[str, Any]], str]:
    from rag_knowledge.repository.vector_store import VectorStore

    raw = VectorStore().get_chunk_stats_source()
    ids = raw.get("ids") or []
    docs = raw.get("documents") or []
    metas = raw.get("metadatas") or []
    chunks: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    for chunk_id, document, metadata in zip(ids, docs, metas):
        meta = dict(metadata or {})
        text = str(document or "")
        digest.update(str(chunk_id).encode("utf-8"))
        digest.update(text.encode("utf-8"))
        digest.update(json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        chunks.append({"id": str(chunk_id), "document": text, "metadata": meta})
    return chunks, digest.hexdigest()


def build_matrix(chunks: list[dict[str, Any]], target_slots: int = 100) -> dict[str, Any]:
    by_file: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []

    for chunk in chunks:
        meta = chunk["metadata"]
        source = str(meta.get("source") or meta.get("file_name") or "").strip()
        if not source:
            skipped.append({"reason": "missing_source", "chunk_id": chunk["id"]})
            continue
        if _is_media(source):
            skipped.append({"reason": "media", "source": source, "chunk_id": chunk["id"]})
            continue
        review = str(meta.get("review_status") or "").strip().casefold()
        if review and review != "approved":
            skipped.append({"reason": f"review_status={review}", "source": source, "chunk_id": chunk["id"]})
            continue
        category = str(meta.get("category") or meta.get("content_type") or "text").casefold()
        if category in {"image", "video", "media"}:
            skipped.append({"reason": "non_text_category", "source": source, "chunk_id": chunk["id"]})
            continue

        section_path = str(meta.get("section_path") or "").strip()
        text = chunk["document"]
        if _is_noise(text, section_path):
            skipped.append({"reason": "noise_or_short", "source": source, "chunk_id": chunk["id"]})
            continue

        key = _section_key(section_path, 2)
        if not key:
            # Keep as file-level orphan slot material
            key = "(no_section)"

        file_entry = by_file.setdefault(
            source,
            {
                "source": source,
                "doc_category": meta.get("doc_category") or "",
                "chunk_count": 0,
                "char_count": 0,
                "sections": {},
            },
        )
        file_entry["chunk_count"] += 1
        file_entry["char_count"] += len(text)
        sec = file_entry["sections"].setdefault(
            key,
            {
                "section_key": key,
                "section_path_sample": section_path,
                "section_ids": set(),
                "chunk_ids": [],
                "char_count": 0,
                "best_chunk_id": chunk["id"],
                "best_len": len(text),
            },
        )
        sid = str(meta.get("section_id") or "").strip()
        if sid:
            sec["section_ids"].add(sid)
        sec["chunk_ids"].append(chunk["id"])
        sec["char_count"] += len(text)
        if len(text) > sec["best_len"]:
            sec["best_len"] = len(text)
            sec["best_chunk_id"] = chunk["id"]

    # Convert sets for JSON
    files: list[dict[str, Any]] = []
    for source, entry in by_file.items():
        sections = []
        for sec in entry["sections"].values():
            sections.append(
                {
                    "section_key": sec["section_key"],
                    "section_path_sample": sec["section_path_sample"],
                    "section_ids": sorted(sec["section_ids"]),
                    "chunk_count": len(sec["chunk_ids"]),
                    "char_count": sec["char_count"],
                    "best_chunk_id": sec["best_chunk_id"],
                }
            )
        sections.sort(key=lambda s: (-s["char_count"], s["section_key"]))
        files.append(
            {
                "source": source,
                "doc_category": entry["doc_category"],
                "chunk_count": entry["chunk_count"],
                "char_count": entry["char_count"],
                "section_count": len(sections),
                "sections": sections,
            }
        )
    files.sort(key=lambda f: (_source_priority(f["source"]), -f["char_count"], f["source"]))

    # Allocate slots: each file ≥1; remaining to richest L2 sections of priority manuals.
    slots: list[dict[str, Any]] = []
    used_keys: set[tuple[str, str]] = set()

    def add_slot(file_row: dict[str, Any], sec: dict[str, Any], reason: str) -> None:
        marker = (file_row["source"], sec["section_key"])
        if marker in used_keys:
            return
        used_keys.add(marker)
        slots.append(
            {
                "slot_id": f"slot-{len(slots) + 1:03d}",
                "source": file_row["source"],
                "doc_category": file_row["doc_category"],
                "section_key": sec["section_key"],
                "section_path_sample": sec["section_path_sample"],
                "section_ids": sec["section_ids"],
                "best_chunk_id": sec["best_chunk_id"],
                "chunk_count": sec["chunk_count"],
                "char_count": sec["char_count"],
                "allocation_reason": reason,
            }
        )

    # Pass 1: one best section per file
    for file_row in files:
        if not file_row["sections"]:
            continue
        add_slot(file_row, file_row["sections"][0], "per_file_minimum")

    # Pass 2: fill to target with additional sections (prefer priority sources, skip no_section)
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for file_row in files:
        for sec in file_row["sections"][1:]:
            if sec["section_key"] == "(no_section)":
                continue
            if sec["char_count"] < 120:
                continue
            candidates.append((file_row, sec))
    candidates.sort(
        key=lambda pair: (
            _source_priority(pair[0]["source"]),
            -pair[1]["char_count"],
            pair[0]["source"],
            pair[1]["section_key"],
        )
    )
    for file_row, sec in candidates:
        if len(slots) >= target_slots:
            break
        add_slot(file_row, sec, "section_fill")

    # If still short, allow no_section / smaller sections
    if len(slots) < target_slots:
        extra: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for file_row in files:
            for sec in file_row["sections"]:
                marker = (file_row["source"], sec["section_key"])
                if marker in used_keys:
                    continue
                extra.append((file_row, sec))
        extra.sort(key=lambda pair: (-pair[1]["char_count"], pair[0]["source"]))
        for file_row, sec in extra:
            if len(slots) >= target_slots:
                break
            add_slot(file_row, sec, "backfill")

    return {
        "matrix_version": "v5-coverage-matrix-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_slots": target_slots,
        "slot_count": len(slots),
        "text_file_count": len(files),
        "files": files,
        "slots": slots,
        "skipped_summary": _summarize_skipped(skipped),
        "skipped_sample": skipped[:50],
    }


def _summarize_skipped(skipped: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in skipped:
        counts[str(row.get("reason") or "unknown")] += 1
    return dict(sorted(counts.items()))


def to_markdown(matrix: dict[str, Any]) -> str:
    lines = [
        "# v5 覆盖矩阵（文件 × 一/二级章节）",
        "",
        f"- generated_at: `{matrix.get('generated_at')}`",
        f"- corpus_snapshot_hash: `{matrix.get('corpus_snapshot_hash')}`",
        f"- live_chunks: **{matrix.get('live_chunk_count')}**",
        f"- text_files: **{matrix.get('text_file_count')}**",
        f"- planned_slots: **{matrix.get('slot_count')}** / target {matrix.get('target_slots')}",
        f"- skipped: `{matrix.get('skipped_summary')}`",
        "",
        "## 覆盖槽位",
        "",
        "| slot | source | section_key | chunks | chars | reason |",
        "|---|---|---:|---:|---:|---|",
    ]
    for slot in matrix.get("slots") or []:
        lines.append(
            f"| {slot['slot_id']} | `{Path(slot['source']).name}` | {slot['section_key']} | "
            f"{slot['chunk_count']} | {slot['char_count']} | {slot['allocation_reason']} |"
        )
    lines.extend(["", "## 文本源文件", "", "| source | sections | chunks | chars |", "|---|---:|---:|---:|"])
    for file_row in matrix.get("files") or []:
        lines.append(
            f"| `{Path(file_row['source']).name}` | {file_row['section_count']} | "
            f"{file_row['chunk_count']} | {file_row['char_count']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build v5 coverage matrix from live Chroma")
    parser.add_argument("--out-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--target-slots", type=int, default=100)
    args = parser.parse_args(argv)

    chunks, snapshot = _load_chunks()
    matrix = build_matrix(chunks, target_slots=args.target_slots)
    matrix["corpus_snapshot_hash"] = snapshot
    matrix["live_chunk_count"] = len(chunks)

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.out_md.write_text(to_markdown(matrix), encoding="utf-8")
    print(
        json.dumps(
            {
                "slot_count": matrix["slot_count"],
                "text_file_count": matrix["text_file_count"],
                "live_chunk_count": len(chunks),
                "out_json": str(args.out_json),
                "out_md": str(args.out_md),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not (90 <= matrix["slot_count"] <= 110):
        print(
            f"WARNING: slot_count={matrix['slot_count']} outside 90-110 target band",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
