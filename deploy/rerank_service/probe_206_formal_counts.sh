#!/bin/bash
python3 <<'PY'
import sqlite3
con=sqlite3.connect('/data/rag_python/data/rag_relational.db')
cur=con.cursor()
def q(sql):
    return cur.execute(sql).fetchone()[0]
print('206 total entities', q("select count(*) from entities where review_status='approved'"))
print('206 total relations', q("select count(*) from relations where review_status='approved'"))
print('206 seed ents', q("select count(*) from entities where created_by='seed:product_backbone'"))
print('206 seed rels', q("select count(*) from relations where created_by='seed:product_backbone'"))
# product-ish types often shown in product mode
types=('Product','Tool','Utility','Service','Module','Format','Layer','FunctionArea','ManagementModule','RenderingSystem','MainTool','StampServerService','ServiceLibrary','EnvironmentComponent','Command')
ph=','.join('?'*len(types))
print('206 product-mode-ish entities', cur.execute(f"select count(*) from entities where review_status='approved' and entity_type in ({ph})", types).fetchone()[0])
con.close()
PY
