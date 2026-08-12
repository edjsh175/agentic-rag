#!/bin/bash
# Align formal seed:product_backbone on 206 with product_relation_backbone.json
set -euo pipefail
DB=/app/data/rag_relational.db
BACKBONE=/app/data/product_relation_backbone.json
HOST_DATA=/data/rag_python/data
TS=$(date +%Y%m%d_%H%M%S)

echo "=== stop rag-service for sqlite write ==="
docker stop rag-service

echo "=== ensure scripts in container filesystem (stopped container still has RW layer) ==="
# Copy into stopped container works
docker cp /tmp/cleanup_obsolete_product_backbone_seed.py rag-service:/app/scripts/cleanup_obsolete_product_backbone_seed.py
docker cp /tmp/sync_product_backbone_to_graph.py rag-service:/app/sync_product_backbone_to_graph.py
docker cp /tmp/run_graph_build.py rag-service:/app/run_graph_build.py

echo "=== start for python exec ==="
docker start rag-service
for i in $(seq 1 40); do
  curl -fsS --max-time 3 http://127.0.0.1:10605/health >/dev/null && break
  sleep 3
done

echo "=== cleanup dry-run summary ==="
docker exec -e PYTHONPATH=/app -w /app rag-service \
  python scripts/cleanup_obsolete_product_backbone_seed.py --json > "/tmp/backbone_cleanup_dry_${TS}.json"
python3 - <<PY
import json
from pathlib import Path
d=json.loads(Path("/tmp/backbone_cleanup_dry_${TS}.json").read_text(encoding="utf-8"))
diff=d["diff"]
print({k:diff[k] for k in ["formal_entity_count","formal_relation_count","old_seed_entity_count","old_seed_relation_count","to_delete_count","to_keep_count","to_add_count"]})
PY

echo "=== cleanup --apply --backup ==="
docker exec -e PYTHONPATH=/app -w /app rag-service \
  python scripts/cleanup_obsolete_product_backbone_seed.py --apply --backup --json \
  > "/tmp/backbone_cleanup_apply_${TS}.json"
cp -a "/tmp/backbone_cleanup_apply_${TS}.json" "$HOST_DATA/backbone_cleanup_apply_${TS}.json"
python3 - <<PY
import json
from pathlib import Path
d=json.loads(Path("/tmp/backbone_cleanup_apply_${TS}.json").read_text(encoding="utf-8"))
print("backup", d.get("backup_path"))
print("cleanup", {k:d["cleanup"][k] for k in ["deleted_relations","deleted_entity_count","applied"]})
print("to_add", d["diff"]["to_add_count"])
PY

echo "=== stage ==="
docker exec -e PYTHONPATH=/app -w /app rag-service \
  python sync_product_backbone_to_graph.py --stage --path "$BACKBONE" \
  --review-status pending --confirm-db-path "$DB" --json \
  > "/tmp/backbone_stage_${TS}.json"
cp -a "/tmp/backbone_stage_${TS}.json" "$HOST_DATA/backbone_stage_${TS}.json"
BATCH=$(python3 -c "import json;print(json.load(open('/tmp/backbone_stage_${TS}.json',encoding='utf-8'))['batch_id'])")
echo "BATCH=$BATCH"
python3 -c "import json;d=json.load(open('/tmp/backbone_stage_${TS}.json',encoding='utf-8'));print('stats',d.get('stats'))"

echo "=== review by kind ==="
for kind in entity alias relation; do
  docker exec -e PYTHONPATH=/app -w /app rag-service \
    python run_graph_build.py review --batch "$BATCH" --approve-kind "$kind"
done

echo "=== apply ==="
# backup path from cleanup report
BACKUP=$(python3 - <<PY
import json
from pathlib import Path
d=json.loads(Path("/tmp/backbone_cleanup_apply_${TS}.json").read_text(encoding="utf-8"))
print(d["backup_path"])
PY
)
docker exec -e PYTHONPATH=/app -w /app rag-service \
  python run_graph_build.py apply --batch "$BATCH" \
  --confirm-db-path "$DB" \
  --confirm-batch "$BATCH" \
  --confirm-backup "$BACKUP" \
  --json > "/tmp/backbone_apply_${TS}.json" || {
  # Some apply CLIs print non-json; still show output
  docker exec -e PYTHONPATH=/app -w /app rag-service \
    python run_graph_build.py apply --batch "$BATCH" \
    --confirm-db-path "$DB" \
    --confirm-batch "$BATCH" \
    --confirm-backup "$BACKUP" | tee "/tmp/backbone_apply_${TS}.txt"
}

echo "=== verify seed counts ==="
python3 <<'PY'
import sqlite3
con=sqlite3.connect('/data/rag_python/data/rag_relational.db')
cur=con.cursor()
print('entities', cur.execute("select count(*) from entities where created_by='seed:product_backbone'").fetchone()[0])
print('relations', cur.execute("select count(*) from relations where created_by='seed:product_backbone'").fetchone()[0])
con.close()
PY

curl -fsS http://127.0.0.1:10605/health >/dev/null && echo health_ok
echo DONE
