"""Safe rebuild preview and formal replacement of automatic graph facts."""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from rag_knowledge.services.graph_audit import GraphAuditService
from rag_knowledge.services.graph_cleanup import GraphCleanupService
from rag_knowledge.services.graph_governance import (
    assert_write_confirmation,
    is_safe_review_candidate,
    resolve_db_path,
    summarize_review_selection,
)
from rag_knowledge.services.graph_manual_export import GraphManualFactExporter
from rag_knowledge.services.graph_extraction import GraphBuilder, GraphCandidateApplier
from rag_knowledge.services.backbone_guard import (
    CONFLICT_REASON,
    load_backbone_constraints,
    relation_conflicts_with_backbone,
)
from rag_knowledge.services.ollama_health import assert_ollama_reachable

# Re-export for callers/tests that import from safe_rebuild.
__all__ = [
    "CONFLICT_REASON",
    "SafeRebuildDryRunService",
    "SafeRebuildService",
    "classify_sources",
    "is_preserved_creator",
    "is_replaceable_creator",
    "load_backbone_constraints",
    "relation_conflicts_with_backbone",
]

PRESERVED_CREATORS = frozenset({
    "admin",
    "manual",
    "seed",
    "rule:special",
    "rule:special_relations",
    "rule:profile_sync",
})

ENTITY_APPROVE_CONFIDENCE = 0.88
ALIAS_APPROVE_CONFIDENCE = 0.90
RELATION_APPROVE_CONFIDENCE = 0.80


def _candidate_confidence(payload: dict) -> float:
    """Rule extractors often omit confidence; treat missing/None as 1.0."""
    raw = payload.get("confidence")
    if raw is None:
        props = payload.get("properties")
        if isinstance(props, dict):
            raw = props.get("confidence")
    if raw is None:
        return 1.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def is_preserved_creator(created_by: str) -> bool:
    value = str(created_by or "")
    return value in PRESERVED_CREATORS or value.startswith("seed:")


def is_replaceable_creator(created_by: str) -> bool:
    value = str(created_by or "")
    if is_preserved_creator(value):
        return False
    return value.startswith("rule:") or value.startswith("llm:")


def _sum_source_counts(entity_sources: dict[str, int], relation_sources: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for key, value in entity_sources.items():
        merged[key] = merged.get(key, 0) + value
    for key, value in relation_sources.items():
        merged[key] = merged.get(key, 0) + value
    return merged


def classify_sources(entity_sources: dict[str, int], relation_sources: dict[str, int]) -> tuple[dict[str, int], dict[str, int]]:
    merged = _sum_source_counts(entity_sources, relation_sources)
    preserved: dict[str, int] = {}
    superseded: dict[str, int] = {}
    for key, count in merged.items():
        if is_preserved_creator(key):
            preserved[key] = count
        elif is_replaceable_creator(key):
            superseded[key] = count
    return preserved, superseded


def check_backbone_integrity(db, constraints: dict) -> dict:
    missing = []
    present = 0
    with db._get_conn() as conn:
        for edge in constraints.get("relations") or []:
            row = conn.execute(
                """
                SELECT r.id
                FROM relations r
                JOIN entities s ON r.source_entity_id = s.id
                JOIN entities t ON r.target_entity_id = t.id
                WHERE s.name = ? AND t.name = ? AND r.relation_type = ?
                LIMIT 1
                """,
                (edge["source"], edge["target"], edge["relation_type"]),
            ).fetchone()
            if row:
                present += 1
            else:
                missing.append(edge)
    total = present + len(missing)
    return {
        "total": total,
        "present": present,
        "missing": missing,
        "complete": not missing and total > 0,
    }


class SafeRebuildDryRunService:
    def __init__(self, db):
        self.db = db

    def run(self, output_json: str, output_md: str) -> dict:
        audit = GraphAuditService(self.db).audit()
        with self.db._get_conn() as conn:
            rows = conn.execute("SELECT created_by, COUNT(*) AS count FROM entities GROUP BY created_by").fetchall()
            entity_sources = {str(row["created_by"]): int(row["count"]) for row in rows}
            rows = conn.execute("SELECT created_by, COUNT(*) AS count FROM relations GROUP BY created_by").fetchall()
            relation_sources = {str(row["created_by"]): int(row["count"]) for row in rows}
            pending = conn.execute("SELECT COUNT(*) FROM extraction_candidates WHERE status = 'pending'").fetchone()[0]
        preserved, superseded = classify_sources(entity_sources, relation_sources)
        constraints = load_backbone_constraints()
        backbone = check_backbone_integrity(self.db, constraints)
        report = {
            "dry_run": True,
            "formal_graph_modified": False,
            "manual_fact_preserved": True,
            "audit_before": audit,
            "preserved_by_source": preserved,
            "superseded_by_source": superseded,
            "backbone_integrity": backbone,
            "candidate_preview": {"pending_candidates": int(pending)},
            "before_after": {
                "entities_before": audit["total_entities"],
                "relations_before": audit["total_relations"],
                "candidates_added": 0,
            },
        }
        _write_report(output_json, output_md, report, title="Safe Rebuild Dry-run Report")
        return report


class SafeRebuildService:
    """Formal rebuild: backup → preserve protected facts → replace auto facts → extract/review/apply."""

    def __init__(self, db, chunk_source=None):
        self.db = db
        self._chunk_source = chunk_source

    def run(
        self,
        *,
        output_json: str,
        output_md: str,
        include_llm: bool = False,
        confirm_db_path: str | None = None,
        backup_dir: str = "data/backups",
        manual_export_path: str = "data/manual_graph_facts_pre_round3.json",
        limit: int | None = None,
        doc_categories: list[str] | None = None,
        apply_approved: bool = True,
    ) -> dict:
        db_path = resolve_db_path()
        assert_write_confirmation(
            db_path=db_path,
            confirm_db_path=confirm_db_path,
            require_backup=False,
        )

        # Fail before superseding automatic facts if LLM is required but Ollama is down.
        if include_llm:
            assert_ollama_reachable()

        # Phase A — backup
        backup_path = self._backup_db(backup_dir)

        # Phase B — export protected facts
        export_summary = GraphManualFactExporter(self.db).export_manual(manual_export_path)

        # Phase C — audit before
        audit_before = GraphAuditService(self.db).audit()
        constraints = load_backbone_constraints()
        backbone_before = check_backbone_integrity(self.db, constraints)
        if constraints.get("relations") and not backbone_before["complete"]:
            raise ValueError(
                "product_relation_backbone edges missing before rebuild: "
                + json.dumps(backbone_before["missing"][:10], ensure_ascii=False)
            )

        # Phase D — supersede replaceable automatic facts
        superseded = self._supersede_automatic_facts()

        # Phase E — full extract
        builder = GraphBuilder(db=self.db, chunk_source=self._chunk_source)
        build_result = builder.build_full(
            force_rebuild=True,
            limit=limit,
            doc_categories=doc_categories,
            include_llm=include_llm,
        )
        batch_id = build_result.batch_id

        # Phase F — split review + backbone conflict reject
        review_stats = self._split_review(batch_id, constraints)

        apply_audit = None
        if apply_approved:
            pending = self.db.list_extraction_candidates(batch_id, "pending")
            if pending:
                deferred_ids = [item["id"] for item in pending]
                self.db.review_extraction_candidates(
                    batch_id, deferred_ids, "rejected", "round3_deferred_pending"
                )
                review_stats["deferred_rejected"] = len(deferred_ids)
            approved = self.db.list_extraction_candidates(batch_id, "approved")
            if approved:
                self.db.set_extraction_batch_status(batch_id, "approved")
                apply_audit = GraphCandidateApplier(self.db).apply(
                    batch_id,
                    operator="safe_rebuild",
                    backup_path=str(backup_path),
                )
            else:
                review_stats["apply_skipped"] = "no_approved_candidates"

        # Phase I — cleanup stale links
        cleanup = GraphCleanupService(self.db).cleanup_stale_links(dry_run=False)

        # Phase J — audit after + backbone check
        audit_after = GraphAuditService(self.db).audit()
        backbone_after = check_backbone_integrity(self.db, constraints)
        if constraints.get("relations") and not backbone_after["complete"]:
            raise ValueError(
                "product_relation_backbone edges missing after rebuild; restore from "
                f"{backup_path}: {json.dumps(backbone_after['missing'][:10], ensure_ascii=False)}"
            )

        report = {
            "dry_run": False,
            "formal_graph_modified": True,
            "manual_fact_preserved": True,
            "backup_path": str(backup_path),
            "manual_export_path": manual_export_path,
            "manual_export_summary": export_summary,
            "audit_before": audit_before,
            "audit_after": audit_after,
            "backbone_before": backbone_before,
            "backbone_after": backbone_after,
            "superseded": superseded,
            "extract": {"batch_id": batch_id, "stats": build_result.stats},
            "review": review_stats,
            "apply": apply_audit,
            "cleanup": cleanup,
            "before_after": {
                "entities_before": audit_before["total_entities"],
                "relations_before": audit_before["total_relations"],
                "entities_after": audit_after["total_entities"],
                "relations_after": audit_after["total_relations"],
                "section_ratio_before": audit_before.get("section_ratio"),
                "section_ratio_after": audit_after.get("section_ratio"),
                "business_entity_ratio_before": audit_before.get("business_entity_ratio"),
                "business_entity_ratio_after": audit_after.get("business_entity_ratio"),
            },
        }
        _write_report(output_json, output_md, report, title="Safe Rebuild Execute Report")
        return report

    def _backup_db(self, backup_dir: str) -> Path:
        db_path = Path(self.db._db_path).resolve()
        out_dir = Path(backup_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = out_dir / f"rag_relational_pre_round3_{stamp}.db"
        shutil.copy2(db_path, backup_path)
        return backup_path.resolve()

    def _supersede_automatic_facts(self) -> dict:
        with self.db._get_conn() as conn:
            entity_rows = conn.execute("SELECT id, created_by FROM entities").fetchall()
            relation_rows = conn.execute(
                """
                SELECT r.id, r.created_by, r.source_entity_id, r.target_entity_id
                FROM relations r
                """
            ).fetchall()

            preserved_entity_ids = {
                str(row["id"]) for row in entity_rows if is_preserved_creator(row["created_by"])
            }
            # Endpoints of preserved relations must stay even if their own creator is replaceable.
            for row in relation_rows:
                if is_preserved_creator(row["created_by"]):
                    preserved_entity_ids.add(str(row["source_entity_id"]))
                    preserved_entity_ids.add(str(row["target_entity_id"]))

            replaceable_relation_ids = [
                str(row["id"]) for row in relation_rows if is_replaceable_creator(row["created_by"])
            ]
            replaceable_entity_ids = [
                str(row["id"])
                for row in entity_rows
                if is_replaceable_creator(row["created_by"]) and str(row["id"]) not in preserved_entity_ids
            ]

            deleted_relations = 0
            for relation_id in replaceable_relation_ids:
                cur = conn.execute("DELETE FROM relations WHERE id = ?", (relation_id,))
                deleted_relations += cur.rowcount

            deleted_entities = 0
            for entity_id in replaceable_entity_ids:
                cur = conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
                deleted_entities += cur.rowcount

        return {
            "deleted_relations": deleted_relations,
            "deleted_entities": deleted_entities,
            "preserved_entity_anchors": len(preserved_entity_ids),
        }

    def _split_review(self, batch_id: str, constraints: dict) -> dict:
        pending = self.db.list_extraction_candidates(batch_id, "pending")
        batch = self.db.get_extraction_batch(batch_id) or {}

        conflict_ids = []
        for item in pending:
            if item["candidate_kind"] != "relation":
                continue
            if relation_conflicts_with_backbone(item["payload"], constraints):
                conflict_ids.append(item["id"])
        rejected_conflicts = 0
        if conflict_ids:
            rejected_conflicts = self.db.review_extraction_candidates(
                batch_id, conflict_ids, "rejected", CONFLICT_REASON
            )

        approved_total = 0
        known_entities = {
            str(row["name"])
            for row in self.db.list_entities()
            if row.get("name")
        }
        passes = [
            ("entity", ENTITY_APPROVE_CONFIDENCE, None),
            ("alias", ALIAS_APPROVE_CONFIDENCE, None),
            ("relation", RELATION_APPROVE_CONFIDENCE, None),
            ("field", RELATION_APPROVE_CONFIDENCE, None),
            ("link", RELATION_APPROVE_CONFIDENCE, None),
        ]
        for kind, threshold, _ in passes:
            pending = self.db.list_extraction_candidates(batch_id, "pending")
            selected = []
            for item in pending:
                if item["candidate_kind"] != kind:
                    continue
                confidence = _candidate_confidence(item["payload"])
                if confidence < threshold:
                    continue
                if not is_safe_review_candidate(item, batch=batch, approve_kind=kind):
                    continue
                payload = item["payload"]
                if kind == "relation":
                    if relation_conflicts_with_backbone(payload, constraints):
                        continue
                    source = str(payload.get("source_name") or "").strip()
                    target = str(payload.get("target_name") or "").strip()
                    if source not in known_entities or target not in known_entities:
                        continue
                elif kind == "alias":
                    if str(payload.get("entity_name") or "").strip() not in known_entities:
                        continue
                elif kind == "link":
                    if str(payload.get("entity_name") or "").strip() not in known_entities:
                        continue
                elif kind == "field":
                    # field payloads may use table/entity name fields depending on extractor
                    owner = str(
                        payload.get("entity_name")
                        or payload.get("table_name")
                        or payload.get("parent_name")
                        or ""
                    ).strip()
                    if owner and owner not in known_entities:
                        continue
                selected.append(item)
            if not selected:
                continue
            ids = [item["id"] for item in selected]
            summary = summarize_review_selection(
                ids, pending, batch=batch, status="approved", approve_kind=kind, explicit_ids=False
            )
            updated = self.db.review_extraction_candidates(
                batch_id, summary["ids_to_update"], "approved", f"safe_rebuild:{kind}>={threshold}"
            )
            approved_total += updated
            if kind == "entity":
                approved_ids = set(summary["ids_to_update"])
                for item in selected:
                    if item["id"] not in approved_ids:
                        continue
                    name = str(item["payload"].get("name") or "").strip()
                    if name:
                        known_entities.add(name)

        remaining_pending = len(self.db.list_extraction_candidates(batch_id, "pending"))
        return {
            "approved": approved_total,
            "rejected_backbone_conflicts": rejected_conflicts,
            "remaining_pending_before_defer": remaining_pending,
        }


def _write_report(output_json: str, output_md: str, report: dict, *, title: str) -> None:
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(output_json).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    Path(output_md).parent.mkdir(parents=True, exist_ok=True)
    before = report.get("before_after") or {}
    lines = [
        f"# {title}",
        "",
        f"- Formal graph modified: `{not report.get('dry_run', True)}`",
        f"- Manual facts preserved: `{report.get('manual_fact_preserved')}`",
        f"- Backup: `{report.get('backup_path', '')}`",
        f"- Entities before/after: `{before.get('entities_before')}` → `{before.get('entities_after', before.get('entities_before'))}`",
        f"- Relations before/after: `{before.get('relations_before')}` → `{before.get('relations_after', before.get('relations_before'))}`",
        "",
    ]
    if report.get("dry_run"):
        lines.append("## Preserved sources")
        lines.extend(f"- `{key}`: {value}" for key, value in (report.get("preserved_by_source") or {}).items())
        lines.extend(["", "## Superseded automatic sources"])
        lines.extend(f"- `{key}`: {value}" for key, value in (report.get("superseded_by_source") or {}).items())
        backbone = report.get("backbone_integrity") or {}
        lines.extend([
            "",
            "## Backbone integrity",
            f"- present/total: `{backbone.get('present')}/{backbone.get('total')}`",
            f"- complete: `{backbone.get('complete')}`",
        ])
    else:
        lines.append("## Supersede")
        for key, value in (report.get("superseded") or {}).items():
            lines.append(f"- `{key}`: {value}")
        extract = report.get("extract") or {}
        lines.extend(["", "## Extract", f"- batch_id: `{extract.get('batch_id')}`"])
        review = report.get("review") or {}
        lines.extend(["", "## Review"])
        for key, value in review.items():
            lines.append(f"- `{key}`: {value}")
        backbone = report.get("backbone_after") or {}
        lines.extend([
            "",
            "## Backbone integrity after",
            f"- present/total: `{backbone.get('present')}/{backbone.get('total')}`",
            f"- complete: `{backbone.get('complete')}`",
        ])
    Path(output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
