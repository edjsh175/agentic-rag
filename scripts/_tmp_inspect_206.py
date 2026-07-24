#!/usr/bin/env python3
"""Read-only + safe smoke diagnostics on production 206 for new rag-backend image."""
from __future__ import annotations

import paramiko

HOST = "192.168.10.206"
USER = "root"
PASSWORD = "ykqgis@2025"


def run(c, cmd, timeout=60):
    print(f"\n======= {cmd[:130]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    print(out.encode("ascii", "replace").decode(), flush=True)
    return o.channel.recv_exit_status(), out


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    # 1) inventory — do not stop anything
    for cmd in [
        "hostname; date; uptime",
        "docker ps -a --format 'table {{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' | head -30",
        "docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedSince}}' | head -20",
        "ls -lah /data/setup 2>/dev/null | head -30",
        "ls -lah /data/rag_python 2>/dev/null | head -30",
        "test -f /data/rag_python/config.ini && echo HAS_CONFIG || echo NO_CONFIG",
        "ss -lntp | grep -E ':10605|:10606|:80 ' || true",
        "docker inspect rag-service --format 'Image={{.Config.Image}} ID={{.Image}} Status={{.State.Status}} Health={{.State.Health.Status}}' 2>/dev/null || "
        "docker ps -a --filter name=rag --format '{{.Names}} {{.Image}} {{.Status}}'",
    ]:
        run(c, cmd)

    # 2) which image IDs exist for rag-backend
    run(
        c,
        "docker images rag-backend -a --no-trunc --format '{{.ID}} {{.Repository}}:{{.Tag}} {{.Size}}'; "
        "echo '---'; docker image inspect 5dea395b85bd --format 'Id={{.Id}} Created={{.Created}} Size={{.Size}}' 2>&1 | head",
    )

    # 3) quick peek at production config (safe keys only)
    run(
        c,
        "grep -E '^(base_url|enabled|method|embedding|llm|persist_directory|db_path|watch_directory|data_dir|log_dir)\\s*=' "
        "/data/rag_python/config.ini 2>/dev/null | head -40",
    )

    # 4) recent logs from live container only (read)
    run(c, "docker logs --tail 40 rag-service 2>&1 | tail -40")
    run(c, "ls -lah /data/rag_python/logs 2>/dev/null | tail -10; tail -n 20 /data/rag_python/logs/rag.log 2>/dev/null || true")

    c.close()
    print("\nINVENTORY_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
