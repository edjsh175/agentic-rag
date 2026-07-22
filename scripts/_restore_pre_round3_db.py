"""Restore rag_relational.db from Round3 pre-execute backup, then re-run execute."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag_knowledge.services.graph_governance import resolve_db_path

BACKUP = ROOT / "data" / "backups" / "rag_relational_pre_round3_20260722_125634.db"


def main() -> int:
    if not BACKUP.is_file():
        raise SystemExit(f"backup missing: {BACKUP}")
    db_path = Path(resolve_db_path())
    # Remove WAL sidecars so restore is clean
    for side in (f"{db_path}-wal", f"{db_path}-shm"):
        p = Path(side)
        if p.exists():
            p.unlink()
    shutil.copy2(BACKUP, db_path)
    print("restored", db_path, "from", BACKUP, "size", db_path.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
