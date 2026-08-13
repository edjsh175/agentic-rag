#!/bin/bash
set -euo pipefail
docker exec -e PYTHONPATH=/app -w /app rag-service python - <<'PY'
from collections import Counter
from rag_knowledge.repository.vector_store import VectorStore
VectorStore._instance = None
vs = VectorStore()
# discover collection attr
attrs = [a for a in dir(vs) if 'collect' in a.lower() or a in ('client','_client','store')]
print('attrs', attrs)
col = getattr(vs, 'collection', None) or getattr(vs, '_collection', None)
if col is None and hasattr(vs, 'get_collection'):
    col = vs.get_collection()
print('col_type', type(col))
count = col.count()
print('count', count)
got = col.get(include=['metadatas'])
metas = got.get('metadatas') or []
print('metas', len(metas))
print('review_status', dict(Counter((m or {}).get('review_status') or '<none>' for m in metas).most_common()))
print('kb_name', dict(Counter((m or {}).get('kb_name') or '<none>' for m in metas).most_common()))
approved = sum(1 for m in metas if (m or {}).get('review_status') == 'approved')
pending = sum(1 for m in metas if (m or {}).get('review_status') == 'pending')
print('approved', approved, 'pending', pending, 'rejected', sum(1 for m in metas if (m or {}).get('review_status')=='rejected'))
# sample sources containing pipeline
hits=[]
for m in metas:
    src=str((m or {}).get('source') or (m or {}).get('file_path') or '')
    if 'pipeline' in src.lower() or '管线' in src:
        hits.append((src, (m or {}).get('review_status'), (m or {}).get('kb_name'), (m or {}).get('doc_category')))
print('pipeline_related_meta_rows', len(hits))
for h in hits[:15]:
    print(' ', h)
PY

echo "=== ask pipeline nonstream ==="
curl -fsS --max-time 120 -H 'Content-Type: application/json' \
  -d '{"question":"pipeline","kb_name":"全部知识库"}' \
  http://127.0.0.1:10605/query > /tmp/q_pipeline.json || true
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/tmp/q_pipeline.json')
if not p.exists() or not p.stat().st_size:
    print('query failed/empty'); raise SystemExit
d=json.loads(p.read_text(encoding='utf-8'))
ans=(d.get('answer') or '')[:300]
srcs=d.get('source_documents') or []
print('answer_head', ans.replace('\n',' ')[:300])
print('sources', len(srcs))
print('keys', list(d.keys()))
if d.get('downshift_notice'):
    print('downshift', d.get('downshift_notice'))
PY

echo "=== recent log around pipeline / 未查询 ==="
grep -nE "pipeline|未查询到|0 个来源|source_documents|检索结果|retrieve" /data/rag_python/logs/rag.log | tail -80

echo "=== file_index summary ==="
python3 - <<'PY'
import json
from pathlib import Path
from collections import Counter
p=Path('/data/rag_python/data/file_index.json')
data=json.loads(p.read_text(encoding='utf-8'))
print('type', type(data).__name__)
if isinstance(data, dict):
    # maybe {"files":...} or path map
    if 'files' in data:
        files=data['files']
    else:
        files=data
    print('entries', len(files))
    # try parse records
    statuses=Counter(); kbs=Counter()
    newest=[]
    for k,v in (files.items() if isinstance(files,dict) else enumerate(files)):
        if not isinstance(v, dict):
            continue
        statuses[(v.get('review_status') or v.get('status') or '<none>')] += 1
        kbs[(v.get('kb_name') or '<none>')] += 1
        newest.append((v.get('updated_at') or v.get('mtime') or v.get('indexed_at') or '', k if isinstance(k,str) else v.get('file_path') or v.get('path'), v.get('chunk_count')))
    print('file_status', dict(statuses))
    print('file_kb', dict(kbs.most_common()))
    newest=sorted([x for x in newest if x[0]], reverse=True)[:12]
    print('newest_files')
    for row in newest:
        print(' ', row)
PY
