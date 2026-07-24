import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)
for label, cmd in [
    ("images", 'docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedSince}}"'),
    ("rag_related", "docker images | grep -iE 'rag|python|REPOSITORY' || true"),
]:
    print(f"==== {label} ====")
    _i, o, e = c.exec_command(cmd)
    print(o.read().decode() or e.read().decode())
c.close()
