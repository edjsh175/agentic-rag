#!/bin/bash
set -euo pipefail
python3 <<'PY'
import json, sqlite3
from pathlib import Path
j=json.loads(Path('/data/rag_python/data/product_relation_backbone.json').read_text(encoding='utf-8'))
want={e['name']: e.get('entity_type') for e in j['entities']}
con=sqlite3.connect('/data/rag_python/data/rag_relational.db')
con.row_factory=sqlite3.Row
conflicts=[]
missing=[]
for name, et in want.items():
    row=con.execute('select entity_type, created_by from entities where name=?', (name,)).fetchone()
    if not row:
        missing.append(name)
    elif row['entity_type'] != et:
        conflicts.append((name, row['entity_type'], et, row['created_by']))
print('conflicts', len(conflicts))
for c in conflicts:
    print(c)
print('missing_in_db', len(missing))
print(missing[:20])
# batch state
b=con.execute("select id,status from extraction_batches where id='1abf35b7-4da4-461b-9786-08abf254f348'").fetchone()
print('batch', dict(b) if b else None)
pend=con.execute("select review_status, count(*) from extraction_candidates where batch_id=? group by review_status", ('1abf35b7-4da4-461b-9786-08abf254f348',)).fetchall()
print('candidates', pend)
con.close()
PY
