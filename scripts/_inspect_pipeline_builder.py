import sqlite3

conn = sqlite3.connect('data/rag_relational.db')
conn.row_factory = sqlite3.Row

p = conn.execute("SELECT id, name, entity_type FROM entities WHERE name = 'PipelineBuilder'").fetchone()
print("PipelineBuilder Entity:", dict(p) if p else "NOT FOUND")

if p:
    rels = conn.execute("""
        SELECT r.id, r.relation_type, s.name as src, s.entity_type as src_type, t.name as tgt, t.entity_type as tgt_type, r.created_by
        FROM relations r
        JOIN entities s ON s.id = r.source_entity_id
        JOIN entities t ON t.id = r.target_entity_id
        WHERE s.id = ? OR t.id = ?
    """, (p["id"], p["id"])).fetchall()

    print(f"\nTotal relations involving PipelineBuilder: {len(rels)}")
    for r in rels:
        print(f"  - [{r['relation_type']}] {r['src']} ({r['src_type']}) -> {r['tgt']} ({r['tgt_type']}) | created_by={r['created_by']}")
