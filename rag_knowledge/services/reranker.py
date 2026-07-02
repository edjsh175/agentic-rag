"""
重排序器（Cross-Encoder Reranker）

在粗召回后对候选文档精排，提升检索相关性。
支持 FlagEmbedding（BGE/Qwen3 Reranker）和 sentence-transformers CrossEncoder。
"""
import logging
from abc import ABC, abstractmethod
from pathlib import Path

from langchain_core.documents import Document

logger = logging.getLogger(__name__)


def _resolve_model_name(model_name: str) -> str:
    """Resolve an explicit local model directory and reject partial downloads."""
    raw = model_name.strip()
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
# 工厂函数
# ------------------------------------------------------------------

def create_reranker(reranker_type: str, model_name: str) -> BaseReranker:
    """根据类型创建对应的重排序器实例（模型懒加载，首次 rerank() 时才下载/加载）"""
    model_name = _resolve_model_name(model_name)
    if reranker_type == "bge":
        return FlagReranker(model_name)
    elif reranker_type in ("cross_encoder", "cross-encoder", "sentence_transformers"):
        return CrossEncoderReranker(model_name)
    else:
        raise ValueError(
            f"不支持的重排序器类型: {reranker_type}，可选: bge, cross_encoder"
        )
