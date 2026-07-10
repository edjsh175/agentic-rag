"""CLI for staging retrieval intent profiles into graph extraction batches."""
from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_governance import (
    assert_staging_review_status,
    assert_write_confirmation,
    resolve_db_path,
)
from rag_knowledge.services.profile_graph_sync import ProfileGraphSyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage retrieval intent profiles into graph candidates")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--stage", action="store_true", help="Persist a pending extraction batch (staging only)")
    mode.add_argument("--apply", action="store_true", help="Deprecated alias for --stage")
    parser.add_argument("--profile-id")
    parser.add_argument("--review-status", choices=("pending", "approved"), default="pending")
    parser.add_argument("--confirm-db-path")
    parser.add_argument("--write-policy-output", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _skipped_generics(preview) -> list[dict]:
    skipped = []
    for profile in preview.profiles:
        for diagnostic in profile.diagnostics:
            if diagnostic.code == "generic_recall_term":
                skipped.append(
                    {
                        "profile_id": profile.profile_id,
                        "term": diagnostic.term,
                        "reason": diagnostic.message,
                    }
                )
    return skipped


def main(argv: list[str] | None = None, *, db: RelationalDB | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = db or RelationalDB()
    db_path = resolve_db_path()
    service = ProfileGraphSyncService(db=db)

    if args.dry_run:
        preview = service.preview(args.profile_id)
        payload = {
            "mode": "dry-run",
            **preview.to_dict(),
            "skipped_generics": _skipped_generics(preview),
            "suggested_policies": service.suggest_policies(args.profile_id),
        }
        if args.write_policy_output:
            output_path = Path(__file__).resolve().parent / "data" / "retrieval_intent_policies.generated.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(payload["suggested_policies"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            payload["policy_output"] = str(output_path)
        _emit(payload)
        return 0

    if args.apply:
        warnings.warn(
            "--apply is deprecated for profile staging; use --stage (staging only, does not write graph main tables)",
            DeprecationWarning,
            stacklevel=2,
        )

    assert_staging_review_status(args.review_status, db_path=db_path)
    assert_write_confirmation(
        db_path=db_path,
        confirm_db_path=args.confirm_db_path,
    )

    result = service.build_batch(args.profile_id, review_status=args.review_status)
    preview = service.preview(args.profile_id)
    batch = service.db.get_extraction_batch(result.batch_id)
    payload = {
        "mode": "stage",
        "deprecated_apply_alias": bool(args.apply),
        "review_status": args.review_status,
        "batch_id": result.batch_id,
        "batch": batch,
        "stats": result.stats,
        "skipped_generics": _skipped_generics(preview),
        "suggested_policies": service.suggest_policies(args.profile_id),
    }
    if args.write_policy_output:
        output_path = Path(__file__).resolve().parent / "data" / "retrieval_intent_policies.generated.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload["suggested_policies"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        payload["policy_output"] = str(output_path)
    _emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
