from __future__ import annotations

import logging
from rag_knowledge.repository.relational_db import RelationalDB

logger = logging.getLogger(__name__)


class GraphCleanupService:
    """Service to clean up stale data in the knowledge graph."""

    def __init__(self, db: RelationalDB | None = None):
        self.db = db or RelationalDB()

    def cleanup_stale_links(self, dry_run: bool = False) -> dict:
        """Find and remove entity_chunk_links where chunk_id does not exist in Chroma."""
        # 1. Fetch valid chunk IDs from Chroma VectorStore
        try:
            from rag_knowledge.repository.vector_store import VectorStore
            store = VectorStore()
            data = store._get_store()._collection.get(include=[])
            valid_chunk_ids = set(data.get("ids") or [])
        except Exception as e:
            logger.error("Chroma is not accessible during cleanup: %s", e)
            raise ValueError(f"Chroma collection is not accessible; cleanup-stale-links aborted. Error: {e}")

        with self.db._get_conn() as conn:
            # 2. Get distinct chunk IDs from database
            rows = conn.execute("SELECT DISTINCT chunk_id FROM entity_chunk_links").fetchall()
            db_chunk_ids = {row["chunk_id"] for row in rows}

            # 3. Find invalid chunk IDs
            stale_chunk_ids = db_chunk_ids - valid_chunk_ids
            if not stale_chunk_ids:
                return {
                    "stale_links_before": 0,
                    "stale_links_deleted": 0,
                    "stale_links_after": 0,
                    "entities_deleted": 0,
                    "relations_deleted": 0,
                    "samples": []
                }

            # Convert to list for batch processing
            stale_chunk_list = list(stale_chunk_ids)

            # 4. Fetch count and samples of stale links
            # We construct standard batch parameters to avoid SQLITE_LIMIT_VARIABLE_NUMBER
            total_stale_links = 0
            samples = []
            
            # Fetch samples
            sample_rows = conn.execute(f"""
                SELECT l.id, l.chunk_id, l.source, e.name as entity_name
                FROM entity_chunk_links l
                JOIN entities e ON l.entity_id = e.id
                WHERE l.chunk_id IN ({','.join('?' for _ in stale_chunk_list[:5])})
            """, stale_chunk_list[:5]).fetchall()
            samples = [dict(r) for r in sample_rows]

            # Fetch total count of links to delete
            batch_size = 500
            for i in range(0, len(stale_chunk_list), batch_size):
                chunk_batch = stale_chunk_list[i:i + batch_size]
                placeholders = ",".join("?" for _ in chunk_batch)
                cnt = conn.execute(
                    f"SELECT COUNT(*) FROM entity_chunk_links WHERE chunk_id IN ({placeholders})",
                    chunk_batch
                ).fetchone()[0]
                total_stale_links += cnt

            # 5. Delete links if not dry_run
            deleted_count = 0
            if not dry_run:
                for i in range(0, len(stale_chunk_list), batch_size):
                    chunk_batch = stale_chunk_list[i:i + batch_size]
                    placeholders = ",".join("?" for _ in chunk_batch)
                    cur = conn.execute(
                        f"DELETE FROM entity_chunk_links WHERE chunk_id IN ({placeholders})",
                        chunk_batch
                    )
                    deleted_count += cur.rowcount
                logger.info("Successfully deleted %d stale entity_chunk_links.", deleted_count)
            else:
                logger.info("[Dry Run] Would delete %d stale entity_chunk_links.", total_stale_links)

            return {
                "stale_links_before": total_stale_links,
                "stale_links_deleted": deleted_count if not dry_run else 0,
                "stale_links_after": total_stale_links - deleted_count if not dry_run else total_stale_links,
                "entities_deleted": 0,
                "relations_deleted": 0,
                "samples": samples
            }
