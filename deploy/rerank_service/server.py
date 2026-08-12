"""最小 Rerank HTTP 服务示例（建议部署在 GPU 机，如 158）。

协议与 ``rag_knowledge.services.reranker.HttpReranker`` 对齐：

    POST /rerank
    {"query": "...", "documents": ["..."], "top_k": 8}
    -> {"scores": [float, ...]}

依赖（GPU 机单独环境，勿装进 206 瘦镜像）::

    pip install fastapi uvicorn FlagEmbedding

启动示例::

    export RERANK_MODEL=/data/models/bge-reranker-v2-m3
    uvicorn deploy.rerank_service.server:app --host 0.0.0.0 --port 8001

或在本目录::

    uvicorn server:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="rag-rerank-service", version="1.0.0")

_MODEL = None
_MODEL_NAME = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
_USE_FP16 = os.environ.get("RERANK_USE_FP16", "true").lower() == "true"


class RerankRequest(BaseModel):
    query: str
    documents: list[str] = Field(default_factory=list)
    top_k: int | None = None
    model: str | None = None


class RerankResponse(BaseModel):
    scores: list[float]


def _get_model():
    global _MODEL
    if _MODEL is None:
        from FlagEmbedding import FlagReranker

        _MODEL = FlagReranker(_MODEL_NAME, use_fp16=_USE_FP16)
    return _MODEL


@app.on_event("startup")
def _startup() -> None:
    # Fail fast if local model cannot load; avoid first-request HF downloads.
    _get_model()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "model": _MODEL_NAME}


@app.post("/rerank", response_model=RerankResponse)
def rerank(body: RerankRequest) -> RerankResponse:
    if not body.documents:
        return RerankResponse(scores=[])
    model = _get_model()
    pairs = [[body.query, text] for text in body.documents]
    scores = model.compute_score(pairs)
    if isinstance(scores, float):
        scores = [scores]
    return RerankResponse(scores=[float(s) for s in scores])
