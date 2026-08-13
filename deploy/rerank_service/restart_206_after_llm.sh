#!/bin/bash
set -euo pipefail
# verify config + restart no-scan service + health
grep -n '^llm =' /data/rag_python/config.ini | head -5
sed -n '16,21p' /data/rag_python/config.ini
docker restart rag-service
for i in $(seq 1 40); do
  curl -fsS --max-time 3 http://127.0.0.1:10605/health >/dev/null && break
  sleep 3
  if [[ $i -eq 40 ]]; then docker logs rag-service --tail 50; exit 1; fi
done
curl -fsS http://127.0.0.1:10605/health; echo
curl -fsS http://127.0.0.1:10605/models 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); print(json.dumps({k:d.get(k) for k in d if k in ("current","llm","embedding","models") or "llm" in k.lower()}, ensure_ascii=False)[:800] if isinstance(d,dict) else d)' || true
