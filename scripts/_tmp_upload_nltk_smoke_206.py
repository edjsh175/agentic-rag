#!/usr/bin/env python3
"""Upload local nltk_data to 206 and smoke-test new image with mount. Live untouched."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST, USER, PASSWORD = "192.168.10.206", "root", "ykqgis@2025"
LOCAL = Path(r"C:\Users\Administrator\AppData\Roaming\nltk_data")
REMOTE = "/data/setup/nltk_data"
SMOKE = "/data/setup/rag_smoke_5dea"
NAME = "rag-smoke-nltkfix"
IMAGE = "rag-backend:cpu-no-reranker"


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
    print(f"\n======= {cmd[:160]} =======", flush=True)
    _i, o, _e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    print(out.encode("ascii", "replace").decode(), flush=True)
    return out


def upload_dir(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    try:
        sftp.stat(remote)
    except FileNotFoundError:
        sftp.mkdir(remote)
    for root, dirs, files in os.walk(local):
        rel = os.path.relpath(root, local).replace("\\", "/")
        rdir = remote if rel == "." else f"{remote}/{rel}"
        try:
            sftp.stat(rdir)
        except FileNotFoundError:
            sftp.mkdir(rdir)
        for d in dirs:
            rd = f"{rdir}/{d}"
            try:
                sftp.stat(rd)
            except FileNotFoundError:
                sftp.mkdir(rd)
        for f in files:
            lp = Path(root) / f
            rp = f"{rdir}/{f}"
            print(f"PUT {lp} -> {rp}", flush=True)
            sftp.put(str(lp), rp)


def main() -> int:
    if not LOCAL.is_dir():
        print(f"MISSING local {LOCAL}", flush=True)
        return 1

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    run(c, f"rm -rf {REMOTE} && mkdir -p {REMOTE}")
    sftp = c.open_sftp()
    # only the two packages required
    for sub in [
        "taggers/averaged_perceptron_tagger_eng",
        "tokenizers/punkt_tab",
    ]:
        src = LOCAL / sub
        # also upload zip if present (nltk accepts either)
        zip_src = Path(str(src) + ".zip")
        parent = f"{REMOTE}/{Path(sub).parent.as_posix()}"
        run(c, f"mkdir -p {parent}")
        if src.is_dir():
            upload_dir(sftp, src, f"{REMOTE}/{sub}")
        if zip_src.is_file():
            print(f"PUT zip {zip_src}", flush=True)
            sftp.put(str(zip_src), f"{REMOTE}/{sub}.zip")
    sftp.close()

    run(c, f"find {REMOTE} -type f | head -40; du -sh {REMOTE}")

    run(c, f"docker rm -f {NAME} 2>/dev/null; true")
    run(
        c,
        f"docker run -d --name {NAME} -p 10606:10605 "
        f"-e RERANKER_ENABLED=false -e PYTHONUNBUFFERED=1 -e SKIP_INITIAL_SCAN=true "
        f"-v {SMOKE}/config.ini:/app/config.ini:ro "
        f"-v {SMOKE}/data:/app/data "
        f"-v {SMOKE}/logs:/app/logs "
        f"-v {SMOKE}/chroma_db:/app/chroma_db "
        f"-v {SMOKE}/watch:/app/watch_directory "
        f"-v {REMOTE}:/root/nltk_data:ro "
        f"{IMAGE}",
    )

    ok = False
    for i in range(24):
        time.sleep(5)
        out = run(
            c,
            f"docker inspect --format='Status={{{{.State.Status}}}}' {NAME}; "
            f"curl -sS -m 3 -w 'http=%{{http_code}}\\n' http://127.0.0.1:10606/health || echo fail; "
            f"docker logs --tail 20 {NAME} 2>&1",
        )
        if "http=200" in out:
            ok = True
            break
        if "Status=exited" in out:
            break
        print(f"wait[{i}]", flush=True)

    run(c, "curl -sS -m 3 http://127.0.0.1:10605/health; echo")
    print("\nSMOKE", "PASS" if ok else "FAIL", flush=True)
    c.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
