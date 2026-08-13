#!/bin/bash
set -euo pipefail
TS=$(date +%Y%m%d_%H%M%S)
OUT=/data/rag_python/handover_${TS}
mkdir -p "$OUT" "$OUT/data" "$OUT/rerank_service"

echo "=== build rag-web ==="
ls -la /tmp/rag_web_build
docker build -t rag-web:latest /tmp/rag_web_build
docker save rag-web:latest -o "$OUT/rag-web.tar"

echo "=== backend image ==="
# prefer existing http-reranker export; refresh if missing
if [[ ! -f /data/rag_python/rag-backend-cpu-http-reranker.tar ]]; then
  docker save rag-backend:cpu-http-reranker -o /data/rag_python/rag-backend-cpu-http-reranker.tar
fi
cp -a /data/rag_python/rag-backend-cpu-http-reranker.tar "$OUT/rag-backend.tar"

echo "=== data (no chroma, no backups) ==="
DATA_SRC=/data/rag_python/data
# essential files
for f in \
  rag_relational.db \
  retrieval_intent_policies.json \
  agents.json \
  document_profile_map.json \
  domain_catalog.json \
  product_relation_backbone.json \
  product_relation_backbone_preview.json \
  ingestion_decisions.json \
  manual_graph_facts.json
 do
  if [[ -f "$DATA_SRC/$f" ]]; then
    cp -a "$DATA_SRC/$f" "$OUT/data/"
  fi
done
if [[ -d "$DATA_SRC/migrations" ]]; then
  cp -a "$DATA_SRC/migrations" "$OUT/data/"
fi
# empty placeholders for runtime
mkdir -p "$OUT/data/chats" "$OUT/data/qa_traces"
# do NOT copy file_index (rebuild will create)

echo "=== sizes ==="
du -sh "$OUT" "$OUT"/* 2>/dev/null | head -20
ls -lah "$OUT"
echo "OUT=$OUT"
