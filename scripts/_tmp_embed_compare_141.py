#!/usr/bin/env python3
"""Compare embedding reachability: 141-host vs container vs improve fwd."""
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)

script = r'''
import urllib.request, json, time
payload = json.dumps({"model": "qwen3-embedding", "prompt": "hello"}).encode()
req = urllib.request.Request(
    "http://192.168.10.2:11435/api/embeddings",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
t = time.time()
try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = resp.read()
        data = json.loads(body)
        print("host_urllib", resp.status, "secs", round(time.time()-t,2), "dim", len(data.get("embedding") or []))
except Exception as e:
    print("host_urllib ERR", type(e).__name__, e)
'''
sftp = c.open_sftp()
with sftp.file("/tmp/embed_host.py", "w") as f:
    f.write(script)
sftp.close()

_i, o, e = c.exec_command("python3 /tmp/embed_host.py", timeout=240, get_pty=True)
print("HOST:", o.read().decode(errors="replace"), flush=True)

_i, o, e = c.exec_command(
    "docker exec rag-service python /tmp/test_embed.py 2>/dev/null || "
    "(docker cp /tmp/embed_host.py rag-service:/tmp/embed_host.py && docker exec rag-service python /tmp/embed_host.py)",
    timeout=240,
    get_pty=True,
)
print("CTN:", o.read().decode(errors="replace"), flush=True)
c.close()
