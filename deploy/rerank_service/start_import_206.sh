#!/bin/bash
set -euo pipefail
TS=$(date +%Y%m%d_%H%M%S)
LOG=/tmp/chroma_import_${TS}.log
BASE=/data/rag_python

docker rm -f rag-chroma-import 2>/dev/null || true
docker stop rag-service 2>/dev/null || true

rm -rf "$BASE/chroma_db"
mkdir -p "$BASE/chroma_db"
cp -a /tmp/file_index.pruned.json "$BASE/data/file_index.json"

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
  rag-backend:cpu-http-reranker \
  python /tmp/import_chroma_portable.py \
  >"$LOG" 2>&1 &

echo "LOG=$LOG"
echo "PID=$!"
sleep 15
echo "=== log head ==="
tail -n 40 "$LOG" || true
docker ps -a --filter name=rag-chroma-import --format '{{.Names}} {{.Status}}'
