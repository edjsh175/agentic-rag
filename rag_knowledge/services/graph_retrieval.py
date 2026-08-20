"""Deterministic entity linking and evidence-backed graph expansion for RAG."""
from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import re
from typing import Any

from langchain_core.documents import Document

from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.relation_policy import (
    GRAPH_RELATIONS_BY_INTENT,
    graph_relations_for_intent,
)

logger = logging.getLogger(__name__)

# Prefer concrete leaves over wide Product/Tool when truncating max_links.
_LEAF_ENTITY_TYPES = frozenset(
    {
        "Error",
        "Command",
        "Procedure",
        "ConfigItem",
        "EnvironmentComponent",
        "Field",
        "DataTable",
        "Step",
        "Solution",
    }
)
_WIDE_ENTITY_TYPES = frozenset({"Product", "Tool", "Document", "Section", "Module", "FunctionArea"})


@dataclass(frozen=True)
class LinkedEntity:
    entity_id: str
    canonical_name: str
    entity_type: str
    confidence: float
    match_method: str
    excluded_entity_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphGuardContext:
    linked_entity_ids: tuple[str, ...] = ()
    linked_names: tuple[str, ...] = ()
    linked_aliases: tuple[str, ...] = ()
    linked_doc_categories: tuple[str, ...] = ()
    excluded_entity_ids: tuple[str, ...] = ()
    excluded_names: tuple[str, ...] = ()
    excluded_aliases: tuple[str, ...] = ()
    excluded_doc_categories: tuple[str, ...] = ()
    excluded_chunk_ids: tuple[str, ...] = ()
    strict_exclusion: bool = False
    question_mentions_excluded: bool = False


@dataclass(frozen=True)
class GraphContext:
    linked_entities: tuple[LinkedEntity, ...] = ()
    expanded_entity_ids: tuple[str, ...] = ()
    relation_ids: tuple[str, ...] = ()
    chunk_ids: tuple[str, ...] = ()
    retrieval_queries: tuple[str, ...] = ()
    excluded_chunk_ids: tuple[str, ...] = ()
    fallback_reason: str | None = None
    guard: GraphGuardContext | None = None


class GraphEntityGuard:
    """Build guard context used by graph-aware fusion without mixing it into retrieval flow."""

    def __init__(self, db: RelationalDB | None = None):
        self.db = db or RelationalDB()

    def build(self, question: str, intent: str, linked: tuple[LinkedEntity, ...], context: GraphContext) -> GraphGuardContext:
        linked_entity_ids = tuple(item.entity_id for item in linked)
        linked_names: list[str] = []
        linked_aliases: list[str] = []
        linked_doc_categories: list[str] = []

        for item in linked:
            entity = self.db.get_entity(item.entity_id)
            if entity:
                linked_names.append(entity.get("canonical_name") or entity["name"])
                if entity.get("doc_category"):
                    linked_doc_categories.append(entity["doc_category"])
                aliases = [
                    alias["alias"]
                    for alias in self.db.list_aliases(item.entity_id)
                    if alias.get("review_status") == "approved"
                ]
                linked_aliases.extend(aliases)

        excluded_entity_ids = tuple(sorted({entity_id for item in linked for entity_id in item.excluded_entity_ids}))
        excluded_names: list[str] = []
        excluded_aliases: list[str] = []
        excluded_doc_categories: list[str] = []

        for entity_id in excluded_entity_ids:
            entity = self.db.get_entity(entity_id)
            if entity:
                excluded_names.append(entity.get("canonical_name") or entity["name"])
                if entity.get("doc_category"):
                    excluded_doc_categories.append(entity["doc_category"])
                aliases = [
                    alias["alias"]
                    for alias in self.db.list_aliases(entity_id)
                    if alias.get("review_status") == "approved"
                ]
                excluded_aliases.extend(aliases)

        question_cf = question.casefold()
        question_mentions_excluded = any(
            name and name.casefold() in question_cf
            for name in [*excluded_names, *excluded_aliases]
        )

        strict_exclusion = (
            len(linked) == 1
            and linked[0].confidence >= 0.9
            and intent != "comparison"
            and not question_mentions_excluded
        )

        return GraphGuardContext(
            linked_entity_ids=linked_entity_ids,
            linked_names=tuple(sorted(set(linked_names))),
            linked_aliases=tuple(sorted(set(linked_aliases))),
            linked_doc_categories=tuple(sorted(set(linked_doc_categories))),
            excluded_entity_ids=excluded_entity_ids,
            excluded_names=tuple(sorted(set(excluded_names))),
            excluded_aliases=tuple(sorted(set(excluded_aliases))),
            excluded_doc_categories=tuple(sorted(set(excluded_doc_categories))),
            excluded_chunk_ids=context.excluded_chunk_ids,
            strict_exclusion=strict_exclusion,
            question_mentions_excluded=question_mentions_excluded,
        )


class GraphFusionScorer:
    """Own graph-aware fusion scoring and exclusion heuristics."""

    @staticmethod
    def fuse(
        retrieval_docs: list[Document],
        graph_docs: list[Document],
        *,
        top_k: int,
        graph_weight: float = 1.25,
        rrf_k: int = 60,
        excluded_chunk_ids: tuple[str, ...] = (),
        exclusion_weight: float = 0.35,
        graph_guard: GraphGuardContext | dict | None = None,
        max_graph_only_slots: int = 1,
        protect_text_top1: bool = True,
    ) -> list[Document]:
        guard_linked_names = ()
        guard_linked_aliases = ()
        guard_linked_doc_categories = ()
        guard_excluded_doc_categories = ()
        guard_excluded_names = ()
        guard_excluded_chunk_ids = excluded_chunk_ids
        strict_exclusion = False
        question_mentions_excluded = False

        if graph_guard is not None:
            if isinstance(graph_guard, dict):
                guard_linked_names = graph_guard.get("linked_names", ())
                guard_linked_aliases = graph_guard.get("linked_aliases", ())
                guard_linked_doc_categories = graph_guard.get("linked_doc_categories", ())
                guard_excluded_doc_categories = graph_guard.get("excluded_doc_categories", ())
                guard_excluded_names = graph_guard.get("excluded_names", ())
                guard_excluded_chunk_ids = graph_guard.get("excluded_chunk_ids", excluded_chunk_ids)
                strict_exclusion = graph_guard.get("strict_exclusion", False)
                question_mentions_excluded = graph_guard.get("question_mentions_excluded", False)
            else:
                guard_linked_names = getattr(graph_guard, "linked_names", ())
                guard_linked_aliases = getattr(graph_guard, "linked_aliases", ())
                guard_linked_doc_categories = getattr(graph_guard, "linked_doc_categories", ())
                guard_excluded_doc_categories = getattr(graph_guard, "excluded_doc_categories", ())
                guard_excluded_names = getattr(graph_guard, "excluded_names", ())
                guard_excluded_chunk_ids = getattr(graph_guard, "excluded_chunk_ids", excluded_chunk_ids)
                strict_exclusion = getattr(graph_guard, "strict_exclusion", False)
                question_mentions_excluded = getattr(graph_guard, "question_mentions_excluded", False)

        fused: dict[str, dict] = {}
        for label, weight, docs in (
            ("retrieval", 1.0, retrieval_docs),
            ("graph", graph_weight, graph_docs),
        ):
            for rank, doc in enumerate(docs, start=1):
                chunk_id = str(doc.metadata.get("chunk_id") or "")
                if not chunk_id:
                    continue

                if strict_exclusion and chunk_id in guard_excluded_chunk_ids:
                    continue

                meta = doc.metadata or {}
                doc_cat = meta.get("doc_category") or ""
                section_path = meta.get("section_path") or ""
                content = doc.page_content or ""
                source = meta.get("source") or ""

                is_excluded_cat_and_name = False
                if doc_cat in guard_excluded_doc_categories:
                    text_to_check = f"{section_path} {content} {source}"
                    for name in guard_excluded_names:
                        if name and name.lower() in text_to_check.lower():
                            is_excluded_cat_and_name = True
                            break

                multiplier = 1.0
                if not question_mentions_excluded and chunk_id in guard_excluded_chunk_ids:
                    multiplier *= 0.15
                if not question_mentions_excluded and is_excluded_cat_and_name:
                    multiplier *= 0.2

                mentions_linked = False
                text_to_check_linked = f"{section_path} {content} {source}"
                for target in [*guard_linked_names, *guard_linked_aliases]:
                    if target and target.lower() in text_to_check_linked.lower():
                        mentions_linked = True
                        break
                if mentions_linked:
                    multiplier *= 1.5

                if label == "graph":
                    multiplier *= 1.2
                if doc_cat in guard_linked_doc_categories:
                    multiplier *= 1.1

                effective_weight = weight * multiplier
                if label == "retrieval" and chunk_id in guard_excluded_chunk_ids and not strict_exclusion and not graph_guard:
                    effective_weight *= exclusion_weight

                entry = fused.setdefault(
                    chunk_id,
                    {"doc": doc, "score": 0.0, "best_rank": rank, "labels": []},
                )
                entry["score"] += effective_weight / (rrf_k + rank)
                entry["best_rank"] = min(entry["best_rank"], rank)
                if label not in entry["labels"]:
                    entry["labels"].append(label)

        ranked = sorted(
            fused.items(),
            key=lambda item: (-item[1]["score"], item[1]["best_rank"], item[0]),
        )

        text_top1_id = ""
        if protect_text_top1:
            for doc in retrieval_docs:
                cid = str((doc.metadata or {}).get("chunk_id") or "")
                if not cid:
                    continue
                if cid in guard_excluded_chunk_ids:
                    continue
                text_top1_id = cid
                break

        def _emit(entry: dict) -> Document:
            doc = entry["doc"]
            doc.metadata["rrf_score"] = entry["score"]
            doc.metadata["matched_query_kinds"] = entry["labels"]
            return doc

        def _is_graph_only(entry: dict) -> bool:
            labels = entry.get("labels") or []
            return "graph" in labels and "retrieval" not in labels

        result: list[Document] = []
        used: set[str] = set()
        graph_only_used = 0
        max_graph = max(0, int(max_graph_only_slots))

        if text_top1_id and text_top1_id in fused:
            result.append(_emit(fused[text_top1_id]))
            used.add(text_top1_id)

        for chunk_id, entry in ranked:
            if len(result) >= top_k:
                break
            if chunk_id in used:
                continue
            if _is_graph_only(entry):
                if graph_only_used >= max_graph:
                    continue
                graph_only_used += 1
            result.append(_emit(entry))
            used.add(chunk_id)
        return result


class EntityLinker:
    """Resolve approved entity names and aliases without an LLM."""

    _INTENT_TYPES = {
        "procedure": {"Tool", "Service", "Procedure"},
        "deployment": {"Service", "Product", "Tool"},
        "config": {"Service", "Tool", "ConfigItem", "DataTable", "Field"},
        "troubleshooting": {"Error", "Solution", "Service", "Tool"},
        "dependency": {"Product", "Tool", "Service", "EnvironmentComponent", "Command"},
    }

    def __init__(self, db: RelationalDB | None = None, min_confidence: float = 0.75):
        self.db = db or RelationalDB()
        self.min_confidence = min_confidence

    @staticmethod
    def _find_flexible_span(text: str, name: str) -> tuple[int, int] | None:
        """Find name in text; allow optional whitespace between Latin and non-Latin runs."""
        if not text or not name:
            return None
        # Fast path: exact (case-insensitive) contiguous match
        start = text.casefold().find(name.casefold())
        if start != -1:
            return start, start + len(name)

        # Build pattern: optional \\s* at Latin↔non-Latin boundaries and where name had spaces
        buf: list[str] = []
        prev_kind: str | None = None
        for ch in name:
            if ch.isspace():
                buf.append(r"\s*")
                prev_kind = "space"
                continue
            kind = "latin" if ch.isascii() and ch.isalnum() else "other"
            if prev_kind in {"latin", "other"} and kind != prev_kind:
                buf.append(r"\s*")
            buf.append(re.escape(ch))
            prev_kind = kind
        pattern = "".join(buf)
        if not pattern:
            return None
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        return match.start(), match.end()

    @staticmethod
    def _command_stem_variants(name: str) -> tuple[str, ...]:
        """run_local_1.bat → (run_local_1.bat, run_local_1, run_local)."""
        raw = (name or "").strip()
        if not raw:
            return ()
        variants: list[str] = [raw]
        stem = raw.rsplit(".", 1)[0] if "." in raw else raw
        if stem and stem not in variants:
            variants.append(stem)
        # 去掉末尾 _数字（多脚本编号）
        base = re.sub(r"_\d+$", "", stem)
        if base and len(base) >= 4 and base not in variants:
            variants.append(base)
        return tuple(variants)

    @classmethod
    def _question_contains_name(cls, question: str, name: str, *, case_sensitive: bool = False) -> bool:
        if not question or not name:
            return False
        names = (name,)
        # Command 文件名：题面常写词干（run_local）而非完整 run_local_1.bat
        if "." in name or re.search(r"_\d+$", name.rsplit(".", 1)[0]):
            names = cls._command_stem_variants(name)
        for candidate in names:
            if case_sensitive:
                if candidate in question:
                    return True
                if cls._find_flexible_span(question, candidate) is not None:
                    return True
            else:
                if candidate.casefold() in question.casefold():
                    return True
                if cls._find_flexible_span(question, candidate) is not None:
                    return True
        return False

    def _explicit_tier(
        self,
        *,
        entity: dict,
        method: str,
        q_kind: str,
        original_question: str | None,
    ) -> int:
        if q_kind in ("last_user", "source_anchor"):
            return 1
        if method not in {"name_exact", "alias_exact", "command_stem"} or not original_question:
            return 2

        candidate_names = [
            entity.get("name") or "",
            entity.get("canonical_name") or "",
        ]
        if method == "command_stem":
            expanded: list[str] = []
            for name in candidate_names:
                expanded.extend(self._command_stem_variants(name))
            candidate_names = expanded
        if any(self._question_contains_name(original_question, name, case_sensitive=True) for name in candidate_names):
            return 4
        if any(self._question_contains_name(original_question, name, case_sensitive=False) for name in candidate_names):
            return 3
        return 2

    def _selection_key(self, linked: LinkedEntity, original_question: str | None) -> tuple:
        in_orig = 0
        if original_question:
            in_orig = int(
                self._question_contains_name(original_question, linked.canonical_name)
                or self._question_contains_name(original_question, linked.canonical_name, case_sensitive=True)
            )
        leaf = int(linked.entity_type in _LEAF_ENTITY_TYPES)
        wide = int(linked.entity_type in _WIDE_ENTITY_TYPES)
        return (-in_orig, -leaf, wide, -linked.confidence, linked.canonical_name)

    def link(self, question: str, intent: str) -> tuple[LinkedEntity, ...]:
        """Backward compatibility for single string question linking."""
        from rag_knowledge.services.query_contextualizer import RetrievalQuery
        return self.link_queries([RetrievalQuery(question, "original", 1.0)], intent, original_question=question)

    def link_queries(self, queries: list[Any], intent: str, original_question: str | None = None) -> tuple[LinkedEntity, ...]:
        """Resolve entities across multiple contextualized queries with overlap and tie-breaker handling."""
        from rag_knowledge.services.query_contextualizer import RetrievalQuery

        all_linked: dict[str, LinkedEntity] = {}
        # 非重叠多实体保留（原 comparison=3；其余从 1 提到 3，避免环境叶子被宽 Product 挤掉）
        max_links = 3

        # 始终先用用户原题做一轮匹配，避免 backbone/rewrite query 改写后丢掉叶子实体
        effective_queries: list[Any] = []
        orig = (original_question or "").strip()
        if orig:
            effective_queries.append(RetrievalQuery(orig, "original", 1.0))
        for q_spec in queries or []:
            effective_queries.append(q_spec)

        for q_spec in effective_queries:
            q_text = q_spec.text if hasattr(q_spec, "text") else str(q_spec)
            text = normalize_entity_name(q_text or "")
            if not text:
                continue

            entities = self.db.list_entities(review_status="approved")
            by_id = {item["id"]: item for item in entities}
            candidates: dict[str, tuple[float, str, int, int]] = {}

            # 1. 精确名称匹配与位置记录（允许 Latin↔中文边界空白）
            for entity in entities:
                names = [entity.get("name") or "", entity.get("canonical_name") or ""]
                for index, name in enumerate(names):
                    name = normalize_entity_name(name)
                    if not name:
                        continue
                    span = self._find_flexible_span(text, name)
                    if span is None:
                        continue
                    start_idx, end_idx = span
                    score = 0.98 if index == 0 else 0.97
                    if entity["id"] in candidates:
                        if score > candidates[entity["id"]][0]:
                            candidates[entity["id"]] = (score, "name_exact", start_idx, end_idx)
                    else:
                        candidates[entity["id"]] = (score, "name_exact", start_idx, end_idx)

                # Command：题面写 run_local 时可命中 run_local_1.bat
                if entity.get("entity_type") == "Command" and entity["id"] not in candidates:
                    primary = normalize_entity_name(entity.get("name") or "")
                    for variant in self._command_stem_variants(primary)[1:]:
                        span = self._find_flexible_span(text, variant)
                        if span is None:
                            continue
                        start_idx, end_idx = span
                        candidates[entity["id"]] = (0.93, "command_stem", start_idx, end_idx)
                        break

            # 2. 别名精确匹配与位置记录
            for alias in self.db.list_aliases():
                if alias.get("review_status") != "approved":
                    continue
                alias_text = normalize_entity_name(alias.get("alias") or "")
                if alias_text and alias["entity_id"] in by_id:
                    span = self._find_flexible_span(text, alias_text)
                    if span is None:
                        continue
                    start_idx, end_idx = span
                    score = 0.95
                    if alias["entity_id"] in candidates:
                        if score > candidates[alias["entity_id"]][0]:
                            candidates[alias["entity_id"]] = (score, "alias_exact", start_idx, end_idx)
                    else:
                        candidates[alias["entity_id"]] = (score, "alias_exact", start_idx, end_idx)

            # 3. 意图类型得分微调与优先级判断
            preferred = self._INTENT_TYPES.get(intent, set())
            ranked: list[tuple[int, float, str, str, int, int]] = []

            for entity_id, (score, method, start, end) in candidates.items():
                entity = by_id[entity_id]
                adjusted = score + (0.01 if entity["entity_type"] in preferred else 0.0)
                q_kind = q_spec.kind if hasattr(q_spec, "kind") else "original"
                tier = self._explicit_tier(
                    entity=entity,
                    method=method,
                    q_kind=q_kind,
                    original_question=original_question,
                )
                ranked.append((tier, adjusted, entity_id, method, start, end))

            # 4. 显式实体优先过滤 (filter_entity_candidates)
            if original_question and ranked:
                from rag_knowledge.services.query_entity_guard import filter_entity_candidates
                candidate_names = [by_id[c[2]]["name"] for c in ranked]
                filtered_names = filter_entity_candidates(original_question, candidate_names)
                filtered_names_set = set(filtered_names)
                ranked = [c for c in ranked if by_id[c[2]]["name"] in filtered_names_set]

            valid_candidates = [c for c in ranked if c[1] >= self.min_confidence]

            # 5. 冲突消歧 (Overlapping ties are ambiguous)
            ambiguous_ids = set()
            for i, c1 in enumerate(valid_candidates):
                for j, c2 in enumerate(valid_candidates):
                    if i >= j:
                        continue
                    overlap = max(c1[4], c2[4]) < min(c1[5], c2[5])
                    if not overlap or c1[0] != c2[0] or abs(c1[1] - c2[1]) > 1e-9:
                        continue
                    # Command 词干撞在同一 span（run_local_1/2.bat）：保留一个，避免双双丢弃
                    if c1[3] == "command_stem" and c2[3] == "command_stem":
                        drop = c1[2] if c1[2] > c2[2] else c2[2]
                        ambiguous_ids.add(drop)
                    else:
                        ambiguous_ids.add(c1[2])
                        ambiguous_ids.add(c2[2])

            non_ambiguous = [c for c in valid_candidates if c[2] not in ambiguous_ids]
            # 排序：优先级从高到低，分数从高到低，span长度从长到短，实体名长度从长到短
            non_ambiguous.sort(key=lambda item: (
                -item[0],
                -item[1],
                -(item[5] - item[4]),
                -len(by_id[item[2]]["name"])
            ))

            # 6. 贪婪过滤重叠匹配
            selected: list[tuple[int, float, str, str, int, int]] = []
            for c in non_ambiguous:
                overlap = False
                for s in selected:
                    if max(c[4], s[4]) < min(c[5], s[5]):
                        overlap = True
                        break
                if not overlap:
                    selected.append(c)

            # 7. 生成 LinkedEntity 并收集 excluded 关系
            for tier, adjusted, entity_id, method, start, end in selected[:max_links]:
                if entity_id not in all_linked:
                    entity = by_id[entity_id]
                    excluded: set[str] = set()
                    for relation in self.db.list_relations(entity_id=entity_id, relation_type="different_from", review_status="approved"):
                        other = relation["target_entity_id"] if relation["source_entity_id"] == entity_id else relation["source_entity_id"]
                        excluded.add(other)
                    all_linked[entity_id] = LinkedEntity(
                        entity_id=entity_id,
                        canonical_name=entity.get("canonical_name") or entity["name"],
                        entity_type=entity["entity_type"],
                        confidence=min(adjusted, 1.0),
                        match_method=method,
                        excluded_entity_ids=tuple(sorted(excluded)),
                    )

        ordered = sorted(
            all_linked.values(),
            key=lambda item: self._selection_key(item, original_question),
        )
        # 排错叶子已在原题中显式出现时，丢掉改写注入的宽 Tool/Product，避免邻域挤占 Error 证据
        if original_question:
            leaf_in_orig = [
                item
                for item in ordered
                if item.entity_type in {"Error", "Solution"}
                and self._question_contains_name(original_question, item.canonical_name)
            ]
            if leaf_in_orig:
                ordered = [
                    item
                    for item in ordered
                    if item.entity_type not in _WIDE_ENTITY_TYPES
                    or self._question_contains_name(original_question, item.canonical_name)
                ]
        return tuple(ordered[:max_links])


class GraphExpander:
    """Expand approved graph edges and return only evidence-backed chunk IDs."""

    # Compatibility alias for diagnostics/tests; source of truth lives in relation_policy.py.
    _RELATIONS = GRAPH_RELATIONS_BY_INTENT

    def __init__(
        self,
        db: RelationalDB | None = None,
        max_entities: int = 16,
        max_chunks: int = 24,
        min_entity_confidence: float = 0.7,
        min_relation_confidence: float = 0.7,
    ):
        self.db = db or RelationalDB()
        self.max_entities = max_entities
        self.max_chunks = max_chunks
        self.min_entity_confidence = min_entity_confidence
        self.min_relation_confidence = min_relation_confidence

    @staticmethod
    def _is_direction_allowed(relation_type: str, from_type: str, direction: str, intent: str) -> bool:
        """Enforce edge direction rules according to intent and node types."""
        # Bi-directional / non-directed relations
        if relation_type in {"different_from", "alias_of", "requires"}:
            return True

        if relation_type == "belongs_to":
            # Product is target (owner), Service/Tool/DataTable/etc are source (child)
            if from_type == "Product":
                return direction == "reverse"
            if from_type in {"Service", "Tool", "DataTable"}:
                return direction == "forward"
            return False

        if relation_type == "uses_config":
            # Service/Tool uses_config ConfigItem
            if from_type in {"Service", "Tool"}:
                return direction == "forward"
            if from_type == "ConfigItem":
                return direction == "reverse"
            return False

        if relation_type == "has_step":
            # Procedure has_step Step
            if from_type == "Procedure":
                return direction == "forward"
            if from_type == "Step":
                return direction == "reverse"
            return False

        if relation_type == "has_table":
            # Tool/Service has_table DataTable
            if from_type in {"Tool", "Service"}:
                return direction == "forward"
            if from_type == "DataTable":
                return direction == "reverse"
            return False

        if relation_type == "has_field":
            # DataTable has_field Field
            if from_type in {"DataTable", "Table"}:
                return direction == "forward"
            if from_type == "Field":
                return direction == "reverse"
            return False

        if relation_type == "defined_in":
            # Entity defined_in Document/Section; chunk evidence uses entity_chunk_links.
            return direction == "forward"

        # Default fallback
        return True

    def expand(
        self,
        linked_entities: tuple[LinkedEntity, ...],
        intent: str,
        question: str | None = None,
    ) -> GraphContext:
        if not linked_entities:
            return GraphContext(fallback_reason="no_linked_entity")

        initial_entity_ids = {linked.entity_id for linked in linked_entities}
        excluded = {item for linked in linked_entities for item in linked.excluded_entity_ids}
        ordered_entities = list(dict.fromkeys(linked.entity_id for linked in linked_entities))
        visited = set(ordered_entities)
        frontier = list(ordered_entities)
        relation_ids: list[str] = []
        allowed = graph_relations_for_intent(intent)
        max_hops = 2 if intent in {"procedure", "deployment"} else 1
        question_tokens = self._question_rank_tokens(question or "")

        for _ in range(max_hops):
            next_frontier: list[str] = []
            for entity_id in frontier:
                for relation in self.db.list_relations(entity_id=entity_id, review_status="approved"):
                    if relation["relation_type"] not in allowed:
                        continue
                    
                    # Confidence thresholds
                    if relation.get("confidence", 1.0) < self.min_relation_confidence:
                        continue

                    # Directional edge checks
                    direction = "forward" if relation["source_entity_id"] == entity_id else "reverse"
                    from_type = relation["source_type"] if direction == "forward" else relation["target_type"]
                    if not self._is_direction_allowed(relation["relation_type"], from_type, direction, intent):
                        continue

                    other = relation["target_entity_id"] if relation["source_entity_id"] == entity_id else relation["source_entity_id"]
                    if other in excluded:
                        continue

                    # Verify other node confidence meets threshold
                    other_node = self.db.get_entity(other)
                    if not other_node or other_node.get("confidence", 1.0) < self.min_entity_confidence:
                        continue

                    if relation["id"] not in relation_ids:
                        relation_ids.append(relation["id"])
                    if other not in visited and len(visited) < self.max_entities:
                        visited.add(other)
                        ordered_entities.append(other)
                        can_expand_next = True
                        if relation["relation_type"] == "belongs_to" and from_type != "Product":
                            can_expand_next = False
                        if can_expand_next:
                            next_frontier.append(other)
            frontier = next_frontier
            if not frontier:
                break

        entity_ids = tuple(ordered_entities)
        chunk_ids: list[str] = []
        excluded_chunk_ids: list[str] = []
        queries: list[str] = []

        # 初始叶子实体优先贡献 chunk，避免宽 Product/Tool 的大量链接把 Error/Command 挤出 max_chunks
        def _entity_chunk_priority(entity_id: str) -> tuple[int, int]:
            if entity_id in initial_entity_ids:
                ent = self.db.get_entity(entity_id) or {}
                et = ent.get("entity_type") or ""
                if et in _LEAF_ENTITY_TYPES:
                    return (0, 0)
                if et in _WIDE_ENTITY_TYPES:
                    return (2, 0)
                return (1, 0)
            return (3, 0)

        ranked_entity_ids = sorted(entity_ids, key=_entity_chunk_priority)

        for entity_id in ranked_entity_ids:
            entity = self.db.get_entity(entity_id)
            is_metadata_only_product = (
                entity
                and entity.get("entity_type") == "Product"
                and entity_id not in initial_entity_ids
            )
            if entity and not is_metadata_only_product and entity["name"] not in queries:
                queries.append(entity["name"])
            if is_metadata_only_product:
                continue
            links = list(self.db.list_links(entity_id=entity_id))
            if (
                question_tokens
                and entity
                and entity.get("entity_type") == "Product"
                and entity_id in initial_entity_ids
            ):
                links = self._rank_links_by_question(links, question_tokens)
            for link in links:
                chunk_id = link["chunk_id"]
                if chunk_id not in chunk_ids and len(chunk_ids) < self.max_chunks:
                    chunk_ids.append(chunk_id)

        for entity_id in sorted(excluded):
            for link in self.db.list_links(entity_id=entity_id):
                chunk_id = link["chunk_id"]
                if chunk_id not in excluded_chunk_ids:
                    excluded_chunk_ids.append(chunk_id)

        return GraphContext(
            linked_entities=linked_entities,
            expanded_entity_ids=entity_ids,
            relation_ids=tuple(relation_ids),
            chunk_ids=tuple(chunk_ids),
            retrieval_queries=tuple(queries),
            excluded_chunk_ids=tuple(excluded_chunk_ids),
            fallback_reason=None if chunk_ids else "no_graph_evidence",
        )

    @staticmethod
    def _question_rank_tokens(question: str) -> tuple[str, ...]:
        q = (question or "").strip()
        if not q:
            return ()
        q_cf = q.casefold()
        seeds = (
            "概述", "简介", "介绍", "能力", "部署", "运维", "安装", "配置",
            "排查", "错误", "启动", "环境", "服务", "功能说明", "代理",
        )
        found = [tok for tok in seeds if tok.casefold() in q_cf]
        # 概述/运维题：证据里常见「服务部署」「功能说明」虽未出现在题面，一并作为弱偏好
        if any(tok in found for tok in ("概述", "简介", "介绍", "能力", "部署", "运维")):
            for extra in ("服务部署", "功能说明"):
                if extra not in found:
                    found.append(extra)
        return tuple(found)

    @staticmethod
    def _rank_links_by_question(links: list[dict], tokens: tuple[str, ...]) -> list[dict]:
        def score(link: dict) -> tuple[int, int, str]:
            text = f"{link.get('evidence_text') or ''} {link.get('chunk_id') or ''}".casefold()
            hit = sum(1 for tok in tokens if tok and tok.casefold() in text)
            # Prefer service-overview evidence over deep OS-install leaves
            bonus = 0
            if "服务部署" in text:
                bonus += 3
            if "功能说明" in text:
                bonus += 2
            depth = text.count(">")
            return (-(hit + bonus), depth, str(link.get("chunk_id") or ""))

        return sorted(links, key=score)


class GraphRetriever:
    """Resolve a graph context, load its evidence chunks, filter by metadata, and fuse ranked channels."""

    def __init__(
        self,
        db: RelationalDB | None = None,
        *,
        store=None,
        min_confidence: float = 0.75,
        min_link_confidence: float = 0.75,
        min_entity_confidence: float = 0.7,
        min_relation_confidence: float = 0.7,
        max_entities: int = 16,
        max_chunks: int = 24,
        chunk_index_lookup=None,
    ):
        self.db = db or RelationalDB()
        if store is None:
            from rag_knowledge.repository.vector_store import VectorStore
            store = VectorStore()
        self.store = store
        if chunk_index_lookup is None:
            from rag_knowledge.config import Config
            from rag_knowledge.services.chunk_index_lookup import ChunkIndexLookupService
            chunk_index_lookup = ChunkIndexLookupService(Config().data_dir / "file_index.json")
        self.chunk_index_lookup = chunk_index_lookup

        # Backward compatibility fallbacks
        min_ent_conf = min_entity_confidence
        if min_link_confidence != 0.75:
            min_ent_conf = min_link_confidence
        elif min_confidence != 0.75:
            min_ent_conf = min_confidence

        self.linker = EntityLinker(self.db, min_confidence=min_ent_conf)
        self.expander = GraphExpander(
            self.db,
            max_entities=max_entities,
            max_chunks=max_chunks,
            min_entity_confidence=min_ent_conf,
            min_relation_confidence=min_relation_confidence,
        )
        self.guard_builder = GraphEntityGuard(self.db)

    def link_scope_roots(self, scope: Any) -> tuple[LinkedEntity, ...]:
        """将已锁定的 EvidenceScope root 精确映射到图谱实体，不再做 query lexical resolution。"""
        norm_scope = getattr(scope, "evidence_scope", scope) if scope is not None else None
        roots = tuple(getattr(norm_scope, "root_entities", ()) or ())
        if not norm_scope or not getattr(norm_scope, "is_identity_locked", False) or not roots:
            return ()

        def _key(value: str) -> str:
            return normalize_entity_name(value or "").casefold()

        entities = self.db.list_entities(review_status="approved")
        by_name: dict[str, dict] = {}
        for entity in entities:
            for name in (entity.get("name") or "", entity.get("canonical_name") or ""):
                key = _key(name)
                if key:
                    by_name.setdefault(key, entity)

        excluded_ids = tuple(sorted({
            by_name[_key(name)]["id"]
            for name in (getattr(norm_scope, "excluded_rebindings", None) or ())
            if _key(name) in by_name
        }))
        linked: list[LinkedEntity] = []
        seen: set[str] = set()
        for root in roots:
            entity = by_name.get(_key(root))
            if not entity or entity["id"] in seen:
                continue
            seen.add(entity["id"])
            linked.append(LinkedEntity(
                entity_id=entity["id"],
                canonical_name=entity.get("canonical_name") or entity.get("name") or root,
                entity_type=entity.get("entity_type") or "",
                confidence=1.0,
                match_method="scope_root",
                excluded_entity_ids=excluded_ids,
            ))
        return tuple(linked)

    def retrieve(
        self,
        question: str,
        intent: str,
        queries: list[Any] | None = None,
        kb_name: str | None = None,
        doc_category: str | None = None,
        review_status: str | None = "approved",
        scope: Any = None,
    ) -> tuple[GraphContext, list[Document]]:
        try:
            if queries is None:
                from rag_knowledge.services.query_contextualizer import RetrievalQuery
                queries = [RetrievalQuery(question, "original", 1.0)]

            norm_scope = getattr(scope, "evidence_scope", scope) if scope is not None else None
            if norm_scope is not None and getattr(norm_scope, "is_identity_locked", False):
                linked = self.link_scope_roots(norm_scope)
            else:
                linked = self.linker.link_queries(queries, intent, original_question=question)
            context = self.expander.expand(linked, intent, question=question)
            if norm_scope is not None and getattr(norm_scope, "grant_id", None):
                # V1.6: a tool-step Grant authorizes evidence for its target only.
                # Graph neighbors may be used by GrantResolver to authorize a later step,
                # but they must not enter this step's candidate/evidence pool implicitly.
                target_ids = tuple(dict.fromkeys(item.entity_id for item in linked))
                target_chunks: list[str] = []
                target_queries: list[str] = []
                for entity_id in target_ids:
                    entity = self.db.get_entity(entity_id) or {}
                    name = str(entity.get("name") or "").strip()
                    if name and name not in target_queries:
                        target_queries.append(name)
                    for link in self.db.list_links(entity_id=entity_id):
                        chunk_id = str(link.get("chunk_id") or "").strip()
                        if chunk_id and chunk_id not in target_chunks and len(target_chunks) < self.expander.max_chunks:
                            target_chunks.append(chunk_id)
                context = replace(
                    context,
                    expanded_entity_ids=target_ids,
                    relation_ids=(),
                    chunk_ids=tuple(target_chunks),
                    retrieval_queries=tuple(target_queries),
                    fallback_reason=None if target_chunks else "no_graph_evidence",
                )
            context = replace(
                context,
                guard=self.guard_builder.build(question, intent, linked, context),
            )

            if not context.chunk_ids:
                return context, []
            collection = self.store.get_chroma()._collection
            payload = collection.get(ids=list(context.chunk_ids), include=["documents", "metadatas"])
            loaded = {
                chunk_id: (content, metadata or {})
                for chunk_id, content, metadata in zip(
                    payload.get("ids") or [],
                    payload.get("documents") or [],
                    payload.get("metadatas") or [],
                )
            }
            docs: list[Document] = []
            expanded_ids = set(context.expanded_entity_ids)
            admissible_names = set(getattr(norm_scope, "admissible_entities", None) or ())
            for chunk_id in context.chunk_ids:
                if chunk_id not in loaded:
                    continue
                content, meta = loaded[chunk_id]
                # Metadata filtering (review_status, kb_name, doc_category)
                if review_status and meta.get("review_status", "approved") != review_status:
                    continue
                if doc_category and meta.get("doc_category") != doc_category:
                    continue
                if kb_name:
                    actual_kb_name = meta.get("kb_name")
                    if not actual_kb_name:
                        actual_kb_name = self.chunk_index_lookup.by_chunk_id(chunk_id).get("kb_name")
                    if actual_kb_name != kb_name:
                        continue
                doc_meta = dict(meta)
                doc_meta["chunk_id"] = chunk_id
                doc_meta["retrieval_channel"] = "graph"

                # entity_chunk_links 是图召回该 chunk 的正式 provenance；优先选择当前 Scope 可准入实体。
                links = [
                    link for link in self.db.list_links(chunk_id=chunk_id)
                    if link.get("entity_id") in expanded_ids
                ]
                chosen_link = None
                if admissible_names:
                    chosen_link = next(
                        (link for link in links if str(link.get("entity_name") or "") in admissible_names),
                        None,
                    )
                if chosen_link is None and links:
                    chosen_link = links[0]
                if chosen_link is not None:
                    graph_entity = str(chosen_link.get("entity_name") or "").strip()
                    doc_meta["graph_provenance_link_id"] = str(chosen_link.get("id") or "")
                    doc_meta["graph_provenance_entity"] = graph_entity
                    if graph_entity:
                        doc_meta["scope_entity"] = graph_entity

                docs.append(Document(page_content=content, metadata=doc_meta))

            if not docs:
                return replace(context, fallback_reason="graph_evidence_filtered"), []
            return context, docs
        except Exception as exc:
            logger.warning("GraphRetrieval error: %s", exc)
            return GraphContext(fallback_reason="graph_query_failed"), []

    def revision(self) -> str:
        """Return a revision hash representing the state of the graph data and retriever settings."""
        with self.db._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) entity_count, COALESCE(MAX(updated_at), '') entity_updated "
                "FROM entities WHERE review_status = 'approved'"
            ).fetchone()
            relation_row = conn.execute(
                "SELECT COUNT(*) relation_count, COALESCE(MAX(created_at), '') relation_created "
                "FROM relations WHERE review_status = 'approved'"
            ).fetchone()
            alias_row = conn.execute(
                "SELECT COUNT(*) alias_count, COALESCE(MAX(created_at), '') alias_created "
                "FROM aliases WHERE review_status = 'approved'"
            ).fetchone()
            link_row = conn.execute(
                "SELECT COUNT(*) link_count, COALESCE(MAX(created_at), '') link_created "
                "FROM entity_chunk_links"
            ).fetchone()
            latest_apply = conn.execute(
                "SELECT COALESCE(MAX(applied_at), '') FROM extraction_batches WHERE status = 'applied'"
            ).fetchone()[0]
        
        state_dict = {
            "entities": {
                "count": row["entity_count"],
                "updated_at": row["entity_updated"]
            },
            "relations": {
                "count": relation_row["relation_count"],
                "created_at": relation_row["relation_created"]
            },
            "aliases": {
                "count": alias_row["alias_count"],
                "created_at": alias_row["alias_created"]
            },
            "links": {
                "count": link_row["link_count"],
                "created_at": link_row["link_created"]
            },
            "extraction_applied_at": latest_apply,
            "config": {
                "min_entity_confidence": self.linker.min_confidence,
                "min_relation_confidence": self.expander.min_relation_confidence,
                "max_entities": self.expander.max_entities,
                "max_chunks": self.expander.max_chunks,
            }
        }
        
        import hashlib
        import json
        state_json = json.dumps(state_dict, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(state_json.encode("utf-8")).hexdigest()

    @staticmethod
    def fuse(
        retrieval_docs: list[Document],
        graph_docs: list[Document],
        *,
        top_k: int,
        graph_weight: float = 1.25,
        rrf_k: int = 60,
        excluded_chunk_ids: tuple[str, ...] = (),
        exclusion_weight: float = 0.35,
        graph_guard: GraphGuardContext | dict | None = None,
        max_graph_only_slots: int = 1,
        protect_text_top1: bool = True,
    ) -> list[Document]:
        return GraphFusionScorer.fuse(
            retrieval_docs,
            graph_docs,
            top_k=top_k,
            graph_weight=graph_weight,
            rrf_k=rrf_k,
            excluded_chunk_ids=excluded_chunk_ids,
            exclusion_weight=exclusion_weight,
            graph_guard=graph_guard,
            max_graph_only_slots=max_graph_only_slots,
            protect_text_top1=protect_text_top1,
        )
