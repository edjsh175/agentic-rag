"""Agent-only candidate generation and query-scoped evidence admission.

Identity names the subject of a question.  It is intentionally not a Chroma
filter in this module: each generator contributes bounded candidates and the
admission stage decides whether a candidate can support this *specific* query.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from langchain_core.documents import Document

from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.services.relation_policy import is_candidate_expansion_relation


_OVERVIEW_TERMS = ("主要功能", "功能", "用途", "作用", "是什么", "概览", "能力")
_OVERVIEW_EVIDENCE_TERMS = ("用于", "功能", "支持", "作用", "提供", "实现", "能力")
_DEPLOYMENT_TERMS = ("部署", "安装", "上传", "目录", "路径", "配置位置")


def _norm(value: Any) -> str:
    return normalize_entity_name(str(value or "")).casefold()


def _same(left: Any, right: Any) -> bool:
    return bool(_norm(left) and _norm(left) == _norm(right))


def _chunk_key(doc: Document) -> str:
    chunk_id = str((doc.metadata or {}).get("chunk_id") or "").strip()
    if chunk_id:
        return chunk_id
    return hashlib.sha256(doc.page_content.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class CandidateProvenance:
    generator: str
    rank: int
    graph_path: tuple[str, ...] = ()
    entity_link: bool = False
    exact_lexical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "generator": self.generator,
            "rank": self.rank,
            "graph_path": list(self.graph_path),
            "entity_link": self.entity_link,
            "exact_lexical": self.exact_lexical,
        }


@dataclass
class CandidateResult:
    document: Document
    target_entity: str
    provenance: list[CandidateProvenance] = field(default_factory=list)
    fusion_score: float = 0.0
    structural_flags: list[str] = field(default_factory=list)

    @property
    def chunk_id(self) -> str:
        return _chunk_key(self.document)

    @property
    def source_generators(self) -> list[str]:
        return list(dict.fromkeys(item.generator for item in self.provenance))

    def trace(self) -> dict[str, Any]:
        meta = self.document.metadata or {}
        source_scores = {
            item.generator: round(1.0 / (60 + item.rank), 6)
            for item in self.provenance
        }
        return {
            "chunk_id": self.chunk_id,
            "candidate_sources": self.source_generators,
            "document_entity": meta.get("document_entity") or meta.get("entity_name") or "",
            "mentioned_entities": meta.get("mentioned_entities") or [],
            "graph_paths": [list(item.graph_path) for item in self.provenance if item.graph_path],
            "fusion_score": self.fusion_score,
            "rerank_score": meta.get("rerank_score"),
            "source_scores": source_scores,
            "lexical_score": source_scores.get("exact_lexical"),
            "bm25_score": source_scores.get("bm25"),
            "vector_score": source_scores.get("vector"),
            "structural_flags": list(self.structural_flags),
        }


@dataclass(frozen=True)
class AdmissionResult:
    verdict: str
    entity_relevance: str
    intent_relevance: str
    reason: str
    signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "entity_relevance": self.entity_relevance,
            "intent_relevance": self.intent_relevance,
            "reason": self.reason,
            "admission_signals": list(self.signals),
        }


@dataclass(frozen=True)
class CandidateBudgets:
    direct_entity: int = 20
    entity_chunk: int = 20
    graph_expansion: int = 30
    exact_lexical: int = 20
    bm25: int = 40
    vector: int = 40
    merged: int = 80


class AgentCandidatePipeline:
    """Bounded multi-path candidate generation for one Agent retrieve call."""

    def __init__(self, *, vector_store: Any, retrieval_strategy: Any, graph_db: Any = None):
        self._store = vector_store
        self._strategy = retrieval_strategy
        self._graph_db = graph_db

    @staticmethod
    def _hard_boundary(doc: Document, *, kb_name: str | None, review_status: str | None, doc_category: str | None) -> bool:
        meta = doc.metadata or {}
        return (
            (not kb_name or meta.get("kb_name") == kb_name)
            and (not review_status or meta.get("review_status") == review_status)
            and (not doc_category or meta.get("doc_category") == doc_category)
        )

    def _chunks(self, filters: dict[str, Any], limit: int) -> list[Document]:
        if not filters or limit <= 0:
            return []
        return self._store.get_chunks_by_metadata(filters, limit=limit)

    @staticmethod
    def _generator_filter(
        predicate: dict[str, Any], *, kb_name: str | None, review_status: str | None, doc_category: str | None,
    ) -> dict[str, Any]:
        """Apply only true data boundaries to a generator-local lookup."""
        conditions = [predicate]
        if kb_name:
            conditions.append({"kb_name": kb_name})
        if review_status:
            conditions.append({"review_status": review_status})
        if doc_category:
            conditions.append({"doc_category": doc_category})
        return conditions[0] if len(conditions) == 1 else {"$and": conditions}

    def _entity(self, name: str) -> dict[str, Any] | None:
        if self._graph_db is None:
            return None
        for item in self._graph_db.list_entities(review_status="approved"):
            if _same(item.get("name"), name) or _same(item.get("canonical_name"), name):
                return item
        return None

    def _linked_chunks(
        self, entity: dict[str, Any] | None, limit: int, *, kb_name: str | None, review_status: str | None, doc_category: str | None,
    ) -> list[Document]:
        if entity is None or self._graph_db is None:
            return []
        ids = [str(item.get("chunk_id") or "") for item in self._graph_db.list_links(entity_id=str(entity.get("id") or ""))]
        ids = [item for item in ids if item]
        return self._chunks(
            self._generator_filter(
                {"chunk_id": {"$in": ids[:limit]}},
                kb_name=kb_name, review_status=review_status, doc_category=doc_category,
            ),
            limit,
        ) if ids else []

    def _graph_neighbors(self, target: str) -> list[tuple[str, tuple[str, ...]]]:
        entity = self._entity(target)
        if entity is None or self._graph_db is None:
            return []
        entity_id = str(entity.get("id") or "")
        found: list[tuple[str, tuple[str, ...]]] = []
        for relation in self._graph_db.list_relations(entity_id=entity_id, review_status="approved"):
            relation_type = str(relation.get("relation_type") or "")
            if not is_candidate_expansion_relation(relation_type):
                continue
            source_id = str(relation.get("source_entity_id") or "")
            name_key = "target_name" if source_id == entity_id else "source_name"
            neighbor = str(relation.get(name_key) or "").strip()
            if neighbor:
                found.append((neighbor, (f"{target} --{relation_type}--> {neighbor}",)))
        return found

    def generate(
        self,
        question: str,
        *,
        target_entity: str,
        kb_name: str | None,
        review_status: str | None,
        doc_category: str | None,
        budgets: CandidateBudgets = CandidateBudgets(),
    ) -> list[CandidateResult]:
        """Generate independent candidate lists, then deduplicate and RRF-fuse."""
        target = str(target_entity or "").strip()
        if not target:
            return []
        lists: list[tuple[str, Iterable[Document], tuple[str, ...], bool, bool]] = []
        lists.append((
            "direct_document_entity",
            self._chunks(
                self._generator_filter(
                    {"document_entity": target},
                    kb_name=kb_name, review_status=review_status, doc_category=doc_category,
                ),
                budgets.direct_entity,
            ),
            (), False, False,
        ))
        target_graph_entity = self._entity(target)
        lists.append((
            "entity_chunk_link",
            self._linked_chunks(
                target_graph_entity, budgets.entity_chunk,
                kb_name=kb_name, review_status=review_status, doc_category=doc_category,
            ),
            (), True, False,
        ))
        graph_docs: list[tuple[Document, tuple[str, ...]]] = []
        for neighbor, path in self._graph_neighbors(target):
            for doc in self._chunks(
                self._generator_filter(
                    {"document_entity": neighbor},
                    kb_name=kb_name, review_status=review_status, doc_category=doc_category,
                ),
                budgets.graph_expansion,
            ):
                graph_docs.append((doc, path))
                if len(graph_docs) >= budgets.graph_expansion:
                    break
            if len(graph_docs) >= budgets.graph_expansion:
                break
        # Keep graph paths; generator inputs normally use Document lists.
        candidates: dict[str, CandidateResult] = {}

        def add(generator: str, docs: Iterable[Document], *, graph_path: tuple[str, ...] = (), entity_link: bool = False, exact: bool = False) -> None:
            for rank, doc in enumerate(docs, start=1):
                if not self._hard_boundary(doc, kb_name=kb_name, review_status=review_status, doc_category=doc_category):
                    continue
                key = _chunk_key(doc)
                result = candidates.setdefault(key, CandidateResult(document=doc, target_entity=target))
                result.provenance.append(CandidateProvenance(generator, rank, graph_path, entity_link, exact))

        for generator, docs, path, link, exact in lists:
            add(generator, docs, graph_path=path, entity_link=link, exact=exact)
        for doc, path in graph_docs:
            add("graph_expansion", [doc], graph_path=path)

        # Lexical, BM25 and vector never receive Identity as a global filter.
        bm25 = self._strategy._get_bm25()
        lexical_docs = bm25.search(target, kb_name=kb_name, review_status=review_status, doc_category=doc_category, top_k=budgets.exact_lexical)
        add("exact_lexical", (doc for doc in lexical_docs if target.casefold() in doc.page_content.casefold()), exact=True)
        bm25_docs = bm25.search(question, kb_name=kb_name, review_status=review_status, doc_category=doc_category, top_k=budgets.bm25)
        add("bm25", bm25_docs)
        vector_docs = self._strategy._retrieve_vector(
            question, kb_name, doc_category, review_status, "similarity", budgets.vector, scope=None,
        )
        add("vector", vector_docs)

        for result in candidates.values():
            result.fusion_score = sum(1.0 / (60 + item.rank) for item in result.provenance)
        return sorted(candidates.values(), key=lambda item: item.fusion_score, reverse=True)[:budgets.merged]

    def structural_guard(self, candidates: list[CandidateResult], *, target_entity: str) -> list[CandidateResult]:
        """Reject only an explicit sibling conflict with no target evidence signal."""
        target = str(target_entity or "").strip()
        target_graph = self._entity(target)
        sibling_names: set[str] = set()
        if target_graph is not None and self._graph_db is not None:
            target_id = str(target_graph.get("id") or "")
            for relation in self._graph_db.list_relations(entity_id=target_id, relation_type="different_from", review_status="approved"):
                sibling_names.add(str(relation.get("target_name") if str(relation.get("source_entity_id") or "") == target_id else relation.get("source_name") or ""))
        kept: list[CandidateResult] = []
        for candidate in candidates:
            meta = candidate.document.metadata or {}
            content = candidate.document.page_content.casefold()
            document_entity = str(meta.get("document_entity") or meta.get("entity_name") or "")
            mentioned = meta.get("mentioned_entities") or []
            has_target = target.casefold() in content or any(_same(item, target) for item in mentioned)
            has_link = any(item.entity_link for item in candidate.provenance)
            has_graph = any(item.graph_path for item in candidate.provenance)
            if any(_same(document_entity, sibling) for sibling in sibling_names) and not (has_target or has_link or has_graph):
                candidate.structural_flags.append("REJECT:explicit_sibling_without_target_signal")
                continue
            candidate.structural_flags.append("PASS")
            kept.append(candidate)
        return kept

    @staticmethod
    def admit(
        question: str,
        candidate: CandidateResult,
        *,
        target_entity: str,
        semantic_admitter: Callable[[str, CandidateResult, AdmissionResult], AdmissionResult | None] | None = None,
    ) -> AdmissionResult:
        """Fast admission with an optional semantic arbiter for ambiguous cases."""
        target = str(target_entity or "").strip()
        meta = candidate.document.metadata or {}
        content = candidate.document.page_content
        folded = content.casefold()
        doc_entity = str(meta.get("document_entity") or meta.get("entity_name") or "")
        mentioned = meta.get("mentioned_entities") or []
        signals: list[str] = []
        exact_target = target.casefold() in folded
        if exact_target:
            signals.append("exact_target_mention")
        if _same(doc_entity, target):
            signals.append("document_entity_match")
        if any(_same(item, target) for item in mentioned):
            signals.append("mentioned_entity_match")
        if any(item.entity_link for item in candidate.provenance):
            signals.append("entity_chunk_link")
        entity = "HIGH" if signals else ("MEDIUM" if any(item.graph_path for item in candidate.provenance) else "LOW")
        overview_question = any(term in question.casefold() for term in _OVERVIEW_TERMS)
        deployment_only = any(term in folded for term in _DEPLOYMENT_TERMS) and not any(term in folded for term in _OVERVIEW_EVIDENCE_TERMS)
        if overview_question and deployment_only:
            intent = "LOW"
            signals.append("overview_intent_mismatch")
        elif overview_question:
            intent = "HIGH" if any(term in folded for term in _OVERVIEW_EVIDENCE_TERMS) else "LOW"
        else:
            intent_terms = (
                "配置", "端口", "部署", "安装", "路径", "目录", "参数", "接口", "错误",
                "排查", "流程", "步骤", "依赖", "区别", "比较", "功能", "用途",
            )
            requested_terms = [term for term in intent_terms if term in question.casefold()]
            tokens = [token for token in re.findall(r"[A-Za-z0-9_]{2,}", question) if token.casefold() != target.casefold()]
            intent = "HIGH" if (
                (not requested_terms or any(term in folded for term in requested_terms))
                and (not tokens or any(token.casefold() in folded for token in tokens))
            ) else "LOW"
        if entity == "HIGH" and intent == "HIGH":
            return AdmissionResult("PASS", entity, intent, "target_and_query_intent_supported", tuple(signals))
        deterministic = AdmissionResult("REJECT", entity, intent, "insufficient_entity_or_intent_support", tuple(signals))
        # Graph provenance can make a text unit plausibly relevant without an
        # exact surface mention.  It is neither evidence nor a Python special
        # case: let the configured helper model decide this narrow ambiguity.
        if semantic_admitter is not None and entity == "MEDIUM" and intent == "HIGH":
            semantic = semantic_admitter(question, candidate, deterministic)
            if semantic is not None:
                return semantic
        return deterministic

    @staticmethod
    def admitted_documents(candidates: list[CandidateResult], admissions: dict[str, AdmissionResult]) -> list[Document]:
        docs: list[Document] = []
        for candidate in candidates:
            admission = admissions.get(candidate.chunk_id)
            if admission is None or admission.verdict != "PASS":
                continue
            meta = dict(candidate.document.metadata or {})
            meta["candidate_sources"] = candidate.source_generators
            meta["candidate_provenance"] = [item.to_dict() for item in candidate.provenance]
            meta["candidate_fusion_score"] = candidate.fusion_score
            meta["structural_guard"] = list(candidate.structural_flags)
            meta["admission"] = admission.to_dict()
            meta["admission_verdict"] = admission.verdict
            docs.append(Document(page_content=candidate.document.page_content, metadata=meta))
        return docs
