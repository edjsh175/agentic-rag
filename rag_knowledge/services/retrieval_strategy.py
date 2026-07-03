"""
检索策略调度器

按配置选择 mmr / similarity / bm25 / hybrid 检索方式。
"""
import asyncio
import functools
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.repository.vector_store import VectorStore

logger = logging.getLogger(__name__)


class RetrievalStrategy:
    """检索策略调度器"""

    def __init__(self):
        self._cfg = Config()
        self._store = VectorStore()
        self._bm25 = None  # 懒加载
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, self._cfg.cache.retrieval_executor_workers)
        )

    def _get_bm25(self):
        if self._bm25 is None:
            from rag_knowledge.services.bm25_store import BM25Store
            self._bm25 = BM25Store()
        return self._bm25

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def retrieve(
        self,
        question: str,
        kb_name: str | None = None,
        doc_category: str | None = None,
        review_status: str | None = "approved",
        method: str | None = None,
        top_k: int | None = None,
    ) -> list[Document]:
        """
        执行检索，返回 LangChain Document 列表。

        参数：
          method: 检索方式，None 则使用配置值（mmr/similarity/bm25/hybrid）
          top_k:  覆盖配置中的 retrieval_top_k（用于重排序等场景获取更多候选文档）
        """
        actual_method = method or self._cfg.retrieval_strategy
        logger.info("检索策略: %s | kb=%s", actual_method, kb_name or "auto")

        supported = {"mmr", "similarity", "bm25", "hybrid"}
        if actual_method not in supported:
            raise ValueError(
                f"不支持的检索策略: {actual_method}，可选值: {', '.join(sorted(supported))}"
            )

        if actual_method == "bm25":
            return self._retrieve_bm25(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                top_k=top_k,
            )
        elif actual_method == "hybrid":
            return self._retrieve_hybrid(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                top_k=top_k,
            )
        elif actual_method == "similarity":
            return self._retrieve_vector(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                search_type="similarity", top_k=top_k,
            )
        else:
            # 默认 mmr
            return self._retrieve_vector(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                search_type="mmr", top_k=top_k,
            )

    async def aretrieve(
        self,
        question: str,
        kb_name: str | None = None,
        doc_category: str | None = None,
        review_status: str | None = "approved",
        method: str | None = None,
        top_k: int | None = None,
    ) -> list[Document]:
        actual_method = method or self._cfg.retrieval_strategy
        logger.info("async 检索策略: %s | kb=%s", actual_method, kb_name or "auto")

        supported = {"mmr", "similarity", "bm25", "hybrid"}
        if actual_method not in supported:
            raise ValueError(
                f"不支持的检索策略: {actual_method}，可选值: {', '.join(sorted(supported))}"
            )

        if actual_method == "bm25":
            return await self._aretrieve_bm25(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                top_k=top_k,
            )
        if actual_method == "hybrid":
            return await self._aretrieve_hybrid(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                top_k=top_k,
            )
        if actual_method == "similarity":
            return await self._aretrieve_vector(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                search_type="similarity", top_k=top_k,
            )
        return await self._aretrieve_vector(
            question, kb_name=kb_name,
            doc_category=doc_category, review_status=review_status,
            search_type="mmr", top_k=top_k,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _build_filter(
        self,
        kb_name: str | None,
        review_status: str | None,
        doc_category: str | None,
    ) -> dict | None:
        """构建 ChromaDB 过滤条件字典"""
        conditions = []
        if kb_name:
            conditions.append({"kb_name": kb_name})
        if review_status:
            conditions.append({"review_status": review_status})
        if doc_category:
            conditions.append({"doc_category": doc_category})
        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def _retrieve_vector(
        self,
        question: str,
        kb_name: str | None,
        doc_category: str | None,
        review_status: str | None,
        search_type: str,
        top_k: int | None = None,
    ) -> list[Document]:
        """MMR / Similarity 向量检索"""
        chroma = self._store.get_chroma()
        filt = self._build_filter(kb_name, review_status, doc_category)

        search_kwargs: dict = {"k": top_k or self._cfg.retrieval_top_k, "filter": filt}
        if search_type == "mmr":
            search_kwargs["fetch_k"] = self._cfg.retrieval_fetch_k
            search_kwargs["lambda_mult"] = self._cfg.retrieval_lambda_mult

        retriever = chroma.as_retriever(search_type=search_type, search_kwargs=search_kwargs)
        return retriever.invoke(question)

    def _retrieve_bm25(
        self,
        question: str,
        kb_name: str | None,
        doc_category: str | None,
        review_status: str | None,
        top_k: int | None = None,
    ) -> list[Document]:
        """BM25 关键词检索"""
        return self._get_bm25().search(
            question,
            kb_name=kb_name,
            review_status=review_status,
            doc_category=doc_category,
            top_k=top_k or self._cfg.retrieval_top_k,
        )

    def _retrieve_hybrid(
        self,
        question: str,
        kb_name: str | None,
        doc_category: str | None,
        review_status: str | None,
        top_k: int | None = None,
    ) -> list[Document]:
        """Similarity + BM25，并使用 RRF 融合两路排名。"""
        if self._cfg.retrieval_fusion_method != "rrf":
            raise ValueError(
                f"不支持的融合方式: {self._cfg.retrieval_fusion_method}，当前仅支持 rrf"
            )

        candidate_k = self._cfg.retrieval_candidate_k
        vector_docs = self._retrieve_vector(
            question, kb_name=kb_name,
            doc_category=doc_category, review_status=review_status,
            search_type="similarity", top_k=candidate_k,
        )
        bm25_docs = self._retrieve_bm25(
            question, kb_name=kb_name,
            doc_category=doc_category, review_status=review_status,
            top_k=candidate_k,
        )
        return self._rrf_fuse(
            [vector_docs, bm25_docs],
            rrf_k=self._cfg.retrieval_rrf_k,
            top_k=top_k or self._cfg.retrieval_top_k,
        )

    async def _run_blocking(self, func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        executor = getattr(self, "_executor", None)
        bound = functools.partial(func, *args, **kwargs)
        if executor is None:
            return await asyncio.to_thread(bound)
        return await loop.run_in_executor(executor, bound)

    async def _aretrieve_vector(
        self,
        question: str,
        kb_name: str | None,
        doc_category: str | None,
        review_status: str | None,
        search_type: str,
        top_k: int | None = None,
    ) -> list[Document]:
        return await self._run_blocking(
            self._retrieve_vector,
            question,
            kb_name,
            doc_category,
            review_status,
            search_type,
            top_k,
        )

    async def _aretrieve_bm25(
        self,
        question: str,
        kb_name: str | None,
        doc_category: str | None,
        review_status: str | None,
        top_k: int | None = None,
    ) -> list[Document]:
        return await self._run_blocking(
            self._retrieve_bm25,
            question,
            kb_name,
            doc_category,
            review_status,
            top_k,
        )

    async def _aretrieve_hybrid(
        self,
        question: str,
        kb_name: str | None,
        doc_category: str | None,
        review_status: str | None,
        top_k: int | None = None,
    ) -> list[Document]:
        if self._cfg.retrieval_fusion_method != "rrf":
            raise ValueError(
                f"不支持的融合方式: {self._cfg.retrieval_fusion_method}，当前仅支持 rrf"
            )

        candidate_k = self._cfg.retrieval_candidate_k
        started = time.perf_counter()
        vector_docs, bm25_docs = await asyncio.gather(
            self._aretrieve_vector(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                search_type="similarity", top_k=candidate_k,
            ),
            self._aretrieve_bm25(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                top_k=candidate_k,
            ),
        )
        logger.debug(
            "Hybrid async recall finished | kb=%s | elapsed=%.3fs",
            kb_name or "auto",
            time.perf_counter() - started,
        )
        return self._rrf_fuse(
            [vector_docs, bm25_docs],
            rrf_k=self._cfg.retrieval_rrf_k,
            top_k=top_k or self._cfg.retrieval_top_k,
        )

    @staticmethod
    def _rrf_fuse(
        ranked_lists: list[list[Document]],
        rrf_k: int,
        top_k: int,
    ) -> list[Document]:
        """按 chunk_id 去重并执行 Reciprocal Rank Fusion。"""
        fused: dict[str, dict] = {}
        for docs in ranked_lists:
            for rank, doc in enumerate(docs, start=1):
                chunk_id = doc.metadata.get("chunk_id")
                if not chunk_id:
                    logger.warning("Hybrid 候选缺少 chunk_id，已跳过")
                    continue
                entry = fused.setdefault(
                    chunk_id,
                    {"doc": doc, "score": 0.0, "best_rank": rank},
                )
                entry["score"] += 1.0 / (rrf_k + rank)
                entry["best_rank"] = min(entry["best_rank"], rank)

        ranked = sorted(
            fused.items(),
            key=lambda item: (-item[1]["score"], item[1]["best_rank"], item[0]),
        )
        return [entry["doc"] for _, entry in ranked[:top_k]]
