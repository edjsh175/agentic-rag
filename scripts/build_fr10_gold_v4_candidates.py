#!/usr/bin/env python3
"""Derive auditable FR-10 v4 candidate sets from frozen v3.2 without mutating it."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs/04_已完成归档/01_文档解析与切块/已完成-第0B轮-并行准备与预研/评测基线与黄金集"
V32 = BASE / "multi_chunk_qa_gold_v3_2.json"
V32_MANIFEST = BASE / "multi_chunk_qa_gold_v3_2.manifest.json"
RETRIEVAL_OUT = BASE / "multi_chunk_qa_gold_v4_retrieval_candidate.json"
GOVERNANCE_OUT = BASE / "multi_chunk_qa_gold_v4_governance_candidate.json"
MANIFEST_OUT = BASE / "multi_chunk_qa_gold_v4.candidate.manifest.json"

RETRIEVAL_CATEGORIES = {"fact", "procedure", "cross_section", "table"}
GOVERNANCE_CATEGORIES = {"conflict", "none"}


def _json_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _with_scope(items: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    scoped = copy.deepcopy(items)
    for item in scoped:
        item["evaluation_scope"] = scope
    return scoped


def partition_gold(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Return the v4 scopes after explicitly excluding deferred OCR/media items."""
    groups = {
        "retrieval": _with_scope(
            [item for item in items if item.get("category") in RETRIEVAL_CATEGORIES], "fr10_retrieval"
        ),
        "governance": _with_scope(
            [item for item in items if item.get("category") in GOVERNANCE_CATEGORIES], "answer_governance"
        ),
    }
    all_ids = [str(item.get("id")) for rows in groups.values() for item in rows]
    excluded_media = [item for item in items if item.get("category") == "ocr"]
    if len(items) != 120 or len(all_ids) != 110 or len(set(all_ids)) != 110 or len(excluded_media) != 10:
        raise ValueError("v3.2 must yield 110 v4 candidates after excluding 10 OCR/media items")
    if {name: len(rows) for name, rows in groups.items()} != {
        "retrieval": 90,
        "governance": 20,
    }:
        raise ValueError("v4 candidate scopes must contain 90 retrieval and 20 governance items")
    return groups


def build_manifest(
    groups: dict[str, list[dict[str, Any]]], parent_manifest: dict[str, Any], excluded_media_ids: list[str]
) -> dict[str, Any]:
    return {
        "gold_version": "v4-candidate",
        "status": "not_frozen",
        "parent_gold": V32.name,
        "parent_gold_sha256": parent_manifest["gold_sha256"],
        "corpus_snapshot_hash": parent_manifest["corpus_snapshot_hash"],
        "scope_policy": {
            "fr10_retrieval": "90 text-answerable items only; eligible for evidence-recall gates after manual review",
            "answer_governance": "10 conflict and 10 refusal items; excluded from evidence-recall gates",
        },
        "candidate_files": {
            "fr10_retrieval": RETRIEVAL_OUT.name,
            "answer_governance": GOVERNANCE_OUT.name,
        },
        "counts": {name: len(rows) for name, rows in groups.items()},
        "excluded_items": {
            "reason": "OCR/media capability is deferred outside v4.",
            "category": "ocr",
            "count": len(excluded_media_ids),
            "ids": excluded_media_ids,
        },
        "freeze_requirement": "Every retrieval item must have a manually approved evidence review record; do not compare candidate results with v3.2 120-item metrics.",
    }


def main() -> int:
    items = json.loads(V32.read_text(encoding="utf-8"))
    parent_manifest = json.loads(V32_MANIFEST.read_text(encoding="utf-8"))
    groups = partition_gold(items)
    excluded_media_ids = [str(item["id"]) for item in items if item.get("category") == "ocr"]
    manifest = build_manifest(groups, parent_manifest, excluded_media_ids)

    for path, rows in ((RETRIEVAL_OUT, groups["retrieval"]), (GOVERNANCE_OUT, groups["governance"])):
        path.write_text(_json_payload(rows), encoding="utf-8")
    manifest["candidate_sha256"] = {
        name: hashlib.sha256(_json_payload(rows).encode("utf-8")).hexdigest()
        for name, rows in groups.items()
    }
    MANIFEST_OUT.write_text(_json_payload(manifest), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
