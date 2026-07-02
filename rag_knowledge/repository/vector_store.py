"""
向量数据库操作层 —— 封装 ChromaDB 的所有读写

职责：
  - 连接与管理 Chroma 集合
  - 文档向量化与批量存储
  - 语义相似度检索
  - 按 ID 删除文档块
"""
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
        self._embeddings = OllamaEmbeddings(
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
        self._embeddings = OllamaEmbeddings(
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
            _gc.collect()

        Path(self._persist_dir).mkdir(parents=True, exist_ok=True)

    def count(self) -> int:
        """当前集合中文本块总数"""
        return self._get_store()._collection.count()

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
