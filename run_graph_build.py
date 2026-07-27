"""CLI for deterministic, reviewable knowledge-graph extraction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_extraction import GraphBuilder, GraphCandidateApplier, GraphQualityService
from rag_knowledge.services.graph_governance import (
    approve_all_allowed,
    assert_write_confirmation,
    resolve_db_path,
    summarize_review_selection,
)
from rag_knowledge.services.graph_text_migration import GraphTextMigration
from rag_knowledge.services.safe_rebuild import SafeRebuildDryRunService, SafeRebuildService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase B deterministic graph builder")
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract")
    extract.add_argument("--limit", type=int)
    extract.add_argument("--doc-category", action="append", dest="doc_categories")
    extract.add_argument("--chunk-id", action="append", dest="chunk_ids")
    extract.add_argument("--force-rebuild", action="store_true")
    extract.add_argument("--include-llm", action="store_true")
    extract.add_argument("--resume-batch", dest="resume_batch")

    listing = sub.add_parser("list")
    listing.add_argument("--batch")
    listing.add_argument("--status", default="")

    export = sub.add_parser("export")
    export.add_argument("--batch", required=True)
    export.add_argument("--output", required=True)

    review = sub.add_parser("review")
    review.add_argument("--batch", required=True)
    group = review.add_mutually_exclusive_group(required=False)
    group.add_argument("--approve-all", action="store_true")
    group.add_argument("--approve", nargs="+")
    group.add_argument("--reject", nargs="+")
    review.add_argument("--summary", action="store_true")
    review.add_argument("--approve-type")
    review.add_argument("--approve-relation-type")
    review.add_argument("--approve-confidence-above", type=float)
    review.add_argument("--reject-confidence-below", type=float)
    review.add_argument("--approve-source")
    review.add_argument("--approve-kind")
    review.add_argument("--reason", default="")

    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("--batch", required=True)
    apply_cmd.add_argument("--confirm-db-path")
    apply_cmd.add_argument("--confirm-batch")
    apply_cmd.add_argument("--confirm-backup")

    sub.add_parser("repair-text")

    quality = sub.add_parser("quality")
    target = quality.add_mutually_exclusive_group(required=True)
    target.add_argument("--batch")
    target.add_argument("--graph", action="store_true")
    quality.add_argument("--profile", choices=("partial", "full"), default="full")
    quality.add_argument("--llm", action="store_true")

    rebuild_safe = sub.add_parser("rebuild-safe")
    rebuild_mode = rebuild_safe.add_mutually_exclusive_group(required=True)
    rebuild_mode.add_argument("--dry-run", action="store_true")
    rebuild_mode.add_argument("--execute", action="store_true")
    rebuild_safe.add_argument("--include-llm", action="store_true")
    rebuild_safe.add_argument("--confirm-db-path")
    rebuild_safe.add_argument("--backup-dir", default="data/backups")
    rebuild_safe.add_argument("--limit", type=int)
    rebuild_safe.add_argument("--doc-category", action="append", dest="doc_categories")
    rebuild_safe.add_argument("--output-json", default="data/rebuild_safe_dry_run_report.json")
    rebuild_safe.add_argument("--output-md", default="data/rebuild_safe_dry_run_report.md")

    audit = sub.add_parser("audit")
    audit.add_argument("--output-json", default="data/graph_audit_report.json")
    audit.add_argument("--output-md", default="data/graph_audit_report.md")

    cleanup = sub.add_parser("cleanup-stale-links")
    cleanup.add_argument("--dry-run", action="store_true")

    export_man = sub.add_parser("export-manual")
    export_man.add_argument("--output", default="data/manual_graph_facts.json")

    recover = sub.add_parser(
        "recover-relations",
        help="Two-phase salvage of endpoint entities + legal relations from rejected LLM candidates",
    )
    recover.add_argument("--source-batch", action="append", dest="source_batches", required=True)
    recover.add_argument(
        "--relation-type",
        action="append",
        dest="relation_types",
        help="Default: has_step, has_procedure, runs_command",
    )
    recover.add_argument("--entity-min-conf", type=float, default=0.80)
    recover.add_argument("--rel-min-conf", type=float, default=0.80)
    recover.add_argument(
        "--include-possible-duplicate",
        action="store_true",
        help="Lift possible_duplicate diagnostic entities (never type_conflict)",
    )
    recover.add_argument("--output-json", default="data/recover_relations_plan.json")
    recover.add_argument(
        "--stage",
        action="store_true",
        help="Write approved recovery batch (then run apply with confirm trio); default is dry-run",
    )
    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None, *, db: RelationalDB | None = None, chunk_source=None) -> int:
    args = build_parser().parse_args(argv)
    db = db or RelationalDB()

    if args.command == "extract":
        builder = GraphBuilder(db=db, chunk_source=chunk_source)
        if args.resume_batch:
            result = builder.resume_batch(
                args.resume_batch,
                include_llm=True if args.include_llm else None,
            )
        elif args.chunk_ids:
            result = builder.build_incremental(args.chunk_ids, include_llm=args.include_llm)
        else:
            result = builder.build_full(
                args.force_rebuild, args.limit, args.doc_categories, include_llm=args.include_llm
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
        batch = db.get_extraction_batch(args.batch) or {}
        if args.summary:
            _print({"batch_id": args.batch, "summary": _review_summary(pending)})
            return 0
        explicit_ids = False
        if args.approve_all:
            allowed, reason = approve_all_allowed(batch, pending)
            if not allowed:
                raise ValueError(reason)
            ids = [item["id"] for item in pending]
            status = "approved"
        elif args.approve:
            ids, status = args.approve, "approved"
            explicit_ids = True
        elif args.reject:
            ids, status = args.reject, "rejected"
        elif any(getattr(args, name) is not None for name in ("approve_type", "approve_relation_type", "approve_confidence_above", "reject_confidence_below", "approve_source", "approve_kind")):
            selected = _filter_review_candidates(pending, args)
            ids, status = [item["id"] for item in selected], "approved"
        else:
            raise ValueError("review requires an action or --summary")
        requested_ids = list(ids)
        review_summary = summarize_review_selection(
            requested_ids,
            pending,
            batch=batch,
            status=status,
            approve_kind=getattr(args, "approve_kind", None),
            explicit_ids=explicit_ids,
        )
        ids = review_summary["ids_to_update"]
        updated = db.review_extraction_candidates(args.batch, ids, status, args.reason)
        remaining = db.list_extraction_candidates(args.batch, "pending")
        if not remaining:
            approved = db.list_extraction_candidates(args.batch, "approved")
            db.set_extraction_batch_status(args.batch, "approved" if approved else "rejected")
        _print({
            "requested": review_summary["requested"],
            "selected": review_summary["selected"],
            "rejected_by_safety": review_summary["rejected_by_safety"],
            "updated": updated,
            "missing_or_not_pending": review_summary["missing_or_not_pending"],
            "status": status,
            "remaining_pending": len(remaining),
        })
        return 0

    if args.command == "apply":
        db_path = resolve_db_path()
        assert_write_confirmation(
            db_path=db_path,
            confirm_db_path=getattr(args, "confirm_db_path", None),
            confirm_batch=getattr(args, "confirm_batch", None),
            batch_id=args.batch,
            confirm_backup=getattr(args, "confirm_backup", None),
            require_backup=True,
        )
        audit = GraphCandidateApplier(db).apply(
            args.batch,
            operator="cli",
            backup_path=getattr(args, "confirm_backup", None) or "",
        )
        _print({"batch_id": args.batch, "status": "applied", "audit": audit})
        return 0

    if args.command == "repair-text":
        _print(GraphTextMigration(db).apply())
        return 0

    if args.command == "audit":
        from rag_knowledge.services.graph_audit import GraphAuditService
        service = GraphAuditService(db)
        report = service.generate_reports(args.output_json, args.output_md)
        _print({
            "status": "completed",
            "total_entities": report["total_entities"],
            "total_relations": report["total_relations"],
            "stale_links": report["stale_link_count"],
            "orphan_entities": report["orphan_entity_count"],
            "output_json": args.output_json,
            "output_md": args.output_md
        })
        return 0

    if args.command == "cleanup-stale-links":
        from rag_knowledge.services.graph_cleanup import GraphCleanupService
        service = GraphCleanupService(db)
        result = service.cleanup_stale_links(args.dry_run)
        _print(result)
        return 0

    if args.command == "export-manual":
        from rag_knowledge.services.graph_manual_export import GraphManualFactExporter
        exporter = GraphManualFactExporter(db)
        summary = exporter.export_manual(args.output)
        _print({
            "status": "completed",
            "output": args.output,
            "summary": summary
        })
        return 0

    if args.command == "recover-relations":
        from pathlib import Path

        from rag_knowledge.services.relation_recovery import RelationRecoveryService

        service = RelationRecoveryService(db)
        plan = service.plan(
            source_batches=list(args.source_batches),
            relation_types=list(args.relation_types) if args.relation_types else None,
            entity_min_conf=float(args.entity_min_conf),
            rel_min_conf=float(args.rel_min_conf),
            include_possible_duplicate=bool(args.include_possible_duplicate),
        )
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(plan.summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if not args.stage:
            _print(
                {
                    "status": "dry_run",
                    "output_json": str(out_path),
                    "entity_count": plan.summary.get("entity_count"),
                    "rel_count": plan.summary.get("rel_count"),
                    "skip": plan.summary.get("skip"),
                    "rel_by_type": plan.summary.get("rel_by_type"),
                }
            )
            return 0
        if not plan.items:
            _print({"status": "empty", "output_json": str(out_path), "message": "nothing to stage"})
            return 1
        batch_id = service.stage(plan)
        pre = GraphQualityService(db).inspect_batch(batch_id)
        _print(
            {
                "status": "staged",
                "batch_id": batch_id,
                "batch_status": "approved",
                "output_json": str(out_path),
                "entity_count": plan.summary.get("entity_count"),
                "rel_count": plan.summary.get("rel_count"),
                "preflight_ok": pre.ok,
                "preflight_errors": pre.errors,
                "next": (
                    "run_graph_build.py apply --batch "
                    f"{batch_id} --confirm-db-path <db> --confirm-batch {batch_id} "
                    "--confirm-backup <backup>"
                ),
            }
        )
        return 0 if pre.ok else 1

    if args.command == "quality":
        quality = GraphQualityService(db)
        report = quality.inspect_graph(profile=args.profile) if args.graph else quality.inspect_llm_batch(args.batch) if args.llm else quality.inspect_batch(args.batch)
        _print({"ok": report.ok, "errors": report.errors, "warnings": report.warnings, "stats": report.stats})
        return 0 if report.ok else 1

    if args.command == "rebuild-safe":
        if args.execute:
            service = SafeRebuildService(db=db, chunk_source=chunk_source)
            report = service.run(
                output_json=args.output_json,
                output_md=args.output_md,
                include_llm=args.include_llm,
                confirm_db_path=args.confirm_db_path,
                backup_dir=args.backup_dir,
                limit=args.limit,
                doc_categories=args.doc_categories,
            )
            _print({
                "status": "completed",
                "dry_run": False,
                "batch_id": (report.get("extract") or {}).get("batch_id"),
                "backup_path": report.get("backup_path"),
                "report": report,
            })
            return 0
        service = SafeRebuildDryRunService(db)
        report = service.run(args.output_json, args.output_md)
        _print({"status": "completed", "dry_run": True, "report": report})
        return 0


def _filter_review_candidates(pending: list[dict], args) -> list[dict]:
    selected = []
    for item in pending:
        payload = item["payload"]
        if getattr(args, "approve_kind", None) and item["candidate_kind"] != args.approve_kind:
            continue
        if args.approve_type and payload.get("entity_type") != args.approve_type:
            continue
        if args.approve_relation_type and payload.get("relation_type") != args.approve_relation_type:
            continue
        confidence = float(payload.get("confidence", 0))
        if args.approve_confidence_above is not None and confidence < args.approve_confidence_above:
            continue
        if args.reject_confidence_below is not None and confidence >= args.reject_confidence_below:
            continue
        if args.approve_source and args.approve_source not in str(payload.get("source", payload.get("source_chunk_id", ""))):
            continue
        selected.append(item)
    return selected


def _review_summary(pending: list[dict]) -> dict:
    by_kind = {kind: sum(item["candidate_kind"] == kind for item in pending) for kind in {item["candidate_kind"] for item in pending}}
    return {
        "pending": len(pending),
        "by_kind": by_kind,
        "alias_pending": by_kind.get("alias", 0),
        "by_entity_type": {kind: sum(item["payload"].get("entity_type") == kind for item in pending) for kind in {item["payload"].get("entity_type") for item in pending if item["payload"].get("entity_type")} },
    }


if __name__ == "__main__":
    raise SystemExit(main())
