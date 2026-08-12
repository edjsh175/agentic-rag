#!/bin/bash
set -euo pipefail
echo "=== files ==="
ls -la /data/rag_python/data/product_relation_backbone*.json 2>/dev/null || true
ls -la /data/rag_python/product_relation_backbone*.json 2>/dev/null || true
find /data/rag_python -maxdepth 3 -name 'product_relation_backbone*.json' 2>/dev/null
echo "=== sha256 ==="
sha256sum /data/rag_python/data/product_relation_backbone.json /data/rag_python/data/product_relation_backbone_preview.json 2>/dev/null || true
echo "=== counts via python ==="
python3 <<'PY'
import json
from pathlib import Path
for name in [
    "/data/rag_python/data/product_relation_backbone.json",
    "/data/rag_python/data/product_relation_backbone_preview.json",
]:
    p = Path(name)
    if not p.exists():
        print(name, "MISSING")
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    ents = d.get("entities") or d.get("nodes") or []
    rels = d.get("relations") or d.get("edges") or []
    print(f"{p.name}: keys={list(d.keys())[:8]} entities={len(ents)} relations={len(rels)} bytes={p.stat().st_size}")
PY
echo "=== formal seed counts in sqlite ==="
python3 <<'PY'
import sqlite3
from pathlib import Path
db = Path("/data/rag_python/data/rag_relational.db")
if not db.exists():
    print("db missing"); raise SystemExit
con = sqlite3.connect(str(db))
cur = con.cursor()
for q in [
    "select count(*) from entities where created_by like '%product_backbone%'",
    "select count(*) from relations where created_by like '%product_backbone%'",
    "select created_by, count(*) from entities where created_by like '%backbone%' group by created_by",
    "select created_by, count(*) from relations where created_by like '%backbone%' group by created_by",
]:
    try:
        rows = cur.execute(q).fetchall()
        print(q, "=>", rows)
    except Exception as e:
        print(q, "ERR", e)
con.close()
PY
