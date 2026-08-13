#!/bin/bash
set -euo pipefail
python3 <<'PY'
import sqlite3
from pathlib import Path
db=Path('/data/rag_python/chroma_db/chroma.sqlite3')
print('sqlite_size', db.stat().st_size)
con=sqlite3.connect(str(db))
print('collections:')
for r in con.execute('select id,name,dimension from collections'):
    print(r)
print('segments:')
for r in con.execute('select id,type,collection,scope from segments'):
    print(r)
print('embeddings_total', con.execute('select count(*) from embeddings').fetchone()[0])
# per collection via segments
for r in con.execute('''
select c.name, count(e.id)
from collections c
join segments s on s.collection = c.id
left join embeddings e on e.segment_id = s.id
group by c.name
'''):
    print('name_count', r)
# list segment dirs present
root=Path('/data/rag_python/chroma_db')
print('dirs', sorted([p.name for p in root.iterdir() if p.is_dir()]))
PY

docker exec -e PYTHONPATH=/app -w /app rag-service python - <<'PY'
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.config import Config
Config._instance=None
VectorStore._instance=None
c=Config()
print('cfg collection', c.collection_name)
vs=VectorStore()
print('count', vs.count())
chroma=vs.get_chroma()
col=chroma._collection
print('col name', col.name)
# peek ids
got=col.get(limit=5, include=[])
print('sample ids', (got.get('ids') or [])[:5])
print('get all ids len', len(col.get(include=[]).get('ids') or []))
PY
