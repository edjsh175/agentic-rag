"""Graph staging, review, and apply safety gates for production writes."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rag_knowledge.config import Config

PROFILE_ALIAS_SOURCE_FIELDS = frozenset({"entity_aliases", "section_families"})

APPROVE_ALL_FORBIDDEN_MODES = frozenset({
    "profile_sync",
    "domain_catalog_seed",
    "product_backbone_seed",
    "manual_import",
})

RULE_BATCH_MODES = frozenset({"full", "incremental"})


@dataclass(frozen=True)
class ApplyAuditRecord:
    batch_id: str
    mode: str
    operator: str
    started_at: str
    counts_before: dict
    counts_after: dict
    candidate_summary: dict
    backup_path: str = ""

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "mode": self.mode,
            "operator": self.operator,
            "started_at": self.started_at,
            "counts_before": self.counts_before,
            "counts_after": self.counts_after,
            "candidate_summary": self.candidate_summary,
            "backup_path": self.backup_path,
        }


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def live_relational_db_path() -> Path:
    return (project_root() / "data" / "rag_relational.db").resolve()


def resolve_db_path(db_path: Path | str | None = None) -> Path:
    if db_path is not None:
        return Path(db_path).resolve()
    return Path(Config().relational_db_path).resolve()


def is_production_relational_db(db_path: Path | str | None = None) -> bool:
    if os.getenv("PYTEST_CURRENT_TEST") and os.getenv("ALLOW_LIVE_STORAGE_IN_TESTS") != "1":
        return False
    return resolve_db_path(db_path) == live_relational_db_path()


def assert_staging_review_status(review_status: str, *, db_path: Path | str | None = None) -> None:
    if review_status != "approved":
        return
    if is_production_relational_db(db_path):
        raise ValueError(
            "production database only allows review_status=pending for staging; "
            "use review commands to approve candidates explicitly"
        )


def assert_write_confirmation(
    *,
    db_path: Path | str,
    confirm_db_path: str | None,
    confirm_batch: str | None = None,
    batch_id: str | None = None,
    confirm_backup: str | None = None,
    require_backup: bool = False,
) -> None:
    if not is_production_relational_db(db_path):
        return
    actual = str(resolve_db_path(db_path))
    if not confirm_db_path:
        raise ValueError(f"production write requires --confirm-db-path {actual}")
    if str(Path(confirm_db_path).resolve()) != actual:
        raise ValueError(f"--confirm-db-path must match database path: {actual}")
    if batch_id is not None:
        if not confirm_batch:
            raise ValueError(f"production write requires --confirm-batch {batch_id}")
        if confirm_batch != batch_id:
            raise ValueError(f"--confirm-batch must match batch id: {batch_id}")
    if require_backup and not confirm_backup:
        raise ValueError("production apply requires --confirm-backup <backup path>")
    if confirm_backup and not Path(confirm_backup).is_file():
        raise ValueError(f"backup file not found: {confirm_backup}")


def batch_has_llm_candidates(pending: list[dict], batch: dict | None) -> bool:
    filters = (batch or {}).get("filters") or {}
    if isinstance(filters, str):
        try:
            filters = json.loads(filters)
        except json.JSONDecodeError:
            filters = {}
    if filters.get("include_llm"):
        return True
    return any(
        item["payload"].get("created_by") == "llm:schema_extractor"
        for item in pending
    )


def approve_all_allowed(batch: dict | None, pending: list[dict]) -> tuple[bool, str]:
    mode = (batch or {}).get("mode") or ""
    if mode in APPROVE_ALL_FORBIDDEN_MODES:
        return False, f"approve-all is forbidden for batch mode: {mode}"
    if batch_has_llm_candidates(pending, batch):
        return False, "approve-all is forbidden for LLM extraction batches"
    if mode not in RULE_BATCH_MODES:
        return False, f"approve-all is not supported for batch mode: {mode}"
    if any(item["candidate_kind"] == "diagnostic" for item in pending):
        return False, "approve-all requires no pending diagnostics"
    if any(item["candidate_kind"] == "alias" for item in pending):
        return False, "approve-all cannot bulk-approve alias candidates"
    if not pending:
        return True, ""
    if not all(is_safe_review_candidate(item, batch=batch) for item in pending):
        return False, "approve-all requires all pending candidates to pass safety checks"
    return True, ""


def is_safe_review_candidate(
    item: dict,
    *,
    batch: dict | None = None,
    approve_kind: str | None = None,
    explicit_id: bool = False,
) -> bool:
    if item["candidate_kind"] == "diagnostic":
        return False
    if item["candidate_kind"] == "alias":
        if not explicit_id and approve_kind != "alias":
            return False
        batch = batch or {}
        if batch.get("mode") == "product_backbone_seed":
            return _safe_product_backbone_alias_candidate(item, batch)
        if batch.get("mode") == "domain_catalog_seed":
            return _safe_domain_catalog_alias_candidate(item, batch)
        return _safe_profile_sync_alias_candidate(item, batch)
    payload = item["payload"]
    evidence_text = str(payload.get("evidence_text") or item.get("evidence_text") or "")
    if not evidence_text and not payload.get("evidences"):
        return False
    if payload.get("resolution_action") in {"alias", "reuse", "diagnostic", "bind", "alias_of", "conflict", "uncertain"}:
        return False
    from rag_knowledge.services.backbone_guard import describe_conflict, load_backbone_constraints
    bb_constraints = load_backbone_constraints()
    if describe_conflict(item["candidate_kind"], payload, bb_constraints):
        return False
    return True


def _safe_profile_sync_alias_candidate(item: dict, batch: dict) -> bool:
    if batch.get("mode") != "profile_sync":
        return False
    payload = item["payload"]
    metadata = payload.get("metadata") or {}
    if not metadata.get("profile_id"):
        return False
    if metadata.get("source_field") not in PROFILE_ALIAS_SOURCE_FIELDS:
        return False
    evidence = str(payload.get("evidence_text") or "")
    return evidence.startswith("profile:")


def _safe_product_backbone_alias_candidate(item: dict, batch: dict) -> bool:
    if batch.get("mode") != "product_backbone_seed":
        return False
    payload = item["payload"]
    if payload.get("created_by") != "seed:product_backbone":
        return False
    evidence = str(payload.get("evidence_text") or item.get("evidence_text") or "")
    return evidence.startswith("product_backbone:")


def _safe_domain_catalog_alias_candidate(item: dict, batch: dict) -> bool:
    if batch.get("mode") != "domain_catalog_seed":
        return False
    payload = item["payload"]
    if payload.get("created_by") != "seed:domain_catalog":
        return False
    evidence = str(payload.get("evidence_text") or item.get("evidence_text") or "")
    return evidence.startswith("domain_catalog:")


def filter_approvable_candidate_ids(
    candidate_ids: list[str],
    pending: list[dict],
    *,
    batch: dict | None = None,
    approve_kind: str | None = None,
    explicit_ids: bool = False,
) -> tuple[list[str], list[str]]:
    pending_by_id = {item["id"]: item for item in pending}
    approved: list[str] = []
    rejected: list[str] = []
    for candidate_id in candidate_ids:
        item = pending_by_id.get(candidate_id)
        if item is None:
            approved.append(candidate_id)
            continue
        if is_safe_review_candidate(
            item,
            batch=batch,
            approve_kind=approve_kind,
            explicit_id=explicit_ids,
        ):
            approved.append(candidate_id)
        else:
            rejected.append(candidate_id)
    return approved, rejected


def graph_counts(db) -> dict:
    with db._get_conn() as conn:
        entities = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        relations = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        aliases = conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
        links = conn.execute("SELECT COUNT(*) FROM entity_chunk_links").fetchone()[0]
    return {
        "entities": int(entities),
        "relations": int(relations),
        "aliases": int(aliases),
        "entity_chunk_links": int(links),
    }


def candidate_summary(candidates: list[dict]) -> dict:
    summary: dict[str, int] = {}
    for item in candidates:
        key = item["candidate_kind"]
        summary[key] = summary.get(key, 0) + 1
    return summary


def append_apply_audit(record: ApplyAuditRecord) -> Path:
    audit_path = Path(Config().data_dir) / "graph_apply_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    return audit_path


def summarize_review_selection(
    requested_ids: list[str],
    pending: list[dict],
    *,
    batch: dict | None = None,
    status: str,
    approve_kind: str | None = None,
    explicit_ids: bool = False,
) -> dict:
    pending_by_id = {item["id"]: item for item in pending}
    found_pending_ids = [candidate_id for candidate_id in requested_ids if candidate_id in pending_by_id]
    missing_or_not_pending = len(requested_ids) - len(found_pending_ids)
    if status != "approved":
        return {
            "requested": len(requested_ids),
            "selected": len(found_pending_ids),
            "rejected_by_safety": 0,
            "ids_to_update": found_pending_ids,
            "missing_or_not_pending": missing_or_not_pending,
        }
    safe_ids, unsafe_ids = filter_approvable_candidate_ids(
        found_pending_ids,
        pending,
        batch=batch,
        approve_kind=approve_kind,
        explicit_ids=explicit_ids,
    )
    return {
        "requested": len(requested_ids),
        "selected": len(found_pending_ids),
        "rejected_by_safety": len(unsafe_ids),
        "ids_to_update": safe_ids,
        "missing_or_not_pending": missing_or_not_pending,
    }


def assert_production_apply_allowed() -> None:
    if is_production_relational_db():
        raise ValueError("production graph apply is CLI-only; use run_graph_build.py apply with confirmation flags")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def cascade_rejected_endpoint_relations(db, batch_id: str) -> int:
    """Cascade reject relation candidates whose endpoints are missing from approved entities and DB."""
    with db._get_conn() as conn:
        all_candidates = db.list_extraction_candidates(batch_id)
        approved_entities = {
            c["payload"].get("name") for c in all_candidates
            if c["candidate_kind"] == "entity" and c["status"] == "approved"
        }
        existing_entities = {
            row["name"] for row in conn.execute("SELECT name FROM entities").fetchall()
        }
        all_valid_entities = approved_entities | existing_entities

        invalid_relation_ids = []
        for c in all_candidates:
            if c["candidate_kind"] == "relation" and c["status"] == "approved":
                payload = c["payload"] or {}
                src = payload.get("source_name")
                tgt = payload.get("target_name")
                if src not in all_valid_entities or tgt not in all_valid_entities:
                    invalid_relation_ids.append(c["id"])

        if invalid_relation_ids:
            placeholders = ", ".join("?" for _ in invalid_relation_ids)
            conn.execute(
                f"UPDATE extraction_candidates SET status = 'rejected', "
                f"rejection_reason = 'missing relation endpoint (auto-cascade)', "
                f"reviewed_at = ? WHERE batch_id = ? AND id IN ({placeholders})",
                [db._now(), batch_id] + invalid_relation_ids
            )
        return len(invalid_relation_ids)
