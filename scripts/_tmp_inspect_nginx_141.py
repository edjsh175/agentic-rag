#!/usr/bin/env python3
"""Inspect nginx / ragWeb on 141 for production-like frontend deploy."""
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)
cmds = [
    "rpm -q nginx 2>/dev/null || dpkg -l nginx 2>/dev/null | tail -1; which nginx; nginx -v 2>&1",
    "systemctl is-active nginx 2>&1; systemctl is-enabled nginx 2>&1",
    "ls -la /data/html/ragWeb 2>&1; ls -la /data/html/ragWeb/assets 2>&1 | head",
    "ls /etc/nginx/conf.d 2>&1; ls /etc/nginx/sites-enabled 2>&1",
    "grep -RIn 'ragWeb\\|10605\\|rag' /etc/nginx 2>/dev/null | head -40",
    "ss -lntp | grep -E ':80|:443|:10605' || true",
    "docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'",
]
for cmd in cmds:
    print(f"======= {cmd[:100]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=30)
    print((o.read() or e.read()).decode(errors="replace").encode("ascii", "replace").decode(), flush=True)
c.close()
