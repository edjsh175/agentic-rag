#!/usr/bin/env python3
"""Switch 206 live rag-service to rag-backend:cpu-no-reranker with nltk_data mount.
Keeps old container renamed for rollback. Does not delete old images.
"""
from __future__ import annotations

import json
import time

import paramiko

HOST, USER, PASSWORD = "192.168.10.206", "root", "ykqgis@2025"
NEW_IMAGE = "rag-backend:cpu-no-reranker"
LIVE = "rag-service"
BACKUP = "rag-service_old_c1da7d40_" + time.strftime("%Y%m%d_%H%M%S")
SMOKE = "rag-smoke-nltkfix"
NLTK = "/data/setup/nltk_data"


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 180) -> tuple[int, str]:
    print(f"\n======= {cmd[:170]} =======", flush=True)
    _i, o, _e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode(errors="replace")
    code = o.channel.recv_exit_status()
    print(out.encode("ascii", "replace").decode(), flush=True)
    return code, out


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    # 0) preflight
    code, out = run(
        c,
        f"test -d {NLTK}/tokenizers/punkt_tab && test -d {NLTK}/taggers/averaged_perceptron_tagger_eng && echo NLTK_OK; "
        f"docker image inspect {NEW_IMAGE} --format 'NEW={{{{.Id}}}}'; "
        f"curl -sS -m 3 http://127.0.0.1:10605/health; echo",
    )
    if "NLTK_OK" not in out:
        print("ABORT: nltk_data incomplete", flush=True)
        return 1

    # dump live mounts/env
    code, inspect_out = run(
        c,
        f"docker inspect {LIVE} --format '{{{{json .}}}}' > /tmp/rag_live_inspect.json && "
        f"python3 - <<'PY'\n"
        "import json\n"
        "d=json.load(open('/tmp/rag_live_inspect.json'))\n"
        "c=d['Config']; h=d['HostConfig']\n"
        "print('Image', d['Image'])\n"
        "print('Cmd', c.get('Cmd'))\n"
        "print('Env:')\n"
        "for e in c.get('Env') or []:\n"
        "  if e.split('=',1)[0] in ('SKIP_INITIAL_SCAN','RERANKER_ENABLED','PATH') or e.startswith('PIP_') or 'RERANK' in e or 'SKIP' in e:\n"
        "    print(' ', e)\n"
        "print('Binds:')\n"
        "for b in (h.get('Binds') or []):\n"
        "  print(' ', b)\n"
        "print('PortBindings', h.get('PortBindings'))\n"
        "print('RestartPolicy', h.get('RestartPolicy'))\n"
        "PY",
    )

    # parse binds from remote
    code, binds_out = run(
        c,
        "python3 - <<'PY'\n"
        "import json\n"
        "d=json.load(open('/tmp/rag_live_inspect.json'))\n"
        "binds=d['HostConfig'].get('Binds') or []\n"
        "print('\\n'.join(binds))\n"
        "PY",
    )
    binds = [ln.strip() for ln in binds_out.splitlines() if ln.strip() and ":/app/" in ln]
    # ensure nltk bind
    nltk_bind = f"{NLTK}:/root/nltk_data:ro"
    if not any("/root/nltk_data" in b for b in binds):
        binds.append(nltk_bind)

    vol_args = " ".join(f"-v {b}" for b in binds)
    print("VOL_ARGS=", vol_args, flush=True)

    # 1) stop smoke on 10606
    run(c, f"docker rm -f {SMOKE} 2>/dev/null; true")

    # 2) rename/stop live for rollback
    run(c, f"docker stop {LIVE} && docker rename {LIVE} {BACKUP}")
    run(c, "ss -lntp | grep 10605 || echo PORT_FREE")

    # 3) start new
    # Keep RERANKER_ENABLED=false; SKIP_INITIAL_SCAN kept for compatibility even if new code ignores it
    start_cmd = (
        f"docker run -d --name {LIVE} --restart unless-stopped "
        f"-p 10605:10605 "
        f"-e RERANKER_ENABLED=false "
        f"-e SKIP_INITIAL_SCAN=true "
        f"-e PYTHONUNBUFFERED=1 "
        f"{vol_args} "
        f"{NEW_IMAGE}"
    )
    code, out = run(c, start_cmd)
    if code != 0:
        print("START_FAILED — rolling back", flush=True)
        run(c, f"docker rm -f {LIVE} 2>/dev/null; docker rename {BACKUP} {LIVE}; docker start {LIVE}")
        run(c, "sleep 3; curl -sS -m 5 http://127.0.0.1:10605/health; echo")
        return 1

    # 4) wait health (initial scan may take minutes on prod watch dir)
    ok = False
    for i in range(60):  # up to ~5 min
        time.sleep(5)
        code, out = run(
            c,
            f"docker inspect --format='Status={{{{.State.Status}}}} Exit={{{{.State.ExitCode}}}}' {LIVE}; "
            f"curl -sS -m 3 -w 'http=%{{http_code}}\\n' http://127.0.0.1:10605/health || echo fail; "
            f"docker logs --tail 8 {LIVE} 2>&1",
            timeout=30,
        )
        if "http=200" in out and '"status":"ok"' in out:
            ok = True
            break
        if "Status=exited" in out:
            break
        print(f"wait[{i}]", flush=True)

    if not ok:
        print("HEALTH_FAIL — rolling back to old container", flush=True)
        run(c, f"docker logs --tail 80 {LIVE} 2>&1")
        run(c, f"docker rm -f {LIVE}; docker rename {BACKUP} {LIVE}; docker start {LIVE}")
        time.sleep(5)
        run(c, "curl -sS -m 5 http://127.0.0.1:10605/health; echo")
        return 1

    # 5) post-check
    run(
        c,
        f"docker ps --filter name=rag-service --format 'table {{{{.Names}}}}\\t{{{{.Image}}}}\\t{{{{.Status}}}}\\t{{{{.Ports}}}}'; "
        f"docker inspect {LIVE} --format 'Image={{{{.Config.Image}}}} ID={{{{.Image}}}}'; "
        f"curl -sS http://127.0.0.1:10605/health; echo; "
        f"curl -sS http://127.0.0.1:10605/stats 2>/dev/null | head -c 400; echo",
    )
    print(f"\nSWITCH_PASS old_kept_as={BACKUP}", flush=True)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
