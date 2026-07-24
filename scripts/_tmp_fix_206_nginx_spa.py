#!/usr/bin/env python3
"""Add SPA try_files to 206 nginx ragWeb (8004) server block."""
from __future__ import annotations

import time

import paramiko

HOST, USER, PASSWORD = "192.168.10.206", "root", "ykqgis@2025"

EDIT_PY = r'''
from pathlib import Path
p = Path("/etc/nginx/nginx.conf")
# latin-1 preserves all bytes 1:1
text = p.read_bytes().decode("latin-1")
marker = "listen 8004 ssl;"
start = text.find(marker)
if start < 0:
    raise SystemExit("no 8004 server")
window = text[start:start + 3000]
if "try_files $uri $uri/ /index.html" in window:
    print("ALREADY_HAS_TRY_FILES")
    raise SystemExit(0)
idx = text.find("location /scraping/", start)
if idx < 0:
    raise SystemExit("no scraping location")
pos = -1
for trailer in ("\n}\n\n\n\nserver{", "\n}\n\n\nserver{", "\n}\n\nserver{"):
    pos = text.find(trailer, idx)
    if pos >= 0:
        break
if pos < 0:
    raise SystemExit("cannot find 8004 server end")
spa = "\nlocation / {\n    try_files $uri $uri/ /index.html;\n}"
new = text[:pos] + spa + text[pos:]
p.write_bytes(new.encode("latin-1"))
print("INSERTED_SPA")
'''


def run(c, cmd, timeout=60):
    print(f"\n======= {cmd[:160]} =======", flush=True)
    _i, o, _e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    code = o.channel.recv_exit_status()
    print(out.encode("ascii", "replace").decode(), flush=True)
    return code, out


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    ts = time.strftime("%Y%m%d_%H%M%S")
    run(c, f"cp -a /etc/nginx/nginx.conf /etc/nginx/nginx.conf.bak_spa_{ts}")

    sftp = c.open_sftp()
    with sftp.file("/tmp/fix_nginx_spa.py", "w") as f:
        f.write(EDIT_PY)
    sftp.close()

    code, out = run(c, "python3 /tmp/fix_nginx_spa.py")
    if code != 0:
        return 1

    code, out = run(c, "nginx -t -c /etc/nginx/nginx.conf")
    if code != 0:
        run(c, f"cp -a /etc/nginx/nginx.conf.bak_spa_{ts} /etc/nginx/nginx.conf")
        print("NGINX_TEST_FAILED rolled back", flush=True)
        return 1

    run(c, "systemctl reload nginx")
    run(
        c,
        "curl -sk -m 5 -o /dev/null -w 'root=%{http_code}\\n' https://127.0.0.1:8004/; "
        "curl -sk -m 5 -o /dev/null -w 'graph=%{http_code}\\n' https://127.0.0.1:8004/admin/graph; "
        "curl -sk -m 5 -o /dev/null -w 'graph_q=%{http_code}\\n' "
        "'https://127.0.0.1:8004/admin/graph?source=product_backbone_preview'; "
        "curl -sk -m 5 -o /dev/null -w 'api=%{http_code}\\n' https://127.0.0.1:8004/api/health; "
        "nl -ba /etc/nginx/nginx.conf | sed -n '185,205p'",
    )
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
