#!/bin/bash
set -euo pipefail
TS=$(date +%Y%m%d_%H%M%S)
DATA=/data/rag_python/data
DB=$DATA/rag_relational.db
SRC=/tmp/rag_relational_from_local.db
BAK_DIR=$DATA/backups
mkdir -p "$BAK_DIR"

echo "=== stop rag-service ==="
docker stop rag-service

echo "=== backup current db (+wal/shm if any) ==="
cp -a "$DB" "$BAK_DIR/rag_relational.pre_local_overwrite_${TS}.db"
[[ -f "$DB-wal" ]] && cp -a "$DB-wal" "$BAK_DIR/rag_relational.pre_local_overwrite_${TS}.db-wal" || true
[[ -f "$DB-shm" ]] && cp -a "$DB-shm" "$BAK_DIR/rag_relational.pre_local_overwrite_${TS}.db-shm" || true
ls -lh "$BAK_DIR/rag_relational.pre_local_overwrite_${TS}.db"*

echo "=== replace ==="
test -f "$SRC"
ls -lh "$SRC"
rm -f "$DB-wal" "$DB-shm"
cp -a "$SRC" "$DB"
chmod 644 "$DB"
# ensure no stale wal from old connection
rm -f "$DB-wal" "$DB-shm"

echo "=== verify file counts before start ==="
python3 <<'PY'
import sqlite3
con=sqlite3.connect('/data/rag_python/data/rag_relational.db')
cur=con.cursor()
print('entities_approved', cur.execute("select count(*) from entities where review_status='approved'").fetchone()[0])
print('relations_approved', cur.execute("select count(*) from relations where review_status='approved'").fetchone()[0])
print('seed_ents', cur.execute("select count(*) from entities where created_by='seed:product_backbone'").fetchone()[0])
print('seed_rels', cur.execute("select count(*) from relations where created_by='seed:product_backbone'").fetchone()[0])
con.close()
PY

echo "=== start rag-service ==="
docker start rag-service
for i in $(seq 1 50); do
  if curl -fsS --max-time 3 http://127.0.0.1:10605/health >/dev/null; then
    echo "healthy try=$i"
    break
  fi
  sleep 3
  if [[ $i -eq 50 ]]; then
    echo "health timeout"
    docker logs rag-service --tail 80
    exit 1
  fi
done
curl -fsS http://127.0.0.1:10605/health; echo

echo "=== API formal graph sample ==="
# If admin graph endpoint supports counts via response size
curl -fsS --max-time 60 'http://127.0.0.1:10605/admin/knowledge_graph?doc_category=all&mode=product' -o /tmp/formal_graph.json || \
curl -fsS --max-time 60 'http://127.0.0.1:10605/admin/knowledge_graph' -o /tmp/formal_graph.json || true
python3 <<'PY'
import json
from pathlib import Path
p=Path('/tmp/formal_graph.json')
if p.exists() and p.stat().st_size:
    d=json.loads(p.read_text(encoding='utf-8'))
    print('api nodes', len(d.get('nodes') or []), 'edges', len(d.get('edges') or []), 'keys', list(d.keys())[:8])
else:
    print('api graph empty/failed')
PY

echo "BACKUP=$BAK_DIR/rag_relational.pre_local_overwrite_${TS}.db"
echo DONE
