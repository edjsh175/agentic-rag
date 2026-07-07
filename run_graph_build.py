"""CLI for deterministic, reviewable knowledge-graph extraction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_extraction import GraphBuilder, GraphCandidateApplier, GraphQualityService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase B deterministic graph builder")
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract")
    extract.add_argument("--limit", type=int)
    extract.add_argument("--doc-category", action="append", dest="doc_categories")
    extract.add_argument("--chunk-id", action="append", dest="chunk_ids")
    extract.add_argument("--force-rebuild", action="store_true")

    listing = sub.add_parser("list")
    listing.add_argument("--batch")
    listing.add_argument("--status", default="")

    export = sub.add_parser("export")
    export.add_argument("--batch", required=True)
    export.add_argument("--output", required=True)

    review = sub.add_parser("review")
    review.add_argument("--batch", required=True)
    group = review.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve-all", action="store_true")
    group.add_argument("--approve", nargs="+")
    group.add_argument("--reject", nargs="+")
    review.add_argument("--reason", default="")

    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("--batch", required=True)

    quality = sub.add_parser("quality")
    target = quality.add_mutually_exclusive_group(required=True)
    target.add_argument("--batch")
    target.add_argument("--graph", action="store_true")
    quality.add_argument("--profile", choices=("partial", "full"), default="full")
    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None, *, db: RelationalDB | None = None, chunk_source=None) -> int:
    args = build_parser().parse_args(argv)
    db = db or RelationalDB()

    if args.command == "extract":
        builder = GraphBuilder(db=db, chunk_source=chunk_source)
        result = (
            builder.build_incremental(args.chunk_ids)
            if args.chunk_ids
            else builder.build_full(args.force_rebuild, args.limit, args.doc_categories)
        )
        _print({"batch_id": result.batch_id, "stats": result.stats})
        return 0

    if args.command == "list":
        payload = db.list_extraction_candidates(args.batch, args.status) if args.batch else db.list_extraction_batches(args.status)
        _print(payload)
        return 0

    if args.command == "export":
        batch = db.get_extraction_batch(args.batch)
        if not batch:
            raise KeyError("extraction batch not found")
        payload = {"batch": batch, "candidates": db.list_extraction_candidates(args.batch)}
        Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    if args.command == "review":
        pending = db.list_extraction_candidates(args.batch, "pending")
        if args.approve_all:
            ids = [item["id"] for item in pending]
            status = "approved"
        elif args.approve:
            ids, status = args.approve, "approved"
        else:
            ids, status = args.reject, "rejected"
        updated = db.review_extraction_candidates(args.batch, ids, status, args.reason)
        remaining = db.list_extraction_candidates(args.batch, "pending")
        if not remaining:
            approved = db.list_extraction_candidates(args.batch, "approved")
            db.set_extraction_batch_status(args.batch, "approved" if approved else "rejected")
        _print({
            "requested": len(ids),
            "updated": updated,
            "missing_or_not_pending": len(ids) - updated,
            "status": status,
            "remaining_pending": len(remaining),
        })
        return 0

    if args.command == "apply":
        GraphCandidateApplier(db).apply(args.batch)
        _print({"batch_id": args.batch, "status": "applied"})
        return 0

    quality = GraphQualityService(db)
    report = quality.inspect_graph(profile=args.profile) if args.graph else quality.inspect_batch(args.batch)
    _print({"ok": report.ok, "errors": report.errors, "warnings": report.warnings, "stats": report.stats})
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
