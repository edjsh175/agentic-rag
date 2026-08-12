#!/bin/bash
set -euo pipefail
IMG_NEW=rag-backend:cpu-http-reranker

# Find pre-rename or any container holding 10605
echo "=== containers ==="
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'

# Stop anything using name rag-service or old backup still binding port
for c in $(docker ps -aq --filter name=rag-service); do
  echo "stop/rm $c"
  docker stop "$c" >/dev/null || true
done

# Also stop renamed pre_* if still up
for c in $(docker ps -aq --filter name=rag-service_pre_http_rerank); do
  echo "stop old $c"
  docker stop "$c" >/dev/null || true
done

# Remove failed new container if exists with name rag-service
docker rm -f rag-service >/dev/null 2>&1 || true

# Keep one old container renamed for rollback (already renamed earlier); ensure name free
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

echo "=== wait health ==="
for i in $(seq 1 50); do
  if curl -fsS --max-time 3 http://127.0.0.1:10605/health >/tmp/health.json 2>/dev/null; then
    echo "healthy at try $i"
    cat /tmp/health.json; echo
    break
  fi
  sleep 3
  if [ "$i" -eq 50 ]; then
    echo "health timeout"
    docker logs rag-service --tail 100
    exit 1
  fi
done

docker exec rag-service python -c "from rag_knowledge.services.reranker import HttpReranker, create_reranker; r=create_reranker('http','',base_url='http://192.168.10.158:8001'); print('factory',type(r).__name__); print('HttpReranker=OK')"
docker exec rag-service python -c "from rag_knowledge.config import Config; Config._instance=None; c=Config(); print('enabled',c.reranker_enabled,'type',c.reranker_type,'base',c.reranker_base_url)"
# Grep startup log for reranker
docker logs rag-service 2>&1 | grep -i '重排序\|rerank\|HttpReranker' | tail -20 || true
echo DONE
