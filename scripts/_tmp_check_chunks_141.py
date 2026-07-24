#!/usr/bin/env python3
import json
import urllib.request

import paramiko

HOST = "192.168.10.141"
BASE = f"http://{HOST}:10605"

for path in ("/stats", "/stats/chunks", "/scan/index"):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        body = r.read().decode()
        print(f"==== {path} ====")
        print(body[:2500])

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)
_i, o, e = c.exec_command(
    "docker exec rag-service python - <<'PY'\n"
    "from rag_knowledge.repository.vector_store import VectorStore\n"
    "vs=VectorStore()\n"
    "print('count', vs.count())\n"
    "col=vs._collection\n"
    "print('collection', getattr(col,'name',None))\n"
    "got=col.get(limit=3, include=['metadatas','documents'])\n"
    "print('ids', got.get('ids'))\n"
    "print('n_meta', len(got.get('metadatas') or []))\n"
    "if got.get('metadatas'):\n"
    "  print('meta0', got['metadatas'][0])\n"
    "PY",
    timeout=60,
    get_pty=True,
)
print(o.read().decode(errors="replace").encode("ascii","replace").decode())
c.close()
