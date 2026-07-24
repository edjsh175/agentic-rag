#!/usr/bin/env python3
"""Verify empty nltk_data mount is enough to boot; list exact packages needed."""
from __future__ import annotations

import time

import paramiko

HOST, USER, PASSWORD = "192.168.10.206", "root", "ykqgis@2025"
SMOKE = "/data/setup/rag_smoke_5dea"
IMAGE = "rag-backend:cpu-no-reranker"


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    def run(cmd: str, timeout: int = 90) -> str:
        print(f"\n======= {cmd[:160]} =======", flush=True)
        _i, o, _e = c.exec_command(cmd, timeout=timeout, get_pty=True)
        out = o.read().decode(errors="replace")
        print(out.encode("ascii", "replace").decode(), flush=True)
        return out

    run("curl -sS -m 3 http://127.0.0.1:10606/health; echo; curl -sS -m 3 http://127.0.0.1:10605/health; echo")
    run("docker logs --tail 12 rag-smoke-nltkfix 2>&1")

    run("docker rm -f rag-smoke-empty 2>/dev/null; mkdir -p /data/setup/nltk_data_empty")
    run(
        f"docker run -d --name rag-smoke-empty -p 10607:10605 "
        f"-e PYTHONUNBUFFERED=1 -e RERANKER_ENABLED=false "
        f"-v {SMOKE}/config.ini:/app/config.ini:ro "
        f"-v {SMOKE}/data:/app/data "
        f"-v {SMOKE}/logs:/app/logs "
        f"-v {SMOKE}/chroma_db:/app/chroma_db "
        f"-v /data/setup/nltk_data_empty:/root/nltk_data:ro "
        f"{IMAGE}"
    )
    time.sleep(15)
    out = run(
        "curl -sS -m 3 -w '\\nhttp=%{http_code}\\n' http://127.0.0.1:10607/health || echo fail; "
        "docker logs --tail 25 rag-smoke-empty 2>&1"
    )

    # package presence check script on remote
    run(
        "cat > /tmp/check_nltk.py <<'EOF'\n"
        "import nltk\n"
        "for pkg in ['tokenizers/punkt_tab','tokenizers/punkt',"
        "'taggers/averaged_perceptron_tagger_eng','taggers/averaged_perceptron_tagger']:\n"
        "  try:\n"
        "    print('FOUND', pkg, nltk.data.find(pkg))\n"
        "  except Exception as e:\n"
        "    print('MISS', pkg, type(e).__name__)\n"
        "EOF"
    )
    run(
        f"docker run --rm --network none -w /app "
        f"-v /data/setup/nltk_data_empty:/root/nltk_data:ro "
        f"-v /tmp/check_nltk.py:/tmp/check_nltk.py:ro "
        f"{IMAGE} python -u /tmp/check_nltk.py"
    )

    # cleanup empty smoke; keep nltkfix for inspection
    run("docker rm -f rag-smoke-empty 2>/dev/null; true")
    print("\nVERIFY_DONE", flush=True)
    print("empty_boot", "Uvicorn running" in out or "http=200" in out, flush=True)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
