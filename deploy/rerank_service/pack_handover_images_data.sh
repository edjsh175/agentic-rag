#!/bin/bash
set -euo pipefail
TS=$(date +%Y%m%d_%H%M%S)
OUT=/data/rag_python/handover_${TS}
mkdir -p "$OUT/data" "$OUT/rerank_service"

echo "=== save rag-web ==="
docker save rag-web:latest -o "$OUT/rag-web.tar"

echo "=== backend image ==="
if [[ ! -f /data/rag_python/rag-backend-cpu-http-reranker.tar ]]; then
  docker save rag-backend:cpu-http-reranker -o /data/rag_python/rag-backend-cpu-http-reranker.tar
fi
cp -a /data/rag_python/rag-backend-cpu-http-reranker.tar "$OUT/rag-backend.tar"

echo "=== data (no chroma, no backups) ==="
DATA_SRC=/data/rag_python/data
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
  else
    echo "skip missing $f"
  fi
done
if [[ -d "$DATA_SRC/migrations" ]]; then
  cp -a "$DATA_SRC/migrations" "$OUT/data/"
fi
mkdir -p "$OUT/data/chats" "$OUT/data/qa_traces"

echo "=== done ==="
du -sh "$OUT" "$OUT"/*
ls -lah "$OUT"
echo "OUT=$OUT"
