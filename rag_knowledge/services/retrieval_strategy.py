"""
检索策略调度器

按配置选择 mmr / similarity / bm25 / hybrid 检索方式。
"""
import asyncio
import functools
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.retrieval_intent import RetrievalIntentResolver
from rag_knowledge.services.retrieval_diagnostics import (
    record_request,
    record_stage,
    scope_filter_summary,
)

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

    @staticmethod
    def _normalize_scope(scope: Any) -> Any:
        if scope is None:
            return None
        if hasattr(scope, "to_evidence_scope"):
            return scope.to_evidence_scope()
        return scope

    @staticmethod
    def _requires_structural_admission(scope: Any) -> bool:
        if scope is None:
            return False
        if getattr(scope, "candidate_pipeline_v2", False):
            return False
        if getattr(scope, "target_entities", None) or getattr(scope, "materialized_chunk_ids", None):
            return True
        return bool(getattr(scope, "is_identity_locked", False) and hasattr(scope, "is_structurally_admissible"))

    @staticmethod
    def _filter_by_scope(docs: list[Document], scope: Any) -> list[Document]:
        """Execute final structural admission for either a legacy scope or one step-level Grant."""
        if not docs or scope is None:
            return docs
        norm_scope = RetrievalStrategy._normalize_scope(scope)
        if norm_scope is None or not RetrievalStrategy._requires_structural_admission(norm_scope):
            return docs

        is_grant = hasattr(norm_scope, "grant_id")
        filtered: list[Document] = []
        for doc in docs:
            meta = doc.metadata or {}
            chunk_id = str(meta.get("chunk_id") or "").strip()
            doc_ent = str(
                meta.get("scope_entity")
                or meta.get("document_entity")
                or meta.get("entity_name")
                or ""
            ).strip()
            if not norm_scope.is_structurally_admissible(doc_ent, chunk_id):
                logger.debug(
                    "Structural admission rejected chunk | entity=%s chunk=%s ref=%s",
                    doc_ent or "<unscoped>",
                    chunk_id,
                    getattr(norm_scope, "grant_id", None) or getattr(norm_scope, "scope_id", ""),
                )
                continue

            provenance = norm_scope.get_provenance(doc_ent) if doc_ent else None
            if is_grant:
                is_materialized = bool(
                    chunk_id and chunk_id in (getattr(norm_scope, "materialized_chunk_ids", None) or ())
                )
                meta["grant_id"] = getattr(norm_scope, "grant_id", "")
                meta["identity_scope_id"] = getattr(norm_scope, "identity_scope_id", "")
                meta["grant_target_entities"] = list(getattr(norm_scope, "target_entities", ()) or ())
                meta["grant_source_type"] = getattr(norm_scope, "source_type", "")
                meta["grant_source_ref"] = getattr(norm_scope, "source_ref", "")
                meta["grant_admitted"] = True
                meta["evidence_target_entity"] = (
                    getattr(norm_scope, "primary_root", None) if is_materialized else doc_ent
                ) or getattr(norm_scope, "primary_root", None) or ""
                meta["scope_id"] = getattr(norm_scope, "identity_scope_id", "")
                meta["scope_admitted"] = True
                meta["scope_admission_reason"] = "materialized_chunk" if is_materialized else "exploration_grant"
            else:
                meta["scope_id"] = getattr(norm_scope, "scope_id", "")
                meta["scope_root"] = getattr(norm_scope, "primary_root", None) or ""
                binding = getattr(norm_scope, "binding_strength", None)
                meta["scope_binding_strength"] = getattr(binding, "value", binding) or ""
                meta["scope_admitted"] = True
                meta["scope_admission_reason"] = (
                    "materialized_chunk"
                    if chunk_id and getattr(norm_scope, "materialized_chunk_ids", None) and chunk_id in norm_scope.materialized_chunk_ids
                    else "admissible_entity"
                )
            if provenance is not None:
                meta["provenance_source_type"] = provenance.source_type
                meta["provenance_path"] = provenance.to_dict()
            filtered.append(doc)
        return filtered

    def retrieve(
        self,
        question: str,
        kb_name: str | None = None,
        doc_category: str | None = None,
        review_status: str | None = "approved",
        method: str | None = None,
        top_k: int | None = None,
        candidate_k: int | None = None,
        scope: Any = None,
    ) -> list[Document]:
        """
        执行检索，返回 LangChain Document 列表。

        参数：
          method: 检索方式，None 则使用配置值（mmr/similarity/bm25/hybrid）
          top_k:  覆盖配置中的 retrieval_top_k（用于重排序等场景获取更多候选文档）
          candidate_k: 覆盖 hybrid 每路候选池大小
          scope: 检索范围 (EvidenceScope 或 RetrievalScope)
        """
        norm_scope = self._normalize_scope(scope)
        cat = doc_category or (norm_scope.doc_category if norm_scope else None)
        actual_method = method or self._cfg.retrieval_strategy
        retrieval_query = self._augment_structured_query(question)
        effective_top_k = self._intent_candidate_top_k(question, top_k)
        logger.info("检索策略: %s | kb=%s | scope=%s", actual_method, kb_name or "auto", getattr(norm_scope, "scope_id", "none"))

        supported = {"mmr", "similarity", "bm25", "hybrid"}
        if actual_method not in supported:
            raise ValueError(
                f"不支持的检索策略: {actual_method}，可选值: {', '.join(sorted(supported))}"
            )

        if actual_method == "bm25":
            docs = self._retrieve_bm25(
                retrieval_query, kb_name=kb_name,
                doc_category=cat, review_status=review_status,
                top_k=effective_top_k,
                scope=norm_scope,
            )
            record_request(
                channel="bm25",
                method="bm25",
                query=retrieval_query,
                requested_k=effective_top_k,
                docs=docs,
                structural_filter=scope_filter_summary(norm_scope),
            )
        elif actual_method == "hybrid":
            docs = self._retrieve_hybrid(
                question, kb_name=kb_name,
                doc_category=cat, review_status=review_status,
                top_k=effective_top_k,
                candidate_k=candidate_k,
                scope=norm_scope,
            )
        elif actual_method == "similarity":
            docs = self._retrieve_vector(
                question, kb_name=kb_name,
                doc_category=cat, review_status=review_status,
                search_type="similarity", top_k=effective_top_k,
                scope=norm_scope,
            )
            record_request(
                channel="vector",
                method="similarity",
                query=question,
                requested_k=effective_top_k,
                docs=docs,
                structural_filter=self._build_filter(kb_name, review_status, cat, scope=norm_scope),
            )
        else:
            # 默认 mmr
            docs = self._retrieve_vector(
                question, kb_name=kb_name,
                doc_category=cat, review_status=review_status,
                search_type="mmr", top_k=effective_top_k,
                scope=norm_scope,
            )
            record_request(
                channel="vector",
                method="mmr",
                query=question,
                requested_k=effective_top_k,
                docs=docs,
                structural_filter=self._build_filter(kb_name, review_status, cat, scope=norm_scope),
            )
        docs = self._filter_by_scope(docs, norm_scope)
        record_stage("scoped_recall", docs)
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
        scope: Any = None,
    ) -> list[Document]:
        norm_scope = self._normalize_scope(scope)
        cat = doc_category or (norm_scope.doc_category if norm_scope else None)
        actual_method = method or self._cfg.retrieval_strategy
        retrieval_query = self._augment_structured_query(question)
        effective_top_k = self._intent_candidate_top_k(question, top_k)
        logger.info("async 检索策略: %s | kb=%s | scope=%s", actual_method, kb_name or "auto", getattr(norm_scope, "scope_id", "none"))

        supported = {"mmr", "similarity", "bm25", "hybrid"}
        if actual_method not in supported:
            raise ValueError(
                f"不支持的检索策略: {actual_method}，可选值: {', '.join(sorted(supported))}"
            )

        if actual_method == "bm25":
            docs = await self._aretrieve_bm25(
                retrieval_query, kb_name=kb_name,
                doc_category=cat, review_status=review_status,
                top_k=effective_top_k,
                scope=norm_scope,
            )
            record_request(
                channel="bm25",
                method="bm25",
                query=retrieval_query,
                requested_k=effective_top_k,
                docs=docs,
                structural_filter=scope_filter_summary(norm_scope),
            )
        elif actual_method == "hybrid":
            docs = await self._aretrieve_hybrid(
                question, kb_name=kb_name,
                doc_category=cat, review_status=review_status,
                top_k=effective_top_k,
                candidate_k=candidate_k,
                scope=norm_scope,
            )
        elif actual_method == "similarity":
            docs = await self._aretrieve_vector(
                question, kb_name=kb_name,
                doc_category=cat, review_status=review_status,
                search_type="similarity", top_k=effective_top_k,
                scope=norm_scope,
            )
            record_request(
                channel="vector",
                method="similarity",
                query=question,
                requested_k=effective_top_k,
                docs=docs,
                structural_filter=self._build_filter(kb_name, review_status, cat, scope=norm_scope),
            )
        else:
            docs = await self._aretrieve_vector(
                question, kb_name=kb_name,
                doc_category=cat, review_status=review_status,
                search_type="mmr", top_k=effective_top_k,
                scope=norm_scope,
            )
            record_request(
                channel="vector",
                method="mmr",
                query=question,
                requested_k=effective_top_k,
                docs=docs,
                structural_filter=self._build_filter(kb_name, review_status, cat, scope=norm_scope),
            )
        docs = self._filter_by_scope(docs, norm_scope)
        record_stage("scoped_recall", docs)
        return docs

    @staticmethod
    def _augment_structured_query(query: str) -> str:
        normalized = (query or "").strip()
        if not normalized:
            return normalized
        plan = RetrievalIntentResolver.default().resolve(normalized)
        expanded = plan.expand_query(normalized)
        if not any(hint in normalized for hint in _TABLE_QUERY_HINTS):
            return expanded
        if all(term in normalized for term in _TABLE_CONTENT_HINTS):
            return expanded
        expanded_terms = set(expanded.split())
        suffix_terms = [term for term in _STRUCTURED_QUERY_SUFFIX.split() if term not in expanded_terms]
        if not suffix_terms:
            return expanded
        return f"{expanded} {' '.join(suffix_terms)}"

    @staticmethod
    def _intent_candidate_top_k(query: str, top_k: int | None) -> int | None:
        return RetrievalIntentResolver.default().resolve(query or "").effective_top_k(top_k)

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
        scope: Any = None,
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

        norm_scope = self._normalize_scope(scope)
        cat = doc_category or (norm_scope.doc_category if norm_scope else None)

        if len(queries) == 1:
            return self.retrieve(
                queries[0], kb_name=kb_name, doc_category=cat,
                review_status=review_status, method=method, top_k=top_k,
                candidate_k=candidate_k, scope=norm_scope,
            )

        actual_method = method or self._cfg.retrieval_strategy
        actual_top_k = top_k or self._cfg.retrieval_top_k
        # 每个查询拉取 candidate_k 条，给 RRF 足够的候选池
        actual_candidate_k = candidate_k or self._cfg.retrieval_candidate_k
        per_query_k = max(actual_top_k, actual_candidate_k)

        logger.info(
            "多查询检索: %d queries | method=%s | kb=%s | per_query_k=%d | final_top_k=%d | scope=%s",
            len(queries), actual_method, kb_name or "auto", per_query_k, actual_top_k, getattr(norm_scope, "scope_id", "none"),
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
                    q, kb_name=kb_name, doc_category=cat,
                    review_status=review_status, method=actual_method,
                    top_k=per_query_k,
                    candidate_k=actual_candidate_k,
                    scope=norm_scope,
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

        fused = self._rrf_fuse(
            all_ranked,
            rrf_k=self._cfg.retrieval_rrf_k,
            top_k=actual_top_k,
            weights=effective_weights,
            labels=effective_labels,
        )
        fused = self._filter_by_scope(self._maybe_prefer_sdk_manuals(queries, fused), norm_scope)
        record_stage("merged_recall", fused)
        return fused

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
        scope: Any = None,
    ) -> list[Document]:
        """异步版多查询检索。各查询并发执行。"""
        if not queries:
            return []

        norm_scope = self._normalize_scope(scope)
        cat = doc_category or (norm_scope.doc_category if norm_scope else None)

        if len(queries) == 1:
            return await self.aretrieve(
                queries[0], kb_name=kb_name, doc_category=cat,
                review_status=review_status, method=method, top_k=top_k,
                candidate_k=candidate_k, scope=norm_scope,
            )

        actual_method = method or self._cfg.retrieval_strategy
        actual_top_k = top_k or self._cfg.retrieval_top_k
        actual_candidate_k = candidate_k or self._cfg.retrieval_candidate_k
        per_query_k = max(actual_top_k, actual_candidate_k)

        logger.info(
            "async 多查询检索: %d queries | method=%s | kb=%s | per_query_k=%d | final_top_k=%d | scope=%s",
            len(queries), actual_method, kb_name or "auto", per_query_k, actual_top_k, getattr(norm_scope, "scope_id", "none"),
        )

        effective_weights: list[float] = []
        effective_labels: list[str] = []
        valid_queries: list[str] = []
        for i, q in enumerate(queries):
            q = q.strip()
            if not q:
                continue
            valid_queries.append(q)
            weight = query_weights[i] if query_weights and i < len(query_weights) else 1.0
            label = query_labels[i] if query_labels and i < len(query_labels) else f"query_{i}"
            effective_weights.append(weight)
            effective_labels.append(label)

        if not valid_queries:
            return []

        tasks = [
            self.aretrieve(
                q, kb_name=kb_name, doc_category=cat,
                review_status=review_status, method=actual_method,
                top_k=per_query_k,
                candidate_k=actual_candidate_k,
                scope=norm_scope,
            )
            for q in valid_queries
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_ranked: list[list[Document]] = []
        final_weights: list[float] = []
        final_labels: list[str] = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.warning("async 多查询检索 query[%d] 失败，跳过: %s", i, res)
                continue
            all_ranked.append(res)
            final_weights.append(effective_weights[i])
            final_labels.append(effective_labels[i])

        if not all_ranked:
            return []

        if len(all_ranked) == 1 and query_weights is None and query_labels is None:
            return all_ranked[0][:actual_top_k]

        fused = self._rrf_fuse(
            all_ranked,
            rrf_k=self._cfg.retrieval_rrf_k,
            top_k=actual_top_k,
            weights=final_weights,
            labels=final_labels,
        )
        fused = self._filter_by_scope(self._maybe_prefer_sdk_manuals(valid_queries, fused), norm_scope)
        record_stage("merged_recall", fused)
        return fused

    def _maybe_prefer_sdk_manuals(self, queries, docs: list) -> list:
        """多查询 RRF 合并后再次优选手册（单路 hybrid 内的 prefer 会被跨查询 RRF 覆盖）。"""
        from rag_knowledge.services.sdk_code_job import (
            is_sdk_style_retrieval_query,
            prefer_sdk_manual_docs,
        )

        if not queries:
            return docs
        if not any(is_sdk_style_retrieval_query(str(q)) for q in queries):
            return docs
        return prefer_sdk_manual_docs(docs)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _build_filter(
        self,
        kb_name: str | None,
        review_status: str | None,
        doc_category: str | None,
        scope: Any = None,
    ) -> dict | None:
        """构建 ChromaDB pre-TopK 结构过滤条件。"""
        conditions = []
        if kb_name:
            conditions.append({"kb_name": kb_name})
        if review_status:
            conditions.append({"review_status": review_status})
        norm_scope = self._normalize_scope(scope)
        cat = doc_category or (norm_scope.doc_category if norm_scope else None)
        if cat:
            conditions.append({"doc_category": cat})

        if norm_scope is not None and self._requires_structural_admission(norm_scope):
            scope_branches: list[dict] = []
            targets = sorted(getattr(norm_scope, "target_entities", None) or ())
            admissible = targets or sorted(getattr(norm_scope, "admissible_entities", None) or ())
            materialized = sorted(getattr(norm_scope, "materialized_chunk_ids", None) or ())
            if admissible:
                scope_branches.append({"document_entity": {"$in": admissible}})
            if materialized:
                scope_branches.append({"chunk_id": {"$in": materialized}})
            if scope_branches:
                conditions.append(
                    scope_branches[0] if len(scope_branches) == 1 else {"$or": scope_branches}
                )

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
        scope: Any = None,
    ) -> list[Document]:
        """MMR / Similarity 向量检索"""
        chroma = self._store.get_chroma()
        filt = self._build_filter(kb_name, review_status, doc_category, scope=scope)

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
        scope: Any = None,
    ) -> list[Document]:
        """BM25 关键词检索"""
        norm_scope = self._normalize_scope(scope)
        cat = doc_category or (norm_scope.doc_category if norm_scope else None)
        return self._get_bm25().search(
            question,
            kb_name=kb_name,
            review_status=review_status,
            doc_category=cat,
            top_k=top_k or self._cfg.retrieval_top_k,
            scope=norm_scope,
        )

    def _finalize_sdk_hybrid(
        self,
        question: str,
        branches: list[list[Document]],
        *,
        top_k: int,
        candidate_k: int,
        labels: list[str] | None = None,
    ) -> list[Document]:
        """RRF-fuse Hybrid branches; for SDK/style queries prefer 接口说明书 over Cookbook."""
        from rag_knowledge.services.sdk_code_job import (
            build_sdk_manual_bm25_hint,
            prefer_sdk_manual_docs,
        )

        hint = build_sdk_manual_bm25_hint(question)
        pool_k = max(top_k, candidate_k)
        fused = self._rrf_fuse(
            branches,
            rrf_k=self._cfg.retrieval_rrf_k,
            top_k=pool_k,
            labels=labels,
        )
        if hint:
            fused = prefer_sdk_manual_docs(fused)
            logger.info(
                "sdk_manual_prefer | hint=%r pool=%d returned=%d",
                hint[:80],
                pool_k,
                min(len(fused), top_k),
            )
        return fused[:top_k]

    def _retrieve_hybrid(
        self,
        question: str,
        kb_name: str | None,
        doc_category: str | None,
        review_status: str | None,
        top_k: int | None = None,
        candidate_k: int | None = None,
        scope: Any = None,
    ) -> list[Document]:
        """Similarity + BM25，并使用 RRF 融合两路排名。"""
        if self._cfg.retrieval_fusion_method != "rrf":
            raise ValueError(
                f"不支持的融合方式: {self._cfg.retrieval_fusion_method}，当前仅支持 rrf"
            )

        from rag_knowledge.services.sdk_code_job import build_sdk_manual_bm25_hint

        candidate_k = candidate_k or self._cfg.retrieval_candidate_k
        actual_top_k = top_k or self._cfg.retrieval_top_k
        augmented_query = self._augment_structured_query(question)
        scope_kw = {"scope": scope} if scope is not None else {}
        vector_docs = self._retrieve_vector(
            question, kb_name=kb_name,
            doc_category=doc_category, review_status=review_status,
            search_type="similarity", top_k=candidate_k,
            **scope_kw,
        )
        record_request(
            channel="vector",
            method="similarity",
            query=question,
            requested_k=candidate_k,
            docs=vector_docs,
            structural_filter=self._build_filter(kb_name, review_status, doc_category, scope=scope),
        )
        bm25_docs = self._retrieve_bm25(
            augmented_query, kb_name=kb_name,
            doc_category=doc_category, review_status=review_status,
            top_k=candidate_k,
            **scope_kw,
        )
        record_request(
            channel="bm25",
            method="bm25",
            query=augmented_query,
            requested_k=candidate_k,
            docs=bm25_docs,
            structural_filter=scope_filter_summary(scope),
        )
        branches = [vector_docs, bm25_docs]
        labels = ["vector", "bm25"]
        hint = build_sdk_manual_bm25_hint(question)
        if hint:
            sdk_docs = self._retrieve_bm25(
                hint,
                kb_name=kb_name,
                doc_category=doc_category,
                review_status=review_status,
                top_k=candidate_k,
                **scope_kw,
            )
            record_request(
                channel="bm25",
                method="sdk_manual",
                query=hint,
                requested_k=candidate_k,
                docs=sdk_docs,
                structural_filter=scope_filter_summary(scope),
            )
            branches.append(sdk_docs)
            labels.append("sdk_manual")
        merged = self._finalize_sdk_hybrid(
            question,
            branches,
            top_k=actual_top_k,
            candidate_k=candidate_k,
            labels=labels,
        )
        record_stage("merged_recall", merged)
        return merged

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
        scope: Any = None,
    ) -> list[Document]:
        return await self._run_blocking(
            self._retrieve_vector,
            question,
            kb_name,
            doc_category,
            review_status,
            search_type,
            top_k,
            scope,
        )

    async def _aretrieve_bm25(
        self,
        question: str,
        kb_name: str | None,
        doc_category: str | None,
        review_status: str | None,
        top_k: int | None = None,
        scope: Any = None,
    ) -> list[Document]:
        return await self._run_blocking(
            self._retrieve_bm25,
            question,
            kb_name,
            doc_category,
            review_status,
            top_k,
            scope,
        )

    async def _aretrieve_hybrid(
        self,
        question: str,
        kb_name: str | None,
        doc_category: str | None,
        review_status: str | None,
        top_k: int | None = None,
        candidate_k: int | None = None,
        scope: Any = None,
    ) -> list[Document]:
        if self._cfg.retrieval_fusion_method != "rrf":
            raise ValueError(
                f"不支持的融合方式: {self._cfg.retrieval_fusion_method}，当前仅支持 rrf"
            )

        from rag_knowledge.services.sdk_code_job import build_sdk_manual_bm25_hint

        candidate_k = candidate_k or self._cfg.retrieval_candidate_k
        actual_top_k = top_k or self._cfg.retrieval_top_k
        augmented_query = self._augment_structured_query(question)
        hint = build_sdk_manual_bm25_hint(question)
        started = time.perf_counter()
        scope_kw = {"scope": scope} if scope is not None else {}
        tasks = [
            self._aretrieve_vector(
                question, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                search_type="similarity", top_k=candidate_k,
                **scope_kw,
            ),
            self._aretrieve_bm25(
                augmented_query, kb_name=kb_name,
                doc_category=doc_category, review_status=review_status,
                top_k=candidate_k,
                scope=scope,
            ),
        ]
        labels = ["vector", "bm25"]
        if hint:
            tasks.append(
                self._aretrieve_bm25(
                    hint,
                    kb_name=kb_name,
                    doc_category=doc_category,
                    review_status=review_status,
                    top_k=candidate_k,
                    scope=scope,
                )
            )
            labels.append("sdk_manual")
        results = await asyncio.gather(*tasks)
        branch_queries = [question, augmented_query] + ([hint] if hint else [])
        branch_methods = ["similarity", "bm25"] + (["sdk_manual"] if hint else [])
        for label, method_name, branch_query, branch_docs in zip(labels, branch_methods, branch_queries, results):
            record_request(
                channel="vector" if label == "vector" else "bm25",
                method=method_name,
                query=branch_query,
                requested_k=candidate_k,
                docs=branch_docs,
                structural_filter=(
                    self._build_filter(kb_name, review_status, doc_category, scope=scope)
                    if label == "vector"
                    else scope_filter_summary(scope)
                ),
            )
        logger.debug(
            "Hybrid async recall finished | kb=%s | branches=%d | elapsed=%.3fs",
            kb_name or "auto",
            len(results),
            time.perf_counter() - started,
        )
        merged = self._finalize_sdk_hybrid(
            question,
            list(results),
            top_k=actual_top_k,
            candidate_k=candidate_k,
            labels=labels,
        )
        record_stage("merged_recall", merged)
        return merged

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
