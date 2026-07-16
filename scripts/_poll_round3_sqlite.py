import sqlite3
c = sqlite3.connect(r"data/rag_relational.db")
print("e", c.execute("select count(*) from entities").fetchone()[0], "r", c.execute("select count(*) from relations").fetchone()[0])
print("bb", c.execute("select count(*) from entities where created_by='seed:product_backbone'").fetchone()[0], c.execute("select count(*) from relations where created_by='seed:product_backbone'").fetchone()[0])
rows = c.execute("select substr(id,1,8),status,mode,created_at,stats_json from extraction_batches order by created_at desc limit 2").fetchall()
for r in rows:
    print(r[0], r[1], r[2], r[3], (r[4] or "")[:200])
