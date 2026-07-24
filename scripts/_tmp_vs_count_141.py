#!/usr/bin/env python3
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.10.141", 22, "root", "123456", timeout=20, allow_agent=False, look_for_keys=False)
script = r'''
from rag_knowledge.repository.vector_store import VectorStore
vs = VectorStore()
print("count", vs.count())
raw = vs._collection.get(include=["metadatas"], limit=5)
print("n_ids", len(raw.get("ids") or []))
print("ids_sample", (raw.get("ids") or [])[:3])
metas = raw.get("metadatas") or []
print("meta_sample", metas[:1])
# peek all review statuses
all_raw = vs._collection.get(include=["metadatas"])
from collections import Counter
c = Counter((m or {}).get("review_status") for m in (all_raw.get("metadatas") or []))
print("review_status", dict(c))
print("total_metas", len(all_raw.get("metadatas") or []))
'''
sftp = c.open_sftp()
with sftp.file("/tmp/vs_count.py", "w") as f:
    f.write(script)
sftp.close()
_i, o, e = c.exec_command(
    "docker cp /tmp/vs_count.py rag-service:/tmp/vs_count.py && docker exec rag-service python /tmp/vs_count.py",
    timeout=120,
    get_pty=True,
)
print(o.read().decode(errors="replace"))
print(e.read().decode(errors="replace"))
c.close()
