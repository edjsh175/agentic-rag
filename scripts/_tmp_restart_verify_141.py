#!/usr/bin/env python3
import json
import time
import urllib.request

import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)
_i, o, e = c.exec_command("cd /opt/rag && docker compose restart rag-service", timeout=120, get_pty=True)
print(o.read().decode(errors="replace").encode("ascii", "replace").decode())

for i in range(24):
    time.sleep(5)
    _i, o, e = c.exec_command(
        "docker inspect --format='{{.State.Health.Status}}' rag-service", timeout=20
    )
    st = o.read().decode().strip()
    print(f"wait {i} {st}", flush=True)
    if st == "healthy":
        break

c.close()

for path in ("/health", "/stats"):
    with urllib.request.urlopen(f"http://192.168.10.141:10605{path}", timeout=30) as r:
        print(path, r.status, r.read().decode()[:500])

# smoke query
payload = json.dumps({"question": "StampTools 是什么？", "kb_name": "全部知识库"}).encode()
req = urllib.request.Request(
    "http://192.168.10.141:10605/query",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=300) as r:
        body = r.read().decode()
        print("QUERY", r.status, body[:1200])
except Exception as exc:
    print("QUERY_ERR", exc)
