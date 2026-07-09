"""CLI for staging retrieval intent profiles into graph extraction batches."""
from __future__ import annotations

import argparse
import json

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.profile_graph_sync import ProfileGraphSyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage retrieval intent profiles into graph candidates")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--profile-id")
    parser.add_argument("--review-status", choices=("pending", "approved"), default="pending")
    parser.add_argument("--json", action="store_true")
    return parser


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None, *, db: RelationalDB | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = ProfileGraphSyncService(db=db or RelationalDB())

    if args.dry_run:
        preview = service.preview(args.profile_id)
        _emit({"mode": "dry-run", **preview.to_dict()})
        return 0

    result = service.build_batch(args.profile_id, review_status=args.review_status)
    batch = service.db.get_extraction_batch(result.batch_id)
    _emit(
        {
            "mode": "apply",
            "review_status": args.review_status,
            "batch": batch,
            "stats": result.stats,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
