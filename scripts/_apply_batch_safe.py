"""
Approve safe candidates for batch and apply to formal graph.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_extraction.pipeline import GraphCandidateApplier, GraphQualityService

BATCH_ID = "be785ee3-017b-4650-a390-952d8614bf89"

def main():
    db = RelationalDB()
    pending = db.list_extraction_candidates(BATCH_ID, "pending")
    print(f"Total pending candidates in batch {BATCH_ID}: {len(pending)}")

    # Exclude rejected diagnostics from approval
    approved_ids = []
    for c in pending:
        if c["candidate_kind"] != "diagnostic":
            approved_ids.append(c["id"])

    print(f"Approving {len(approved_ids)} valid candidates...")
    db.review_extraction_candidates(BATCH_ID, approved_ids, "approved", "Auto-approved valid candidates")
    db.set_extraction_batch_status(BATCH_ID, "approved")

    print("Applying batch to formal database...")
    applier = GraphCandidateApplier(db=db)
    result = applier.apply(BATCH_ID)
    print("Batch applied successfully!", result)

if __name__ == "__main__":
    main()
