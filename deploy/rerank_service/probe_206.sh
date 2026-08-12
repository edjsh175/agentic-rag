#!/bin/bash
set -e
echo "=== docker ps ==="
docker ps --format '{{.Names}} {{.Image}} {{.Status}}'
echo "=== reranker config ==="
grep -A15 '^\[reranker\]' /data/rag_python/config.ini || true
echo "=== env RERANK ==="
docker inspect rag-service --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -i RERANK || true
echo "=== HttpReranker import ==="
docker exec rag-service python -c "from rag_knowledge.services.reranker import HttpReranker; print('HttpReranker=OK')" 2>&1 || echo "HttpReranker=MISSING"
echo "=== connectivity 158:8001 ==="
curl -sS -m 5 http://192.168.10.158:8001/health || echo "curl_health_fail"
