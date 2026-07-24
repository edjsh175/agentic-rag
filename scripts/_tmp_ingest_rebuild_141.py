#!/usr/bin/env python3
"""List watch files on 141, rebuild chroma with approve_all, verify stats."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import paramiko

HOST = "192.168.10.141"
USER = "root"
PASSWORD = "123456"
BASE = f"http://{HOST}:10605"


def run(c, cmd, timeout=120):
    print(f"\n======= {cmd[:140]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    print(out.encode("ascii", "replace").decode(), end="" if out.endswith("\n") else "\n", flush=True)
    return o.channel.recv_exit_status(), out


def http_json(method: str, url: str, payload: dict | None = None, timeout: int = 3600):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode(errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    run(
        c,
        "echo FILES=$(find /data/apache-tomcat-9.0.89/webapps/zsltStaticData -type f | wc -l); "
        "find /data/apache-tomcat-9.0.89/webapps/zsltStaticData -type f | head -40; "
        "du -sh /data/apache-tomcat-9.0.89/webapps/zsltStaticData",
    )

    # ollama via forwarder from container
    code, out = run(
        c,
        "docker exec rag-service python -c "
        "\"import httpx; r=httpx.get('http://192.168.10.2:11435/api/tags', timeout=10); "
        "print(r.status_code, len(r.json().get('models',[])))\"",
    )
    if "200" not in out:
        print("OLLAMA_UNREACHABLE_FROM_CONTAINER", flush=True)
        return 1

    print("\n======= POST /rebuild =======", flush=True)
    status, body = http_json(
        "POST",
        f"{BASE}/rebuild",
        {"confirmation": "REBUILD_KNOWLEDGE_BASE", "approve_all_chunks": True},
        timeout=7200,
    )
    print(f"REBUILD_HTTP={status}\n{body[:3000]}", flush=True)
    if status != 200:
        run(c, "cd /opt/rag && docker compose logs --tail=100")
        return 1

    time.sleep(2)
    for path in ("/health", "/stats", "/stats/chunks"):
        st, b = http_json("GET", f"{BASE}{path}", timeout=60)
        print(f"\nGET {path} -> {st}\n{b[:1500]}", flush=True)

    run(
        c,
        "du -sh /data/rag_python/chroma_db; "
        "python3 -c \"import json; d=json.load(open('/data/rag_python/data/file_index.json')); "
        "print('file_index_entries', len(d) if isinstance(d, dict) else type(d))\"",
    )
    c.close()
    print("\nINGEST_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
