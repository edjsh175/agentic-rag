#!/usr/bin/env python3
"""Deep hang probe for rag-backend:cpu-no-reranker on 206. Live rag-service untouched."""
from __future__ import annotations

import time

import paramiko

HOST = "192.168.10.206"
USER = "root"
PASSWORD = "ykqgis@2025"
IMAGE = "rag-backend:cpu-no-reranker"
SMOKE = "/data/setup/rag_smoke_5dea"
NAME = "rag-smoke-5dea"


def run(c, cmd, timeout=180):
    print(f"\n======= {cmd[:160]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    print(out.encode("ascii", "replace").decode(), flush=True)
    return o.channel.recv_exit_status(), out


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    # image layout
    run(
        c,
        f"docker run --rm --entrypoint sh {IMAGE} -c "
        "'pwd; ls -la /app | head -40; echo ---; ls -la /app/rag_knowledge | head -20; "
        "echo ---; head -5 /app/run.py; echo ---; "
        "python -c \"import sys; print(sys.path); import os; print(os.listdir(\\\"/app\\\"))\"'",
    )

    # detailed import probe WITH /app as cwd
    probe = r'''
import sys, time, os
os.chdir("/app")
if "/app" not in sys.path:
    sys.path.insert(0, "/app")
print("cwd", os.getcwd(), "path0", sys.path[0], flush=True)
print("has_rag", os.path.isdir("/app/rag_knowledge"), flush=True)

steps = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("chromadb", "chromadb"),
    ("langchain_ollama", "langchain_ollama"),
    ("jieba", "jieba"),
    ("rank_bm25", "rank_bm25"),
    ("unstructured", "unstructured"),
    ("cv2", "cv2"),
    ("rag_knowledge", "rag_knowledge"),
    ("rag_knowledge.config", "rag_knowledge.config"),
    ("Config()", "CFG"),
    ("rag_knowledge.services.loader", "rag_knowledge.services.loader"),
    ("rag_knowledge.services.scanner", "rag_knowledge.services.scanner"),
    ("rag_knowledge.services.rag", "rag_knowledge.services.rag"),
    ("rag_knowledge.repository.vector_store", "rag_knowledge.repository.vector_store"),
    ("rag_knowledge.repository.relational_db", "rag_knowledge.repository.relational_db"),
    ("rag_knowledge.api.routes", "rag_knowledge.api.routes"),
    ("rag_knowledge.__main__", "rag_knowledge.__main__"),
]
for label, name in steps:
    t = time.time()
    print(f"BEGIN {label}", flush=True)
    try:
        if name == "CFG":
            from rag_knowledge.config import Config
            cfg = Config()
            print(f"OK Config data={cfg.data_dir} log={cfg.log_dir} {time.time()-t:.2f}s", flush=True)
        else:
            __import__(name)
            print(f"OK {label} {time.time()-t:.2f}s", flush=True)
    except Exception as e:
        print(f"ERR {label} {type(e).__name__}: {e}", flush=True)
        raise
print("PROBE_DONE", flush=True)
'''
    run(c, f"cat > {SMOKE}/import_probe2.py <<'EOF'\n{probe}\nEOF")

    print("\n##### import probe2 (workdir /app, timeout 180s) #####", flush=True)
    run(
        c,
        f"timeout 180 docker run --rm --network none -w /app "
        f"-v {SMOKE}/config.ini:/app/config.ini:ro "
        f"-v {SMOKE}/data:/app/data "
        f"-v {SMOKE}/logs:/app/logs "
        f"-v {SMOKE}/chroma_db:/app/chroma_db "
        f"-v {SMOKE}/import_probe2.py:/tmp/import_probe2.py:ro "
        f"{IMAGE} python -u /tmp/import_probe2.py",
        timeout=200,
    )

    # strace / py-spy style: what is hung process doing?
    print("\n##### inspect hung smoke container #####", flush=True)
    run(
        c,
        f"docker inspect {NAME} --format 'Status={{{{.State.Status}}}} Pid={{{{.State.Pid}}}} Started={{{{.State.StartedAt}}}}'; "
        f"ps -o pid,etime,pcpu,pmem,stat,wchan:20,cmd -p $(docker inspect -f '{{{{.State.Pid}}}}' {NAME}) 2>/dev/null; "
        f"ls -l /proc/$(docker inspect -f '{{{{.State.Pid}}}}' {NAME})/fd 2>/dev/null | head -40; "
        f"cat /proc/$(docker inspect -f '{{{{.State.Pid}}}}' {NAME})/stack 2>/dev/null | head -30; "
        f"timeout 3 strace -p $(docker inspect -f '{{{{.State.Pid}}}}' {NAME}) 2>&1 | tail -40 || true",
        timeout=60,
    )

    # py-level: docker exec python -c printing threads if possible
    run(
        c,
        f"docker exec {NAME} ls -la /app | head -20; "
        f"docker exec {NAME} sh -c 'ls -la /proc/1/fd | head -30; cat /proc/1/wchan; cat /proc/1/status | head -20'",
        timeout=30,
    )

    # compare: old live image entrypoint vs new
    run(
        c,
        "echo LIVE; docker inspect rag-service --format 'Image={{.Image}} Cmd={{json .Config.Cmd}} Entrypoint={{json .Config.Entrypoint}} WorkDir={{.Config.WorkingDir}} Env={{json .Config.Env}}'; "
        f"echo SMOKE; docker inspect {NAME} --format 'Image={{{{.Image}}}} Cmd={{{{json .Config.Cmd}}}} Entrypoint={{{{json .Config.Entrypoint}}}} WorkDir={{{{.Config.WorkingDir}}}} Env={{{{json .Config.Env}}}}'",
    )

    # try: run with PYTHONUNBUFFERED and see if any stdout via docker logs after short wait with script that only imports config
    print("\n##### minimal main steps via docker exec on NEW one-shot #####", flush=True)
    run(c, "docker rm -f rag-hang-trace 2>/dev/null; true")
    run(
        c,
        f"docker run -d --name rag-hang-trace -w /app "
        f"-e PYTHONUNBUFFERED=1 "
        f"-v {SMOKE}/config.ini:/app/config.ini:ro "
        f"-v {SMOKE}/data:/app/data "
        f"-v {SMOKE}/logs:/app/logs "
        f"-v {SMOKE}/chroma_db:/app/chroma_db "
        f"{IMAGE} python -u -c \""
        "import sys; print('1', flush=True); "
        "from rag_knowledge.config import Config; print('2 config', flush=True); "
        "cfg=Config(); print('3 cfg ok', cfg.log_dir, flush=True); "
        "from rag_knowledge.__main__ import _setup_logging; print('4 import setup', flush=True); "
        "_setup_logging(cfg.log_dir); print('5 logging ok', flush=True); "
        "from rag_knowledge.services.scanner import DirectoryScanner; print('6 scanner', flush=True); "
        "from rag_knowledge.services.rag import RagChain; print('7 rag', flush=True); "
        "from rag_knowledge.repository.vector_store import VectorStore; print('8 store', flush=True); "
        "print('DONE', flush=True)"
        "\"",
    )
    time.sleep(25)
    run(c, "docker logs rag-hang-trace 2>&1; echo ---; docker inspect rag-hang-trace --format 'Status={{.State.Status}} Exit={{.State.ExitCode}}'; docker stats --no-stream rag-hang-trace 2>&1")

    c.close()
    print("\nPROBE2_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
