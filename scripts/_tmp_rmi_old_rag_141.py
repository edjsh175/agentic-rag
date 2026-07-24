import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)


def run(cmd: str) -> tuple[int, str]:
    print(f"======= {cmd} =======", flush=True)
    _i, o, e = c.exec_command(cmd, get_pty=True)
    out = o.read().decode(errors="replace")
    print(out, end="" if out.endswith("\n") else "\n", flush=True)
    return o.channel.recv_exit_status(), out


# containers using the image
run(
    "docker ps -a --filter ancestor=rag-backend:cpu-no-reranker "
    "--format '{{.ID}} {{.Status}} {{.Names}}'"
)
# stop/remove any such containers so rmi can proceed
run(
    "ids=$(docker ps -aq --filter ancestor=rag-backend:cpu-no-reranker); "
    "if [ -n \"$ids\" ]; then docker rm -f $ids; fi"
)

code, _ = run("docker rmi rag-backend:cpu-no-reranker")
if code != 0:
    # force if tag stuck but layers shared
    run("docker rmi -f rag-backend:cpu-no-reranker || true")

run('docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}"')
c.close()
print("DONE", flush=True)
