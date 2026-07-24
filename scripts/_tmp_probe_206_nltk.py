#!/usr/bin/env python3
"""Confirm NLTK hang and offline workaround on 206. Live untouched."""
from __future__ import annotations

import time

import paramiko

HOST = "192.168.10.206"
USER = "root"
PASSWORD = "ykqgis@2025"
IMAGE = "rag-backend:cpu-no-reranker"
SMOKE = "/data/setup/rag_smoke_5dea"


def run(c, cmd, timeout=120):
    print(f"\n======= {cmd[:160]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    print(out.encode("ascii", "replace").decode(), flush=True)
    return o.channel.recv_exit_status(), out


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    # cleanup previous probes
    run(c, "docker rm -f rag-hang-trace rag-smoke-nltk 2>/dev/null; true")

    # who owns 185.199.108.133 / what nltk wants
    run(
        c,
        "timeout 5 bash -c 'echo >/dev/tcp/185.199.108.133/443' && echo TCP443_OK || echo TCP443_FAIL; "
        "getent hosts raw.githubusercontent.com github.com 2>/dev/null | head -5; "
        "curl -sS -m 5 -I https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/index.xml 2>&1 | head -10",
        timeout=30,
    )

    # check nltk data inside NEW vs find trigger
    run(
        c,
        f"docker run --rm -w /app {IMAGE} python -u -c \""
        "import nltk, os; print('nltk', nltk.__file__); print('path', nltk.data.path); "
        "[print(p, os.path.isdir(p), os.listdir(p)[:8] if os.path.isdir(p) else None) for p in nltk.data.path]; "
        "\"",
        timeout=60,
    )

    # Does old live image have nltk data?
    run(
        c,
        "docker exec rag-service python -u -c \""
        "import nltk, os; print('nltk', getattr(nltk,'__version__', '?')); print('path', nltk.data.path); "
        "[print('DIR', p, 'exists', os.path.isdir(p), 'items', (os.listdir(p)[:10] if os.path.isdir(p) else None)) for p in nltk.data.path]; "
        "\" 2>&1 | head -40",
        timeout=60,
    )

    # Work around: create empty/minimal nltk dirs + env to avoid download? Or download on host if possible.
    # Better: start with NLTK_DATA pointing to pre-seeded dir; also set UNSTRUCTURED_DISABLE_DOWNLOAD? 
    # Test: import with network but fake offline via NLTK downloader blocked - use pre-created tokenizers stubs?

    # Quick fix test: docker run with --add-host raw.githubusercontent.com:127.0.0.1 so DNS fails fast... 
    # Actually connect hangs to real IP - so DNS works. Need either data present or fail-fast.

    # Check if we can copy nltk from old container
    run(
        c,
        "docker exec rag-service sh -c 'find / -type d -name nltk_data 2>/dev/null | head; find /usr -name punkt* 2>/dev/null | head; find /root -name nltk_data 2>/dev/null | head'",
        timeout=60,
    )

    # Smoke with network disabled should fail-fast past nltk and reach uvicorn IF that's the only hang
    print("\n##### smoke with --network none (expect nltk fail-fast, maybe boot) #####", flush=True)
    run(c, "docker rm -f rag-smoke-nltk 2>/dev/null; true")
    run(
        c,
        f"docker run -d --name rag-smoke-nltk --network none "
        f"-e RERANKER_ENABLED=false -e PYTHONUNBUFFERED=1 "
        f"-v {SMOKE}/config.ini:/app/config.ini:ro "
        f"-v {SMOKE}/data:/app/data "
        f"-v {SMOKE}/logs:/app/logs "
        f"-v {SMOKE}/chroma_db:/app/chroma_db "
        f"-v {SMOKE}/watch:/app/watch_directory "
        f"{IMAGE}",
    )
    # no host port with network none - check logs inside
    for i in range(12):
        time.sleep(5)
        code, out = run(
            c,
            "docker logs --tail 30 rag-smoke-nltk 2>&1; "
            "docker stats --no-stream rag-smoke-nltk 2>&1; "
            f"ls -lah {SMOKE}/logs; tail -n 20 {SMOKE}/logs/rag.log 2>/dev/null || true",
            timeout=30,
        )
        if "Uvicorn running" in out or "Application startup complete" in out:
            print("NETWORK_NONE_BOOT_OK", flush=True)
            break
        if "Status=exited" in out or "Error" in out and "Traceback" in out:
            break
        print(f"wait[{i}]", flush=True)

    # Also: hang-trace still running? kill smoke containers that hang but keep live
    run(c, "docker rm -f rag-hang-trace 2>/dev/null; true")

    c.close()
    print("\nNLTK_PROBE_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
