#!/usr/bin/env python3
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)

# write test script into container via stdin is hard; put on host and docker cp
test = r'''
import httpx, time
url = "http://192.168.10.2:11435"
for model in ["qwen3-embedding", "qwen3-embedding:latest"]:
    t = time.time()
    try:
        r = httpx.post(f"{url}/api/embeddings", json={"model": model, "prompt": "hello"}, timeout=180)
        emb = r.json().get("embedding") or []
        print(model, "status", r.status_code, "secs", round(time.time()-t, 2), "dim", len(emb))
    except Exception as e:
        print(model, "ERR", type(e).__name__, str(e)[:200])
# longer text similar to chunk
long = ("段落测试 " * 200)
t = time.time()
try:
    r = httpx.post(url + "/api/embeddings", json={"model": "qwen3-embedding", "prompt": long}, timeout=180)
    print("long", r.status_code, "secs", round(time.time()-t, 2), "dim", len(r.json().get("embedding") or []))
except Exception as e:
    print("long ERR", type(e).__name__, str(e)[:200])
'''
sftp = c.open_sftp()
with sftp.file("/tmp/test_embed.py", "w") as f:
    f.write(test)
sftp.close()
_i, o, e = c.exec_command(
    "docker cp /tmp/test_embed.py rag-service:/tmp/test_embed.py && "
    "docker exec rag-service python /tmp/test_embed.py",
    timeout=600,
    get_pty=True,
)
print(o.read().decode(errors="replace"))
c.close()
