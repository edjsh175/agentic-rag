"""
重排序器（Cross-Encoder Reranker）

在粗召回后对候选文档精排，提升检索相关性。
支持：
- FlagEmbedding（BGE/Qwen3 Reranker）本地进程内加载
- sentence-transformers CrossEncoder
- HTTP 远程服务（type=http，由 GPU 机提供 /rerank）
"""
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

logger = logging.getLogger(__name__)


def _resolve_model_name(model_name: str) -> str:
    """Resolve an explicit local model directory and reject partial downloads."""
    raw = model_name.strip()
    if not raw:
        return raw
    path = Path(raw).expanduser()
    is_explicit_path = (
        path.is_absolute() or raw.startswith(("./", ".\\")) or path.exists()
    )
    if not is_explicit_path:
        return raw

    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"重排序模型目录不存在: {path}")

    has_config = (path / "config.json").is_file()
    has_weights = any(
        (path / filename).is_file()
        for filename in (
            "model.safetensors", "model.safetensors.index.json",
            "pytorch_model.bin", "pytorch_model.bin.index.json",
        )
    )
    if not (has_config and has_weights):
        raise ValueError(f"重排序模型目录不完整（缺少 config.json 或模型权重）: {path}")
    return str(path)


# ------------------------------------------------------------------
# 抽象基类
# ------------------------------------------------------------------

class BaseReranker(ABC):
    """重排序器抽象基类"""

    @abstractmethod
    def rerank(self, query: str, documents: list[Document], top_k: int) -> list[Document]:
        """对文档列表按与 query 的相关性重新排序，返回 top_k 个文档"""
        ...


# ------------------------------------------------------------------
# FlagEmbedding 实现（BAAI/bge-reranker-v2-m3、Qwen3-Reranker）
# ------------------------------------------------------------------

class FlagReranker(BaseReranker):
    """基于 FlagEmbedding 的重排序器，支持 BGE / Qwen3 Reranker 系列模型"""

    def __init__(self, model_name: str, use_fp16: bool = True):
        self._model_name = model_name
        self._use_fp16 = use_fp16
        self._model = None

    def _ensure_loaded(self):
        if self._model is None:
            from FlagEmbedding import FlagReranker as _FlagReranker
            self._model = _FlagReranker(self._model_name, use_fp16=self._use_fp16)
            logger.info("FlagReranker 模型加载完成: %s", self._model_name)

    def rerank(self, query: str, documents: list[Document], top_k: int) -> list[Document]:
        if not documents or top_k <= 0:
            return []
        self._ensure_loaded()
        pairs = [[query, doc.page_content] for doc in documents]
        scores = self._model.compute_score(pairs)
        # compute_score 对单个 pair 返回 float，多个返回 list
        if isinstance(scores, float):
            scores = [scores]
        ranked = sorted(zip(documents, scores), key=lambda x: x[1], reverse=True)
        return [doc for doc, _ in ranked[:top_k]]


# ------------------------------------------------------------------
# sentence-transformers CrossEncoder 实现（备选方案）
# ------------------------------------------------------------------

class CrossEncoderReranker(BaseReranker):
    """基于 sentence-transformers CrossEncoder 的重排序器"""

    def __init__(self, model_name: str):
        self._model_name = model_name
        self._model = None

    def _ensure_loaded(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder as _CrossEncoder
            self._model = _CrossEncoder(self._model_name)
            logger.info("CrossEncoder 模型加载完成: %s", self._model_name)

    def rerank(self, query: str, documents: list[Document], top_k: int) -> list[Document]:
        if not documents or top_k <= 0:
            return []
        self._ensure_loaded()
        pairs = [[query, doc.page_content] for doc in documents]
        scores = self._model.predict(pairs)
        ranked = sorted(zip(documents, scores), key=lambda x: x[1], reverse=True)
        return [doc for doc, _ in ranked[:top_k]]


# ------------------------------------------------------------------
# HTTP 远程实现（GPU 机独立服务）
# ------------------------------------------------------------------

def _scores_from_http_payload(data: Any, doc_count: int) -> list[float]:
    """Parse /rerank JSON into a score list aligned with input document order."""
    if isinstance(data, list):
        # TEI-style: [{"index": i, "score": s}, ...]
        scores = [0.0] * doc_count
        for item in data:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index", -1))
            if 0 <= idx < doc_count:
                scores[idx] = float(item.get("score", 0.0))
        return scores

    if not isinstance(data, dict):
        raise ValueError(f"重排序服务返回格式无效: {type(data).__name__}")

    if "scores" in data:
        scores = list(data["scores"])
        if len(scores) != doc_count:
            raise ValueError(
                f"重排序 scores 长度不匹配: expect {doc_count}, got {len(scores)}"
            )
        return [float(s) for s in scores]

    results = data.get("results")
    if isinstance(results, list):
        scores = [0.0] * doc_count
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index", -1))
            if 0 <= idx < doc_count:
                scores[idx] = float(item.get("score", 0.0))
        return scores

    raise ValueError("重排序服务响应缺少 scores 或 results")


class HttpReranker(BaseReranker):
    """调用远程 /rerank HTTP 服务（建议部署在 GPU 机，如 158）。

    请求::
        POST {base_url}/rerank
        {"query": "...", "documents": ["..."], "top_k": 8}

    响应（任选其一）::
        {"scores": [0.1, 0.9, ...]}                 # 与 documents 等长
        {"results": [{"index": 0, "score": 0.9}]}  # 或 TEI 风格顶层 list
    """

    def __init__(self, base_url: str, timeout: float = 30.0, model_name: str = ""):
        url = (base_url or "").strip().rstrip("/")
        if not url:
            raise ValueError("HttpReranker 需要非空 base_url")
        self._base_url = url
        self._timeout = float(timeout)
        self._model_name = (model_name or "").strip()

    def rerank(self, query: str, documents: list[Document], top_k: int) -> list[Document]:
        if not documents or top_k <= 0:
            return []

        import httpx

        payload: dict[str, Any] = {
            "query": query,
            "documents": [doc.page_content for doc in documents],
            "top_k": top_k,
        }
        if self._model_name:
            payload["model"] = self._model_name

        endpoint = f"{self._base_url}/rerank"
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(endpoint, json=payload)
            response.raise_for_status()
            data = response.json()

        scores = _scores_from_http_payload(data, len(documents))
        ranked = sorted(zip(documents, scores), key=lambda x: x[1], reverse=True)
        return [doc for doc, _ in ranked[:top_k]]


# ------------------------------------------------------------------
# 工厂函数
# ------------------------------------------------------------------

def create_reranker(
    reranker_type: str,
    model_name: str,
    *,
    base_url: str | None = None,
    timeout: float = 30.0,
) -> BaseReranker:
    """根据类型创建对应的重排序器实例（本地模型懒加载；HTTP 模式无本地权重）。"""
    kind = (reranker_type or "").strip().lower()
    base = (base_url or "").strip() or None

    if kind in ("http", "remote", "api"):
        return HttpReranker(base_url=base or "", timeout=timeout, model_name=model_name or "")

    model_name = _resolve_model_name(model_name)
    if kind == "bge":
        return FlagReranker(model_name)
    if kind in ("cross_encoder", "cross-encoder", "sentence_transformers"):
        return CrossEncoderReranker(model_name)
    raise ValueError(
        f"不支持的重排序器类型: {reranker_type}，可选: bge, cross_encoder, http"
    )
