#!/bin/bash
set -euo pipefail
TS=$(date +%Y%m%d_%H%M%S)
BASE=/data/rag_python
TGZ=/tmp/chroma_db_local_for_206.tgz
BAK=$BASE/backups/chroma_pre_local_overwrite_${TS}

test -f "$TGZ"
ls -lh "$TGZ"

echo "=== stop rag-service ==="
docker stop rag-service

echo "=== backup current chroma + file_index ==="
mkdir -p "$BAK"
if [[ -d $BASE/chroma_db ]]; then
  mv "$BASE/chroma_db" "$BAK/chroma_db"
fi
if [[ -f $BASE/data/file_index.json ]]; then
  cp -a "$BASE/data/file_index.json" "$BAK/file_index.json"
fi
du -sh "$BAK"/* 2>/dev/null || true

echo "=== extract local package ==="
# tarball contains chroma_db/ and data/file_index.json relative paths
mkdir -p "$BASE/data"
tar -xzf "$TGZ" -C "$BASE"
# if tar extracted data/file_index.json under BASE/data/
ls -lah "$BASE/chroma_db" | head -15
ls -lah "$BASE/data/file_index.json"
du -sh "$BASE/chroma_db"

# drop stale chroma wal companions if any odd state
find "$BASE/chroma_db" -name '*.lock' -delete 2>/dev/null || true

echo "=== start rag-service ==="
docker start rag-service
for i in $(seq 1 60); do
  if curl -fsS --max-time 3 http://127.0.0.1:10605/health >/dev/null; then
    echo "healthy try=$i"
    break
  fi
  sleep 3
  if [[ $i -eq 60 ]]; then
    echo "health timeout"
    docker logs rag-service --tail 100
    exit 1
  fi
done
curl -fsS http://127.0.0.1:10605/health; echo

echo "=== verify chunk count + rebuild BM25 ==="
docker exec -e PYTHONPATH=/app -w /app rag-service python -c "
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.bm25_store import BM25Store
VectorStore._instance = None
BM25Store._instance = None
vs = VectorStore()
print('chroma_count', vs.count())
bm = BM25Store()
bm.rebuild()
print('bm25_docs', len(getattr(bm, '_docs', []) or []))
print('bm25_ready', getattr(bm, '_bm25', None) is not None)
"

echo "=== stats ==="
curl -fsS http://127.0.0.1:10605/stats; echo
echo "BACKUP=$BAK"
echo DONE
