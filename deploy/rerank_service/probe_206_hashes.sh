#!/bin/bash
set -euo pipefail
echo "=== sha256 of key files in container ==="
docker exec rag-service sh -c 'python - <<"PY"
import hashlib, pathlib, os, time
files = [
  "/app/rag_knowledge/__init__.py",
  "/app/rag_knowledge/config.py",
  "/app/rag_knowledge/services/rag.py",
  "/app/rag_knowledge/services/scanner.py",
  "/app/rag_knowledge/services/semantic_chunker.py",
  "/app/rag_knowledge/services/graph_resync.py",
  "/app/rag_knowledge/services/knowledge_base_consistency.py",
  "/app/rag_knowledge/services/rebuild_coordinator.py",
  "/app/rag_knowledge/services/chunk_index_lookup.py",
  "/app/rag_knowledge/repository/vector_store.py",
  "/app/rag_knowledge/repository/relational_db.py",
  "/app/rag_knowledge/api/routes.py",
  "/app/rag_knowledge/services/reranker.py",
  "/app/rag_knowledge/services/retrieval_strategy.py",
  "/app/rag_knowledge/services/sdk_code_job.py",
  "/app/run.py",
  "/app/run_api_no_scan.py",
]
for p in files:
    path = pathlib.Path(p)
    if not path.exists():
        print("MISSING", p)
        continue
    h = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    mt = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
    print(f"{h}  {path.stat().st_size:8d}  {mt}  {p}")
print("HAS_GRAPH_RESYNC", pathlib.Path("/app/rag_knowledge/services/graph_resync.py").exists())
print("HAS_SDK_CODE_JOB", pathlib.Path("/app/rag_knowledge/services/sdk_code_job.py").exists())
print("HAS_CHUNK_INDEX", pathlib.Path("/app/rag_knowledge/services/chunk_index_lookup.py").exists())
PY'
echo "=== frontend nginx html ==="
ls -lah /data/html/ragWeb/index.html 2>/dev/null || ls -lah /data/html/ragWeb/ 2>/dev/null | head
stat -c '%y %n' /data/html/ragWeb/index.html 2>/dev/null || true
echo "=== docker images ==="
docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.CreatedSince}} {{.Size}}' | grep -E 'rag-backend|rag-web' || docker images | head
