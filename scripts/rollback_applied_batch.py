"""Rollback an applied extraction batch and clean up DB state."""
import sys
import json
import logging
from rag_knowledge.repository.relational_db import RelationalDB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def rollback_batch(batch_id: str = "4acf5177-f778-4a92-8a3c-be583032dd05"):
    db = RelationalDB()
    with db._get_conn() as conn:
        batch = conn.execute("SELECT * FROM extraction_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            print(f"Batch {batch_id} not found.")
            return

        print(f"Rolling back batch {batch_id} (current status: {batch['status']})...")

        # Get all applied candidates with applied_target_id
        applied_candidates = conn.execute(
            "SELECT id, candidate_kind, applied_target_id FROM extraction_candidates WHERE batch_id=? AND status='applied'",
            (batch_id,)
        ).fetchall()

        target_ids = [c["applied_target_id"] for c in applied_candidates if c["applied_target_id"]]
        print(f"Found {len(applied_candidates)} applied candidates ({len(target_ids)} target IDs).")

        deleted_entities = 0
        deleted_relations = 0
        deleted_links = 0

        if target_ids:
            # Delete targets
            for target_id in target_ids:
                cur = conn.execute("DELETE FROM entities WHERE id=?", (target_id,))
                deleted_entities += cur.rowcount
                cur = conn.execute("DELETE FROM relations WHERE id=? OR source_entity_id=? OR target_entity_id=?", (target_id, target_id, target_id))
                deleted_relations += cur.rowcount
                cur = conn.execute("DELETE FROM entity_chunk_links WHERE id=? OR entity_id=?", (target_id, target_id))
                deleted_links += cur.rowcount

        # Clean up any leftover Section entities and defined_in relations created during extraction
        sec_rows = conn.execute("SELECT id FROM entities WHERE entity_type='Section'").fetchall()
        for sec in sec_rows:
            sec_id = sec["id"]
            conn.execute("DELETE FROM relations WHERE source_entity_id=? OR target_entity_id=?", (sec_id, sec_id))
            conn.execute("DELETE FROM entity_chunk_links WHERE entity_id=?", (sec_id,))
            cur = conn.execute("DELETE FROM entities WHERE id=?", (sec_id,))
            deleted_entities += cur.rowcount

        # Reset candidate status for this batch
        conn.execute(
            "UPDATE extraction_candidates SET status='pending', applied_target_id='', applied_at=NULL WHERE batch_id=? AND status='applied'",
            (batch_id,)
        )

        # Set batch status back to approved/draft
        conn.execute(
            "UPDATE extraction_batches SET status='approved', applied_at=NULL WHERE id=?",
            (batch_id,)
        )

        print(f"Rollback complete:")
        print(f"  - Deleted entities: {deleted_entities}")
        print(f"  - Deleted relations: {deleted_relations}")
        print(f"  - Deleted links: {deleted_links}")
        print(f"  - Batch {batch_id} status reset to 'approved'")

if __name__ == "__main__":
    target_id = sys.argv[1] if len(sys.argv) > 1 else "4acf5177-f778-4a92-8a3c-be583032dd05"
    rollback_batch(target_id)
