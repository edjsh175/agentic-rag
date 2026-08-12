#!/bin/bash
set -euo pipefail
docker exec -i rag-service python - <<'PY'
from rag_knowledge.config import Config
Config._instance = None
c = Config()
print("graph_extraction.llm.enabled =", c.graph_extraction_llm.enabled)
print("graph_extraction.llm.provider =", c.graph_extraction_llm.provider)
print("graph_extraction.llm.model =", c.graph_extraction_llm.model)
print("reranker =", c.reranker_enabled, c.reranker_type, c.reranker_base_url, "timeout", c.reranker_timeout)
print("graph_retrieval.enabled =", c.graph_retrieval.enabled)
print("graph_retrieval.query_rewrite =", c.graph_retrieval.query_rewrite_enabled)
PY
curl -fsS --max-time 5 http://192.168.10.158:8001/health; echo
curl -fsS --max-time 5 http://127.0.0.1:10605/health; echo
