#!/usr/bin/env python3
"""Deploy web/dist to /data/html/ragWeb and add production-like nginx on :8088."""
from __future__ import annotations

import os
from pathlib import Path

import paramiko

HOST = "192.168.10.141"
USER = "root"
PASSWORD = "123456"
LOCAL_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
REMOTE_ROOT = "/data/html/ragWeb"

NGINX_CONF = r"""
# RAG frontend (production-like: host Nginx + static ragWeb + API proxy)
# Port 8088 avoids colliding with existing :80 Stamp portal on this VM.
server {
    listen 8088;
    server_name _;

    root /data/html/ragWeb;
    index index.html;

    client_max_body_size 100m;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:10605/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    location /scraping/ {
        alias /data/rag_python/scrapingImages/;
    }
}
"""


def run(c, cmd, timeout=60):
    print(f"======= {cmd[:120]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    print(out.encode("ascii", "replace").decode(), flush=True)
    return o.channel.recv_exit_status(), out


def sftp_put_dir(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    try:
        sftp.stat(remote)
    except OSError:
        sftp.mkdir(remote)
    for root, dirs, files in os.walk(local):
        rel = os.path.relpath(root, local).replace("\\", "/")
        remote_dir = remote if rel == "." else f"{remote}/{rel}"
        try:
            sftp.stat(remote_dir)
        except OSError:
            sftp.mkdir(remote_dir)
        for name in files:
            lp = Path(root) / name
            rp = f"{remote_dir}/{name}"
            print(f"PUT {lp.relative_to(local)} -> {rp}", flush=True)
            sftp.put(str(lp), rp)


def main() -> int:
    if not (LOCAL_DIST / "index.html").is_file():
        print(f"MISSING dist: {LOCAL_DIST}", flush=True)
        return 1

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    run(c, f"mkdir -p {REMOTE_ROOT} && rm -rf {REMOTE_ROOT}/* {REMOTE_ROOT}/.[!.]* 2>/dev/null; mkdir -p {REMOTE_ROOT}")
    sftp = c.open_sftp()
    sftp_put_dir(sftp, LOCAL_DIST, REMOTE_ROOT)
    with sftp.file("/etc/nginx/conf.d/rag.conf", "w") as f:
        f.write(NGINX_CONF.lstrip("\n"))
    sftp.close()

    code, _ = run(c, "nginx -t && systemctl reload nginx")
    if code != 0:
        print("NGINX_RELOAD_FAILED", flush=True)
        return 1

    run(c, f"ls -la {REMOTE_ROOT}; ls -la {REMOTE_ROOT}/assets | head")
    run(
        c,
        "curl -sS -m 5 -o /dev/null -w 'page=%{http_code}\\n' http://127.0.0.1:8088/; "
        "curl -sS -m 5 -o /dev/null -w 'api_health=%{http_code}\\n' http://127.0.0.1:8088/api/health; "
        "curl -sS -m 5 http://127.0.0.1:8088/api/health; echo; "
        "ss -lntp | grep ':8088' || true",
    )
    c.close()
    print("\nFRONTEND_DEPLOY_DONE", flush=True)
    print(f"Open: http://{HOST}:8088/", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
