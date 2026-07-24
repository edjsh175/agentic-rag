#!/usr/bin/env python3
"""Inspect 206 frontend (host Nginx + /data/html/ragWeb). Live backend untouched."""
from __future__ import annotations

import paramiko

HOST, USER, PASSWORD = "192.168.10.206", "root", "ykqgis@2025"


def run(c, cmd, timeout=60):
    print(f"\n======= {cmd[:160]} =======", flush=True)
    _i, o, _e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    print(o.read().decode(errors="replace").encode("ascii", "replace").decode(), flush=True)


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    run(c, "hostname; ls -lah /data/html/ragWeb 2>/dev/null | head -25; echo ---; ls -lah /data/html 2>/dev/null | head -20")
    run(c, "ls /etc/nginx/conf.d/ 2>/dev/null; ls /etc/nginx/nginx.conf 2>/dev/null; rpm -q nginx 2>/dev/null; systemctl is-active nginx 2>/dev/null")
    run(
        c,
        "grep -RIn --include='*.conf' -E 'rag|10605|ragWeb|/api' /etc/nginx/ 2>/dev/null | head -60",
    )
    run(
        c,
        "ss -lntp | grep -E ':80 |:443 |:8088 ' || true; "
        "curl -sS -m 3 -o /dev/null -w 'root_http=%{http_code} size=%{size_download}\\n' http://127.0.0.1/ || true; "
        "curl -sS -m 3 -o /dev/null -w 'rag_http=%{http_code}\\n' http://127.0.0.1/rag/ || true; "
        "curl -sS -m 3 -o /dev/null -w 'ragWeb_http=%{http_code}\\n' http://127.0.0.1/ragWeb/ || true; "
        "curl -sS -m 3 http://127.0.0.1:10605/health; echo",
    )
    # find index.html location and api base hints
    run(
        c,
        "find /data/html -maxdepth 3 -name 'index.html' 2>/dev/null | head -20; "
        "find /data/html/ragWeb -name '*.js' 2>/dev/null | head -5; "
        "grep -Rao --include='*.js' -E '/api|10605|baseURL' /data/html/ragWeb 2>/dev/null | head -20",
    )
    # compare with setup packages
    run(c, "ls -lah /data/setup/rag_20260713 2>/dev/null | head -30; ls -lah /data/setup 2>/dev/null | head -20")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
