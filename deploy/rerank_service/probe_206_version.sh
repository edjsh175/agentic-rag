#!/bin/bash
set -euo pipefail
echo "=== container ==="
docker inspect rag-service --format 'name={{.Name}} image={{.Config.Image}} created={{.Created}} started={{.State.StartedAt}} cmd={{json .Config.Cmd}}'
echo "=== image ==="
docker image inspect rag-backend:cpu-http-reranker --format 'id={{.Id}} created={{.Created}} size={{.Size}}'
echo "=== image history (top 8) ==="
docker history rag-backend:cpu-http-reranker --no-trunc --format '{{.CreatedBy}} | {{.CreatedSince}} | {{.Size}}' | head -8
echo "=== runtime files mtime/sha256 ==="
docker exec rag-service python - <<'PY'
import hashlib, os, pathlib
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
  "/app/run.py",
  "/app/run_api_no_scan.py",
]
for p in files:
    path = pathlib.Path(p)
    if not path.exists():
        print(f"MISSING {p}")
        continue
    st = path.stat()
    h = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    print(f"{h}  {st.st_mtime:.0f}  {st.st_size:8d}  {p}")
PY
echo "=== health ==="
curl -fsS --max-time 5 http://127.0.0.1:10605/health; echo
echo "=== config model ==="
sed -n '13,21p' /data/rag_python/config.ini
echo "=== chroma count via stats ==="
curl -fsS --max-time 5 http://127.0.0.1:10605/stats; echo
echo "=== git-like version markers in image ==="
docker exec rag-service sh -c 'ls -lah /app/*.py /app/VERSION /app/.git 2>/dev/null | head; grep -R "APP_VERSION\|__version__" /app/rag_knowledge/__init__.py 2>/dev/null'
