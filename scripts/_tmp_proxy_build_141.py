#!/usr/bin/env python3
"""Build on 141 via Windows proxy; upload pinned requirements."""
from pathlib import Path

import paramiko

HOST = "192.168.10.141"
USER = "root"
PASSWORD = "123456"
PROXY = "http://192.168.10.2:8899"
ROOT = Path(__file__).resolve().parents[1]


def run(c, cmd, timeout=120):
    print(f"\n======= {cmd[:140]} =======", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    while True:
        line = o.readline()
        if not line:
            break
        print(line, end="", flush=True)
    code = o.channel.recv_exit_status()
    print(f"exit={code}", flush=True)
    return code


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    # only remove exited containers; keep intermediate layer cache
    run(c, "docker ps -aq --filter status=exited | xargs -r docker rm -f; true")

    _i, o, e = c.exec_command("docker images python:3.11-slim --format '{{.ID}}'")
    img_id = o.read().decode().strip()
    if not img_id:
        print("NO_BASE_IMAGE", flush=True)
        return 1
    print("BASE", img_id, flush=True)

    dockerfile = f"""FROM {img_id}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements-base.txt .
COPY requirements-reranker.txt .
ARG INSTALL_RERANKER=false
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy
RUN set -eux; \\
    export HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" http_proxy="$http_proxy" https_proxy="$https_proxy"; \\
    export NO_PROXY=localhost,127.0.0.1,192.168.10.0/24 no_proxy=localhost,127.0.0.1,192.168.10.0/24; \\
    export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple PIP_TIMEOUT=120 PIP_RETRIES=10; \\
    pip install --no-cache-dir -r requirements-base.txt; \\
    if [ "$INSTALL_RERANKER" = "true" ]; then pip install --no-cache-dir -r requirements-reranker.txt; fi
COPY rag_knowledge/ ./rag_knowledge/
COPY scripts/ ./scripts/
COPY run.py .
COPY run_graph_build.py .
COPY sync_profiles_to_graph.py .
EXPOSE 10605
CMD ["python", "run.py"]
"""

    run(c, "mkdir -p /opt/rag/scripts && touch /opt/rag/scripts/.keep")
    sftp = c.open_sftp()
    sftp.put(str(ROOT / "requirements-base.txt"), "/opt/rag/requirements-base.txt")
    sftp.put(str(ROOT / "requirements-reranker.txt"), "/opt/rag/requirements-reranker.txt")
    with sftp.file("/opt/rag/Dockerfile", "w") as f:
        f.write(dockerfile)
    sftp.close()

    run(c, "grep opencv /opt/rag/requirements-base.txt; grep -n HTTP_PROXY /opt/rag/Dockerfile | head -3")

    # reuse completed early layers; pip RUN still rebuilds if unfinished
    build = (
        "cd /opt/rag && DOCKER_BUILDKIT=0 docker build --pull=false "
        f"--build-arg HTTP_PROXY={PROXY} "
        f"--build-arg HTTPS_PROXY={PROXY} "
        f"--build-arg http_proxy={PROXY} "
        f"--build-arg https_proxy={PROXY} "
        "--build-arg INSTALL_RERANKER=false "
        "-t rag-backend:cpu-no-reranker -f Dockerfile ."
    )
    if run(c, build, timeout=7200) != 0:
        print("BUILD_FAILED", flush=True)
        return 1

    run(
        c,
        "docker images rag-backend; "
        "docker run --rm --network none rag-backend:cpu-no-reranker "
        "python -c \"import fastapi,chromadb,rag_knowledge; print('import_ok')\"",
    )
    c.close()
    print("\nBUILD_ALL_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
