"""CLI for staging product_relation_backbone.json into graph extraction batches."""
from __future__ import annotations

import argparse
import json

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_governance import (
    assert_staging_review_status,
    assert_write_confirmation,
    resolve_db_path,
)
from rag_knowledge.services.product_backbone_graph_sync import ProductBackboneGraphSyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage product relation backbone into graph candidates")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--stage", action="store_true", help="Persist a pending extraction batch (staging only)")
    parser.add_argument("--path", help="Override path to product_relation_backbone.json")
    parser.add_argument("--review-status", choices=("pending", "approved"), default="pending")
    parser.add_argument("--confirm-db-path")
    parser.add_argument("--json", action="store_true")
    return parser


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None, *, db: RelationalDB | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.dry_run:
        service = ProductBackboneGraphSyncService(path=args.path)
        preview = service.preview()
        _emit({
            "mode": "dry-run",
            "path": str(service.path),
            "entity_count": len(preview["entities"]),
            "alias_count": len(preview["aliases"]),
            "relation_count": len(preview["relations"]),
            "diagnostic_count": len(preview["diagnostics"]),
            **preview,
        })
        return 1 if preview["diagnostics"] else 0

    db = db or RelationalDB()
    db_path = resolve_db_path()
    service = ProductBackboneGraphSyncService(db=db, path=args.path)
    assert_staging_review_status(args.review_status, db_path=db_path)
    assert_write_confirmation(db_path=db_path, confirm_db_path=args.confirm_db_path)

    result = service.build_batch(review_status=args.review_status)
    batch = service.db.get_extraction_batch(result.batch_id)
    _emit({
        "mode": "stage",
        "review_status": args.review_status,
        "batch_id": result.batch_id,
        "batch": batch,
        "stats": result.stats,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
