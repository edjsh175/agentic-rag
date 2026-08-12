#!/bin/bash
python3 <<'PY'
import json, sqlite3
from pathlib import Path
j=json.loads(Path('/data/rag_python/data/product_relation_backbone.json').read_text(encoding='utf-8'))
want=[e['name'] for e in j['entities']]
con=sqlite3.connect('/data/rag_python/data/rag_relational.db')
con.row_factory=sqlite3.Row
not_seed=[]
missing=[]
for name in want:
    row=con.execute('select name,entity_type,created_by from entities where name=?', (name,)).fetchone()
    if not row:
        missing.append(name)
    elif not str(row['created_by'] or '').startswith('seed:product_backbone'):
        not_seed.append((name, row['entity_type'], row['created_by']))
print('json entities', len(want))
print('missing', missing)
print('not_seed', len(not_seed))
for x in not_seed:
    print(x)
print('seed ents', con.execute("select count(*) from entities where created_by='seed:product_backbone'").fetchone()[0])
print('seed rels', con.execute("select count(*) from relations where created_by='seed:product_backbone'").fetchone()[0])
# compare relation edge sets
formal=set()
for r in j['relations']:
    formal.add((r['source'], r['relation_type'], r['target']))
db=set()
for r in con.execute('''
select s.name, r.relation_type, t.name
from relations r join entities s on s.id=r.source_entity_id join entities t on t.id=r.target_entity_id
where r.created_by='seed:product_backbone'
'''):
    db.add((r[0], r[1], r[2]))
print('edge only_json', len(formal-db))
print(sorted(list(formal-db))[:20])
print('edge only_db', len(db-formal))
print(sorted(list(db-formal))[:20])
con.close()
PY
