#!/bin/bash
# Recreate rag-service with no-scan bootstrap entry after chroma import.
set -euo pipefail
IMG=rag-backend:cpu-http-reranker

docker rm -f rag-service 2>/dev/null || true
cp -a /tmp/run_api_no_scan.py /data/rag_python/run_api_no_scan.py
sed -i 's/\r$//' /data/rag_python/run_api_no_scan.py

docker run -d --name rag-service --restart unless-stopped \
  -p 10605:10605 \
  -e RERANKER_ENABLED=true \
  -e PYTHONUNBUFFERED=1 \
  -v /data/rag_python/logs:/app/logs \
  -v /data/rag_python/scrape_article:/app/scrape_article \
  -v /data/rag_python/scrapingImages:/app/scrapingImages \
  -v /data/setup/nltk_data:/root/nltk_data \
  -v /data/apache-tomcat-9.0.89/webapps/zsltStaticData:/app/watch_directory \
  -v /data/rag_python/config.ini:/app/config.ini:ro \
  -v /data/rag_python/data:/app/data \
  -v /data/rag_python/chroma_db:/app/chroma_db \
  -v /data/rag_python/run_api_no_scan.py:/app/run_api_no_scan.py:ro \
  -w /app \
  "$IMG" \
  python /app/run_api_no_scan.py

for i in $(seq 1 40); do
  if curl -fsS --max-time 3 http://127.0.0.1:10605/health >/dev/null; then
    break
  fi
  sleep 3
  if [[ $i -eq 40 ]]; then
    docker logs rag-service --tail 80
    exit 1
  fi
done

echo "=== health ==="
curl -fsS http://127.0.0.1:10605/health; echo
echo "=== stats ==="
curl -fsS http://127.0.0.1:10605/stats; echo
echo "=== count ==="
docker exec -e PYTHONPATH=/app -w /app rag-service python -c 'from rag_knowledge.repository.vector_store import VectorStore; VectorStore._instance=None; print(VectorStore().count())'
echo "=== file_index ==="
python3 - <<'PY'
import json
d=json.load(open('/data/rag_python/data/file_index.json',encoding='utf-8'))
print('file_index_files', len(d.get('files',{})))
PY
echo DONE_BOOTSTRAP
