"""Read-only safe rebuild preview."""
from __future__ import annotations

import json
from pathlib import Path

from rag_knowledge.services.graph_audit import GraphAuditService


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
        preserved_keys = ("admin", "manual", "seed", "rule:special", "rule:special_relations")
        preserved = {key: entity_sources.get(key, 0) + relation_sources.get(key, 0) for key in preserved_keys}
        auto_sources = {
            key: value for key, value in {**entity_sources, **relation_sources}.items()
            if key.startswith("rule:") or key.startswith("llm:")
        }
        report = {
            "dry_run": True,
            "formal_graph_modified": False,
            "manual_fact_preserved": True,
            "audit_before": audit,
            "preserved_by_source": preserved,
            "superseded_by_source": auto_sources,
            "candidate_preview": {"pending_candidates": int(pending)},
            "before_after": {"entities_before": audit["total_entities"], "relations_before": audit["total_relations"], "candidates_added": 0},
        }
        Path(output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(output_json).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        Path(output_md).parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Safe Rebuild Dry-run Report",
            "",
            "- Formal graph modified: `false`",
            f"- Manual facts preserved: `{report['manual_fact_preserved']}`",
            f"- Entities before: `{audit['total_entities']}`",
            f"- Relations before: `{audit['total_relations']}`",
            f"- Pending candidate preview: `{pending}`",
            "",
            "## Preserved sources",
        ]
        lines.extend(f"- `{key}`: {value}" for key, value in preserved.items())
        lines.extend(["", "## Superseded automatic sources"])
        lines.extend(f"- `{key}`: {value}" for key, value in auto_sources.items())
        Path(output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report
