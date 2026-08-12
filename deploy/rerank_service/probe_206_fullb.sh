#!/bin/bash
set -euo pipefail
echo "=== [reranker] ==="
awk '/^\[reranker\]/{p=1} p&&/^\[/{if(!/^\[reranker\]/){exit}} p' /data/rag_python/config.ini
echo "=== [graph_retrieval] ==="
awk '/^\[graph_retrieval\]/{p=1} p&&/^\[/{if(!/^\[graph_retrieval\]/){exit}} p' /data/rag_python/config.ini
echo "=== [graph_extraction] / llm ==="
awk '/^\[graph_extraction/{p=1} p&&/^\[/{if(!/^\[graph_extraction/){exit}} p' /data/rag_python/config.ini
echo "=== ollama ==="
awk '/^\[ollama\]/{p=1} p&&/^\[/{if(!/^\[ollama\]/){exit}} p' /data/rag_python/config.ini
echo "=== model llm/vision ==="
grep -E '^(llm|helper_llm|vision|embedding)\s*=' /data/rag_python/config.ini | head -20
echo "=== container env ==="
docker inspect rag-service --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -iE 'RERANK|GRAPH' || true
echo "=== image ==="
docker ps --filter name=^rag-service$ --format '{{.Names}} {{.Image}} {{.Status}}'
