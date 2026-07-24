#!/usr/bin/env python3
"""compose up on 141, /health, then rebuild chroma (empty watch OK)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import paramiko

HOST = "192.168.10.141"
USER = "root"
PASSWORD = "123456"


def run(c, cmd, timeout=120):
    print(f"\n======= {cmd[:140]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    raw = o.read()
    out = raw.decode(errors="replace")
    safe = out.encode("ascii", errors="replace").decode("ascii")
    print(safe, end="" if safe.endswith("\n") else "\n", flush=True)
    return o.channel.recv_exit_status(), out


def http_get(url: str, timeout: int = 10) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode(errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    # ensure image tag matches compose
    run(c, "cd /opt/rag && docker compose down 2>/dev/null; true")
    code, _ = run(c, "cd /opt/rag && docker compose up -d", timeout=180)
    if code != 0:
        run(c, "cd /opt/rag && docker compose logs --tail=80")
        print("COMPOSE_UP_FAILED", flush=True)
        return 1

    # wait health
    healthy = False
    for i in range(36):
        time.sleep(5)
        code, out = run(
            c,
            "docker inspect --format='{{.State.Health.Status}}' rag-service 2>/dev/null || "
            "docker inspect --format='{{.State.Status}}' rag-service",
            timeout=30,
        )
        status = (out or "").strip().splitlines()[-1] if out else ""
        print(f"wait[{i}] container_health={status!r}", flush=True)
        if status == "healthy":
            healthy = True
            break
        if status in {"exited", "dead"}:
            run(c, "cd /opt/rag && docker compose logs --tail=100")
            print("CONTAINER_DEAD", flush=True)
            return 1

    # /health from host (port mapped)
    code, body = http_get(f"http://{HOST}:10605/health", timeout=15)
    print(f"\nHEALTH_HTTP={code}\n{body[:800]}", flush=True)
    if code != 200:
        run(c, "cd /opt/rag && docker compose logs --tail=120")
        print("HEALTH_FAILED", flush=True)
        return 1

    # rebuild chroma (confirmation required)
    payload = json.dumps(
        {"confirmation": "REBUILD_KNOWLEDGE_BASE", "approve_all_chunks": True}
    ).encode()
    req = urllib.request.Request(
        f"http://{HOST}:10605/rebuild",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    print("\n======= POST /rebuild =======", flush=True)
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            rebuild_body = resp.read().decode(errors="replace")
            print(f"REBUILD_HTTP={resp.status}\n{rebuild_body[:2000]}", flush=True)
    except urllib.error.HTTPError as exc:
        rebuild_body = exc.read().decode(errors="replace")
        print(f"REBUILD_HTTP={exc.code}\n{rebuild_body[:2000]}", flush=True)
        run(c, "cd /opt/rag && docker compose logs --tail=80")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"REBUILD_ERROR={exc}", flush=True)
        run(c, "cd /opt/rag && docker compose logs --tail=80")
        return 1

    # post checks
    code2, body2 = http_get(f"http://{HOST}:10605/health", timeout=15)
    print(f"\nHEALTH_AFTER_REBUILD={code2}\n{body2[:800]}", flush=True)
    run(
        c,
        "du -sh /data/rag_python/chroma_db; "
        "ls -la /data/rag_python/chroma_db | head; "
        "python3 -c \"import json;print('file_index_keys',len(json.load(open('/data/rag_python/data/file_index.json'))))\" 2>/dev/null || "
        "wc -c /data/rag_python/data/file_index.json",
    )
    run(c, "cd /opt/rag && docker compose ps")

    if not healthy:
        print("NOTE: docker health may still be starting; HTTP /health was 200", flush=True)
    print("\nALL_DONE", flush=True)
    c.close()
    return 0 if code2 == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
