#!/usr/bin/env python3
"""Inspect /data/rag_python and /opt/rag compose mounts on 141."""
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)
for cmd in [
    "ls -la /data/rag_python 2>&1 || echo MISSING_ROOT",
    "ls -la /data/rag_python/data 2>&1 || echo MISSING_DATA",
    "ls -la /data/rag_python/data/migrations 2>&1 || true",
    "test -f /data/rag_python/config.ini && echo HAS_CONFIG || echo NO_CONFIG",
    "test -f /opt/rag/config-prod.ini && echo HAS_PROD_TEMPLATE || echo NO_PROD_TEMPLATE",
    "test -f /opt/rag/docker-compose.yml && head -80 /opt/rag/docker-compose.yml",
    "df -h /data | tail -1",
]:
    print(f"======= {cmd[:90]} =======")
    _i, o, e = c.exec_command(cmd, timeout=30)
    print(o.read().decode(errors="replace"))
c.close()
