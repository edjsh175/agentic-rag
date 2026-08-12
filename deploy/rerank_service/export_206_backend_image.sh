#!/bin/bash
set -euo pipefail
OUT=/data/rag_python/rag-backend-cpu-http-reranker.tar
echo "=== docker save ==="
docker save rag-backend:cpu-http-reranker -o "$OUT"
ls -lh "$OUT"
sha256sum "$OUT" | tee /tmp/rag-backend-cpu-http-reranker.sha256

echo "=== cleanup 206 residuals ==="
rm -f /tmp/rag_relational_from_local.db
rm -rf /tmp/backbone_sync_local
rm -f /tmp/probe_*.sh /tmp/diag_*.sh /tmp/sync_*.sh /tmp/fix_*.sh /tmp/verify_*.sh \
  /tmp/overwrite_*.sh /tmp/enable_*.sh /tmp/apply_*.sh /tmp/e2e_*.sh \
  /tmp/register_*.bat /tmp/register_*.ps1 /tmp/start_*.bat /tmp/run_*.bat 2>/dev/null || true
rm -f /tmp/bb_*.json /tmp/api_*.json /tmp/formal_graph.json /tmp/health.json \
  /tmp/q.json /tmp/q2.json /tmp/backbone_*.json /tmp/cleanup_*.py \
  /tmp/sync_product_backbone_to_graph.py /tmp/run_graph_build.py \
  /tmp/cleanup_obsolete_product_backbone_seed.py 2>/dev/null || true
echo "DONE"
ls -lh "$OUT"
