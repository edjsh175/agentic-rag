#!/bin/bash
set -euo pipefail
docker exec -e PYTHONPATH=/app -w /app rag-service python -c "
from collections import Counter
from rag_knowledge.repository.vector_store import VectorStore
VectorStore._instance = None
vs = VectorStore()
print('206_count', vs.count())
col = vs.get_chroma()._collection
metas = (col.get(include=['metadatas']).get('metadatas') or [])
print('metas', len(metas))
print('review_status', dict(Counter((m or {}).get('review_status') or '<none>' for m in metas).most_common()))
print('kb_name', dict(Counter((m or {}).get('kb_name') or '<none>' for m in metas).most_common()))
pipe = [((m or {}).get('source') or (m or {}).get('file_path'), (m or {}).get('review_status'), (m or {}).get('kb_name')) for m in metas if 'pipeline' in str((m or {}).get('source') or (m or {}).get('file_path') or '').lower() or '管线' in str((m or {}).get('source') or (m or {}).get('file_path') or '')]
print('pipeline_rows', len(pipe))
for x in pipe[:12]:
    print(' ', x)
"

echo "=== log window around empty pipeline ==="
sed -n '430,500p' /data/rag_python/logs/rag.log

echo "=== today's no-source / empty answers ==="
grep -E "0 个来源|未查询到|模型输出为空|downshift" /data/rag_python/logs/rag.log | tail -50

echo "=== chroma mtime vs local expectation ==="
stat /data/rag_python/chroma_db/chroma.sqlite3 | sed -n '1,8p'
du -sh /data/rag_python/chroma_db
