#!/usr/bin/env python3
"""Preflight on 141 before compose up + chroma rebuild."""
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)

cmds = [
    "docker images rag-backend --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}'",
    "test -f /opt/rag/docker-compose.yml && echo COMPOSE_OK",
    "ls -la /data/rag_python/config.ini /data/rag_python/data/agents.json /data/rag_python/chroma_db | head -20",
    "find /data/apache-tomcat-9.0.89/webapps/zsltStaticData -type f 2>/dev/null | wc -l",
    "find /data/apache-tomcat-9.0.89/webapps/zsltStaticData -type f 2>/dev/null | head -10",
    "curl -sS -m 5 -o /dev/null -w 'ollama_host=%{http_code}\\n' http://192.168.10.158:11434/api/tags || echo ollama_host_fail",
    "grep -E 'base_url|enabled|method' /data/rag_python/config.ini | head -20",
    "which docker-compose; docker compose version 2>&1; ls /usr/local/bin/docker-compose /usr/bin/docker-compose 2>/dev/null",
]
for cmd in cmds:
    print(f"======= {cmd[:100]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=30)
    print(o.read().decode(errors="replace") or e.read().decode(errors="replace"), flush=True)
c.close()
