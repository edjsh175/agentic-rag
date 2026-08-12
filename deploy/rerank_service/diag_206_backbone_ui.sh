#!/bin/bash
set -euo pipefail
echo "=== host files ==="
ls -la /data/rag_python/data/product_relation_backbone*.json
sha256sum /data/rag_python/data/product_relation_backbone.json /data/rag_python/data/product_relation_backbone_preview.json
python3 - <<'PY'
import json
from pathlib import Path
for n in ['product_relation_backbone.json','product_relation_backbone_preview.json']:
    d=json.loads(Path('/data/rag_python/data',n).read_text(encoding='utf-8'))
    print(n, len(d.get('entities') or []), len(d.get('relations') or []))
PY

echo "=== container view ==="
docker exec rag-service ls -la /app/data/product_relation_backbone*.json
docker exec rag-service sha256sum /app/data/product_relation_backbone.json /app/data/product_relation_backbone_preview.json
docker exec rag-service python - <<'PY'
import json
from pathlib import Path
for n in ['product_relation_backbone.json','product_relation_backbone_preview.json']:
    p=Path('/app/data')/n
    d=json.loads(p.read_text(encoding='utf-8'))
    print('container', n, len(d.get('entities') or []), len(d.get('relations') or []), 'bytes', p.stat().st_size)
PY

echo "=== API local ==="
curl -fsS http://127.0.0.1:10605/admin/knowledge_graph/product_backbone_preview > /tmp/api_prev.json
curl -fsS http://127.0.0.1:10605/admin/knowledge_graph/product_backbone_preview_complex > /tmp/api_complex.json || true
python3 - <<'PY'
import json
from pathlib import Path
for p in ['/tmp/api_prev.json','/tmp/api_complex.json']:
    path=Path(p)
    if not path.exists() or path.stat().st_size==0:
        print(p,'empty/missing'); continue
    d=json.loads(path.read_text(encoding='utf-8'))
    print(p, 'nodes', len(d.get('nodes') or []), 'edges', len(d.get('edges') or []), 'keys', list(d.keys())[:10])
PY

echo "=== via nginx 8004 ==="
curl -sk https://127.0.0.1:8004/api/admin/knowledge_graph/product_backbone_preview > /tmp/api_via_nginx.json || curl -s http://127.0.0.1:8004/api/admin/knowledge_graph/product_backbone_preview > /tmp/api_via_nginx.json || true
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/tmp/api_via_nginx.json')
if p.exists() and p.stat().st_size:
    d=json.loads(p.read_text(encoding='utf-8'))
    print('nginx api nodes', len(d.get('nodes') or []), 'edges', len(d.get('edges') or []))
else:
    print('nginx api failed or empty')
PY

echo "=== formal graph sample counts ==="
curl -fsS 'http://127.0.0.1:10605/admin/knowledge_graph?limit=5' | head -c 200; echo
python3 - <<'PY'
import sqlite3
con=sqlite3.connect('/data/rag_python/data/rag_relational.db')
print('seed', con.execute("select count(*) from entities where created_by='seed:product_backbone'").fetchone()[0],
      con.execute("select count(*) from relations where created_by='seed:product_backbone'").fetchone()[0])
# sample names that were deleted in sync (old 175 had extra StampServer services)
for name in ['Apache数据服务','GB28181视频服务','Port服务','ROAM发布服务']:
    r=con.execute("select count(*) from entities where name=?", (name,)).fetchone()[0]
    er=con.execute("""select count(*) from relations r
      join entities s on s.id=r.source_entity_id
      where s.name=? and r.created_by='seed:product_backbone'""", (name,)).fetchone()[0]
    print('entity', name, r, 'seed_rels_as_source', er)
con.close()
PY
