#!/bin/bash
set -euo pipefail
# Build ~20 medium/long docs like real chunks
python3 - <<'PY'
import json,time,urllib.request
docs=[]
base=("StampServer 是服务端组件。"*80)  # ~longish
for i in range(20):
    docs.append(f"候选{i}: "+base)
payload=json.dumps({"query":"什么是 StampServer？","documents":docs,"top_k":8},ensure_ascii=False).encode('utf-8')
print('payload_bytes',len(payload))
req=urllib.request.Request('http://192.168.10.158:8001/rerank',data=payload,headers={'Content-Type':'application/json'},method='POST')
t0=time.time()
with urllib.request.urlopen(req,timeout=180) as resp:
    data=json.loads(resp.read().decode())
print('elapsed_s',round(time.time()-t0,2),'scores',len(data.get('scores',[])))
PY

# Also bump timeout in config to 120
python3 - <<'PY'
from pathlib import Path
p=Path('/data/rag_python/config.ini')
t=p.read_text(encoding='utf-8')
t2=t.replace('timeout = 30','timeout = 120',1)
if t==t2:
    # try within reranker section only already 30
    import re
    t2=re.sub(r'(\[reranker\][\s\S]*?timeout\s*=\s*)\d+', r'\g<1>120', t, count=1)
p.write_text(t2,encoding='utf-8')
print('timeout line:')
for line in p.read_text(encoding='utf-8').splitlines():
    if 'timeout' in line and 'reranker' not in line:
        pass
import re
m=re.search(r'\[reranker\][\s\S]*?(?=\[|\Z)', p.read_text(encoding='utf-8'))
print(m.group(0) if m else 'missing')
PY

docker restart rag-service
for i in $(seq 1 40); do
  curl -fsS --max-time 3 http://127.0.0.1:10605/health >/dev/null && break
  sleep 3
done
curl -fsS http://127.0.0.1:10605/health; echo

# Second query after warm
curl -fsS --max-time 180 -H 'Content-Type: application/json' \
  -d '{"question":"StampServer 如何修改 IP？","kb_name":"已发布文章"}' \
  http://127.0.0.1:10605/query > /tmp/q2.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/q2.json',encoding='utf-8'))
print('answer_head:', (d.get('answer') or '')[:160].replace('\n',' '))
print('sources', len(d.get('source_documents') or []))
PY
docker logs rag-service --since 4m 2>&1 | grep -iE 'rerank|timeout|8001|重排序' | tail -40
