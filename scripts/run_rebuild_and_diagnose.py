"""
Script to apply Product Backbone to SQLite database and re-run topology diagnostics.
"""
import os
import sys
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.product_backbone_graph_sync import ProductBackboneGraphSyncService
from rag_knowledge.services.graph_extraction.pipeline import GraphCandidateApplier
from scripts.verify_graph_topology import run_diagnostics


def main():
    print("=" * 80)
    print(" EXECUTING SAFE PRODUCT BACKBONE APPLY & GRAPH DIAGNOSTICS")
    print("=" * 80)

    cfg = Config()
    db_path = Path(cfg.relational_db_path)
    
    # 1. Safety Backup
    backup_dir = PROJECT_ROOT / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"rag_relational_backup_{timestamp}.db"
    
    print(f"\n[STEP 1] Creating Safety Backup...")
    if db_path.exists():
        with sqlite3.connect(db_path) as src, sqlite3.connect(backup_path) as dst:
            src.backup(dst)
        print(f" -> Backup successfully created at: {backup_path}")
    else:
        print(f" -> DB does not exist at {db_path}, creating new DB.")

    # 2. Stage Product Backbone Batch
    print(f"\n[STEP 2] Staging Product Backbone Batch...")
    db = RelationalDB()
    sync_service = ProductBackboneGraphSyncService(db=db)
    result = sync_service.build_batch(review_status="approved")
    print(f" -> Staged Batch ID: {result.batch_id}")
    print(f" -> Batch Stats: {result.stats}")

    # 3. Apply Batch to Formal Database
    print(f"\n[STEP 3] Applying Batch to Formal Database...")
    applier = GraphCandidateApplier(db=db)
    apply_result = applier.apply(result.batch_id)
    print(f" -> Batch Applied Successfully!")
    if isinstance(apply_result, dict):
        print(f" -> Apply Stats: {apply_result}")

    # 4. Re-run Diagnostics
    print(f"\n[STEP 4] Re-running Topology Diagnostics...")
    run_diagnostics()

    print("\n" + "=" * 80)
    print(" BACKBONE APPLY & RE-DIAGNOSTIC COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
