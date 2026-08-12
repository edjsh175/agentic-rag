#!/bin/bash
# Patch 206 image with HttpReranker client; enable remote rerank to 158.
set -euo pipefail

PATCH_DIR=/tmp/rag_http_rerank_patch
IMG_OLD=rag-backend:cpu-no-reranker
IMG_NEW=rag-backend:cpu-http-reranker
TS=$(date +%Y%m%d%H%M%S)

echo "=== 1) build patched image ==="
cd "$PATCH_DIR"
docker build -t "$IMG_NEW" .

echo "=== 2) backup config.ini ==="
cp -a /data/rag_python/config.ini "/data/rag_python/config.ini.bak_rerank_${TS}"

echo "=== 3) patch [reranker] in config.ini ==="
python3 - <<'PY'
from pathlib import Path
path = Path("/data/rag_python/config.ini")
text = path.read_text(encoding="utf-8")
start = text.find("[reranker]")
if start < 0:
    raise SystemExit("missing [reranker] section")
# find next section after [reranker]
next_idx = text.find("\n[", start + 1)
if next_idx < 0:
    next_idx = len(text)
old = text[start:next_idx]
new = """[reranker]
; HttpReranker → 158 GPU 机
enabled = true
type = http
base_url = http://192.168.10.158:8001
timeout = 30
model =
top_n = 8
candidate_k = 20

"""
path.write_text(text[:start] + new + text[next_idx:], encoding="utf-8")
print("config patched")
PY

grep -A12 '^\[reranker\]' /data/rag_python/config.ini

echo "=== 4) recreate container ==="
# Capture current run flags we need
docker rename rag-service "rag-service_pre_http_rerank_${TS}"

docker run -d \
  --name rag-service \
  --restart unless-stopped \
  -p 10605:10605 \
  -e RERANKER_ENABLED=true \
  -e PYTHONUNBUFFERED=1 \
  -v /data/rag_python/config.ini:/app/config.ini:ro \
  -v /data/rag_python/data:/app/data \
  -v /data/rag_python/chroma_db:/app/chroma_db \
  -v /data/rag_python/logs:/app/logs \
  -v /data/rag_python/scrape_article:/app/scrape_article \
  -v /data/rag_python/scrapingImages:/app/scrapingImages \
  -v /data/setup/nltk_data:/root/nltk_data \
  -v /data/apache-tomcat-9.0.89/webapps/zsltStaticData:/app/watch_directory \
  "$IMG_NEW" \
  python run.py

echo "=== 5) wait health ==="
for i in $(seq 1 40); do
  if curl -fsS --max-time 3 http://127.0.0.1:10605/health >/tmp/health.json 2>/dev/null; then
    echo "healthy at try $i"
    cat /tmp/health.json
    break
  fi
  sleep 3
  if [ "$i" -eq 40 ]; then
    echo "health timeout"
    docker logs rag-service --tail 80
    exit 1
  fi
done

echo "=== 6) verify HttpReranker import ==="
docker exec rag-service python -c "from rag_knowledge.services.reranker import HttpReranker; print('HttpReranker=OK')"

echo "=== 7) stop old container (kept for rollback) ==="
docker stop "rag-service_pre_http_rerank_${TS}" || true
echo "OLD_CONTAINER=rag-service_pre_http_rerank_${TS}"
echo "DONE"
