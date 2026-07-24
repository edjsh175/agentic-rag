#!/usr/bin/env python3
"""Read-only check on 206: graph backbone vs formal DB, chroma rebuild status."""
from __future__ import annotations

import paramiko

HOST, USER, PASSWORD = "192.168.10.206", "root", "ykqgis@2025"


def run(c, cmd, timeout=90):
    print(f"\n======= {cmd[:160]} =======", flush=True)
    _i, o, _e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    print(o.read().decode(errors="replace").encode("ascii", "replace").decode(), flush=True)


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    # chroma / stats
    run(
        c,
        "curl -sS http://127.0.0.1:10605/stats; echo; "
        "curl -sS 'http://127.0.0.1:10605/stats/chunks' 2>/dev/null | head -c 800; echo; "
        "ls -lah /data/rag_python/chroma_db | head -15; "
        "stat -c '%y %n' /data/rag_python/chroma_db 2>/dev/null; "
        "find /data/rag_python/chroma_db -maxdepth 2 -printf '%T+ %p\\n' 2>/dev/null | sort | tail -15",
    )

    # formal graph counts
    run(
        c,
        "python3 - <<'PY'\n"
        "import sqlite3\n"
        "db='/data/rag_python/data/rag_relational.db'\n"
        "con=sqlite3.connect(db)\n"
        "for t in ['entities','relations','aliases','entity_chunk_links','extraction_candidates']:\n"
        "  try:\n"
        "    n=con.execute(f'select count(*) from {t}').fetchone()[0]\n"
        "    print(t, n)\n"
        "  except Exception as e:\n"
        "    print(t, 'ERR', e)\n"
        "# sample created_by\n"
        "try:\n"
        "  rows=con.execute('select created_by, count(*) from entities group by created_by order by 2 desc limit 10').fetchall()\n"
        "  print('entities_by_created_by', rows)\n"
        "  rows=con.execute('select created_by, count(*) from relations group by created_by order by 2 desc limit 10').fetchall()\n"
        "  print('relations_by_created_by', rows)\n"
        "except Exception as e:\n"
        "  print('created_by_err', e)\n"
        "con.close()\n"
        "PY",
    )

    # product backbone files
    run(
        c,
        "ls -lah /data/rag_python/data/product_relation_backbone*.json 2>/dev/null; "
        "ls -lah /data/rag_python/data/*backbone* 2>/dev/null; "
        "find /data/rag_python/data -maxdepth 2 -iname '*backbone*' -o -iname '*product_relation*' 2>/dev/null | head -30",
    )

    # API: knowledge graph vs product backbone preview if any
    run(
        c,
        "curl -sS -m 5 'http://127.0.0.1:10605/admin/knowledge_graph?limit=5' 2>&1 | head -c 500; echo; "
        "curl -sS -m 5 'http://127.0.0.1:10605/admin/knowledge_graph/product_backbone_preview' 2>&1 | head -c 800; echo; "
        "curl -sS -m 5 -o /dev/null -w 'kg=%{http_code} preview=%{http_code}\\n' "
        "http://127.0.0.1:10605/admin/knowledge_graph/product_backbone_preview || true",
    )

    # what frontend graph page calls — from deployed js strings
    run(
        c,
        "grep -oE 'product_backbone[^\"\\']*|knowledge_graph[^\"\\']*|GraphCandidates|/admin/[^\"\\']+' "
        "/data/html/ragWeb/assets/index-YSllTgYN.js 2>/dev/null | sort -u | head -60",
    )

    # switch-time: did we rebuild? check rebuild.lock / recent rebuild markers
    run(
        c,
        "ls -lah /data/rag_python/data/rebuild.lock 2>/dev/null; "
        "ls -lah /data/rag_python/data/*rebuild* 2>/dev/null; "
        "docker inspect rag-service --format 'Image={{.Config.Image}} Started={{.State.StartedAt}}'; "
        "grep -E 'rebuild|clear|REBUILD|首次扫描|Uvicorn' /data/rag_python/logs/rag.log 2>/dev/null | tail -30",
    )

    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
