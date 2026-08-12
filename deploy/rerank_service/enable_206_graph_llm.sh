#!/bin/bash
set -euo pipefail
TS=$(date +%Y%m%d%H%M%S)
CFG=/data/rag_python/config.ini
cp -a "$CFG" "${CFG}.bak_graphllm_${TS}"

python3 <<'PY'
from pathlib import Path
import re
p = Path("/data/rag_python/config.ini")
text = p.read_text(encoding="utf-8")
m = re.search(r"(\[graph_extraction\.llm\][^\[]*?)(^enabled\s*=\s*)(\S+)", text, flags=re.M | re.S)
if not m:
    raise SystemExit("missing [graph_extraction.llm] enabled")
text = text[: m.start(3)] + "true" + text[m.end(3) :]
text = re.sub(
    r"(\[graph_extraction\.llm\][^\[]*?^provider\s*=\s*)(\S+)",
    r"\1ollama",
    text,
    count=1,
    flags=re.M | re.S,
)
text = re.sub(
    r"(\[graph_extraction\.llm\][^\[]*?^model\s*=\s*)(\S+)",
    r"\1qwen3-vl:8b",
    text,
    count=1,
    flags=re.M | re.S,
)
p.write_text(text, encoding="utf-8")
sec = re.search(r"\[graph_extraction\.llm\][\s\S]*?(?=\n\[|\Z)", p.read_text(encoding="utf-8"))
print(sec.group(0) if sec else "missing")
PY

docker restart rag-service
for i in $(seq 1 40); do
  if curl -fsS --max-time 3 http://127.0.0.1:10605/health >/dev/null; then
    break
  fi
  sleep 3
done
curl -fsS http://127.0.0.1:10605/health; echo

docker exec rag-service python <<'PY'
from rag_knowledge.config import Config
Config._instance = None
c = Config()
print("enabled=", c.graph_extraction_llm.enabled)
print("provider=", c.graph_extraction_llm.provider)
print("model=", c.graph_extraction_llm.model)
print("rerank=", c.reranker_enabled, c.reranker_type, c.reranker_base_url)
print("graph_retrieval=", c.graph_retrieval_enabled if hasattr(c, "graph_retrieval_enabled") else getattr(getattr(c, "graph_retrieval", None), "enabled", "?"))
PY
