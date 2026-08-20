"""
BM25 关键词检索索引

从 ChromaDB 全量文档构建 BM25Okapi 索引，支持中文 jieba 分词。
单例模式，通过 rebuild() 重建索引。
"""
import logging
import re
import time
from threading import Lock
from typing import Any

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
        scope: Any = None,
    ) -> list[Document]:
        """
        BM25 关键词检索，返回 top-k 个 Document。

        首次调用时自动构建索引（懒加载）。
        """
        self._ensure_index()

        if not self._bm25 or not self._docs:
            logger.warning("BM25 索引为空，返回空结果")
            return []

        # 使用统一的分词函数（内部使用 casefold()）
        tokenized_query = self._tokenize(query)
        query_terms = set(tokenized_query)

        # BM25 打分 → (index, score) 列表，按分数降序
        scores = self._bm25.get_scores(tokenized_query)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

        norm_scope = getattr(scope, "evidence_scope", scope) if scope is not None else None
        is_locked = bool(norm_scope and getattr(norm_scope, "is_identity_locked", False))

        # 前置过滤元数据与结构准入条件，收集 top_k 个
        results: list[Document] = []
        for idx, score in ranked:
            if score <= 0:
                search_text = (
                    self._metadatas[idx].get("searchable_text")
                    or self._docs[idx].page_content
                )
                doc_terms = set(self._tokenize(search_text))
                if not query_terms.intersection(doc_terms):
                    continue
            meta = self._metadatas[idx]
            if kb_name and meta.get("kb_name") != kb_name:
                continue
            if review_status and meta.get("review_status") != review_status:
                continue
            if doc_category and meta.get("doc_category") != doc_category:
                continue
            if is_locked:
                doc_ent = str(meta.get("document_entity") or meta.get("entity_name") or "").strip()
                chunk_id = str(meta.get("chunk_id") or "").strip()
                if not norm_scope.is_structurally_admissible(doc_ent, chunk_id):
                    continue
            results.append(self._docs[idx])
            if len(results) >= top_k:
                break

        logger.info(
            "BM25 检索: query=%.40s | kb=%s | hits=%d",
            query, kb_name or "all", len(results),
        )
        return results

    def build_index_from_documents(self, docs: list[Document]):
        """从指定 Document 列表直接构建 BM25 索引（支持测试及动态注入）。"""
        self._docs = list(docs)
        self._metadatas = [d.metadata or {} for d in docs]
        tokenized_corpus = [self._tokenize(d.page_content) for d in docs]
        self._bm25 = BM25Okapi(tokenized_corpus) if tokenized_corpus else None

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
        """Build the BM25 index from the full Chroma snapshot."""
        t0 = time.time()
        chroma = VectorStore().get_chroma()
        result = chroma.get(include=["documents", "metadatas"])

        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])
        ids = result.get("ids", [])

        self._bm25 = None
        self._docs = []
        self._metadatas = []

        if not documents:
            logger.warning("ChromaDB contains no documents; BM25 index remains empty")
            return

        indexed_rows: list[tuple[str, str, dict, list[str]]] = []
        for doc_id, doc, meta in zip(ids, documents, metadatas):
            normalized_meta = dict(meta or {})
            searchable_text = normalized_meta.get("searchable_text") or doc
            tokens = self._tokenize(searchable_text)
            if not tokens:
                continue
            normalized_meta.setdefault("chunk_id", doc_id)
            indexed_rows.append((doc_id, doc, normalized_meta, tokens))

        if not indexed_rows:
            logger.warning("BM25 tokenized corpus is empty; skip index build")
            return

        tokenized_corpus = [tokens for _, _, _, tokens in indexed_rows]
        self._bm25 = BM25Okapi(tokenized_corpus)
        for _, doc, normalized_meta, _ in indexed_rows:
            self._metadatas.append(normalized_meta)
            self._docs.append(Document(page_content=doc, metadata=normalized_meta))

        elapsed = time.time() - t0
        logger.info(
            "BM25 index built: %d docs | jieba tokenization | %.2fs",
            len(self._docs), elapsed,
        )

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        normalized = (text or "").strip().casefold()
        if not normalized:
            return []

        try:
            tokens = [token for token in jieba.cut(normalized) if str(token).strip()]
        except Exception:
            tokens = []

        if tokens:
            return tokens

        # Fallback tokenizer for non-empty text when jieba returns nothing unexpectedly.
        return re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]|[^\s]", normalized)
