#!/usr/bin/env python3
"""Upload product backbone JSON to 206 (no graph rebuild) + redeploy frontend nav."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import paramiko

HOST, USER, PASSWORD = "192.168.10.206", "root", "ykqgis@2025"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DIST = ROOT / "web" / "dist"
REMOTE_DATA = "/data/rag_python/data"
REMOTE_WEB = "/data/html/ragWeb"
FILES = [
    "product_relation_backbone_preview.json",
    "product_relation_backbone.json",
]


def run(c, cmd, timeout=120):
    print(f"\n======= {cmd[:160]} =======", flush=True)
    _i, o, _e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    print(out.encode("ascii", "replace").decode(), flush=True)
    return out


def upload_tree(sftp, local: Path, remote: str) -> None:
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
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()

    for name in FILES:
        lp = DATA / name
        if not lp.is_file():
            print("MISSING", lp, flush=True)
            return 1
        d = json.loads(lp.read_text(encoding="utf-8"))
        print(f"local {name}: entities={len(d.get('entities',[]))} relations={len(d.get('relations',[]))}", flush=True)
        rp = f"{REMOTE_DATA}/{name}"
        # backup if exists
        run(c, f"test -f {rp} && cp -a {rp} {rp}.bak_{time.strftime('%Y%m%d_%H%M%S')} || true")
        print(f"PUT {lp} -> {rp}", flush=True)
        sftp.put(str(lp), rp)

    # redeploy frontend dist (already rebuilt locally before calling, or build assumed done)
    if not (DIST / "index.html").is_file():
        print("MISSING dist — build first", flush=True)
        return 1
    bak = f"/data/html/ragWeb.bak_nav_{time.strftime('%Y%m%d_%H%M%S')}"
    run(c, f"cp -a {REMOTE_WEB} {bak}")
    run(c, f"rm -rf {REMOTE_WEB}/assets && mkdir -p {REMOTE_WEB}/assets")
    upload_tree(sftp, DIST, REMOTE_WEB)
    sftp.close()

    out = run(
        c,
        "python3 - <<'PY'\n"
        "import json\n"
        "from pathlib import Path\n"
        f"p=Path('{REMOTE_DATA}/product_relation_backbone_preview.json')\n"
        "d=json.loads(p.read_text(encoding='utf-8'))\n"
        "print('preview_file', len(d.get('entities',[])), len(d.get('relations',[])))\n"
        "PY\n"
        "curl -sS -m 5 http://127.0.0.1:10605/admin/knowledge_graph/product_backbone_preview "
        "| python3 -c \"import sys,json; d=json.load(sys.stdin); print('api', len(d.get('nodes',d.get('entities',[]))), len(d.get('edges',d.get('relations',[]))), list(d)[:8])\"; "
        "curl -sk -m 5 -o /dev/null -w 'page=%{http_code}\\n' 'https://127.0.0.1:8004/admin/graph?source=product_backbone_preview'; "
        "curl -sk -m 5 https://127.0.0.1:8004/api/admin/knowledge_graph/product_backbone_preview "
        "| python3 -c \"import sys,json; d=json.load(sys.stdin); print('via_nginx', len(d.get('nodes',[])), len(d.get('edges',[])))\"; "
        "curl -sS http://127.0.0.1:10605/stats; echo",
    )
    ok = "preview_file 147" in out and "via_nginx" in out
    print("\nBACKBONE_FIX", "PASS" if ok else "CHECK", flush=True)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
