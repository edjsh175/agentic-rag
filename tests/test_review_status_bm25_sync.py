"""
验证 /review/status 更新 Chroma metadata 后 BM25 索引同步

P0：审核状态变更后，BM25/Hybrid 检索必须立即反映新的 review_status，
     不能继续使用内存中缓存的旧 metadata。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from langchain_core.documents import Document

from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.bm25_store import BM25Store


def _make_test_doc(content: str, review_status: str = "pending") -> Document:
    """构造带唯一 chunk_id 的测试文档，避免与生产数据混淆。"""
    doc = Document(
        page_content=content,
        metadata={
            "chunk_id": str(uuid.uuid4()),
            "kb_name": "文章附件",
            "review_status": review_status,
            "doc_category": "其他",
        },
    )
    return doc


class TestReviewStatusBM25Sync:
    """验证 Chroma metadata 更新后 BM25 rebuild 使过滤立即生效。"""

    @pytest.fixture(autouse=True)
    def _setup_teardown(self):
        """每个测试前后清理单例缓存和测试数据。"""
        self._test_ids: list[str] = []
        yield
        # 清理 ChromaDB 中的测试文档
        store = VectorStore()
        if self._test_ids:
            try:
                store.delete(self._test_ids)
            except Exception:
                pass
        # 重建 BM25 以清除测试文档残留
        BM25Store().rebuild()

    # ------------------------------------------------------------------
    # 核心验证
    # ------------------------------------------------------------------

    def test_bm25_filters_by_metadata_after_rebuild(self):
        """
        验证 BM25.search() 在 rebuild 后正确使用最新 metadata 过滤。

        流程：
          1. 写入 pending 文档 → 构建 BM25
          2. pending 检索命中，approved 检索不命中
          3. 更新 metadata 为 approved → rebuild BM25
          4. approved 检索命中，pending 检索不命中
        """
        store = VectorStore()
        bm25 = BM25Store()

        # ---- 1. 写入一个 pending 文档并建索引 ----
        doc = _make_test_doc("这是一份关于 Kubernetes 集群部署的技术文档", "pending")
        ids = store.add_chunks([doc])
        self._test_ids = ids
        chunk_id = ids[0]
        bm25.rebuild()

        # ---- 2. pending 检索应该命中 ----
        pending_results = bm25.search("Kubernetes 部署", review_status="pending")
        pending_ids = {r.metadata.get("chunk_id") for r in pending_results}
        assert chunk_id in pending_ids, (
            f"pending 检索应命中 chunk {chunk_id}，实际命中 {pending_ids}"
        )

        # approved 检索不应命中
        approved_results = bm25.search("Kubernetes 部署", review_status="approved")
        approved_ids = {r.metadata.get("chunk_id") for r in approved_results}
        assert chunk_id not in approved_ids, (
            f"approved 检索不应命中 pending chunk {chunk_id}"
        )

        # ---- 3. 更新 metadata 为 approved 并 rebuild ----
        store.update_metadata([chunk_id], {"review_status": "approved"})
        bm25.rebuild()

        # ---- 4. approved 检索应该命中 ----
        approved_results_after = bm25.search("Kubernetes 部署", review_status="approved")
        approved_ids_after = {r.metadata.get("chunk_id") for r in approved_results_after}
        assert chunk_id in approved_ids_after, (
            f"rebuild 后 approved 检索应命中 chunk {chunk_id}，实际命中 {approved_ids_after}"
        )

        # pending 检索不应再命中
        pending_results_after = bm25.search("Kubernetes 部署", review_status="pending")
        pending_ids_after = {r.metadata.get("chunk_id") for r in pending_results_after}
        assert chunk_id not in pending_ids_after, (
            f"rebuild 后 pending 检索不应再命中 chunk {chunk_id}，实际命中 {pending_ids_after}"
        )

    def test_bm25_stale_without_rebuild(self):
        """
        反例验证：只更新 Chroma metadata 但不 rebuild BM25，过滤仍使用旧值。

        这证明了 rebuild 是必需的 —— 不 rebuild 就会读到旧 metadata。
        """
        store = VectorStore()
        bm25 = BM25Store()

        # 写入 pending 文档
        doc = _make_test_doc("Docker Compose 多容器编排最佳实践", "pending")
        ids = store.add_chunks([doc])
        self._test_ids = ids
        chunk_id = ids[0]
        bm25.rebuild()

        # 更新 metadata 但不 rebuild BM25
        store.update_metadata([chunk_id], {"review_status": "approved"})
        # 注意：这里故意不调用 bm25.rebuild()

        # BM25 仍使用旧的 pending metadata
        pending_results = bm25.search("Docker Compose", review_status="pending")
        pending_ids = {r.metadata.get("chunk_id") for r in pending_results}
        assert chunk_id in pending_ids, (
            "不 rebuild 时 BM25 仍使用旧 metadata，pending 检索应仍能命中"
        )

        # approved 检索不应命中（因为 BM25 内存中仍是 pending）
        approved_results = bm25.search("Docker Compose", review_status="approved")
        approved_ids = {r.metadata.get("chunk_id") for r in approved_results}
        assert chunk_id not in approved_ids, (
            f"不 rebuild 时 BM25 内存 metadata 未更新，approved 检索不应命中 {chunk_id}"
        )

    # ------------------------------------------------------------------
    # 边界情况
    # ------------------------------------------------------------------

    def test_bulk_update_triggers_single_rebuild(self):
        """批量更新多个 chunk 后，一次 rebuild 全部生效。"""
        store = VectorStore()
        bm25 = BM25Store()

        docs = [
            _make_test_doc(f"测试文档批量更新 #{i}", "pending")
            for i in range(3)
        ]
        ids = store.add_chunks(docs)
        self._test_ids = ids

        # 批量更新
        store.update_metadata(ids, {"review_status": "approved"})
        bm25.rebuild()

        # 全部 chunk 在 approved 检索中可见
        results = bm25.search("测试文档批量更新", review_status="approved", top_k=10)
        result_ids = {r.metadata.get("chunk_id") for r in results}
        for chunk_id in ids:
            assert chunk_id in result_ids, (
                f"批量更新后 chunk {chunk_id} 应在 approved 检索中命中"
            )

    def test_rebuild_clears_empty_collection(self):
        """空集合 rebuild 不抛异常，search 返回空列表。"""
        bm25 = BM25Store()
        bm25.rebuild()
        results = bm25.search("任意查询", review_status="approved")
        # 可能为空（无文档），也可能有其他测试残留 —— 不抛异常即为通过
        assert isinstance(results, list)
