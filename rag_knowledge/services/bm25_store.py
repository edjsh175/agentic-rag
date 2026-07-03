"""
BM25 关键词检索索引

从 ChromaDB 全量文档构建 BM25Okapi 索引，支持中文 jieba 分词。
单例模式，通过 rebuild() 重建索引。
"""
import time
import logging
from threading import Lock

import jieba
from rank_bm25 import BM25Okapi
from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.repository.vector_store import VectorStore

logger = logging.getLogger(__name__)


class BM25Store:
    """BM25 关键词检索索引（单例）"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._bm25: BM25Okapi | None = None
        self._docs: list[Document] = []
        self._metadatas: list[dict] = []
        self._build_lock = Lock()
        self._initialized = True

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        kb_name: str | None = None,
        review_status: str | None = "approved",
        doc_category: str | None = None,
        top_k: int = 5,
    ) -> list[Document]:
        """
        BM25 关键词检索，返回 top-k 个 Document。

        首次调用时自动构建索引（懒加载）。
        """
        self._ensure_index()

        if not self._bm25 or not self._docs:
            logger.warning("BM25 索引为空，返回空结果")
            return []

        # jieba 分词
        tokenized_query = [token for token in jieba.cut(query) if token.strip()]
        query_terms = set(tokenized_query)

        # BM25 打分 → (index, score) 列表，按分数降序
        scores = self._bm25.get_scores(tokenized_query)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

        # 后置过滤元数据，收集 top_k 个
        results: list[Document] = []
        for idx, score in ranked:
            if score <= 0:
                doc_terms = {token for token in jieba.cut(self._docs[idx].page_content) if token.strip()}
                if not query_terms.intersection(doc_terms):
                    continue
            meta = self._metadatas[idx]
            if kb_name and meta.get("kb_name") != kb_name:
                continue
            if review_status and meta.get("review_status") != review_status:
                continue
            if doc_category and meta.get("doc_category") != doc_category:
                continue
            results.append(self._docs[idx])
            if len(results) >= top_k:
                break

        logger.info(
            "BM25 检索: query=%.40s | kb=%s | hits=%d",
            query, kb_name or "all", len(results),
        )
        return results

    def rebuild(self):
        """重建 BM25 索引（从 ChromaDB 全量拉取文档）"""
        self._bm25 = None
        self._docs = []
        self._metadatas = []
        self._build_index()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _ensure_index(self):
        """确保索引已构建（懒加载，失败时优雅降级）"""
        if self._bm25 is not None:
            return
        with self._build_lock:
            if self._bm25 is None:
                try:
                    self._build_index()
                except Exception as e:
                    logger.error("BM25 懒加载索引失败，检索将返回空结果: %s", e)

    def _build_index(self):
        """从 ChromaDB 拉取全量文档并构建 BM25Okapi 索引。

        异常会向上传播，由调用方决定处理策略：
          - _ensure_index（懒加载）→ 捕获并降级
          - rebuild（显式重建）  → 由上层 routes.py 捕获并返回错误
        """
        t0 = time.time()
        chroma = VectorStore().get_chroma()
        # 拉取全部文档（不带过滤条件）
        result = chroma.get(include=["documents", "metadatas"])

        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])
        ids = result.get("ids", [])

        if not documents:
            logger.warning("ChromaDB 中没有文档，BM25 索引为空")
            return

        # jieba 分词构建语料
        tokenized_corpus = [list(jieba.cut(doc)) for doc in documents]

        self._bm25 = BM25Okapi(tokenized_corpus)
        self._metadatas = []
        for doc_id, meta in zip(ids, metadatas):
            normalized_meta = dict(meta or {})
            normalized_meta.setdefault("chunk_id", doc_id)
            self._metadatas.append(normalized_meta)
        self._docs = [
            Document(page_content=doc, metadata=meta)
            for doc, meta in zip(documents, self._metadatas)
        ]

        elapsed = time.time() - t0
        logger.info(
            "BM25 索引构建完成: %d 文档 | jieba 分词 | %.2fs",
            len(self._docs), elapsed,
        )
