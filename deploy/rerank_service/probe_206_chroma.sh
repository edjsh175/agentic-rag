#!/bin/bash
set -euo pipefail
echo "=== host chroma / data sizes ==="
du -sh /data/rag_python/chroma_db /data/rag_python/data 2>/dev/null || true
ls -lah /data/rag_python/chroma_db 2>/dev/null | head -20
ls -lah /data/rag_python/data/file_index.json /data/rag_python/data/rag_relational.db 2>/dev/null || true

echo "=== container health / config hints ==="
curl -fsS http://127.0.0.1:10605/health; echo
docker exec rag-service python - <<'PY'
from pathlib import Path
from rag_knowledge.config import Config
Config._instance = None
c = Config()
print("config_file", c.config_file)
print("chroma", getattr(c, "chroma_persist_dir", None) or getattr(c, "chroma_db_path", None))
# common attrs
for k in ["vector_db_path","chroma_path","persist_directory"]:
    if hasattr(c,k): print(k, getattr(c,k))
print("data_dir", c.data_dir)
print("watch", c.watch_directory)
PY

echo "=== chroma collection via container ==="
docker exec -e PYTHONPATH=/app -w /app rag-service python - <<'PY'
from collections import Counter
from rag_knowledge.repository.vector_store import VectorStore
# reset singleton if needed
try:
    VectorStore._instance = None
except Exception:
    pass
vs = VectorStore()
col = vs.collection
# chroma API
count = col.count()
print("collection_count", count)
# sample metadata distribution
try:
    got = col.get(include=["metadatas"], limit=min(count, 5000))
except TypeError:
    got = col.get(include=["metadatas"])
metas = got.get("metadatas") or []
ids = got.get("ids") or []
print("fetched_metas", len(metas), "ids", len(ids))
rs = Counter((m or {}).get("review_status") or "<none>" for m in metas)
kb = Counter((m or {}).get("kb_name") or "<none>" for m in metas)
cat = Counter((m or {}).get("doc_category") or "<none>" for m in metas)
print("review_status", dict(rs.most_common()))
print("kb_name", dict(kb.most_common(10)))
print("doc_category top", dict(cat.most_common(12)))
# file_path samples newest-ish by scanning file_index
from pathlib import Path
import json
fi = Path("/app/data/file_index.json")
if fi.exists():
    data = json.loads(fi.read_text(encoding="utf-8"))
    # structure may be dict path->record or list
    if isinstance(data, dict):
        items = list(data.items())
        print("file_index_entries", len(items))
        # show a few keys
        for k,_ in items[:5]:
            print("fi_sample", k)
    else:
        print("file_index_type", type(data), "len", len(data))
PY

echo "=== stats endpoints ==="
curl -fsS http://127.0.0.1:10605/stats 2>/dev/null | python3 -m json.tool 2>/dev/null | head -80 || true
curl -fsS http://127.0.0.1:10605/stats/chunks 2>/dev/null | python3 -m json.tool 2>/dev/null | head -120 || true

echo "=== recent unanswered / no-content logs ==="
grep -E "未查询到|未检索到|无相关|no relevant|降级|downshift|当前知识库中未" /data/rag_python/logs/rag.log 2>/dev/null | tail -40 || true
grep -E "未查询到|未检索到|无相关|downshift" /data/rag_python/logs/rag_error.log 2>/dev/null | tail -20 || true
echo "=== recent query completions ==="
grep -E "异步查询完成|同步查询完成|query_plan|检索到 0|sources=0|0 个来源" /data/rag_python/logs/rag.log 2>/dev/null | tail -40 || true
