#!/usr/bin/env python3
"""Seed /data/rag_python on 141 with minimal runtime data + config."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import paramiko

HOST = "192.168.10.141"
USER = "root"
PASSWORD = "123456"
ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA = ROOT / "data"
REMOTE_ROOT = "/data/rag_python"
REMOTE_DATA = f"{REMOTE_ROOT}/data"

# Must exist for startup / Intent / Graph admin (deploy/README + data约定)
SEED_FILES = [
    "agents.json",
    "domain_catalog.json",
    "retrieval_intent_policies.json",
    "document_profile_map.json",
    "product_relation_backbone.json",
    "product_relation_backbone_preview.json",
    "structured_retrieval_regression.json",
]

SEED_MIGRATIONS = [
    "migrations/retrieval_intent_profiles_v1.json",
]


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 60) -> int:
    print(f"\n======= {cmd[:120]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    print(o.read().decode(errors="replace"), end="", flush=True)
    return o.channel.recv_exit_status()


def put_bytes(sftp: paramiko.SFTPClient, data: bytes, remote: str) -> None:
    with sftp.file(remote, "wb") as f:
        f.write(data)


def put_file(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    print(f"PUT {local.name} -> {remote}", flush=True)
    sftp.put(str(local), remote)


def make_empty_relational_db(path: Path) -> None:
    """Minimal schema so RelationalDB can open; tables match repository expectations lightly."""
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    # Let app migrate fully on first start; empty file is enough for sqlite connect.
    # Prefer creating via a no-op so the file is a valid sqlite DB.
    conn.execute("SELECT 1")
    conn.commit()
    conn.close()


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    # directories aligned with compose mounts
    run(
        c,
        "mkdir -p "
        f"{REMOTE_ROOT}/{{chroma_db,logs,data/chats,data/migrations,data/qa_traces,scrape_article,scrapingImages}} "
        "/data/apache-tomcat-9.0.89/webapps/zsltStaticData "
        "/data/html/ragWeb/assets",
    )

    sftp = c.open_sftp()

    # config.ini from prod template (Ollama already points to 192.168.10.158)
    local_cfg = ROOT / "config-prod.ini"
    if not local_cfg.is_file():
        print("MISSING local config-prod.ini", flush=True)
        return 1
    put_file(sftp, local_cfg, f"{REMOTE_ROOT}/config.ini")

    for name in SEED_FILES:
        local = LOCAL_DATA / name
        if not local.is_file():
            print(f"MISSING {local}", flush=True)
            return 1
        put_file(sftp, local, f"{REMOTE_DATA}/{name}")

    for rel in SEED_MIGRATIONS:
        local = LOCAL_DATA / rel
        if not local.is_file():
            print(f"MISSING {local}", flush=True)
            return 1
        put_file(sftp, local, f"{REMOTE_DATA}/{rel}")

    # Empty runtime indexes — do NOT copy local file_index / chroma-linked graph DB
    put_bytes(sftp, b"{}\n", f"{REMOTE_DATA}/file_index.json")
    put_bytes(sftp, b"{}\n", f"{REMOTE_DATA}/ingestion_decisions.json")
    put_bytes(sftp, b"{}\n", f"{REMOTE_DATA}/chunk_hit_stats.json")
    put_bytes(sftp, b"", f"{REMOTE_DATA}/graph_apply_audit.jsonl")

    tmp_db = ROOT / "scripts" / "_tmp_empty_rag_relational.db"
    make_empty_relational_db(tmp_db)
    put_file(sftp, tmp_db, f"{REMOTE_DATA}/rag_relational.db")
    tmp_db.unlink(missing_ok=True)

    sftp.close()

    run(
        c,
        f"chmod 644 {REMOTE_ROOT}/config.ini; "
        f"find {REMOTE_DATA} -type f -exec chmod 644 {{}} +; "
        f"ls -la {REMOTE_ROOT}; echo '--- data ---'; ls -la {REMOTE_DATA}; "
        f"echo '--- migrations ---'; ls -la {REMOTE_DATA}/migrations",
    )
    c.close()
    print("\nSEED_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
