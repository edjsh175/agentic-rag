#!/usr/bin/env python3
"""Smoke query with gemma3:4b, then restore qwen3:30b."""
from __future__ import annotations

import json
import re
import time
import urllib.request

import paramiko

HOST = "192.168.10.141"
CFG = "/data/rag_python/config.ini"


def set_llm(sftp, model: str) -> None:
    with sftp.file(CFG, "r") as f:
        text = f.read().decode("utf-8")
    text2, n = re.subn(r"(?m)^llm\s*=\s*.*$", f"llm = {model}", text, count=1)
    if n != 1:
        raise RuntimeError(f"llm line replace failed: {n}")
    with sftp.file(CFG, "w") as f:
        f.write(text2)
    print(f"llm -> {model}", flush=True)


def wait_healthy(c, rounds: int = 24) -> None:
    for i in range(rounds):
        time.sleep(5)
        _i, o, e = c.exec_command(
            "docker inspect --format='{{.State.Health.Status}}' rag-service"
        )
        st = o.read().decode().strip()
        print(f"wait {i} {st}", flush=True)
        if st == "healthy":
            return
    raise RuntimeError("not healthy")


def recreate(c) -> None:
    _i, o, e = c.exec_command(
        "cd /opt/rag && docker compose up -d --force-recreate rag-service",
        timeout=120,
        get_pty=True,
    )
    print(o.read().decode(errors="replace").encode("ascii", "replace").decode(), flush=True)


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()

    set_llm(sftp, "gemma3:4b")
    recreate(c)
    wait_healthy(c)

    payload = json.dumps({"question": "StampTools是什么软件？", "thinking": False}).encode()
    req = urllib.request.Request(
        f"http://{HOST}:10605/query",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        body = json.loads(r.read().decode())
        print("QUERY", r.status, flush=True)
        print("answer", (body.get("answer") or "")[:1000], flush=True)
        print("sources", len(body.get("source_documents") or []), flush=True)

    set_llm(sftp, "qwen3:30b")
    sftp.close()
    recreate(c)
    wait_healthy(c)
    c.close()
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
