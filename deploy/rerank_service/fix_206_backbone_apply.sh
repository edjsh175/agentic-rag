#!/bin/bash
# Fix StampManager type conflict then re-apply failed backbone batch (align to local formal: keep Product).
set -euo pipefail
BATCH=1abf35b7-4da4-461b-9786-08abf254f348
DB=/app/data/rag_relational.db
BACKUP=/app/data/backups/rag_relational_pre_backbone_replace_20260812_083103.db

python3 <<'PY'
import json, sqlite3
BATCH='1abf35b7-4da4-461b-9786-08abf254f348'
con=sqlite3.connect('/data/rag_python/data/rag_relational.db')
con.row_factory=sqlite3.Row
# Patch approved entity candidate StampManager -> Product (match existing DB / local formal)
rows=con.execute(
    "SELECT id, payload_json FROM extraction_candidates WHERE batch_id=? AND candidate_kind='entity'",
    (BATCH,),
).fetchall()
patched=0
for r in rows:
    p=json.loads(r['payload_json'])
    if p.get('name')=='StampManager' and p.get('entity_type')!='Product':
        p['entity_type']='Product'
        con.execute('UPDATE extraction_candidates SET payload_json=? WHERE id=?',
                    (json.dumps(p, ensure_ascii=False), r['id']))
        patched+=1
# reset batch to approved for re-apply
con.execute("UPDATE extraction_batches SET status='approved', error_text='' WHERE id=?", (BATCH,))
con.commit()
print('patched', patched)
# confirm
for r in con.execute("SELECT payload_json FROM extraction_candidates WHERE batch_id=? AND candidate_kind='entity'", (BATCH,)):
    p=json.loads(r['payload_json'])
    if p.get('name')=='StampManager':
        print('StampManager candidate type', p.get('entity_type'))
row=con.execute('select id,status,error_text from extraction_batches where id=?', (BATCH,)).fetchone()
print('batch', dict(row) if row else None)
print('cand_status', con.execute('select status, count(*) from extraction_candidates where batch_id=? group by status', (BATCH,)).fetchall())
con.close()
PY

docker exec -e PYTHONPATH=/app -w /app rag-service \
  python run_graph_build.py apply --batch "$BATCH" \
  --confirm-db-path "$DB" \
  --confirm-batch "$BATCH" \
  --confirm-backup "$BACKUP"

python3 <<'PY'
import sqlite3
con=sqlite3.connect('/data/rag_python/data/rag_relational.db')
print('entities', con.execute("select count(*) from entities where created_by='seed:product_backbone'").fetchone()[0])
print('relations', con.execute("select count(*) from relations where created_by='seed:product_backbone'").fetchone()[0])
print('StampManager', con.execute("select entity_type from entities where name='StampManager'").fetchone()[0])
print('batch', con.execute("select status, error_text from extraction_batches where id='1abf35b7-4da4-461b-9786-08abf254f348'").fetchone())
con.close()
PY

curl -fsS http://127.0.0.1:10605/admin/knowledge_graph/product_backbone_preview > /tmp/bb.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/bb.json',encoding='utf-8'))
print('preview api', len(d.get('nodes') or []), len(d.get('edges') or []))
PY
