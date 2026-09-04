"""Agent-only bounded candidate generation.

Identity names the subject of a question. It is intentionally not a global
retrieval filter here: each generator contributes candidates, while evidence
admission remains a separate downstream authority.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from langchain_core.documents import Document

from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.services.relation_policy import is_candidate_expansion_relation

logger = logging.getLogger(__name__)


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
    linked_entity: str | None = None
    exact_lexical: bool = False
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "generator": self.generator,
            "rank": self.rank,
            "graph_path": list(self.graph_path),
            "entity_link": self.entity_link,
            "linked_entity": self.linked_entity,
            "exact_lexical": self.exact_lexical,
            "weight": self.weight,
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
            item.generator: round(item.weight / (60 + item.rank), 6)
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
        self.diagnostics: dict[str, dict[str, Any]] = {}

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

    def generate(
        self,
        question: str,
        *,
        target_entity: str,
        kb_name: str | None,
        review_status: str | None,
        doc_category: str | None,
        budgets: CandidateBudgets = CandidateBudgets(),
        extra_sources: dict[str, Iterable[Document]] | None = None,
        graph_working_set: Any = None,
    ) -> list[CandidateResult]:
        """Generate independent candidate lists, then deduplicate and RRF-fuse."""
        self.diagnostics = {}
        target = str(target_entity or "").strip()
        lists: list[tuple[str, Iterable[Document], tuple[str, ...], bool, str | None, bool]] = []
        graph_docs: list[tuple[Document, tuple[str, ...], float, str, bool, str | None]] = []
        neighbors: list[tuple[str, tuple[str, ...], float]] = []
        if target:
            lists.append((
                "direct_document_entity",
                self._chunks(
                    self._generator_filter(
                        {"document_entity": target},
                        kb_name=kb_name, review_status=review_status, doc_category=doc_category,
                    ),
                    budgets.direct_entity,
                ),
                (), False, target, False,
            ))
            if graph_working_set is not None and hasattr(graph_working_set, "entity_chunk_links"):
                linked_ids = graph_working_set.entity_chunk_links.get(target.casefold(), ())
                lists.append((
                    "entity_chunk_link",
                    self._chunks(
                        self._generator_filter(
                            {"chunk_id": {"$in": list(linked_ids)[:budgets.entity_chunk]}},
                            kb_name=kb_name, review_status=review_status, doc_category=doc_category,
                        ),
                        budgets.entity_chunk,
                    ),
                    (), True, target, False,
                ))
            if graph_working_set is not None and hasattr(graph_working_set, "entities"):
                for ent_state in getattr(graph_working_set, "entities", {}).values():
                    if _same(ent_state.canonical_name, target):
                        continue
                    rel_paths = []
                    strength = 2.0 if getattr(ent_state, "depth_from_root", 1) == 1 else 1.0
                    for rel in getattr(graph_working_set, "relations", {}).values():
                        s_name = getattr(rel, "source_name", "")
                        t_name = getattr(rel, "target_name", "")
                        r_type = getattr(rel, "relation_type", "")
                        if not is_candidate_expansion_relation(r_type):
                            continue
                        if (_same(s_name, target) and _same(t_name, ent_state.canonical_name)) or (
                            _same(t_name, target) and _same(s_name, ent_state.canonical_name)
                        ):
                            rel_paths.append(f"{target} --{r_type}--> {ent_state.canonical_name}")
                            from rag_knowledge.services.relation_policy import relation_rule
                            rule = relation_rule(r_type)
                            strengths = {"strong": 3.0, "medium": 2.0, "weak": 1.0}
                            strength = max(strength, strengths.get(getattr(rule, "candidate_expansion", "weak"), 1.0))
                    if not rel_paths:
                        continue
                    path = tuple(rel_paths) if rel_paths else (f"{target} -> {ent_state.canonical_name}",)
                    neighbors.append((ent_state.canonical_name, path, strength))
            strength_total = sum(item[2] for item in neighbors) or 1.0
            for neighbor, path, strength in neighbors:
                neighbor_budget = max(1, round(budgets.graph_expansion * strength / strength_total))
                linked_ids = ()
                if graph_working_set is not None and hasattr(graph_working_set, "entity_chunk_links"):
                    linked_ids = graph_working_set.entity_chunk_links.get(neighbor.casefold(), ())
                link_budget = min(len(linked_ids), max(1, neighbor_budget // 2))
                for doc in self._chunks(
                    self._generator_filter(
                        {"chunk_id": {"$in": list(linked_ids)[:link_budget]}},
                        kb_name=kb_name, review_status=review_status, doc_category=doc_category,
                    ),
                    link_budget,
                ) if linked_ids else ():
                    graph_docs.append((doc, path, strength, "graph_entity_chunk_link", True, neighbor))
                document_budget = max(1, neighbor_budget - link_budget)
                for doc in self._chunks(
                    self._generator_filter(
                        {"document_entity": neighbor},
                        kb_name=kb_name, review_status=review_status, doc_category=doc_category,
                    ),
                    document_budget,
                ):
                    graph_docs.append((doc, path, strength, "graph_expansion", False, neighbor))
                    if len(graph_docs) >= budgets.graph_expansion:
                        break
                if len(graph_docs) >= budgets.graph_expansion:
                    break
        # Keep graph paths; generator inputs normally use Document lists.
        candidates: dict[str, CandidateResult] = {}

        def add(
            generator: str,
            docs: Iterable[Document],
            *,
            graph_path: tuple[str, ...] = (),
            entity_link: bool = False,
            linked_entity: str | None = None,
            exact: bool = False,
            weight: float = 1.0,
        ) -> None:
            for rank, doc in enumerate(docs, start=1):
                if not self._hard_boundary(doc, kb_name=kb_name, review_status=review_status, doc_category=doc_category):
                    continue
                key = _chunk_key(doc)
                result = candidates.setdefault(key, CandidateResult(document=doc, target_entity=target))
                result.provenance.append(
                    CandidateProvenance(generator, rank, graph_path, entity_link, linked_entity, exact, weight)
                )

        for generator, docs, path, link, linked_ent, exact in lists:
            add(generator, docs, graph_path=path, entity_link=link, linked_entity=linked_ent, exact=exact)
        for rank, (doc, path, strength, generator, entity_link, linked_ent) in enumerate(graph_docs, start=1):
            if not self._hard_boundary(doc, kb_name=kb_name, review_status=review_status, doc_category=doc_category):
                continue
            key = _chunk_key(doc)
            result = candidates.setdefault(key, CandidateResult(document=doc, target_entity=target))
            result.provenance.append(
                CandidateProvenance(generator, rank, path, entity_link, linked_ent, False, strength)
            )

        for generator, docs in (extra_sources or {}).items():
            add(generator, docs)

        # Lexical, BM25 and vector never receive Identity as a global filter.
        bm25 = self._strategy._get_bm25()
        lexical_docs = bm25.search(target, kb_name=kb_name, review_status=review_status, doc_category=doc_category, top_k=budgets.exact_lexical) if target else []
        if target:
            add("exact_lexical", (doc for doc in lexical_docs if target.casefold() in doc.page_content.casefold()), exact=True)
        bm25_docs = bm25.search(question, kb_name=kb_name, review_status=review_status, doc_category=doc_category, top_k=budgets.bm25)
        add("bm25", bm25_docs)
        try:
            vector_docs = self._strategy._retrieve_vector(
                question, kb_name, doc_category, review_status, "similarity", budgets.vector, scope=None,
            )
            self.diagnostics["vector"] = {"status": "ok"}
        except Exception as exc:
            logger.warning(
                "Vector candidate generator degraded due to %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            self.diagnostics["vector"] = {
                "status": "degraded",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            vector_docs = []
        add("vector", vector_docs)

        for result in candidates.values():
            result.fusion_score = sum(item.weight / (60 + item.rank) for item in result.provenance)
        return sorted(candidates.values(), key=lambda item: item.fusion_score, reverse=True)[:budgets.merged]

    def generator_trace(
        self,
        candidates: Iterable[CandidateResult] | None = None,
        *,
        diagnostics: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Actual multi-path contributions for request trace, not legacy hybrid weights."""
        if isinstance(self, AgentCandidatePipeline):
            target_candidates = candidates if candidates is not None else ()
            effective_diag = dict(getattr(self, "diagnostics", {}) or {})
            if diagnostics:
                effective_diag.update(diagnostics)
        else:
            # Invoked as static method: AgentCandidatePipeline.generator_trace(candidates)
            target_candidates = self
            effective_diag = dict(diagnostics or {})

        summary: dict[str, dict[str, Any]] = {}
        seen: dict[str, set[str]] = {}
        for candidate in target_candidates:
            for provenance in candidate.provenance:
                item = summary.setdefault(provenance.generator, {
                    "candidate_count": 0,
                    "provenance_hits": 0,
                    "rrf_contribution": 0.0,
                    "status": "ok",
                })
                keys = seen.setdefault(provenance.generator, set())
                if candidate.chunk_id not in keys:
                    keys.add(candidate.chunk_id)
                    item["candidate_count"] = int(item["candidate_count"]) + 1
                item["provenance_hits"] = int(item["provenance_hits"]) + 1
                item["rrf_contribution"] = round(
                    float(item["rrf_contribution"]) + provenance.weight / (60 + provenance.rank), 6,
                )

        if effective_diag:
            for gen_name, diag in effective_diag.items():
                if gen_name not in summary:
                    summary[gen_name] = {
                        "candidate_count": 0,
                        "provenance_hits": 0,
                        "rrf_contribution": 0.0,
                        **diag,
                    }
                else:
                    summary[gen_name].update(diag)

        return summary
