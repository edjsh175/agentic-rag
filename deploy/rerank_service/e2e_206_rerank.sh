#!/bin/bash
set -euo pipefail
echo "=== 158 health from 206 ==="
curl -fsS --max-time 5 http://192.168.10.158:8001/health; echo

echo "=== sample query ==="
curl -fsS --max-time 120 -H 'Content-Type: application/json' \
  -d '{"question":"什么是 StampServer？","kb_name":"已发布文章","top_k":5}' \
  http://127.0.0.1:10605/query > /tmp/q.json || {
  echo "query failed"; docker logs rag-service --tail 50; exit 1
}
python3 - <<'PY'
import json
p='/tmp/q.json'
d=json.load(open(p,encoding='utf-8'))
ans=(d.get('answer') or d.get('response') or '')[:200]
srcs=d.get('sources') or d.get('source_documents') or []
print('answer_head:', ans.replace('\n',' ')[:200])
print('sources_n:', len(srcs) if isinstance(srcs,list) else type(srcs))
print('keys:', sorted(d.keys())[:20])
PY

echo "=== recent rerank logs ==="
docker logs rag-service --since 3m 2>&1 | grep -iE 'rerank|HttpReranker|8001|重排序' | tail -30 || true
