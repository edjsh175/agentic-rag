#!/bin/bash
# Align created_by for 6 backbone entities to seed:product_backbone (match local).
set -euo pipefail
python3 <<'PY'
import sqlite3
names = [
    "StampServer",
    "StampTools",
    "PipelineBuilder",
    "DOMBuilder",
    "管线发布服务",
    "StampWebRTC",
]
con = sqlite3.connect("/data/rag_python/data/rag_relational.db")
cur = con.cursor()
for name in names:
    row = cur.execute(
        "select id, created_by from entities where name=?", (name,)
    ).fetchone()
    if not row:
        print("MISSING", name)
        continue
    before = row[1]
    cur.execute(
        "UPDATE entities SET created_by=?, updated_at=datetime('now') WHERE id=?",
        ("seed:product_backbone", row[0]),
    )
    print(f"{name}: {before} -> seed:product_backbone")
con.commit()
print(
    "seed ents",
    cur.execute(
        "select count(*) from entities where created_by='seed:product_backbone'"
    ).fetchone()[0],
)
print(
    "seed rels",
    cur.execute(
        "select count(*) from relations where created_by='seed:product_backbone'"
    ).fetchone()[0],
)
con.close()
PY
