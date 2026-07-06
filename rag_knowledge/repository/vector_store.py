"""
向量数据库操作层 —— 封装 ChromaDB 的所有读写

职责：
  - 连接与管理 Chroma 集合
  - 文档向量化与批量存储
  - 语义相似度检索
  - 按 ID 删除文档块
"""
from __future__ import annotations

import uuid
from pathlib import Path

from rag_knowledge.runtime_guard import validate_chroma_runtime

# Validate before importing either Chroma package so a wrong/missing environment
# gets an actionable error without touching the persistent database.
validate_chroma_runtime()

from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.services.embedding_cache import get_embedding_cache


class CachedOllamaEmbeddings(OllamaEmbeddings):
    def __init__(self, *, cache, **kwargs):
        super().__init__(**kwargs)
        self._cache = cache

    def embed_query(self, text: str) -> list[float]:
        cached = self._cache.get(self.model, text)
        if cached is not None:
            return cached

        vector = super().embed_query(text)
        self._cache.put(self.model, text, vector)
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)
        missing: dict[str, list[int]] = {}

        for index, text in enumerate(texts):
            cached = self._cache.get(self.model, text)
            if cached is not None:
                results[index] = cached
            else:
                missing.setdefault(text, []).append(index)

        if missing:
            missing_texts = list(missing.keys())
            vectors = super().embed_documents(missing_texts)
            for text, vector in zip(missing_texts, vectors):
                self._cache.put(self.model, text, vector)
                for index in missing[text]:
                    results[index] = list(vector)

        return [vector or [] for vector in results]

    async def aembed_query(self, text: str) -> list[float]:
        cached = self._cache.get(self.model, text)
        if cached is not None:
            return cached

        vector = await super().aembed_query(text)
        self._cache.put(self.model, text, vector)
        return vector

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)
        missing: dict[str, list[int]] = {}

        for index, text in enumerate(texts):
            cached = self._cache.get(self.model, text)
            if cached is not None:
                results[index] = cached
            else:
                missing.setdefault(text, []).append(index)

        if missing:
            missing_texts = list(missing.keys())
            vectors = await super().aembed_documents(missing_texts)
            for text, vector in zip(missing_texts, vectors):
                self._cache.put(self.model, text, vector)
                for index in missing[text]:
                    results[index] = list(vector)

        return [vector or [] for vector in results]


class VectorStore:
    """向量数据库操作封装（单例）"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        validate_chroma_runtime()
        cfg = Config()
        self._embedding_cache = get_embedding_cache(
            enabled=cfg.cache.embedding_cache_enabled,
            capacity=cfg.cache.embedding_cache_capacity,
        )
        self._embeddings = CachedOllamaEmbeddings(
            cache=self._embedding_cache,
            model=cfg.embedding_model,
            base_url=cfg.ollama_base_url,
        )
        self._store: Chroma | None = None
        self._persist_dir = self._safe_persist_path(cfg.chroma_dir)
        self._collection_name = cfg.collection_name
        self._initialized = True

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_persist_path(path: Path) -> str:
        """Avoid passing a non-ASCII absolute path to Chroma's native HNSW layer."""
        absolute = Path(path)
        try:
            relative = absolute.relative_to(Path.cwd())
        except ValueError:
            return str(absolute)

        relative_text = str(relative)
        if relative_text.isascii():
            return relative_text
        return str(absolute)

    def _get_store(self) -> Chroma:
        """获取（或创建）Chroma 集合"""
        if self._store is None:
            self._store = Chroma(
                collection_name=self._collection_name,
                embedding_function=self._embeddings,
                persist_directory=self._persist_dir,
                # 当前语料约 600 chunks，低于 Chroma 默认的 1000 条同步阈值。
                # 降低阈值，确保小型知识库也会及时生成完整 HNSW 持久化文件。
                collection_metadata={
                    "hnsw:batch_size": 50,
                    "hnsw:sync_threshold": 100,
                },
            )
        return self._store

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def add_chunks(self, chunks: list[Document]) -> list[str]:
        """
        批量存入文本块

        参数：
          chunks: LangChain Document 列表

        返回：
          每个 Document 在 Chroma 中的 ID 列表
        """
        store = self._get_store()
        doc_ids = []
        for doc in chunks:
            doc_id = str(uuid.uuid4())
            doc.metadata = self._normalize_metadata(doc.metadata)
            doc.metadata["chunk_id"] = doc_id
            doc_ids.append(doc_id)
        store.add_documents(chunks, ids=doc_ids)
        return doc_ids

    def search(self, query: str, k: int = 4, filter: dict | None = None) -> list[Document]:
        """语义相似度检索，返回 top-k 个相关文本块"""
        kwargs = {"k": k}
        if filter:
            kwargs["filter"] = filter
        return self._get_store().similarity_search(query, **kwargs)

    def search_with_score(self, query: str, k: int = 4, filter: dict | None = None) -> list[tuple[Document, float]]:
        """带相关度分数的检索（分数越低越相关）"""
        kwargs = {"k": k}
        if filter:
            kwargs["filter"] = filter
        return self._get_store().similarity_search_with_relevance_scores(query, **kwargs)

    def get_neighbor_chunks(
        self,
        source: str,
        section_index: int,
        window: int = 2,
        review_status: str | None = "approved",
    ) -> list[Document]:
        """按 source 文件名和 section_index 获取相邻 chunk。

        例如命中 source=A.docx, section_index=23，window=2，
        则返回 section_index ∈ [21, 22, 24, 25] 的 chunk（不含自身 23）。
        使用 ChromaDB 原生 metadata filter ($and/$gte/$lte)。
        """
        if not source or section_index is None:
            return []

        where_conditions = [
            {"source": {"$eq": source}},
            {"section_index": {"$gte": int(section_index - window)}},
            {"section_index": {"$lte": int(section_index + window)}},
            {"section_index": {"$ne": int(section_index)}}
        ]
        if review_status:
            where_conditions.append({"review_status": {"$eq": review_status}})

        where = {"$and": where_conditions}
        
        try:
            collection = self._get_store()._collection
            res = collection.get(
                where=where,
                include=["documents", "metadatas"]
            )
            
            documents = []
            ids = res.get("ids") or []
            metadatas = res.get("metadatas") or []
            contents = res.get("documents") or []
            
            for idx, content in enumerate(contents):
                if idx >= len(metadatas):
                    break
                meta = dict(metadatas[idx] or {})
                if "chunk_id" not in meta and idx < len(ids):
                    meta["chunk_id"] = ids[idx]
                documents.append(Document(page_content=content, metadata=meta))
                
            # 按 section_index 升序排序
            try:
                documents.sort(key=lambda d: int(d.metadata.get("section_index", 0)))
            except (TypeError, ValueError):
                pass
                
            return documents
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("获取相邻 chunk 失败: %s", exc)
            return []

    def get_chunks_by_metadata(
        self,
        filters: dict,
        limit: int = 20,
    ) -> list[Document]:
        """按 metadata 条件查询 chunk（不走向量检索）。
        
        filters: ChromaDB where 条件字典
        """
        if not filters:
            return []
        try:
            collection = self._get_store()._collection
            res = collection.get(
                where=filters,
                limit=limit,
                include=["documents", "metadatas"]
            )
            
            documents = []
            ids = res.get("ids") or []
            metadatas = res.get("metadatas") or []
            contents = res.get("documents") or []
            
            for idx, content in enumerate(contents):
                if idx >= len(metadatas):
                    break
                meta = dict(metadatas[idx] or {})
                if "chunk_id" not in meta and idx < len(ids):
                    meta["chunk_id"] = ids[idx]
                documents.append(Document(page_content=content, metadata=meta))
                
            return documents
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("根据元数据查询 chunk 失败: %s", exc)
            return []

    def delete(self, ids: list[str]):
        """按 chunk_id 列表删除"""
        if ids:
            self._get_store().delete(ids)

    def update_metadata(self, ids: list[str], metadata: dict) -> int:
        """按 chunk_id 批量更新 metadata，返回成功更新的数量。"""
        if not ids:
            return 0

        collection = self._get_store()._collection
        existing = collection.get(ids=ids, include=["metadatas"])
        existing_ids = existing.get("ids", [])
        existing_metadatas = existing.get("metadatas", [])
        if not existing_ids:
            return 0

        merged_metadatas = []
        for old_meta in existing_metadatas:
            merged = dict(old_meta or {})
            merged.update(metadata)
            merged_metadatas.append(merged)

        collection.update(ids=existing_ids, metadatas=merged_metadatas)
        return len(existing_ids)

    def set_embedding_model(self, model: str) -> None:
        """
        切换向量模型（仅在重建知识库前调用，否则已有向量无法匹配）
        会销毁当前 Chroma 实例，下次 get_chroma() 时重建
        """
        cfg = Config()
        self._embedding_cache.clear()
        self._embeddings = CachedOllamaEmbeddings(
            cache=self._embedding_cache,
            model=model,
            base_url=cfg.ollama_base_url,
        )
        self._store = None
        self._collection_name = cfg.collection_name

    def get_chroma(self) -> Chroma:
        """暴露 Chroma 实例（给 RAG 链的检索器使用）"""
        return self._get_store()

    def clear(self):
        """清空当前 Chroma 集合，供在线重建使用。"""
        import gc as _gc

        # 在线 /rebuild 时当前进程仍持有 SQLite 连接。Windows 无法在此时
        # 删除 chroma.sqlite3；删除项目唯一的集合即可清空全部向量数据。
        store = self._get_store()
        try:
            store.delete_collection()
        finally:
            self._store = None
            embedding_cache = getattr(self, "_embedding_cache", None)
            if embedding_cache is not None:
                embedding_cache.clear()
            _gc.collect()

        Path(self._persist_dir).mkdir(parents=True, exist_ok=True)

    def count(self) -> int:
        """当前集合中文本块总数"""
        return self._get_store()._collection.count()

    def get_chunk_stats_source(self) -> dict:
        """返回 chunk 统计接口所需的原始数据快照。"""
        return self._get_store()._collection.get(include=["documents", "metadatas"])

    @staticmethod
    def _normalize_metadata(metadata: dict) -> dict:
        """将 metadata 规范化为 Chroma 可接受的基础类型。"""
        normalized = {}
        for key, value in (metadata or {}).items():
            if value is None:
                normalized[key] = ""
            elif isinstance(value, (str, int, float, bool)):
                normalized[key] = value
            else:
                normalized[key] = str(value)
        return normalized
