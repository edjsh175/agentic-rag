#!/bin/bash
# One-shot: wipe chroma, import portable jsonl via docker run (NO uvicorn/scan).
set -euo pipefail
TS=$(date +%Y%m%d_%H%M%S)
BASE=/data/rag_python
EXPORT=/tmp/chroma_export
IMG=rag-backend:cpu-http-reranker
LOG=/tmp/chroma_import_${TS}.log

test -f "$EXPORT/records.jsonl"
test -f /tmp/import_chroma_portable.py
test -f /tmp/file_index.linux.json
wc -l "$EXPORT/records.jsonl"

echo "=== stop rag-service ==="
docker stop rag-service || true

echo "=== quarantine chroma ==="
if [[ -d $BASE/chroma_db ]]; then
  mv "$BASE/chroma_db" "$BASE/backups/chroma_pre_import_${TS}"
fi
mkdir -p "$BASE/chroma_db" "$BASE/backups"

echo "=== restore linux file_index (before start; after import) ==="
cp -a "$BASE/data/file_index.json" "$BASE/data/backups/file_index.pre_import_${TS}.json" 2>/dev/null || true
cp -a /tmp/file_index.linux.json "$BASE/data/file_index.json"

echo "=== one-shot import (no scan) ==="
# Same binds as rag-service; entrypoint = importer only
nohup docker run --rm --name rag-chroma-import \
  -v /data/rag_python/logs:/app/logs \
  -v /data/rag_python/config.ini:/app/config.ini:ro \
  -v /data/rag_python/data:/app/data \
  -v /data/rag_python/chroma_db:/app/chroma_db \
  -v /data/setup/nltk_data:/root/nltk_data \
  -v /tmp/chroma_export:/tmp/chroma_export:ro \
  -v /tmp/import_chroma_portable.py:/tmp/import_chroma_portable.py:ro \
  -e PYTHONPATH=/app \
  -e PYTHONUNBUFFERED=1 \
  -w /app \
  "$IMG" \
  python /tmp/import_chroma_portable.py \
  >"$LOG" 2>&1 &

echo "import_pid=$! log=$LOG"
echo "tail -f $LOG  # monitor"
echo STARTED
