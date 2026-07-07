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

_TABLE_QUERY_HINTS = ("规范", "要求", "字段", "表结构", "点表", "线表", "数据结构")
_TABLE_CONTENT_HINTS = ("字段名", "说明")
_TABLE_SECTION_TERMS = ("点表", "线表", "面表")
_STRUCTURED_QUERY_SUFFIX = " 字段名 说明 数据结构"


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
        candidate_k: int | None = None,
    ) -> list[Document]:
        """
        执行检索，返回 LangChain Document 列表。

        参数：
          method: 检索方式，None 则使用配置值（mmr/similarity/bm25/hybrid）
          top_k:  覆盖配置中的 retrieval_top_k（用于重排序等场景获取更多候选文档）
          candidate_k: 覆盖 hybrid 每路候选池大小
        """
        actual_method = method or self._cfg.retrieval_strategy
        retrieval_query = self._augment_structured_query(question)
        logger.info("检索策略: %s | kb=%s", actual_method, kb_name or "auto")

        supported = {"mmr", "similarity", "bm25", "hybrid"}
        if actual_method not in supported:
            raise ValueError(
                f"不支持的检索策略: {actual_method}，可选值: {', '.join(sorted(supported))}"
            )

        if actual_method == "bm25":
            docs = self._retrieve_bm25(
                retrieval_query, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                top_k=top_k,
            )
        elif actual_method == "hybrid":
            docs = self._retrieve_hybrid(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                top_k=top_k,
                candidate_k=candidate_k,
            )
        elif actual_method == "similarity":
            docs = self._retrieve_vector(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                search_type="similarity", top_k=top_k,
            )
        else:
            # 默认 mmr
            docs = self._retrieve_vector(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                search_type="mmr", top_k=top_k,
            )
        return docs

    async def aretrieve(
        self,
        question: str,
        kb_name: str | None = None,
        doc_category: str | None = None,
        review_status: str | None = "approved",
        method: str | None = None,
        top_k: int | None = None,
        candidate_k: int | None = None,
    ) -> list[Document]:
        actual_method = method or self._cfg.retrieval_strategy
        retrieval_query = self._augment_structured_query(question)
        logger.info("async 检索策略: %s | kb=%s", actual_method, kb_name or "auto")

        supported = {"mmr", "similarity", "bm25", "hybrid"}
        if actual_method not in supported:
            raise ValueError(
                f"不支持的检索策略: {actual_method}，可选值: {', '.join(sorted(supported))}"
            )

        if actual_method == "bm25":
            docs = await self._aretrieve_bm25(
                retrieval_query, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                top_k=top_k,
            )
        elif actual_method == "hybrid":
            docs = await self._aretrieve_hybrid(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                top_k=top_k,
                candidate_k=candidate_k,
            )
        elif actual_method == "similarity":
            docs = await self._aretrieve_vector(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                search_type="similarity", top_k=top_k,
            )
        else:
            docs = await self._aretrieve_vector(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                search_type="mmr", top_k=top_k,
            )
        return docs

    @staticmethod
    def _augment_structured_query(query: str) -> str:
        normalized = (query or "").strip()
        if not normalized:
            return normalized
        if not any(hint in normalized for hint in _TABLE_QUERY_HINTS):
            return normalized
        if all(term in normalized for term in _TABLE_CONTENT_HINTS):
            return normalized
        return f"{normalized}{_STRUCTURED_QUERY_SUFFIX}"

    def _apply_structured_query_boost(self, query: str, docs: list[Document]) -> list[Document]:
        # Legacy no-op helper: structured query boost is now unified in RetrievalQualityStrategy.
        return docs

    @staticmethod
    def _raw_retrieval_score(metadata: dict) -> float:
        for key in ("rerank_score", "rrf_score", "similarity_score", "score"):
            if key in metadata:
                try:
                    return float(metadata[key])
                except (TypeError, ValueError):
                    continue
        return 0.0

    # ------------------------------------------------------------------
    # 多查询检索（Multi-query Retrieval）
    # ------------------------------------------------------------------

    def retrieve_many(
        self,
        queries: list[str],
        kb_name: str | None = None,
        doc_category: str | None = None,
        review_status: str | None = "approved",
        method: str | None = None,
        top_k: int | None = None,
        query_weights: list[float] | None = None,
        query_labels: list[str] | None = None,
        candidate_k: int | None = None,
    ) -> list[Document]:
        """对多个查询分别检索，用 RRF 融合所有结果。

        每个查询走完整策略（hybrid 内部已含 vector+BM25 的 RRF），
        外层再做一次跨查询 RRF，保证多角度召回的综合排序。

        参数：
          queries: 多个检索查询（原始问题、上下文化改写、来源锚点等）
          其余参数同 retrieve()
        """
        if not queries:
            return []

        if len(queries) == 1:
            return self.retrieve(
                queries[0], kb_name=kb_name, doc_category=doc_category,
                review_status=review_status, method=method, top_k=top_k,
                candidate_k=candidate_k,
            )

        actual_method = method or self._cfg.retrieval_strategy
        actual_top_k = top_k or self._cfg.retrieval_top_k
        # 每个查询拉取 candidate_k 条，给 RRF 足够的候选池
        actual_candidate_k = candidate_k or self._cfg.retrieval_candidate_k
        per_query_k = max(actual_top_k, actual_candidate_k)

        logger.info(
            "多查询检索: %d queries | method=%s | kb=%s | per_query_k=%d | final_top_k=%d",
            len(queries), actual_method, kb_name or "auto", per_query_k, actual_top_k,
        )

        all_ranked: list[list[Document]] = []
        effective_weights: list[float] = []
        effective_labels: list[str] = []
        for i, q in enumerate(queries):
            q = q.strip()
            if not q:
                continue
            try:
                docs = self.retrieve(
                    q, kb_name=kb_name, doc_category=doc_category,
                    review_status=review_status, method=actual_method,
                    top_k=per_query_k,
                    candidate_k=actual_candidate_k,
                )
                all_ranked.append(docs)
                weight = query_weights[i] if query_weights and i < len(query_weights) else 1.0
                label = query_labels[i] if query_labels and i < len(query_labels) else f"query_{i}"
                effective_weights.append(weight)
                effective_labels.append(label)
                logger.info(
                    "multi_query_branch | index=%d label=%s weight=%.2f hits=%d",
                    i, label, weight, len(docs),
                )
            except Exception as e:
                logger.warning("多查询检索 query[%d] 失败，跳过: %s", i, e)

        if not all_ranked:
            return []

        if len(all_ranked) == 1 and query_weights is None and query_labels is None:
            return all_ranked[0][:actual_top_k]

        return self._rrf_fuse(
            all_ranked,
            rrf_k=self._cfg.retrieval_rrf_k,
            top_k=actual_top_k,
            weights=effective_weights,
            labels=effective_labels,
        )

    async def aretrieve_many(
        self,
        queries: list[str],
        kb_name: str | None = None,
        doc_category: str | None = None,
        review_status: str | None = "approved",
        method: str | None = None,
        top_k: int | None = None,
        query_weights: list[float] | None = None,
        query_labels: list[str] | None = None,
        candidate_k: int | None = None,
    ) -> list[Document]:
        """异步版多查询检索。各查询并发执行。"""
        if not queries:
            return []

        if len(queries) == 1:
            return await self.aretrieve(
                queries[0], kb_name=kb_name, doc_category=doc_category,
                review_status=review_status, method=method, top_k=top_k,
                candidate_k=candidate_k,
            )

        actual_method = method or self._cfg.retrieval_strategy
        actual_top_k = top_k or self._cfg.retrieval_top_k
        actual_candidate_k = candidate_k or self._cfg.retrieval_candidate_k
        per_query_k = max(actual_top_k, actual_candidate_k)

        logger.info(
            "async 多查询检索: %d queries | method=%s | kb=%s | per_query_k=%d | final_top_k=%d",
            len(queries), actual_method, kb_name or "auto", per_query_k, actual_top_k,
        )

        async def _fetch_one(q: str) -> list[Document]:
            q = q.strip()
            if not q:
                return []
            try:
                return await self.aretrieve(
                    q, kb_name=kb_name, doc_category=doc_category,
                    review_status=review_status, method=actual_method,
                    top_k=per_query_k,
                    candidate_k=actual_candidate_k,
                )
            except Exception as e:
                logger.warning("async 多查询检索 query 失败，跳过: %s", e)
                return []

        started = time.perf_counter()
        fetched = await asyncio.gather(*[_fetch_one(q) for q in queries])
        all_ranked: list[list[Document]] = []
        effective_weights: list[float] = []
        effective_labels: list[str] = []
        for i, docs in enumerate(fetched):
            if not docs:
                continue
            weight = query_weights[i] if query_weights and i < len(query_weights) else 1.0
            label = query_labels[i] if query_labels and i < len(query_labels) else f"query_{i}"
            all_ranked.append(docs)
            effective_weights.append(weight)
            effective_labels.append(label)
            logger.info(
                "async_multi_query_branch | index=%d label=%s weight=%.2f hits=%d",
                i, label, weight, len(docs),
            )

        logger.debug(
            "async 多查询检索并发完成 | %d/%d 有效 | elapsed=%.3fs",
            len(all_ranked), len(queries), time.perf_counter() - started,
        )

        if not all_ranked:
            return []
        if len(all_ranked) == 1 and query_weights is None and query_labels is None:
            return all_ranked[0][:actual_top_k]

        return self._rrf_fuse(
            all_ranked,
            rrf_k=self._cfg.retrieval_rrf_k,
            top_k=actual_top_k,
            weights=effective_weights,
            labels=effective_labels,
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
        candidate_k: int | None = None,
    ) -> list[Document]:
        """Similarity + BM25，并使用 RRF 融合两路排名。"""
        if self._cfg.retrieval_fusion_method != "rrf":
            raise ValueError(
                f"不支持的融合方式: {self._cfg.retrieval_fusion_method}，当前仅支持 rrf"
            )

        candidate_k = candidate_k or self._cfg.retrieval_candidate_k
        augmented_query = self._augment_structured_query(question)
        vector_docs = self._retrieve_vector(
            question, kb_name=kb_name,
            doc_category=doc_category, review_status=review_status,
            search_type="similarity", top_k=candidate_k,
        )
        bm25_docs = self._retrieve_bm25(
            augmented_query, kb_name=kb_name,
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
        candidate_k: int | None = None,
    ) -> list[Document]:
        if self._cfg.retrieval_fusion_method != "rrf":
            raise ValueError(
                f"不支持的融合方式: {self._cfg.retrieval_fusion_method}，当前仅支持 rrf"
            )

        candidate_k = candidate_k or self._cfg.retrieval_candidate_k
        augmented_query = self._augment_structured_query(question)
        started = time.perf_counter()
        vector_docs, bm25_docs = await asyncio.gather(
            self._aretrieve_vector(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                search_type="similarity", top_k=candidate_k,
            ),
            self._aretrieve_bm25(
                augmented_query, kb_name=kb_name,
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
        weights: list[float] | None = None,
        labels: list[str] | None = None,
    ) -> list[Document]:
        """按 chunk_id 去重并执行可选加权的 Reciprocal Rank Fusion。"""
        fused: dict[str, dict] = {}
        for list_index, docs in enumerate(ranked_lists):
            weight = weights[list_index] if weights and list_index < len(weights) else 1.0
            label = labels[list_index] if labels and list_index < len(labels) else f"query_{list_index}"
            for rank, doc in enumerate(docs, start=1):
                chunk_id = doc.metadata.get("chunk_id")
                if not chunk_id:
                    logger.warning("Hybrid 候选缺少 chunk_id，已跳过")
                    continue
                entry = fused.setdefault(
                    chunk_id,
                    {"doc": doc, "score": 0.0, "best_rank": rank, "labels": []},
                )
                entry["score"] += weight / (rrf_k + rank)
                entry["best_rank"] = min(entry["best_rank"], rank)
                if label not in entry["labels"]:
                    entry["labels"].append(label)

        ranked = sorted(
            fused.items(),
            key=lambda item: (-item[1]["score"], item[1]["best_rank"], item[0]),
        )
        result: list[Document] = []
        for _, entry in ranked[:top_k]:
            doc = entry["doc"]
            doc.metadata["rrf_score"] = entry["score"]
            doc.metadata["matched_query_kinds"] = entry["labels"]
            result.append(doc)
        logger.info(
            "rrf_fusion | branches=%d candidates=%d returned=%d weighted=%s",
            len(ranked_lists), len(fused), len(result), weights is not None,
        )
        return result
