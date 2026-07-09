from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from rag_knowledge.repository.relational_db import RelationalDB

logger = logging.getLogger(__name__)

MANUAL_CREATORS = ('admin', 'manual', 'seed', 'rule:special', 'rule:special_relations')


class GraphManualFactExporter:
    """Exporter for manual/seed/special facts in the knowledge graph."""

    def __init__(self, db: RelationalDB | None = None):
        self.db = db or RelationalDB()

    def export_manual(self, output_path: str) -> dict:
        """Export manual/seed/special entities, relations, aliases, and links to JSON."""
        # 1. Fetch valid chunk IDs from Chroma VectorStore
        valid_chunk_ids = set()
        chroma_accessible = True
        try:
            from rag_knowledge.repository.vector_store import VectorStore
            store = VectorStore()
            data = store._get_store()._collection.get(include=[])
            valid_chunk_ids = set(data.get("ids") or [])
        except Exception as e:
            logger.warning("Chroma is not accessible during export: %s. Stale link filtering skipped.", e)
            chroma_accessible = False

        with self.db._get_conn() as conn:
            # 2. Query manual entities
            placeholders = ",".join("?" for _ in MANUAL_CREATORS)
            db_entities = conn.execute(
                f"SELECT * FROM entities WHERE created_by IN ({placeholders})",
                MANUAL_CREATORS
            ).fetchall()

            entities = []
            for row in db_entities:
                entities.append({
                    "name": row["name"],
                    "canonical_name": row["canonical_name"],
                    "entity_type": row["entity_type"],
                    "description": row["description"] or "",
                    "properties_json": row["properties_json"] or "{}",
                    "doc_category": row["doc_category"] or "",
                    "confidence": row["confidence"],
                    "review_status": row["review_status"],
                    "created_by": row["created_by"]
                })

            # 3. Query manual relations mapping end-points to canonical names
            db_relations = conn.execute(f"""
                SELECT r.*,
                       COALESCE(NULLIF(s.canonical_name, ''), s.name) as source_canonical_name,
                       COALESCE(NULLIF(t.canonical_name, ''), t.name) as target_canonical_name
                FROM relations r
                JOIN entities s ON r.source_entity_id = s.id
                JOIN entities t ON r.target_entity_id = t.id
                WHERE r.created_by IN ({placeholders})
            """, MANUAL_CREATORS).fetchall()

            relations = []
            for row in db_relations:
                relations.append({
                    "source_canonical_name": row["source_canonical_name"],
                    "target_canonical_name": row["target_canonical_name"],
                    "relation_type": row["relation_type"],
                    "properties_json": row["properties_json"] or "{}",
                    "confidence": row["confidence"],
                    "evidence_text": row["evidence_text"] or "",
                    "source_chunk_id": row["source_chunk_id"] or "",
                    "review_status": row["review_status"],
                    "created_by": row["created_by"]
                })

            # 4. Query manual aliases (or aliases linked to manual entities)
            db_aliases = conn.execute(f"""
                SELECT a.*, COALESCE(NULLIF(e.canonical_name, ''), e.name) as entity_canonical_name
                FROM aliases a
                JOIN entities e ON a.entity_id = e.id
                WHERE e.created_by IN ({placeholders})
                   OR a.evidence_text LIKE 'special_rule:%'
            """, MANUAL_CREATORS).fetchall()

            aliases = []
            for row in db_aliases:
                aliases.append({
                    "entity_canonical_name": row["entity_canonical_name"],
                    "alias": row["alias"],
                    "confidence": row["confidence"],
                    "source_chunk_id": row["source_chunk_id"] or "",
                    "evidence_text": row["evidence_text"] or "",
                    "review_status": row["review_status"]
                })

            # 5. Query links associated with manual entities
            db_links = conn.execute(f"""
                SELECT l.*, COALESCE(NULLIF(e.canonical_name, ''), e.name) as entity_canonical_name
                FROM entity_chunk_links l
                JOIN entities e ON l.entity_id = e.id
                WHERE e.created_by IN ({placeholders})
            """, MANUAL_CREATORS).fetchall()

            links = []
            skipped_stale_links = 0
            for row in db_links:
                if chroma_accessible and row["chunk_id"] not in valid_chunk_ids:
                    skipped_stale_links += 1
                    continue
                links.append({
                    "entity_canonical_name": row["entity_canonical_name"],
                    "chunk_id": row["chunk_id"],
                    "link_type": row["link_type"],
                    "section_path": row["section_path"] or "",
                    "page_label": row["page_label"] or "",
                    "evidence_text": row["evidence_text"] or "",
                    "source": row["source"] or ""
                })

        # 6. Build export payload
        payload = {
            "exported_at": datetime.now().isoformat(),
            "schema_version": "v1",
            "entities": entities,
            "relations": relations,
            "aliases": aliases,
            "entity_chunk_links": links,
            "summary": {
                "entities": len(entities),
                "relations": len(relations),
                "aliases": len(aliases),
                "entity_chunk_links": len(links),
                "skipped_stale_links": skipped_stale_links
            }
        }

        # Ensure directory folders exist
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Successfully exported manual graph facts to %s", output_path)

        return payload["summary"]
