#!/usr/bin/env python3
"""Reliable 206 rebuild + backbone seed via uploaded shell/python helpers."""
from __future__ import annotations

import json
import time
from pathlib import Path

import paramiko

HOST, USER, PASSWORD = "192.168.10.206", "root", "ykqgis@2025"
ROOT = Path(__file__).resolve().parents[1]
DB_APP = "/app/data/rag_relational.db"
DB_HOST = "/data/rag_python/data/rag_relational.db"
BACKBONE = "/app/data/product_relation_backbone.json"


REBUILD_SH = r"""#!/bin/bash
set -euo pipefail
OUT=/tmp/rag_rebuild_out.json
ERR=/tmp/rag_rebuild_err.txt
EXITF=/tmp/rag_rebuild_exit.txt
rm -f "$OUT" "$ERR" "$EXITF"
echo "rebuild_start $(date -Is)" > /tmp/rag_rebuild_progress.txt
curl -sS -m 14400 -X POST http://127.0.0.1:10605/rebuild \
  -H 'Content-Type: application/json' \
  -d '{"confirmation":"REBUILD_KNOWLEDGE_BASE","approve_all_chunks":true}' \
  > "$OUT" 2> "$ERR"
ec=$?
echo "$ec" > "$EXITF"
echo "rebuild_end $(date -Is) exit=$ec" >> /tmp/rag_rebuild_progress.txt
exit "$ec"
"""


def run(c, cmd, timeout=120):
    print(f"\n======= {cmd[:170]} =======", flush=True)
    _i, o, _e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    code = o.channel.recv_exit_status()
    print(out.encode("ascii", "replace").decode(), flush=True)
    print(f"exit={code}", flush=True)
    return code, out


def dex(c, args, timeout=300):
    return run(
        c,
        f"docker exec -e PYTHONPATH=/app -w /app rag-service python {args}",
        timeout=timeout,
    )


def parse_json(text: str):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    # cleanup any stale fake state
    run(c, "pkill -f '/tmp/rag_do_rebuild.sh' 2>/dev/null || true; rm -f /tmp/rag_rebuild_exit.txt /tmp/rag_rebuild_out.json")

    code, out = run(
        c,
        "curl -sS -m 5 http://127.0.0.1:10605/health; echo; "
        "test -f /data/rag_python/data/product_relation_backbone.json && echo BACKBONE_OK",
    )
    if '"status":"ok"' not in out or "BACKBONE_OK" not in out:
        print("PREFLIGHT_FAIL", flush=True)
        return 1

    ts = time.strftime("%Y%m%d_%H%M%S")
    run(
        c,
        f"mkdir -p /data/rag_python/data/backups && "
        f"cp -a {DB_HOST} /data/rag_python/data/backups/rag_relational_pre_rebuild_seed_{ts}.db",
    )

    sftp = c.open_sftp()
    with sftp.file("/tmp/rag_do_rebuild.sh", "w") as f:
        f.write(REBUILD_SH)
    sftp.put(str(ROOT / "sync_product_backbone_to_graph.py"), "/tmp/sync_product_backbone_to_graph.py")
    sftp.close()
    run(c, "chmod +x /tmp/rag_do_rebuild.sh")

    print("\n##### REBUILD START #####", flush=True)
    run(c, "nohup /tmp/rag_do_rebuild.sh >/tmp/rag_rebuild_nohup.log 2>&1 & echo PID=$!; sleep 1; ps -ef | grep rag_do_rebuild | grep -v grep; ls -lah /tmp/rag_rebuild*")

    rebuild_ok = False
    for i in range(900):  # up to ~2.5h @ 10s
        time.sleep(10)
        code, out = run(
            c,
            "ps -ef | grep -E 'rag_do_rebuild|curl.*rebuild' | grep -v grep || echo NO_REBUILD_PROC; "
            "if [ -f /tmp/rag_rebuild_exit.txt ]; then echo DONE_EXIT=$(cat /tmp/rag_rebuild_exit.txt); "
            "echo OUT_HEAD; head -c 1500 /tmp/rag_rebuild_out.json; echo; "
            "echo ERR_TAIL; tail -n 30 /tmp/rag_rebuild_err.txt; "
            "else echo STILL_RUNNING; cat /tmp/rag_rebuild_progress.txt 2>/dev/null; "
            "tail -n 15 /data/rag_python/logs/rag.log | grep -E 'rebuild|Rebuild|扫描|scan|embedding|POST /rebuild' || "
            "tail -n 5 /data/rag_python/logs/rag.log; "
            "curl -sS -m 2 http://127.0.0.1:10605/stats || true; echo; fi",
            timeout=60,
        )
        if "DONE_EXIT=" in out and "STILL_RUNNING" not in out.split("DONE_EXIT=")[0][-20:]:
            # careful: only treat as done when file exists path taken
            pass
        if "DONE_EXIT=0" in out:
            rebuild_ok = True
            break
        if "DONE_EXIT=" in out and "DONE_EXIT=0" not in out:
            print("REBUILD_FAILED_EXIT", flush=True)
            break
        if i % 6 == 0:
            print(f"wait[{i}] {i*10}s", flush=True)

    if not rebuild_ok:
        print("REBUILD_FAILED", flush=True)
        run(c, "cat /tmp/rag_rebuild_progress.txt /tmp/rag_rebuild_nohup.log /tmp/rag_rebuild_err.txt 2>/dev/null; tail -n 100 /data/rag_python/logs/rag.log")
        return 1

    run(c, "curl -sS http://127.0.0.1:10605/health; echo; curl -sS http://127.0.0.1:10605/stats; echo; python3 -c \"import json;print(json.load(open('/tmp/rag_rebuild_out.json')))\"")
    print("REBUILD_PASS", flush=True)

    # seed
    print("\n##### SEED BACKBONE #####", flush=True)
    run(c, "docker cp /tmp/sync_product_backbone_to_graph.py rag-service:/app/sync_product_backbone_to_graph.py")
    code, out = dex(c, f"sync_product_backbone_to_graph.py --dry-run --path {BACKBONE} --json", timeout=120)
    if code != 0:
        print("DRY_RUN_FAILED", flush=True)
        return 1
    code, out = dex(
        c,
        f"sync_product_backbone_to_graph.py --stage --path {BACKBONE} "
        f"--review-status pending --confirm-db-path {DB_APP} --json",
        timeout=180,
    )
    if code != 0:
        print("STAGE_FAILED", flush=True)
        return 1
    blob = parse_json(out) or {}
    batch_id = blob.get("batch_id")
    if not batch_id:
        print("NO_BATCH_ID", flush=True)
        return 1
    print(f"BATCH={batch_id}", flush=True)
    for kind in ("entity", "relation", "alias"):
        code, _ = dex(c, f"run_graph_build.py review --batch {batch_id} --approve-kind {kind}", timeout=180)
        if code != 0:
            print(f"REVIEW_{kind}_FAILED", flush=True)
            return 1
    backup = f"/app/data/backups/rag_relational_pre_backbone_apply_{batch_id}.db"
    run(c, "docker exec rag-service mkdir -p /app/data/backups")
    run(c, f"docker exec rag-service cp {DB_APP} {backup}")
    code, _ = dex(
        c,
        f"run_graph_build.py apply --batch {batch_id} "
        f"--confirm-db-path {DB_APP} --confirm-batch {batch_id} --confirm-backup {backup}",
        timeout=300,
    )
    if code != 0:
        print("APPLY_FAILED", flush=True)
        return 1

    run(
        c,
        "python3 - <<'PY'\n"
        "import sqlite3\n"
        f"con=sqlite3.connect('{DB_HOST}')\n"
        "cur=con.cursor()\n"
        "for t in ('entities','relations','aliases','entity_chunk_links'):\n"
        "  print(t, cur.execute(f'select count(*) from {t}').fetchone()[0])\n"
        "print('by_created_by_entities', cur.execute('select created_by,count(*) from entities group by 1 order by 2 desc').fetchall())\n"
        "print('by_created_by_relations', cur.execute('select created_by,count(*) from relations group by 1 order by 2 desc').fetchall())\n"
        "print('backbone_e', cur.execute(\"select count(*) from entities where created_by='seed:product_backbone'\").fetchone()[0])\n"
        "print('backbone_r', cur.execute(\"select count(*) from relations where created_by='seed:product_backbone'\").fetchone()[0])\n"
        "PY\n"
        "curl -sS http://127.0.0.1:10605/stats; echo",
    )
    print("\nALL_DONE", flush=True)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
