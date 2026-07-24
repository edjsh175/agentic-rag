#!/usr/bin/env python3
"""Deploy local web/dist to 206 /data/html/ragWeb with backup. Backend untouched."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST, USER, PASSWORD = "192.168.10.206", "root", "ykqgis@2025"
LOCAL_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
REMOTE = "/data/html/ragWeb"
BACKUP = f"/data/html/ragWeb.bak_{time.strftime('%Y%m%d_%H%M%S')}"


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
    print(f"\n======= {cmd[:160]} =======", flush=True)
    _i, o, _e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    print(out.encode("ascii", "replace").decode(), flush=True)
    return out


def upload_tree(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
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
            print(f"PUT {lp.name} -> {rp}", flush=True)
            sftp.put(str(lp), rp)


def main() -> int:
    if not (LOCAL_DIST / "index.html").is_file():
        print(f"MISSING dist: {LOCAL_DIST}", flush=True)
        return 1

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    # backup
    run(c, f"cp -a {REMOTE} {BACKUP} && ls -lah {BACKUP} | head -10 && echo BACKUP_OK")

    # clear assets (old hashed files) then upload
    run(c, f"rm -rf {REMOTE}/assets && mkdir -p {REMOTE}/assets")
    sftp = c.open_sftp()
    upload_tree(sftp, LOCAL_DIST, REMOTE)
    sftp.close()

    run(c, f"ls -lah {REMOTE}; ls -lah {REMOTE}/assets; cat {REMOTE}/index.html")

    # verify
    out = run(
        c,
        "curl -sk -m 5 -o /dev/null -w 'page=%{http_code}\\n' https://127.0.0.1:8004/; "
        "curl -sk -m 5 https://127.0.0.1:8004/api/health; echo; "
        "JS=$(grep -oE '/assets/[^\" ]+\\.js' /data/html/ragWeb/index.html | head -1); "
        "CSS=$(grep -oE '/assets/[^\" ]+\\.css' /data/html/ragWeb/index.html | head -1); "
        "echo JS=$JS CSS=$CSS; "
        "curl -sk -m 5 -o /dev/null -w 'js=%{http_code}\\n' https://127.0.0.1:8004$JS; "
        "curl -sk -m 5 -o /dev/null -w 'css=%{http_code}\\n' https://127.0.0.1:8004$CSS; "
        "curl -sS -m 3 http://127.0.0.1:10605/health; echo",
    )

    ok = "page=200" in out and '"status":"ok"' in out and "js=200" in out
    print(f"\nDEPLOY {'PASS' if ok else 'FAIL'} backup={BACKUP}", flush=True)
    c.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
