#!/usr/bin/env python3
"""Safe smoke of NEW image on 206: port 10606 + temp dirs. Do not touch live rag-service."""
from __future__ import annotations

import time

import paramiko

HOST = "192.168.10.206"
USER = "root"
PASSWORD = "ykqgis@2025"
IMAGE = "rag-backend:cpu-no-reranker"
NAME = "rag-smoke-5dea"
SMOKE_ROOT = "/data/setup/rag_smoke_5dea"


def run(c, cmd, timeout=120):
    print(f"\n======= {cmd[:140]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    print(out.encode("ascii", "replace").decode(), flush=True)
    return o.channel.recv_exit_status(), out


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    # cleanup previous smoke if any
    run(c, f"docker rm -f {NAME} 2>/dev/null; true")
    run(
        c,
        f"rm -rf {SMOKE_ROOT} && mkdir -p {SMOKE_ROOT}/{{data/chats,data/migrations,chroma_db,logs,watch,scrape_article,scrapingImages}}",
    )
    # minimal config from prod, but point paths already container-absolute; copy then ensure ollama ok
    run(
        c,
        f"cp /data/rag_python/config.ini {SMOKE_ROOT}/config.ini && "
        f"cp -n /data/rag_python/data/agents.json {SMOKE_ROOT}/data/ 2>/dev/null; "
        f"cp -n /data/rag_python/data/domain_catalog.json {SMOKE_ROOT}/data/ 2>/dev/null; "
        f"cp -n /data/rag_python/data/retrieval_intent_policies.json {SMOKE_ROOT}/data/ 2>/dev/null; "
        f"cp -n /data/rag_python/data/document_profile_map.json {SMOKE_ROOT}/data/ 2>/dev/null; "
        f"python3 - <<'PY'\n"
        "import json, pathlib, sqlite3\n"
        f"root=pathlib.Path('{SMOKE_ROOT}/data')\n"
        "for name in ['agents.json','domain_catalog.json','retrieval_intent_policies.json','document_profile_map.json']:\n"
        "  p=root/name\n"
        "  if not p.exists(): p.write_text('{}' if 'agents' not in name else '[]', encoding='utf-8')\n"
        "(root/'file_index.json').write_text('{}\\n', encoding='utf-8')\n"
        "db=root/'rag_relational.db'\n"
        "con=sqlite3.connect(db); con.execute('PRAGMA user_version=1'); con.commit(); con.close()\n"
        "print('seed_ok')\n"
        "PY",
    )

    # A) import probe inside new image (no mounts) — find hang package
    print("\n##### A) import probe (network none, no mounts) #####", flush=True)
    probe = r"""
import sys, time
steps = [
    'sys',
    'fastapi',
    'uvicorn',
    'chromadb',
    'langchain',
    'langchain_ollama',
    'rag_knowledge',
    'rag_knowledge.config',
    'rag_knowledge.__main__',
]
for name in steps:
    t=time.time()
    print(f'BEGIN {name}', flush=True)
    try:
        if name == 'sys':
            pass
        else:
            __import__(name)
        print(f'OK {name} {time.time()-t:.2f}s', flush=True)
    except Exception as e:
        print(f'ERR {name} {type(e).__name__}: {e}', flush=True)
        sys.exit(1)
print('IMPORT_PROBE_DONE', flush=True)
"""
    run(c, f"cat > {SMOKE_ROOT}/import_probe.py <<'EOF'\n{probe}\nEOF")
    code, out = run(
        c,
        f"timeout 90 docker run --rm --network none --name {NAME}-import "
        f"-v {SMOKE_ROOT}/import_probe.py:/tmp/import_probe.py:ro "
        f"{IMAGE} python /tmp/import_probe.py",
        timeout=120,
    )

    # B) Config() + logging only
    print("\n##### B) Config/logging probe with smoke mounts #####", flush=True)
    cfg_probe = r"""
import time, sys
print('BEGIN config', flush=True)
t=time.time()
from rag_knowledge.config import Config
cfg=Config()
print(f'OK Config {time.time()-t:.2f}s data={cfg.data_dir} log={cfg.log_dir}', flush=True)
print('BEGIN setup_logging', flush=True)
t=time.time()
from rag_knowledge import __main__ as m
# call whatever logging setup exists
if hasattr(m, 'setup_logging'):
    m.setup_logging()
    print(f'OK setup_logging {time.time()-t:.2f}s', flush=True)
else:
    print('NO setup_logging attr', flush=True)
print('CFG_PROBE_DONE', flush=True)
"""
    run(c, f"cat > {SMOKE_ROOT}/cfg_probe.py <<'EOF'\n{cfg_probe}\nEOF")
    run(
        c,
        f"timeout 60 docker run --rm --name {NAME}-cfg "
        f"-v {SMOKE_ROOT}/config.ini:/app/config.ini:ro "
        f"-v {SMOKE_ROOT}/data:/app/data "
        f"-v {SMOKE_ROOT}/logs:/app/logs "
        f"-v {SMOKE_ROOT}/chroma_db:/app/chroma_db "
        f"-v {SMOKE_ROOT}/cfg_probe.py:/tmp/cfg_probe.py:ro "
        f"{IMAGE} python /tmp/cfg_probe.py",
        timeout=90,
    )

    # C) full run.py smoke on 10606
    print("\n##### C) full run.py on :10606 #####", flush=True)
    run(c, f"docker rm -f {NAME} 2>/dev/null; true")
    run(
        c,
        f"docker run -d --name {NAME} "
        f"-p 10606:10605 "
        f"-e RERANKER_ENABLED=false "
        f"-e SKIP_INITIAL_SCAN=true "
        f"-v {SMOKE_ROOT}/config.ini:/app/config.ini:ro "
        f"-v {SMOKE_ROOT}/data:/app/data "
        f"-v {SMOKE_ROOT}/logs:/app/logs "
        f"-v {SMOKE_ROOT}/chroma_db:/app/chroma_db "
        f"-v {SMOKE_ROOT}/watch:/app/watch_directory "
        f"-v {SMOKE_ROOT}/scrape_article:/app/scrape_article "
        f"-v {SMOKE_ROOT}/scrapingImages:/app/scrapingImages "
        f"{IMAGE}",
    )

    ok = False
    for i in range(24):
        time.sleep(5)
        code, out = run(
            c,
            f"docker inspect --format='Status={{{{.State.Status}}}} Exit={{{{.State.ExitCode}}}} OOM={{{{.State.OOMKilled}}}}' {NAME}; "
            f"docker top {NAME} 2>/dev/null | head -5; "
            f"curl -sS -m 2 -o /tmp/smoke_health.json -w 'health=%{{http_code}}\\n' http://127.0.0.1:10606/health || echo health=fail; "
            f"ls -lah {SMOKE_ROOT}/logs | tail -5; "
            f"tail -n 15 {SMOKE_ROOT}/logs/rag.log 2>/dev/null || true",
            timeout=30,
        )
        if "health=200" in out:
            ok = True
            print("SMOKE_HEALTH_OK", flush=True)
            break
        if "Status=exited" in out:
            run(c, f"docker logs --tail 80 {NAME}")
            break
        print(f"wait[{i}]", flush=True)

    # leave smoke container for user inspection if failed; remove if ok to avoid clutter? keep for diagnosis
    if ok:
        run(c, f"curl -sS http://127.0.0.1:10606/health; echo; docker rm -f {NAME}")
        print("\nSMOKE_PASS — live rag-service untouched", flush=True)
        return 0

    run(c, f"docker logs --tail 100 {NAME} 2>&1; echo '---'; docker stats --no-stream {NAME} 2>&1")
    print("\nSMOKE_FAIL — container left as", NAME, "for inspection; live untouched", flush=True)
    c.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
