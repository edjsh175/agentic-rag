#!/bin/bash
python3 <<'PY'
import sqlite3
c=sqlite3.connect('/data/rag_python/data/rag_relational.db')
print('batches', [x[1] for x in c.execute('pragma table_info(extraction_batches)')])
print('cands', [x[1] for x in c.execute('pragma table_info(extraction_candidates)')])
PY
