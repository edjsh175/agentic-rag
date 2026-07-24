#!/usr/bin/env python3
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)

script = r'''
import time
from langchain_ollama import OllamaEmbeddings

emb = OllamaEmbeddings(
    model="qwen3-embedding",
    base_url="http://192.168.10.2:11435",
    client_kwargs={"trust_env": False},
)
texts = [("这是一段用于批量向量化测试的中文内容。" * 20) for _ in range(30)]
t = time.time()
try:
    vectors = emb.embed_documents(texts)
    print("batch_ok", len(vectors), "dim", len(vectors[0]), "secs", round(time.time()-t, 2))
except Exception as e:
    print("batch_ERR", type(e).__name__, str(e)[:300], "secs", round(time.time()-t, 2))
'''
sftp = c.open_sftp()
with sftp.file("/tmp/batch_embed.py", "w") as f:
    f.write(script)
sftp.close()
_i, o, e = c.exec_command(
    "docker cp /tmp/batch_embed.py rag-service:/tmp/batch_embed.py && "
    "docker exec rag-service python /tmp/batch_embed.py",
    timeout=600,
    get_pty=True,
)
print(o.read().decode(errors="replace"))
c.close()
