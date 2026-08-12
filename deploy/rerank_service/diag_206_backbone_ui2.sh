#!/bin/bash
set -euo pipefail
echo "=== container files (quoted) ==="
docker exec rag-service sh -c 'ls -la /app/data/product_relation_backbone*.json; sha256sum /app/data/product_relation_backbone.json /app/data/product_relation_backbone_preview.json'

echo "=== which file service loads ==="
docker exec -e PYTHONPATH=/app -w /app rag-service python - <<'PY'
from rag_knowledge.services.product_backbone_preview import ProductBackbonePreviewService
svc = ProductBackbonePreviewService()
print('path', svc.path)
print('exists', svc.path.exists())
g = svc.list_graph_data()
print('service nodes', len(g.nodes), 'edges', len(g.edges))
PY

echo "=== nginx proxy ==="
curl -sk --max-time 10 https://127.0.0.1:8004/api/admin/knowledge_graph/product_backbone_preview -o /tmp/n1.json || true
curl -s --max-time 10 http://127.0.0.1:8004/api/admin/knowledge_graph/product_backbone_preview -o /tmp/n2.json || true
python3 - <<'PY'
import json
from pathlib import Path
for p in ['/tmp/n1.json','/tmp/n2.json']:
    path=Path(p)
    if not path.exists() or path.stat().st_size==0:
        print(p, 'empty'); continue
    try:
        d=json.loads(path.read_text(encoding='utf-8'))
        print(p, 'nodes', len(d.get('nodes') or []), 'edges', len(d.get('edges') or []))
    except Exception as e:
        print(p, 'parse_err', e, path.read_text(encoding='utf-8')[:200])
PY
