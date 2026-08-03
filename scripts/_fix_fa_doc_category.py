import sqlite3

conn = sqlite3.connect('data/rag_relational.db')
conn.row_factory = sqlite3.Row

# Update doc_category for FunctionArea nodes under PipelineBuilder
conn.execute("""
    UPDATE entities
    SET doc_category = 'StampTools'
    WHERE entity_type = 'FunctionArea' AND (name LIKE 'PipelineBuilder%' OR doc_category = '')
""")

conn.commit()
print("Updated FunctionArea entities doc_category to 'StampTools'")
