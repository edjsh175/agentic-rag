#!/usr/bin/env python3
"""Seed product backbone into formal graph on 141; enable query rewrite."""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

import paramiko

HOST = "192.168.10.141"
USER = "root"
PASSWORD = "123456"
ROOT = Path(__file__).resolve().parents[1]
DB = "/app/data/rag_relational.db"
BACKBONE = "/app/data/product_relation_backbone.json"


def run(c, cmd, timeout=300):
    print(f"\n======= {cmd[:140]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    print(out.encode("ascii", "replace").decode(), flush=True)
    code = o.channel.recv_exit_status()
    print(f"exit={code}", flush=True)
    return code, out


def dex(c, py_args: str, timeout=300):
    return run(
        c,
        "docker exec -e PYTHONPATH=/app -w /app rag-service "
        f"python {py_args}",
        timeout=timeout,
    )


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    # ensure CLI present in running container
    sftp = c.open_sftp()
    sftp.put(str(ROOT / "sync_product_backbone_to_graph.py"), "/tmp/sync_product_backbone_to_graph.py")
    sftp.close()
    run(c, "docker cp /tmp/sync_product_backbone_to_graph.py rag-service:/app/sync_product_backbone_to_graph.py")
    run(c, "cp -n /tmp/sync_product_backbone_to_graph.py /opt/rag/sync_product_backbone_to_graph.py 2>/dev/null; true")

    # 1) dry-run
    code, out = dex(c, f"sync_product_backbone_to_graph.py --dry-run --path {BACKBONE} --json", timeout=120)
    if code != 0:
        print("DRY_RUN_FAILED", flush=True)
        return 1
    # extract counts from last json-ish print - already printed

    # 2) stage pending
    code, out = dex(
        c,
        f"sync_product_backbone_to_graph.py --stage --path {BACKBONE} "
        f"--review-status pending --confirm-db-path {DB} --json",
        timeout=180,
    )
    if code != 0:
        print("STAGE_FAILED", flush=True)
        return 1
    batch_id = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and "batch_id" in line:
            try:
                batch_id = json.loads(line).get("batch_id")
            except json.JSONDecodeError:
                pass
    if not batch_id:
        # multiline json
        try:
            start = out.index("{")
            end = out.rindex("}") + 1
            batch_id = json.loads(out[start:end]).get("batch_id")
        except Exception:
            batch_id = None
    if not batch_id:
        print("NO_BATCH_ID", flush=True)
        return 1
    print(f"BATCH={batch_id}", flush=True)

    # 3) review by kind (approve-all forbidden for product_backbone_seed)
    for kind in ("entity", "relation", "alias"):
        code, _ = dex(
            c,
            f"run_graph_build.py review --batch {batch_id} --approve-kind {kind}",
            timeout=180,
        )
        if code != 0:
            print(f"REVIEW_{kind}_FAILED", flush=True)
            return 1

    # 4) backup + apply
    backup = f"/app/data/backups/rag_relational_pre_backbone_{batch_id}.db"
    run(c, "docker exec rag-service mkdir -p /app/data/backups")
    run(c, f"docker exec rag-service cp {DB} {backup}")
    code, _ = dex(
        c,
        f"run_graph_build.py apply --batch {batch_id} "
        f"--confirm-db-path {DB} --confirm-batch {batch_id} --confirm-backup {backup}",
        timeout=300,
    )
    if code != 0:
        print("APPLY_FAILED", flush=True)
        return 1

    # 5) enable graph retrieval + query rewrite on host config
    sftp = c.open_sftp()
    with sftp.file("/data/rag_python/config.ini", "r") as f:
        text = f.read().decode("utf-8")
    text2 = text
    text2, n1 = re.subn(
        r"(?ms)(\[graph_retrieval\]\s*\n)enabled\s*=\s*\w+",
        r"\1enabled = true",
        text2,
        count=1,
    )
    text2, n2 = re.subn(
        r"(?m)^(query_rewrite_enabled\s*=\s*)\w+",
        r"\1true",
        text2,
        count=1,
    )
    if n1 != 1 or n2 != 1:
        print(f"CONFIG_PATCH_FAIL n1={n1} n2={n2}", flush=True)
        return 1
    with sftp.file("/data/rag_python/config.ini", "w") as f:
        f.write(text2)
    sftp.close()
    run(c, "grep -A6 '\\[graph_retrieval\\]' /data/rag_python/config.ini")

    # 6) restart
    run(c, "cd /opt/rag && docker compose up -d --force-recreate rag-service", timeout=120)
    for i in range(24):
        time.sleep(5)
        _i, o, e = c.exec_command(
            "docker inspect --format='{{.State.Health.Status}}' rag-service"
        )
        st = o.read().decode().strip()
        print(f"wait {i} {st}", flush=True)
        if st == "healthy":
            break
    else:
        print("NOT_HEALTHY", flush=True)
        return 1

    # 7) verify
    run(
        c,
        "python3 - <<'PY'\n"
        "import sqlite3\n"
        "con=sqlite3.connect('/data/rag_python/data/rag_relational.db')\n"
        "cur=con.cursor()\n"
        "for t in ('entities','relations','aliases','entity_chunk_links'):\n"
        "  print(t, cur.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0])\n"
        "PY",
    )
    for path in (
        "http://127.0.0.1:8088/api/admin/knowledge_graph/data",
        "http://127.0.0.1:10605/admin/knowledge_graph/data",
        "http://127.0.0.1:8088/api/health",
    ):
        run(c, f"curl -sS -m 20 '{path}' | python3 -c \"import sys,json;d=json.load(sys.stdin); "
            f"print('{path}', 'nodes',len(d.get('nodes',[])), 'edges',len(d.get('edges',d.get('relations',[]))) "
            f"if isinstance(d,dict) else d)\"")

    c.close()
    print("\nBACKBONE_SEED_DONE", flush=True)
    print("Admin graph: http://192.168.10.141:8088/admin/graph", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
