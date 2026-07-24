#!/usr/bin/env python3
"""Retry rebuild after confirming embedding works from container."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import paramiko

HOST = "192.168.10.141"
BASE = f"http://{HOST}:10605"


def run(c, cmd, timeout=120):
    print(f"\n======= {cmd[:120]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    print(out.encode("ascii", "replace").decode(), flush=True)
    return o.channel.recv_exit_status(), out


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, "root", "123456", timeout=30, allow_agent=False, look_for_keys=False)

    # warm embedding with medium text from container
    warm = r'''
import httpx
long = ("x" * 4000)
r = httpx.post(
    "http://192.168.10.2:11435/api/embeddings",
    json={"model": "qwen3-embedding", "prompt": long},
    timeout=180,
)
print("warm", r.status_code, len(r.json().get("embedding") or []))
'''
    sftp = c.open_sftp()
    with sftp.file("/tmp/warm_embed.py", "w") as f:
        f.write(warm)
    sftp.close()
    run(
        c,
        "docker cp /tmp/warm_embed.py rag-service:/tmp/warm_embed.py && "
        "docker exec rag-service python /tmp/warm_embed.py",
        timeout=240,
    )

    payload = json.dumps(
        {"confirmation": "REBUILD_KNOWLEDGE_BASE", "approve_all_chunks": True}
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/rebuild",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    print("\n======= POST /rebuild =======", flush=True)
    try:
        with urllib.request.urlopen(req, timeout=7200) as resp:
            body = resp.read().decode(errors="replace")
            print(f"REBUILD_HTTP={resp.status}\n{body}", flush=True)
            ok = resp.status == 200
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(f"REBUILD_HTTP={exc.code}\n{body}", flush=True)
        run(c, "tail -n 60 /data/rag_python/logs/rag_error.log; cd /opt/rag && docker compose logs --tail=40")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"REBUILD_ERR={exc}", flush=True)
        return 1

    for path in ("/health", "/stats"):
        with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as resp:
            print(f"\nGET {path} {resp.status}\n{resp.read().decode()[:800]}", flush=True)

    run(
        c,
        "du -sh /data/rag_python/chroma_db; "
        "python3 - <<'PY'\n"
        "import json\n"
        "p='/data/rag_python/data/file_index.json'\n"
        "d=json.load(open(p))\n"
        "print('index_type', type(d).__name__)\n"
        "if isinstance(d, dict):\n"
        "  files=d.get('files', d)\n"
        "  print('entries', len(files) if isinstance(files, dict) else files)\n"
        "PY",
    )
    c.close()
    print("\nDONE" if ok else "\nFAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
