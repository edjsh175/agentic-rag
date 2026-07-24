#!/usr/bin/env python3
"""Diagnose empty graph UI on 141."""
import json
import urllib.request

import paramiko

HOST = "192.168.10.141"
BASE = f"http://{HOST}:8088/api"

paths = [
    "/health",
    "/admin/knowledge_graph/stats",
    "/admin/knowledge_graph/entities?limit=5",
    "/admin/knowledge_graph/relations?limit=5",
]
# try common endpoints; adjust after probe
for p in [
    "/admin/knowledge-graph/stats",
    "/admin/knowledge_graph/overview",
    "/admin/graph/stats",
]:
    pass

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)

print("==== DB counts ====", flush=True)
_i, o, e = c.exec_command(
    "python3 - <<'PY'\n"
    "import sqlite3\n"
    "db='/data/rag_python/data/rag_relational.db'\n"
    "con=sqlite3.connect(db)\n"
    "cur=con.cursor()\n"
    "tables=[r[0] for r in cur.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1\")]\n"
    "print('tables', tables)\n"
    "for t in tables:\n"
    "  try:\n"
    "    n=cur.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]\n"
    "    print(f'  {t}: {n}')\n"
    "  except Exception as ex:\n"
    "    print(f'  {t}: ERR {ex}')\n"
    "print('filesize', __import__('os').path.getsize(db))\n"
    "PY",
    timeout=30,
)
print(o.read().decode(errors="replace"), flush=True)

print("==== config graph flags ====", flush=True)
_i, o, e = c.exec_command("grep -A5 '\\[graph_retrieval\\]' /data/rag_python/config.ini; ls -la /data/rag_python/data/product_relation_backbone*.json")
print(o.read().decode(errors="replace"), flush=True)

# discover routes from openapi if available
print("==== probe graph APIs ====", flush=True)
for path in [
    "/admin/knowledge_graph/entities",
    "/admin/knowledge-graph/entities",
    "/admin/knowledge_graph/graph",
    "/admin/knowledge_graph/product_backbone_preview",
]:
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=15) as r:
            body = r.read().decode()
            print(path, r.status, body[:300].replace("\n", " "))
    except Exception as ex:
        print(path, "ERR", ex)

c.close()
