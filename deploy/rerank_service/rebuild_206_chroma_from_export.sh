#!/bin/bash
# Wipe broken Windows-copied chroma; import portable jsonl with Linux-native HNSW.
set -euo pipefail
TS=$(date +%Y%m%d_%H%M%S)
BASE=/data/rag_python
EXPORT=/tmp/chroma_export

test -f "$EXPORT/records.jsonl"
test -f /tmp/import_chroma_portable.py
wc -l "$EXPORT/records.jsonl"

echo "=== stop ==="
docker stop rag-service

echo "=== quarantine broken chroma ==="
if [[ -d $BASE/chroma_db ]]; then
  mv "$BASE/chroma_db" "$BASE/backups/chroma_broken_win_copy_${TS}"
fi
mkdir -p "$BASE/chroma_db"
# keep file_index already synced from local package if present
ls -lah "$BASE/data/file_index.json" || true

echo "=== start empty service ==="
docker start rag-service
for i in $(seq 1 60); do
  curl -fsS --max-time 3 http://127.0.0.1:10605/health >/dev/null && break
  sleep 3
  [[ $i -eq 60 ]] && { docker logs rag-service --tail 80; exit 1; }
done
curl -fsS http://127.0.0.1:10605/health; echo

echo "=== ensure empty collection ==="
docker exec -e PYTHONPATH=/app -w /app rag-service python -c "
from rag_knowledge.repository.vector_store import VectorStore
VectorStore._instance=None
vs=VectorStore()
print('count', vs.count())
"

echo "=== copy importer ==="
docker cp /tmp/import_chroma_portable.py rag-service:/tmp/import_chroma_portable.py
docker cp "$EXPORT" rag-service:/tmp/chroma_export

echo "=== import (re-embed via Ollama@158; may take long) ==="
docker exec -e PYTHONPATH=/app -w /app rag-service python /tmp/import_chroma_portable.py

echo "=== verify ==="
curl -fsS http://127.0.0.1:10605/stats; echo
docker exec -e PYTHONPATH=/app -w /app rag-service python -c "
from rag_knowledge.repository.vector_store import VectorStore
VectorStore._instance=None
print('final_count', VectorStore().count())
"
echo DONE
