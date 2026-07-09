import unittest
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.rag import RagChain


@pytest.fixture(autouse=True)
def _isolated_test_storage(isolated_storage):
    isolated_storage(
        db_name="neighbor-expansion.db",
        chroma_name="neighbor-expansion-chroma",
        data_dir_name="neighbor-expansion-data",
    )


class NeighborExpansionTests(unittest.TestCase):
    def setUp(self):
        # 创建模拟的 VectorStore 和 Collection
        self.mock_store = MagicMock()
        self.mock_collection = MagicMock()
        self.mock_store._collection = self.mock_collection
        
    @patch('rag_knowledge.repository.vector_store.VectorStore._get_store')
    def test_get_neighbor_chunks_builds_correct_filter(self, mock_get_store):
        mock_get_store.return_value = self.mock_store
        
        # 模拟 ChromaDB 返回值
        self.mock_collection.get.return_value = {
            "ids": ["id-1", "id-2"],
            "documents": ["content 1", "content 2"],
            "metadatas": [
                {"source": "test.docx", "section_index": 21, "chunk_id": "id-1"},
                {"source": "test.docx", "section_index": 22, "chunk_id": "id-2"}
            ]
        }
        
        vs = VectorStore()
        # 清空单例状态或注入模拟的 store
        vs._store = self.mock_store
        
        results = vs.get_neighbor_chunks("test.docx", 23, window=2)
        
        # 验证返回的 Document 列表
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].page_content, "content 1")
        self.assertEqual(results[0].metadata["section_index"], 21)
        self.assertEqual(results[1].page_content, "content 2")
        self.assertEqual(results[1].metadata["section_index"], 22)
        
        # 验证调用 collection.get 时传入的 filter 条件
        args, kwargs = self.mock_collection.get.call_args
        self.assertIn("where", kwargs)
        where_clause = kwargs["where"]
        
        self.assertIn("$and", where_clause)
        conditions = where_clause["$and"]
        
        # 验证具体过滤项
        source_cond = next(c for c in conditions if "source" in c)
        self.assertEqual(source_cond["source"]["$eq"], "test.docx")
        
        idx_gte = next(c for c in conditions if "section_index" in c and "$gte" in c["section_index"])
        self.assertEqual(idx_gte["section_index"]["$gte"], 21) # 23 - 2
        
        idx_lte = next(c for c in conditions if "section_index" in c and "$lte" in c["section_index"])
        self.assertEqual(idx_lte["section_index"]["$lte"], 25) # 23 + 2
        
        idx_ne = next(c for c in conditions if "section_index" in c and "$ne" in c["section_index"])
        self.assertEqual(idx_ne["section_index"]["$ne"], 23)

    def test_expand_neighbor_chunks_dedup(self):
        # 准备 RAG 实例
        rag = RagChain()
        
        # 模拟 VectorStore 里的 get_neighbor_chunks
        rag._store = MagicMock()
        
        # 模拟检索返回 2 个相邻 chunk，其中 id-2 已存在，id-3 为新 chunk
        rag._store.get_neighbor_chunks.return_value = [
            Document(page_content="c2", metadata={"chunk_id": "id-2", "source": "a.docx", "section_index": 2}),
            Document(page_content="c3", metadata={"chunk_id": "id-3", "source": "a.docx", "section_index": 3})
        ]
        
        # 输入已有 2 个 Document (id-1, id-2)
        docs = [
            Document(page_content="c1", metadata={"chunk_id": "id-1", "source": "a.docx", "section_index": 1}),
            Document(page_content="c2", metadata={"chunk_id": "id-2", "source": "a.docx", "section_index": 2})
        ]
        
        # 执行扩展（不使用 config window 限制，使用自定义或默认）
        merged = rag._expand_neighbor_chunks(docs, window=1, max_per_source=5)
        
        # 应该总共有 3 个 chunk（id-1, id-2, id-3），已去重
        self.assertEqual(len(merged), 3)
        self.assertEqual([d.metadata["chunk_id"] for d in merged], ["id-1", "id-2", "id-3"])
        
    def test_expand_neighbor_chunks_max_per_source(self):
        rag = RagChain()
        rag._store = MagicMock()
        
        # 模拟返回 3 个新相邻 chunk，都来自 a.docx
        rag._store.get_neighbor_chunks.return_value = [
            Document(page_content="c2", metadata={"chunk_id": "id-2", "source": "a.docx", "section_index": 2}),
            Document(page_content="c3", metadata={"chunk_id": "id-3", "source": "a.docx", "section_index": 3}),
            Document(page_content="c4", metadata={"chunk_id": "id-4", "source": "a.docx", "section_index": 4}),
            Document(page_content="c5", metadata={"chunk_id": "id-5", "source": "a.docx", "section_index": 5})
        ]
        
        # 输入已有 1 个 (id-1)
        docs = [
            Document(page_content="c1", metadata={"chunk_id": "id-1", "source": "a.docx", "section_index": 1})
        ]
        
        # 限制每源最多 3 个 (已有 1，新规加 2，合计 3 个，第四个 id-5 应该被丢弃)
        merged = rag._expand_neighbor_chunks(docs, window=3, max_per_source=3)
        
        self.assertEqual(len(merged), 3)
        self.assertEqual([d.metadata["chunk_id"] for d in merged], ["id-1", "id-2", "id-3"])


if __name__ == "__main__":
    unittest.main()
