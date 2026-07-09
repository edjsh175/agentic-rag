from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import logging

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB

logger = logging.getLogger(__name__)

BUSINESS_ENTITY_TYPES = {
    "Product", "Tool", "Service", "Module", "DataTable", "Field",
    "ConfigItem", "Format", "Procedure", "Step", "Error", "Solution",
    "EnvironmentComponent", "Command"
}


class GraphAuditService:
    """Service to audit knowledge graph health metrics."""

    def __init__(self, db: RelationalDB | None = None):
        self.db = db or RelationalDB()

    def audit(self) -> dict:
        """Compute the 18 specified metrics from relational database and Chroma."""
        valid_chunk_ids = set()
        chroma_accessible = True
        try:
            from rag_knowledge.repository.vector_store import VectorStore
            store = VectorStore()
            # Fetch only ids from Chroma
            data = store._get_store()._collection.get(include=[])
            valid_chunk_ids = set(data.get("ids") or [])
        except Exception as e:
            logger.warning("Chroma is not accessible during graph audit: %s", e)
            chroma_accessible = False

        with self.db._get_conn() as conn:
            # 1. entity_counts
            rows = conn.execute("SELECT entity_type, COUNT(*) as cnt FROM entities GROUP BY entity_type").fetchall()
            entity_counts = {row["entity_type"]: row["cnt"] for row in rows}

            # 2. relation_counts
            rows = conn.execute("SELECT relation_type, COUNT(*) as cnt FROM relations GROUP BY relation_type").fetchall()
            relation_counts = {row["relation_type"]: row["cnt"] for row in rows}

            # 3. total_entities
            total_entities = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

            # 4. total_relations
            total_relations = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]

            # 5. total_entity_chunk_links
            total_entity_chunk_links = conn.execute("SELECT COUNT(*) FROM entity_chunk_links").fetchone()[0]

            # 6. section_ratio
            section_count = entity_counts.get("Section", 0)
            section_ratio = float(section_count) / total_entities if total_entities > 0 else 0.0

            # 7. business_entity_ratio
            business_entity_count = sum(entity_counts.get(t, 0) for t in BUSINESS_ENTITY_TYPES)
            business_entity_ratio = float(business_entity_count) / total_entities if total_entities > 0 else 0.0

            # 8 & 9. stale_link_count & stale_link_samples
            if chroma_accessible:
                all_links = conn.execute("SELECT id, entity_id, chunk_id, source FROM entity_chunk_links").fetchall()
                stale_links = [dict(r) for r in all_links if r["chunk_id"] not in valid_chunk_ids]
                stale_link_count = len(stale_links)
                stale_link_samples = stale_links[:5]
            else:
                stale_link_count = -1
                stale_link_samples = []

            # 10 & 11. orphan_entity_count & orphan_entity_samples
            orphan_rows = conn.execute("""
                SELECT id, name, entity_type FROM entities
                WHERE id NOT IN (SELECT DISTINCT source_entity_id FROM relations)
                  AND id NOT IN (SELECT DISTINCT target_entity_id FROM relations)
                  AND id NOT IN (SELECT DISTINCT entity_id FROM entity_chunk_links)
            """).fetchall()
            orphan_entity_count = len(orphan_rows)
            orphan_entity_samples = [dict(r) for r in orphan_rows[:5]]

            # 12. duplicate_canonical_name_count
            dup_rows = conn.execute("""
                SELECT LOWER(TRIM(canonical_name)) as norm_name, COUNT(*) as cnt
                FROM entities
                WHERE canonical_name IS NOT NULL AND canonical_name != ''
                GROUP BY LOWER(TRIM(canonical_name))
                HAVING cnt > 1
            """).fetchall()
            duplicate_canonical_name_count = len(dup_rows)

            # 13. type_conflict_count
            conflict_rows = conn.execute("""
                SELECT LOWER(TRIM(name)) as norm_name, COUNT(DISTINCT entity_type) as type_cnt
                FROM entities
                GROUP BY LOWER(TRIM(name))
                HAVING type_cnt > 1
            """).fetchall()
            type_conflict_count = len(conflict_rows)

            # 14. manual_fact_count
            # 15. seed_fact_count
            # 16. rule_fact_count
            # 17. llm_fact_count
            entity_sources = conn.execute("SELECT created_by, COUNT(*) as cnt FROM entities GROUP BY created_by").fetchall()
            relation_sources = conn.execute("SELECT created_by, COUNT(*) as cnt FROM relations GROUP BY created_by").fetchall()
            
            source_counts = {}
            for row in entity_sources:
                source_counts[row["created_by"]] = source_counts.get(row["created_by"], 0) + row["cnt"]
            for row in relation_sources:
                source_counts[row["created_by"]] = source_counts.get(row["created_by"], 0) + row["cnt"]

            manual_fact_count = sum(source_counts.get(k, 0) for k in ("admin", "manual"))
            seed_fact_count = source_counts.get("seed", 0)
            rule_fact_count = sum(v for k, v in source_counts.items() if k and k.startswith("rule:"))
            llm_fact_count = sum(v for k, v in source_counts.items() if k and k.startswith("llm:"))

            # 18. document_entity_coverage
            coverage_rows = conn.execute("""
                SELECT
                    l.source as source,
                    COUNT(DISTINCT CASE WHEN e.entity_type = 'Section' THEN l.entity_id END) as section_count,
                    COUNT(DISTINCT CASE WHEN e.entity_type NOT IN ('Document', 'Section') THEN l.entity_id END) as business_entity_count
                FROM entity_chunk_links l
                JOIN entities e ON l.entity_id = e.id
                WHERE l.source IS NOT NULL AND l.source != ''
                GROUP BY l.source
            """).fetchall()
            
            document_entity_coverage = []
            for r in coverage_rows:
                s_count = r["section_count"]
                b_count = r["business_entity_count"]
                score = float(b_count) / s_count if s_count > 0 else 0.0
                document_entity_coverage.append({
                    "source": r["source"],
                    "section_count": s_count,
                    "business_entity_count": b_count,
                    "coverage_score": round(score, 4)
                })

        return {
            "entity_counts": entity_counts,
            "relation_counts": relation_counts,
            "total_entities": total_entities,
            "total_relations": total_relations,
            "total_entity_chunk_links": total_entity_chunk_links,
            "section_ratio": round(section_ratio, 4),
            "business_entity_ratio": round(business_entity_ratio, 4),
            "stale_link_count": stale_link_count,
            "stale_link_samples": stale_link_samples,
            "orphan_entity_count": orphan_entity_count,
            "orphan_entity_samples": orphan_entity_samples,
            "duplicate_canonical_name_count": duplicate_canonical_name_count,
            "type_conflict_count": type_conflict_count,
            "manual_fact_count": manual_fact_count,
            "seed_fact_count": seed_fact_count,
            "rule_fact_count": rule_fact_count,
            "llm_fact_count": llm_fact_count,
            "document_entity_coverage": document_entity_coverage,
            "chroma_accessible": chroma_accessible
        }

    def generate_reports(self, json_path: str, md_path: str) -> dict:
        """Compute audit report and write to JSON and Markdown files."""
        report = self.audit()

        # Ensure directory folders exist
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(md_path).parent.mkdir(parents=True, exist_ok=True)

        # 1. Write JSON Report
        Path(json_path).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        # 2. Write Markdown Report
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        md_lines = [
            "# Knowledge Graph Audit Report",
            f"\n* **Generated At**: {now_str}",
            f"* **Chroma Database Connection**: {'OK' if report['chroma_accessible'] else 'Unavailable'}",
            "\n## 1. Statistics Overview",
            f"* **Total Entities**: {report['total_entities']}",
            f"* **Total Relations**: {report['total_relations']}",
            f"* **Total Evidence Links**: {report['total_entity_chunk_links']}",
            f"* **Section Entities Ratio**: {report['section_ratio'] * 100:.2f}%",
            f"* **Business Entities Ratio**: {report['business_entity_ratio'] * 100:.2f}%",
            "\n## 2. Integrity and Health Issues",
        ]

        if report["stale_link_count"] == -1:
            md_lines.append("* **Stale Links**: Chroma DB is not accessible. Stale link check skipped.")
        else:
            md_lines.append(f"* **Stale Links Count**: {report['stale_link_count']}")
            if report["stale_link_count"] > 0:
                md_lines.append("\n### Stale Link Samples:")
                for sample in report["stale_link_samples"]:
                    md_lines.append(f"  - Link ID: `{sample['id']}` (Chunk ID: `{sample['chunk_id']}`, Source: `{sample['source']}`)")

        md_lines.append(f"\n* **Orphan Entities Count**: {report['orphan_entity_count']}")
        if report["orphan_entity_count"] > 0:
            md_lines.append("\n### Orphan Entity Samples:")
            for sample in report["orphan_entity_samples"]:
                md_lines.append(f"  - Entity: `{sample['name']}` (Type: `{sample['entity_type']}`)")

        md_lines.append(f"\n* **Duplicate Canonical Names Count**: {report['duplicate_canonical_name_count']}")
        md_lines.append(f"* **Case-insensitive Type Conflicts Count**: {report['type_conflict_count']}")

        md_lines.extend([
            "\n## 3. Fact Source Distribution",
            f"* **Manual Facts (admin/manual)**: {report['manual_fact_count']}",
            f"* **Seed Facts**: {report['seed_fact_count']}",
            f"* **Rule-based Facts**: {report['rule_fact_count']}",
            f"* **LLM-extracted Facts**: {report['llm_fact_count']}",
            "\n## 4. Entity Coverage per Document",
            "\n| Document Name | Section Count | Business Entity Count | Coverage Score |",
            "|---|---:|---:|---:|",
        ])

        for doc in report["document_entity_coverage"]:
            md_lines.append(f"| {doc['source']} | {doc['section_count']} | {doc['business_entity_count']} | {doc['coverage_score'] * 100:.2f}% |")

        md_lines.extend([
            "\n## 5. Entities by Type",
            "\n| Entity Type | Count |",
            "|---|---:|",
        ])
        for etype, count in sorted(report["entity_counts"].items(), key=lambda x: x[1], reverse=True):
            md_lines.append(f"| {etype} | {count} |")

        md_lines.extend([
            "\n## 6. Relations by Type",
            "\n| Relation Type | Count |",
            "|---|---:|",
        ])
        for rtype, count in sorted(report["relation_counts"].items(), key=lambda x: x[1], reverse=True):
            md_lines.append(f"| {rtype} | {count} |")

        Path(md_path).write_text("\n".join(md_lines), encoding="utf-8")
        logger.info("Generated graph audit reports: %s and %s", json_path, md_path)
        return report
