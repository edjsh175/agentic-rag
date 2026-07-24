#!/usr/bin/env python3
"""Verify Ollama from 141 host and rag-service container."""
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)

cmds = [
    "curl -sS -m 8 -o /tmp/ollama_tags.json -w 'host_http=%{http_code}\\n' http://192.168.10.158:11434/api/tags",
    "python3 -c \"import json;d=json.load(open('/tmp/ollama_tags.json'));print('models',len(d.get('models',[])));print('\\n'.join(sorted(m.get('name','') for m in d.get('models',[])[:30])))\"",
    "docker exec rag-service python -c \"import httpx; r=httpx.get('http://192.168.10.158:11434/api/tags', timeout=8); print('container_http', r.status_code); print('models', len(r.json().get('models',[])))\"",
    "curl -sS -m 5 http://127.0.0.1:10605/health",
]
for cmd in cmds:
    print(f"======= {cmd[:100]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=60)
    out = o.read().decode(errors="replace")
    err = e.read().decode(errors="replace")
    print((out or err).encode("ascii", "replace").decode(), flush=True)
c.close()
