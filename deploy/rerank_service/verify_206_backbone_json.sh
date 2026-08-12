#!/bin/bash
set -euo pipefail
curl -fsS http://127.0.0.1:10605/admin/knowledge_graph/product_backbone_preview > /tmp/bb_preview_api.json
python3 <<'PY'
import json
from pathlib import Path
d=json.loads(Path('/tmp/bb_preview_api.json').read_text(encoding='utf-8'))
nodes=d.get('nodes') or d.get('entities') or []
edges=d.get('edges') or d.get('relations') or []
print('api_nodes', len(nodes), 'api_edges', len(edges), 'keys', list(d.keys())[:12])
for name in ['product_relation_backbone.json','product_relation_backbone_preview.json']:
    p=Path('/data/rag_python/data')/name
    j=json.loads(p.read_text(encoding='utf-8'))
    print(name, 'entities', len(j.get('entities') or []), 'relations', len(j.get('relations') or []), 'bytes', p.stat().st_size)
PY
