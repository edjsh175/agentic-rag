#!/bin/bash
set -euo pipefail
echo health; curl -fsS --max-time 5 http://127.0.0.1:10605/health || echo HEALTH_FAIL
echo stats; curl -fsS --max-time 5 http://127.0.0.1:10605/stats || echo STATS_FAIL
echo container; docker ps -a --filter name=rag-service --format '{{.Names}} {{.Status}}'
echo file_index; ls -lah /data/rag_python/data/file_index.json
echo chroma; du -sh /data/rag_python/chroma_db
docker exec -e PYTHONPATH=/app -w /app rag-service python -c 'from rag_knowledge.repository.vector_store import VectorStore; VectorStore._instance=None; print("count", VectorStore().count())' || echo COUNT_FAIL
docker logs rag-service --tail 30 2>&1 | tail -30
