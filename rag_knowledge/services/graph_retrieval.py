"""Deterministic entity linking and evidence-backed graph expansion for RAG."""
from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from typing import Any

from langchain_core.documents import Document

from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.repository.relational_db import RelationalDB

logger = logging.getLogger(__name__)


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
        result: list[Document] = []
        for _, entry in ranked[:top_k]:
            doc = entry["doc"]
            doc.metadata["rrf_score"] = entry["score"]
            doc.metadata["matched_query_kinds"] = entry["labels"]
            result.append(doc)
        return result


class EntityLinker:
    """Resolve approved entity names and aliases without an LLM."""

    _INTENT_TYPES = {
        "procedure": {"Tool", "Service", "Procedure"},
        "deployment": {"Service", "Product", "Tool"},
        "config": {"Service", "Tool", "ConfigItem", "DataTable", "Field"},
        "troubleshooting": {"Error", "Solution", "Service", "Tool"},
    }

    def __init__(self, db: RelationalDB | None = None, min_confidence: float = 0.75):
        self.db = db or RelationalDB()
        self.min_confidence = min_confidence

    @staticmethod
    def _question_contains_name(question: str, name: str, *, case_sensitive: bool) -> bool:
        if not question or not name:
            return False
        if case_sensitive:
            return name in question
        return name.casefold() in question.casefold()

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
        if method != "name_exact" or not original_question:
            return 2

        candidate_names = [
            entity.get("name") or "",
            entity.get("canonical_name") or "",
        ]
        if any(self._question_contains_name(original_question, name, case_sensitive=True) for name in candidate_names):
            return 4
        if any(self._question_contains_name(original_question, name, case_sensitive=False) for name in candidate_names):
            return 3
        return 2

    def link(self, question: str, intent: str) -> tuple[LinkedEntity, ...]:
        """Backward compatibility for single string question linking."""
        from rag_knowledge.services.query_contextualizer import RetrievalQuery
        return self.link_queries([RetrievalQuery(question, "original", 1.0)], intent, original_question=question)

    def link_queries(self, queries: list[Any], intent: str, original_question: str | None = None) -> tuple[LinkedEntity, ...]:
        """Resolve entities across multiple contextualized queries with overlap and tie-breaker handling."""
        all_linked: dict[str, LinkedEntity] = {}
        max_links = 3 if intent == "comparison" else 1

        for q_spec in queries:
            q_text = q_spec.text if hasattr(q_spec, "text") else str(q_spec)
            text = normalize_entity_name(q_text or "")
            if not text:
                continue

            entities = self.db.list_entities(review_status="approved")
            by_id = {item["id"]: item for item in entities}
            candidates: dict[str, tuple[float, str, int, int]] = {}

            # 1. 精确名称匹配与位置记录
            for entity in entities:
                names = [entity.get("name") or "", entity.get("canonical_name") or ""]
                for index, name in enumerate(names):
                    name = normalize_entity_name(name)
                    if name:
                        start_idx = text.casefold().find(name.casefold())
                        if start_idx != -1:
                            end_idx = start_idx + len(name)
                            score = 0.98 if index == 0 else 0.97
                            if entity["id"] in candidates:
                                if score > candidates[entity["id"]][0]:
                                    candidates[entity["id"]] = (score, "name_exact", start_idx, end_idx)
                            else:
                                candidates[entity["id"]] = (score, "name_exact", start_idx, end_idx)

            # 2. 别名精确匹配与位置记录
            for alias in self.db.list_aliases():
                if alias.get("review_status") != "approved":
                    continue
                alias_text = normalize_entity_name(alias.get("alias") or "")
                if alias_text and alias["entity_id"] in by_id:
                    start_idx = text.casefold().find(alias_text.casefold())
                    if start_idx != -1:
                        end_idx = start_idx + len(alias_text)
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
                    if i != j:
                        # 检查区间 [start, end) 是否重叠
                        overlap = max(c1[4], c2[4]) < min(c1[5], c2[5])
                        if overlap and c1[0] == c2[0] and c1[1] == c2[1]:
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

        return tuple(list(all_linked.values())[:max_links])


class GraphExpander:
    """Expand approved graph edges and return only evidence-backed chunk IDs."""

    _RELATIONS = {
        "procedure": {"has_step", "requires", "belongs_to", "defined_in"},
        "deployment": {"requires", "uses_config", "belongs_to", "defined_in"},
        "config": {"uses_config", "has_table", "has_field", "defined_in", "belongs_to"},
        "definition": {"belongs_to", "defined_in", "alias_of", "different_from"},
        "comparison": {"belongs_to", "different_from", "requires", "defined_in"},
        "troubleshooting": {"causes", "solved_by", "requires", "uses_config", "defined_in"},
    }

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

    def expand(self, linked_entities: tuple[LinkedEntity, ...], intent: str) -> GraphContext:
        if not linked_entities:
            return GraphContext(fallback_reason="no_linked_entity")

        initial_entity_ids = {linked.entity_id for linked in linked_entities}
        excluded = {item for linked in linked_entities for item in linked.excluded_entity_ids}
        ordered_entities = list(dict.fromkeys(linked.entity_id for linked in linked_entities))
        visited = set(ordered_entities)
        frontier = list(ordered_entities)
        relation_ids: list[str] = []
        allowed = self._RELATIONS.get(intent, self._RELATIONS["definition"])
        max_hops = 2 if intent in {"procedure", "deployment"} else 1

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
        for entity_id in entity_ids:
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
            for link in self.db.list_links(entity_id=entity_id):
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

    def retrieve(
        self,
        question: str,
        intent: str,
        queries: list[Any] | None = None,
        kb_name: str | None = None,
        doc_category: str | None = None,
        review_status: str | None = "approved",
    ) -> tuple[GraphContext, list[Document]]:
        try:
            if queries is None:
                from rag_knowledge.services.query_contextualizer import RetrievalQuery
                queries = [RetrievalQuery(question, "original", 1.0)]

            linked = self.linker.link_queries(queries, intent, original_question=question)
            context = self.expander.expand(linked, intent)
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
        )
