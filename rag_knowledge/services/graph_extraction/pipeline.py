"""Batch orchestration, atomic application, and quality checks."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Callable

from rag_knowledge.models.graph_schema import make_field_entity_name, normalize_entity_name, validate_relation
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_governance import (
    ApplyAuditRecord,
    append_apply_audit,
    candidate_summary,
    graph_counts,
    utc_now,
)
from rag_knowledge.services.domain_catalog import DomainCatalogLoader
from rag_knowledge.services.knowledge_base_consistency import KnowledgeBaseConsistencyService
from rag_knowledge.services.entity_resolution import EntityResolutionService
from rag_knowledge.services.entity_identity import EntityIdentityService, normalize_identity_key
from rag_knowledge.services.relation_direction import (
    DirectionAction,
    LLMRelationDirectionArbiter,
    RelationDirectionService,
)
from rag_knowledge.services.relation_type import (
    LLMRelationTypeArbiter,
    RelationTypeService,
    TypeLabelAction,
)
from rag_knowledge.services.relation_belonging import (
    BelongingAction,
    LLMBelongingArbiter,
    RelationBelongingService,
    collect_candidate_parents,
)
from rag_knowledge.services.backbone_guard import (
    CONFLICT_REASON,
    chunk_in_backbone_neighborhood,
    describe_conflict,
    load_backbone_constraints,
    rule_result_hits_backbone,
)
from rag_knowledge.services.ollama_health import assert_ollama_reachable

from .chunk_mentions_extractor import ChunkMentionsExtractor
from .chapter_leaf_extractor import ChapterLeafExtractor
from .server_leaf_extractor import ServerLeafExtractor
from .leaf_fallback import apply_leaf_rule_fallback
from . import (
    CandidateNormalizer,
    ConfigBlockExtractor,
    DataSpecTableRelationExtractor,
    ExtractionResult,
    SectionPathExtractor,
    TableFieldExtractor,
)


@dataclass(frozen=True)
class BuildBatchResult:
    batch_id: str
    stats: dict


@dataclass
class QualityReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


class GraphBuilder:
    def __init__(self, db: RelationalDB | None = None, chunk_source: Callable[[], list[dict]] | None = None):
        self.db = db or RelationalDB()
        self._chunk_source = chunk_source or self._load_chunks

    @staticmethod
    def _load_chunks() -> list[dict]:
        from rag_knowledge.repository.vector_store import VectorStore

        KnowledgeBaseConsistencyService().assert_consistent()
        data = VectorStore().get_chunk_stats_source()
        return [
            {"chunk_id": str(chunk_id), "content": content or "", "metadata": metadata or {}}
            for chunk_id, content, metadata in zip(
                data.get("ids") or [], data.get("documents") or [], data.get("metadatas") or []
            )
        ]

    def build_full(self, force_rebuild: bool = False, limit: int | None = None,
                   doc_categories: list[str] | None = None, include_llm: bool | None = None,
                   include_entity_resolve: bool = False,
                   include_relation_direction_resolve: bool = False,
                   include_entity_type_resolve: bool = False,
                   include_relation_type_resolve: bool = False,
                   include_relation_belonging_resolve: bool = False,
                   include_leak_salvage: bool = False,
                   resume_batch_id: str | None = None) -> BuildBatchResult:
        chunks = self._chunk_source()
        if doc_categories:
            allowed = set(doc_categories)
            chunks = [item for item in chunks if str((item.get("metadata") or {}).get("doc_category") or "") in allowed]
        if limit is not None:
            chunks = chunks[:limit]
        return self._build(
            "full",
            chunks,
            {
                "limit": limit,
                "doc_categories": doc_categories or [],
                "force_rebuild": force_rebuild,
                "include_llm": include_llm,
                "include_entity_resolve": include_entity_resolve,
                "include_relation_direction_resolve": include_relation_direction_resolve,
                "include_entity_type_resolve": include_entity_type_resolve,
                "include_relation_type_resolve": include_relation_type_resolve,
                "include_relation_belonging_resolve": include_relation_belonging_resolve,
                "include_leak_salvage": include_leak_salvage,
            },
            include_llm=include_llm,
            include_entity_resolve=include_entity_resolve,
            include_relation_direction_resolve=include_relation_direction_resolve,
            include_entity_type_resolve=include_entity_type_resolve,
            include_relation_type_resolve=include_relation_type_resolve,
            include_relation_belonging_resolve=include_relation_belonging_resolve,
            include_leak_salvage=include_leak_salvage,
            resume_batch_id=resume_batch_id,
        )

    def build_incremental(self, chunk_ids: list[str], include_llm: bool | None = None,
                          include_entity_resolve: bool = False,
                          include_relation_direction_resolve: bool = False,
                          include_entity_type_resolve: bool = False,
                          include_relation_type_resolve: bool = False,
                          include_relation_belonging_resolve: bool = False,
                          include_leak_salvage: bool = False,
                          resume_batch_id: str | None = None) -> BuildBatchResult:
        wanted = set(chunk_ids)
        chunks = [item for item in self._chunk_source() if str(item.get("chunk_id") or "") in wanted]
        matched = {str(item.get("chunk_id") or "") for item in chunks}
        missing = sorted(wanted - matched)
        return self._build(
            "incremental",
            chunks,
            {
                "chunk_ids": sorted(wanted),
                "include_llm": include_llm,
                "include_entity_resolve": include_entity_resolve,
                "include_relation_direction_resolve": include_relation_direction_resolve,
                "include_entity_type_resolve": include_entity_type_resolve,
                "include_relation_type_resolve": include_relation_type_resolve,
                "include_relation_belonging_resolve": include_relation_belonging_resolve,
                "include_leak_salvage": include_leak_salvage,
            },
            extra_stats={
                "requested_chunks": len(wanted),
                "matched_chunks": len(matched),
                "missing_chunks": missing,
            },
            missing_chunk_ids=missing,
            include_llm=include_llm,
            include_entity_resolve=include_entity_resolve,
            include_relation_direction_resolve=include_relation_direction_resolve,
            include_entity_type_resolve=include_entity_type_resolve,
            include_relation_type_resolve=include_relation_type_resolve,
            include_relation_belonging_resolve=include_relation_belonging_resolve,
            include_leak_salvage=include_leak_salvage,
            resume_batch_id=resume_batch_id,
        )

    def resume_batch(
        self,
        batch_id: str,
        *,
        include_llm: bool | None = None,
        include_entity_resolve: bool | None = None,
        include_relation_direction_resolve: bool | None = None,
        include_entity_type_resolve: bool | None = None,
        include_relation_type_resolve: bool | None = None,
        include_relation_belonging_resolve: bool | None = None,
        include_leak_salvage: bool | None = None,
    ) -> BuildBatchResult:
        """Continue an interrupted extract batch; skips processed_chunk_ids."""
        batch = self.db.get_extraction_batch(batch_id)
        if not batch:
            raise KeyError(f"extraction batch not found: {batch_id}")
        status = str(batch.get("status") or "")
        if status in {"applied", "approved", "rejected", "superseded", "failed"}:
            raise ValueError(f"cannot resume batch in status={status}")

        filters = json.loads(batch.get("filters_json") or "{}")
        mode = str(batch.get("mode") or "full")
        if include_llm is None:
            if "include_llm" in filters:
                want_llm = bool(filters.get("include_llm"))
            else:
                from rag_knowledge.config import Config
                want_llm = bool(Config().graph_extraction_llm.enabled)
        else:
            want_llm = bool(include_llm)
        want_resolve = (
            bool(filters.get("include_entity_resolve"))
            if include_entity_resolve is None
            else bool(include_entity_resolve)
        )
        want_dir = (
            bool(filters.get("include_relation_direction_resolve"))
            if include_relation_direction_resolve is None
            else bool(include_relation_direction_resolve)
        )
        want_type = (
            bool(filters.get("include_entity_type_resolve"))
            if include_entity_type_resolve is None
            else bool(include_entity_type_resolve)
        )
        want_rtype = (
            bool(filters.get("include_relation_type_resolve"))
            if include_relation_type_resolve is None
            else bool(include_relation_type_resolve)
        )
        want_belong = (
            bool(filters.get("include_relation_belonging_resolve"))
            if include_relation_belonging_resolve is None
            else bool(include_relation_belonging_resolve)
        )
        want_salvage = (
            bool(filters.get("include_leak_salvage"))
            if include_leak_salvage is None
            else bool(include_leak_salvage)
        )

        if mode == "incremental":
            chunk_ids = list(filters.get("chunk_ids") or [])
            return self.build_incremental(
                chunk_ids,
                include_llm=want_llm,
                include_entity_resolve=want_resolve,
                include_relation_direction_resolve=want_dir,
                include_entity_type_resolve=want_type,
                include_relation_type_resolve=want_rtype,
                include_relation_belonging_resolve=want_belong,
                include_leak_salvage=want_salvage,
                resume_batch_id=batch_id,
            )

        limit = filters.get("limit")
        return self.build_full(
            force_rebuild=bool(filters.get("force_rebuild")),
            limit=int(limit) if limit is not None else None,
            doc_categories=list(filters.get("doc_categories") or []) or None,
            include_llm=want_llm,
            include_entity_resolve=want_resolve,
            include_relation_direction_resolve=want_dir,
            include_entity_type_resolve=want_type,
            include_relation_type_resolve=want_rtype,
            include_relation_belonging_resolve=want_belong,
            include_leak_salvage=want_salvage,
            resume_batch_id=batch_id,
        )

    def _build(self, mode: str, chunks: list[dict], filters: dict,
               extra_stats: dict | None = None,
               missing_chunk_ids: list[str] | None = None,
               include_llm: bool | None = None,
               include_entity_resolve: bool = False,
               include_relation_direction_resolve: bool = False,
               include_entity_type_resolve: bool = False,
               include_relation_type_resolve: bool = False,
               include_relation_belonging_resolve: bool = False,
               include_leak_salvage: bool = False,
               resume_batch_id: str | None = None) -> BuildBatchResult:
        snapshot = hashlib.sha256(json.dumps(chunks, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        force_rebuild = bool(filters.get("force_rebuild"))
        processed_chunk_ids: list[str] = []
        done_set: set[str] = set()
        batch_id: str | None = None
        candidate_ids: dict[str, set[str]] = {kind: set() for kind in ("entity", "relation", "field", "link", "diagnostic", "alias")}
        rule_candidate_ids: set[str] = set()
        llm_candidate_ids: set[str] = set()
        type_index: dict[str, str] = {}
        counts = {
            "chunks": len(chunks),
            "entity": 0,
            "relation": 0,
            "alias": 0,
            "field": 0,
            "link": 0,
            "diagnostic": 0,
            "llm_chunks_considered": 0,
            "llm_chunks_skipped": 0,
            "llm_chunks_command_rich": 0,
            "llm_chunks_category_scoped": 0,
            "llm_json_ok": 0,
            "llm_json_failed": 0,
            "llm_json_repaired": 0,
            "llm_usable_candidates": 0,
            "leaf_fallback_entities": 0,
            "leaf_fallback_relations": 0,
            "relation_direction_flipped": 0,
            "processed_chunk_ids": [],
            "extract_progress": "running",
        }
        counts.update(extra_stats or {})

        if resume_batch_id:
            batch = self.db.get_extraction_batch(resume_batch_id)
            if not batch:
                raise KeyError(f"extraction batch not found: {resume_batch_id}")
            status = str(batch.get("status") or "")
            if status in {"applied", "approved", "rejected", "superseded", "failed"}:
                raise ValueError(f"cannot resume batch in status={status}")
            if str(batch.get("mode") or "") != mode:
                raise ValueError(f"resume mode mismatch: batch={batch.get('mode')} requested={mode}")
            if str(batch.get("source_snapshot_hash") or "") != snapshot:
                raise ValueError(
                    f"cannot resume batch {resume_batch_id}: source snapshot changed; "
                    "re-run extract with --force-rebuild"
                )
            existing_stats = json.loads(batch.get("stats_json") or "{}")
            processed_chunk_ids = [str(x) for x in (existing_stats.get("processed_chunk_ids") or [])]
            done_set = set(processed_chunk_ids)
            batch_id = resume_batch_id
            candidate_ids, rule_candidate_ids, llm_candidate_ids = self._load_existing_candidate_sets(batch_id)
            type_index = self._load_type_index(batch_id)
            for key in (
                "llm_chunks_considered",
                "llm_chunks_skipped",
                "llm_chunks_command_rich",
                "llm_chunks_category_scoped",
                "llm_json_ok",
                "llm_json_failed",
                "llm_json_repaired",
                "llm_usable_candidates",
                "leaf_fallback_entities",
                "leaf_fallback_relations",
                "relation_direction_flipped",
                "requested_chunks",
                "matched_chunks",
                "missing_chunks",
            ):
                if key in existing_stats:
                    counts[key] = existing_stats[key]
            for kind, ids in candidate_ids.items():
                counts[kind] = len(ids)
            counts["rule_candidates"] = len(rule_candidate_ids)
            counts["llm_candidates"] = len(llm_candidate_ids)
            counts["processed_chunk_ids"] = list(processed_chunk_ids)
            counts["extract_progress"] = "running"
        elif mode == "full":
            with self.db._get_conn() as conn:
                if force_rebuild:
                    conn.execute(
                        "UPDATE extraction_batches SET status = 'superseded', "
                        "error_text = 'superseded by force rebuild' "
                        "WHERE source_snapshot_hash = ? AND mode = ? AND status NOT IN ('applied', 'superseded')",
                        (snapshot, mode),
                    )
                else:
                    filters_json = json.dumps(filters, ensure_ascii=False, sort_keys=True)
                    existing = conn.execute(
                        "SELECT id, stats_json FROM extraction_batches "
                        "WHERE source_snapshot_hash = ? AND mode = ? AND filters_json = ? AND status NOT IN ('failed', 'rejected', 'superseded') "
                        "ORDER BY created_at DESC LIMIT 1",
                        (snapshot, mode, filters_json),
                    ).fetchone()
                    if existing:
                        existing_stats = json.loads(existing["stats_json"] or "{}")
                        if existing_stats.get("extract_progress") == "running":
                            raise ValueError(
                                f"incomplete batch {existing['id']} still in progress; "
                                f"use --resume-batch {existing['id']} or --force-rebuild"
                            )
                        return BuildBatchResult(str(existing["id"]), existing_stats)

        from .llm_extractor import LLMGraphExtractor, chunk_has_command_signal
        from rag_knowledge.config import Config
        from rag_knowledge.services.entity_identity import LLMEntityTypeArbiter, LLMIdentityArbiter
        cfg = Config()
        actual_include_llm = (
            cfg.graph_extraction_llm.enabled if include_llm is None else bool(include_llm)
        )
        filters = dict(filters)
        filters["include_llm"] = actual_include_llm
        actual_include_entity_resolve = (
            include_entity_resolve or cfg.graph_extraction_llm.entity_resolve_enabled
        )
        actual_include_relation_direction = (
            include_relation_direction_resolve
            or cfg.graph_extraction_llm.relation_direction_resolve_enabled
        )
        actual_include_entity_type = (
            include_entity_type_resolve or cfg.graph_extraction_llm.entity_type_resolve_enabled
        )
        actual_include_relation_type = (
            include_relation_type_resolve or cfg.graph_extraction_llm.relation_type_resolve_enabled
        )
        actual_include_relation_belonging = (
            include_relation_belonging_resolve
            or cfg.graph_extraction_llm.relation_belonging_resolve_enabled
        )
        actual_include_leak_salvage = bool(
            actual_include_llm
            and (include_leak_salvage or cfg.graph_extraction_llm.leak_salvage_enabled)
        )
        backbone_constraints = load_backbone_constraints()
        if (
            actual_include_llm
            or actual_include_entity_resolve
            or actual_include_relation_direction
            or actual_include_entity_type
            or actual_include_relation_type
            or actual_include_relation_belonging
        ):
            provider = (cfg.graph_extraction_llm.provider or "ollama").lower()
            if provider == "ollama":
                assert_ollama_reachable(base_url=cfg.graph_llm_endpoint())
            elif provider in ("openai", "google"):
                # External APIs: require API key; do not probe Ollama /api/tags.
                if not cfg.graph_extraction_endpoint.resolved_api_key():
                    env_hint = (
                        cfg.graph_extraction_llm.api_key_env
                        or ("GOOGLE_API_KEY" if provider == "google" else "OPENAI_API_KEY")
                    )
                    raise ValueError(
                        f"graph_extraction.llm provider={provider} 需要设置环境变量 {env_hint}"
                    )
            else:
                raise ValueError(f"unsupported graph_extraction.llm provider: {provider}")
        llm_extractor = (
            LLMGraphExtractor(backbone_constraints=backbone_constraints) if actual_include_llm else None
        )
        identity_arbiter = (
            LLMIdentityArbiter(use_graph_endpoint=True) if actual_include_entity_resolve else None
        )
        type_arbiter = (
            LLMEntityTypeArbiter(use_graph_endpoint=True) if actual_include_entity_type else None
        )
        direction_arbiter = (
            LLMRelationDirectionArbiter(use_graph_endpoint=True)
            if actual_include_relation_direction
            else None
        )
        relation_type_arbiter = (
            LLMRelationTypeArbiter(use_graph_endpoint=True)
            if actual_include_relation_type
            else None
        )
        belonging_arbiter = (
            LLMBelongingArbiter(use_graph_endpoint=True)
            if actual_include_relation_belonging
            else None
        )
        direction_service = RelationDirectionService(arbiter=direction_arbiter)
        relation_type_service = RelationTypeService(arbiter=relation_type_arbiter)
        catalog = DomainCatalogLoader()
        belonging_service = (
            RelationBelongingService(
                arbiter=belonging_arbiter,
                catalog=catalog,
                backbone_constraints=backbone_constraints,
            )
            if actual_include_relation_belonging
            else None
        )
        section_extractor = SectionPathExtractor(catalog=catalog)
        candidate_normalizer = CandidateNormalizer(catalog=catalog)
        entity_resolver = EntityResolutionService(
            self.db, arbiter=identity_arbiter, type_arbiter=type_arbiter
        )
        batch_type_index: dict[str, str] = {}
        batch_entity_ids: dict[str, str] = {}
        batch_display_names: dict[str, str] = {}
        if batch_id:
            for item in self.db.list_extraction_candidates(batch_id):
                if item.get("candidate_kind") != "entity" or item.get("status") == "rejected":
                    continue
                payload = item.get("payload") or {}
                if str(payload.get("resolution_action") or "new") != "new":
                    continue
                self._remember_batch_identity(
                    batch_type_index,
                    batch_entity_ids,
                    batch_display_names,
                    payload,
                    str(item.get("id") or ""),
                )
        counts["entity_resolve_enabled"] = bool(actual_include_entity_resolve)
        counts["entity_resolve_alias_staged"] = int(counts.get("entity_resolve_alias_staged") or 0)
        counts["entity_type_resolve_enabled"] = bool(actual_include_entity_type)
        counts["entity_type_arbiter_resolved"] = int(counts.get("entity_type_arbiter_resolved") or 0)
        counts["relation_direction_resolve_enabled"] = bool(actual_include_relation_direction)
        counts["relation_direction_llm_flipped"] = int(counts.get("relation_direction_llm_flipped") or 0)
        counts["relation_direction_uncertain"] = int(counts.get("relation_direction_uncertain") or 0)
        counts["relation_type_resolve_enabled"] = bool(actual_include_relation_type)
        counts["relation_type_replaced"] = int(counts.get("relation_type_replaced") or 0)
        counts["relation_type_uncertain"] = int(counts.get("relation_type_uncertain") or 0)
        counts["relation_type_rejected"] = int(counts.get("relation_type_rejected") or 0)
        counts["relation_belonging_resolve_enabled"] = bool(actual_include_relation_belonging)
        counts["relation_belonging_replaced"] = int(counts.get("relation_belonging_replaced") or 0)
        counts["relation_belonging_uncertain"] = int(counts.get("relation_belonging_uncertain") or 0)
        counts["relation_belonging_rejected"] = int(counts.get("relation_belonging_rejected") or 0)
        counts["leak_salvage_enabled"] = bool(actual_include_leak_salvage)
        counts["leak_salvage_triggered"] = 0
        counts["leak_salvage_entities_added"] = 0
        counts["leak_salvage_relations_added"] = 0

        if batch_id is None:
            batch_id = self.db.create_extraction_batch(mode, filters, snapshot)
            self.db.update_extraction_batch_stats(batch_id, counts)

        for chunk in chunks:
            chunk_id = str(chunk.get("chunk_id") or "")
            if chunk_id in done_set:
                continue
            chunk_meta = chunk.get("metadata") or {}
            chunk_section_path = str(chunk_meta.get("section_path") or "")
            chunk_in_neighborhood = chunk_in_backbone_neighborhood(chunk, backbone_constraints)

            # 1. Structural rules (Section/Table/Config). Leaf rules run after LLM (strategy B).
            context = section_extractor.extract(chunk)
            combined = ExtractionResult()
            combined.extend(context)
            combined.extend(DataSpecTableRelationExtractor().extract(chunk, context))
            combined.extend(TableFieldExtractor().extract(chunk, context))
            combined.extend(ConfigBlockExtractor().extract(chunk, context))
            llm_result = ExtractionResult()
            # Collect FunctionAreas before LLM (from structural rules only)
            fa_list = [
                e.name for e in combined.entities if e.entity_type == "FunctionArea"
            ]

            # 2. LLM semantic extractor (primary for navigational leaves when enabled)
            # Gate: backbone neighborhood OR rule hit OR command-rich
            # OR explicit --doc-category filter (category-scoped full LLM on that slice)
            if actual_include_llm and llm_extractor:
                in_neighborhood = chunk_in_backbone_neighborhood(chunk, backbone_constraints)
                rule_hit = rule_result_hits_backbone(combined, backbone_constraints)
                command_rich = chunk_has_command_signal(str(chunk.get("content") or ""))
                category_scoped = bool(filters.get("doc_categories"))
                if not (in_neighborhood or rule_hit or command_rich or category_scoped):
                    counts["llm_chunks_skipped"] = int(counts.get("llm_chunks_skipped") or 0) + 1
                else:
                    if command_rich and not (in_neighborhood or rule_hit or category_scoped):
                        counts["llm_chunks_command_rich"] = int(counts.get("llm_chunks_command_rich") or 0) + 1
                    if category_scoped and not (in_neighborhood or rule_hit or command_rich):
                        counts["llm_chunks_category_scoped"] = int(
                            counts.get("llm_chunks_category_scoped") or 0
                        ) + 1
                    counts["llm_chunks_considered"] = int(counts.get("llm_chunks_considered") or 0) + 1
                    if fa_list:
                        llm_result = llm_extractor.extract(chunk, function_areas=fa_list)
                    else:
                        llm_result = llm_extractor.extract(chunk)
                    json_failed = any(
                        getattr(d, "code", "") == "llm_extraction_failed"
                        for d in (llm_result.diagnostics or [])
                    )
                    if json_failed:
                        counts["llm_json_failed"] = int(counts.get("llm_json_failed") or 0) + 1
                    else:
                        counts["llm_json_ok"] = int(counts.get("llm_json_ok") or 0) + 1
                        # D4 repair not implemented; counter reserved for S6 raw vs repaired split.
                        counts["llm_usable_candidates"] = int(
                            counts.get("llm_usable_candidates") or 0
                        ) + len(llm_result.entities) + len(llm_result.relations)
                    if actual_include_leak_salvage:
                        from .leak_salvage import (
                            assess_leak_risk,
                            build_salvage_note,
                            merge_salvage_result,
                        )
                        leak_reason = assess_leak_risk(
                            chunk, llm_result, rule_result=combined
                        )
                        if leak_reason:
                            counts["leak_salvage_triggered"] = (
                                int(counts.get("leak_salvage_triggered") or 0) + 1
                            )
                            note = build_salvage_note(leak_reason, chunk)
                            if fa_list:
                                salvage_result = llm_extractor.extract(
                                    chunk, function_areas=fa_list, salvage_note=note
                                )
                            else:
                                salvage_result = llm_extractor.extract(
                                    chunk, salvage_note=note
                                )
                            llm_result, e_add, r_add = merge_salvage_result(
                                llm_result, salvage_result
                            )
                            counts["leak_salvage_entities_added"] = (
                                int(counts.get("leak_salvage_entities_added") or 0) + e_add
                            )
                            counts["leak_salvage_relations_added"] = (
                                int(counts.get("leak_salvage_relations_added") or 0) + r_add
                            )
                            if cfg.graph_extraction_llm.rate_limit_delay > 0:
                                import time
                                time.sleep(cfg.graph_extraction_llm.rate_limit_delay)
                    if cfg.graph_extraction_llm.rate_limit_delay > 0:
                        import time
                        time.sleep(cfg.graph_extraction_llm.rate_limit_delay)

            # 3. Leaf rule fallback (ChapterLeaf / ServerLeaf), deduped against LLM leaves
            leaf_result = ExtractionResult()
            leaf_result.extend(ChapterLeafExtractor(catalog=catalog).extract(chunk, context))
            leaf_result.extend(ServerLeafExtractor(catalog=catalog).extract(chunk, context))
            if actual_include_llm:
                leaf_result = apply_leaf_rule_fallback(leaf_result, llm_result)
                counts["leaf_fallback_entities"] = int(counts.get("leaf_fallback_entities") or 0) + len(
                    leaf_result.entities
                )
                counts["leaf_fallback_relations"] = int(counts.get("leaf_fallback_relations") or 0) + len(
                    leaf_result.relations
                )
            combined.extend(leaf_result)

            for kind, items in (
                ("entity", combined.entities), ("relation", combined.relations),
                ("field", combined.fields), ("link", combined.links),
                ("diagnostic", combined.diagnostics),
            ):
                if kind == "entity":
                    items = candidate_normalizer.normalize_entities(items)
                for item in items:
                    payload = asdict(item)
                    if kind == "entity":
                        props = payload.get("properties") or {}
                        if isinstance(props, dict) and props.get("created_by") and not payload.get("created_by"):
                            payload["created_by"] = props["created_by"]
                        resolution = entity_resolver.resolve(
                            item,
                            batch_type_index=batch_type_index,
                            batch_entity_ids=batch_entity_ids,
                            batch_display_names=batch_display_names,
                        )
                        self._apply_identity_resolution(
                            payload, resolution, batch_type_index, counts
                        )
                        for diagnostic in resolution.diagnostics:
                            diagnostic_payload = {
                                "code": diagnostic.code,
                                "message": diagnostic.message,
                                "chunk_id": str(payload.get("source_chunk_id") or ""),
                            }
                            diagnostic_fingerprint = hashlib.sha256(
                                json.dumps(["diagnostic", diagnostic_payload], ensure_ascii=False, sort_keys=True).encode()
                            ).hexdigest()
                            diagnostic_id = self.db.add_extraction_candidate(
                                batch_id, "diagnostic", diagnostic_fingerprint, diagnostic_payload,
                                diagnostic_payload["chunk_id"], diagnostic.message,
                            )
                            self.db.review_extraction_candidates(batch_id, [diagnostic_id], "rejected", diagnostic.message)
                    source_chunk_id = str(payload.get("source_chunk_id") or payload.get("chunk_id") or "")
                    evidence = str(payload.get("evidence_text") or "")
                    if kind in {"entity", "relation", "field"}:
                        payload["evidences"] = [{"source_chunk_id": source_chunk_id, "evidence_text": evidence}]
                    if kind == "relation":
                        payload = self._prepare_relation_payload(
                            payload,
                            type_index,
                            relation_type_service,
                            direction_service,
                            counts,
                            belonging_service=belonging_service,
                            in_neighborhood=chunk_in_neighborhood,
                            section_path=chunk_section_path,
                            backbone_constraints=backbone_constraints,
                        )
                    identity_payload = self._identity_payload(kind, payload)
                    fingerprint = hashlib.sha256(
                        json.dumps([kind, identity_payload], ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest()
                    candidate_id = self.db.add_extraction_candidate(
                        batch_id, kind, fingerprint, payload, source_chunk_id, evidence
                    )
                    if kind == "diagnostic":
                        self.db.review_extraction_candidates(batch_id, [candidate_id], "rejected", payload.get("message", ""))
                    elif kind == "entity":
                        self._note_entity_type(type_index, payload)
                        self._reject_backbone_conflict(
                            batch_id, kind, candidate_id, payload, backbone_constraints, candidate_ids
                        )
                        res_act = payload.get("resolution_action")
                        if res_act in {"reuse", "alias", "diagnostic", "bind", "alias_of", "conflict"}:
                            resolved_id = payload.get("resolved_entity_id")
                            if res_act == "reuse" and resolved_id == candidate_id:
                                # This is a self-folded parent candidate in the current batch. Keep it pending.
                                pass
                            else:
                                reason = f"resolution:{res_act}"
                                if resolved_id:
                                    reason += f":{resolved_id}"
                                self.db.review_extraction_candidates(batch_id, [candidate_id], "rejected", reason)
                            if res_act == "alias" and payload.get("identity_canonical"):
                                staged = self._stage_identity_alias_candidate(
                                    batch_id,
                                    payload,
                                    backbone_constraints,
                                    candidate_ids,
                                    created_by=(
                                        "llm:entity_resolver"
                                        if actual_include_entity_resolve
                                        else "rule:identity"
                                    ),
                                )
                                if staged:
                                    rule_candidate_ids.add(staged)
                                    counts["entity_resolve_alias_staged"] = (
                                        int(counts.get("entity_resolve_alias_staged") or 0) + 1
                                    )
                        elif res_act == "new":
                            self._remember_batch_identity(
                                batch_type_index,
                                batch_entity_ids,
                                batch_display_names,
                                payload,
                                candidate_id,
                            )
                    elif kind == "relation":
                        if (payload.get("properties") or {}).get("direction_flipped"):
                            self._record_direction_flip_diagnostic(
                                batch_id, payload, candidate_ids
                            )
                        if (payload.get("properties") or {}).get("relation_type_rejected") or (
                            payload.get("properties") or {}
                        ).get("belonging_rejected"):
                            reason = str(
                                (payload.get("properties") or {}).get("relation_type_decision_reason")
                                or (payload.get("properties") or {}).get("belonging_decision_reason")
                                or "relation_semantic_rejected"
                            )
                            self.db.review_extraction_candidates(
                                batch_id, [candidate_id], "rejected", reason
                            )
                        else:
                            self._reject_backbone_conflict(
                                batch_id, kind, candidate_id, payload, backbone_constraints, candidate_ids
                            )
                            self._reject_illegal_relation(
                                batch_id, candidate_id, payload, type_index, candidate_ids
                            )
                            self._reject_redundant_has_table_relation(
                                batch_id, candidate_id, payload, type_index, candidate_ids
                            )
                    candidate_ids[kind].add(candidate_id)
                    rule_candidate_ids.add(candidate_id)

            # Stage LLM candidates (llm_result already computed before leaf fallback)
            if actual_include_llm and llm_extractor and (
                llm_result.entities or llm_result.relations or llm_result.diagnostics or llm_result.aliases
            ):
                    for kind, items in (
                        ("entity", llm_result.entities),
                        ("relation", llm_result.relations),
                        ("diagnostic", llm_result.diagnostics),
                    ):
                        if kind == "entity":
                            items = candidate_normalizer.normalize_entities(items)
                        for item in items:
                            payload = asdict(item)
                            if kind == "entity":
                                resolution = entity_resolver.resolve(
                                    item,
                                    batch_type_index=batch_type_index,
                                    batch_entity_ids=batch_entity_ids,
                                    batch_display_names=batch_display_names,
                                )
                                self._apply_identity_resolution(
                                    payload, resolution, batch_type_index, counts
                                )
                                for diagnostic in resolution.diagnostics:
                                    diagnostic_payload = {
                                        "code": diagnostic.code,
                                        "message": diagnostic.message,
                                        "chunk_id": str(payload.get("source_chunk_id") or ""),
                                    }
                                    diagnostic_fingerprint = hashlib.sha256(
                                        json.dumps(["diagnostic", diagnostic_payload], ensure_ascii=False, sort_keys=True).encode()
                                    ).hexdigest()
                                    diagnostic_id = self.db.add_extraction_candidate(
                                        batch_id, "diagnostic", diagnostic_fingerprint, diagnostic_payload,
                                        diagnostic_payload["chunk_id"], diagnostic.message,
                                    )
                                    self.db.review_extraction_candidates(batch_id, [diagnostic_id], "rejected", diagnostic.message)
                            source_chunk_id = str(payload.get("source_chunk_id") or payload.get("chunk_id") or "")
                            evidence = str(payload.get("evidence_text") or "")
                            if kind in {"entity", "relation"}:
                                payload["evidences"] = [{"source_chunk_id": source_chunk_id, "evidence_text": evidence}]

                            payload["created_by"] = "llm:schema_extractor"
                            if kind == "entity":
                                props = payload.get("properties") or {}
                                payload["created_by"] = props.get("created_by", "llm:schema_extractor")
                                payload["confidence"] = props.get("confidence", 1.0)
                                payload["prompt_version"] = props.get("prompt_version", cfg.graph_extraction_llm.prompt_version)
                                payload["extractor_version"] = props.get("extractor_version", cfg.graph_extraction_llm.extractor_version)
                                payload["properties"] = {
                                    **props,
                                    "created_by": payload["created_by"],
                                    "confidence": payload["confidence"],
                                    "prompt_version": payload["prompt_version"],
                                    "extractor_version": payload["extractor_version"],
                                }
                            elif kind == "relation":
                                meta = {}
                                if hasattr(llm_result, "relation_metadata"):
                                    key = (payload["source_name"], payload["relation_type"], payload["target_name"])
                                    meta = llm_result.relation_metadata.get(key) or {}
                                payload["confidence"] = meta.get("confidence", 1.0)
                                payload["prompt_version"] = meta.get("prompt_version", cfg.graph_extraction_llm.prompt_version)
                                payload["extractor_version"] = meta.get("extractor_version", cfg.graph_extraction_llm.extractor_version)
                                props = {
                                    "created_by": "llm:schema_extractor",
                                    "confidence": payload["confidence"],
                                    "prompt_version": payload["prompt_version"],
                                    "extractor_version": payload["extractor_version"],
                                }
                                if meta.get("direction_flipped"):
                                    props["direction_flipped"] = True
                                payload["properties"] = props
                                payload = self._prepare_relation_payload(
                                    payload,
                                    type_index,
                                    relation_type_service,
                                    direction_service,
                                    counts,
                                    belonging_service=belonging_service,
                                    in_neighborhood=chunk_in_neighborhood,
                                    section_path=chunk_section_path,
                                    backbone_constraints=backbone_constraints,
                                )

                            identity_payload = self._identity_payload(kind, payload)
                            fingerprint = hashlib.sha256(
                                json.dumps([kind, identity_payload], ensure_ascii=False, sort_keys=True).encode()
                            ).hexdigest()
                            candidate_id = self.db.add_extraction_candidate(
                                batch_id, kind, fingerprint, payload, source_chunk_id, evidence
                            )
                            if kind == "diagnostic":
                                self.db.review_extraction_candidates(batch_id, [candidate_id], "rejected", payload.get("message", ""))
                            elif kind == "entity":
                                self._note_entity_type(type_index, payload)
                                self._reject_backbone_conflict(
                                    batch_id, kind, candidate_id, payload, backbone_constraints, candidate_ids
                                )
                                res_act = payload.get("resolution_action")
                                if res_act in {"reuse", "alias", "diagnostic", "bind", "alias_of", "conflict"}:
                                    resolved_id = payload.get("resolved_entity_id")
                                    if res_act == "reuse" and resolved_id == candidate_id:
                                        # This is a self-folded parent candidate in the current batch. Keep it pending.
                                        pass
                                    else:
                                        reason = f"resolution:{res_act}"
                                        if resolved_id:
                                            reason += f":{resolved_id}"
                                        self.db.review_extraction_candidates(batch_id, [candidate_id], "rejected", reason)
                                    if res_act == "alias" and payload.get("identity_canonical"):
                                        staged = self._stage_identity_alias_candidate(
                                            batch_id,
                                            payload,
                                            backbone_constraints,
                                            candidate_ids,
                                            created_by=(
                                                "llm:entity_resolver"
                                                if actual_include_entity_resolve
                                                else "llm:schema_extractor"
                                            ),
                                        )
                                        if staged:
                                            llm_candidate_ids.add(staged)
                                            counts["entity_resolve_alias_staged"] = (
                                                int(counts.get("entity_resolve_alias_staged") or 0) + 1
                                            )
                                elif res_act == "new":
                                    self._remember_batch_identity(
                                        batch_type_index,
                                        batch_entity_ids,
                                        batch_display_names,
                                        payload,
                                        candidate_id,
                                    )
                            elif kind == "relation":
                                if (payload.get("properties") or {}).get("direction_flipped"):
                                    self._record_direction_flip_diagnostic(
                                        batch_id, payload, candidate_ids
                                    )
                                if (payload.get("properties") or {}).get("relation_type_rejected") or (
                                    payload.get("properties") or {}
                                ).get("belonging_rejected"):
                                    reason = str(
                                        (payload.get("properties") or {}).get(
                                            "relation_type_decision_reason"
                                        )
                                        or (payload.get("properties") or {}).get(
                                            "belonging_decision_reason"
                                        )
                                        or "relation_semantic_rejected"
                                    )
                                    self.db.review_extraction_candidates(
                                        batch_id, [candidate_id], "rejected", reason
                                    )
                                else:
                                    self._reject_backbone_conflict(
                                        batch_id, kind, candidate_id, payload, backbone_constraints, candidate_ids
                                    )
                                    self._reject_illegal_relation(
                                        batch_id, candidate_id, payload, type_index, candidate_ids
                                    )
                            candidate_ids[kind].add(candidate_id)
                            llm_candidate_ids.add(candidate_id)

                    # Custom handling of aliases in LLM result
                    for alias_item in getattr(llm_result, "aliases", []):
                        payload = dict(alias_item)
                        source_chunk_id = str(payload.get("source_chunk_id") or "")
                        evidence = str(payload.get("evidence_text") or "")
                        payload["created_by"] = "llm:schema_extractor"
                        payload["prompt_version"] = cfg.graph_extraction_llm.prompt_version
                        payload["extractor_version"] = cfg.graph_extraction_llm.extractor_version
                        identity_payload = self._identity_payload("alias", payload)
                        fingerprint = hashlib.sha256(
                            json.dumps(["alias", identity_payload], ensure_ascii=False, sort_keys=True).encode()
                        ).hexdigest()
                        candidate_id = self.db.add_extraction_candidate(
                            batch_id, "alias", fingerprint, payload, source_chunk_id, evidence
                        )
                        self._reject_backbone_conflict(
                            batch_id, "alias", candidate_id, payload, backbone_constraints, candidate_ids
                        )
                        candidate_ids["alias"].add(candidate_id)
                        llm_candidate_ids.add(candidate_id)

            # Generate 'mentions' links for this chunk with guardrails
            if chunk_id:
                chunk_content = str(chunk.get("content") or "")
                is_table = bool("<table>" in chunk_content.lower() or "|" in chunk_content)
                existing_entities = self.db.list_entities()
                mentions_extractor = ChunkMentionsExtractor()
                mentions = mentions_extractor.extract_mentions(chunk_id, chunk_content, existing_entities, is_table=is_table)
                for m in mentions:
                    link_payload = {
                        "entity_id": m["entity_id"],
                        "entity_name": m["entity_name"],
                        "entity_type": m["entity_type"],
                        "chunk_id": chunk_id,
                        "link_type": "mentions",
                        "evidence_text": m["evidence_text"],
                        "created_by": "rule:mentions_extractor",
                    }
                    identity_payload = self._identity_payload("link", link_payload)
                    fingerprint = hashlib.sha256(
                        json.dumps(["link", identity_payload], ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest()
                    link_cand_id = self.db.add_extraction_candidate(
                        batch_id, "link", fingerprint, link_payload, chunk_id, m["evidence_text"]
                    )
                    candidate_ids["link"].add(link_cand_id)
                    rule_candidate_ids.add(link_cand_id)

            if chunk_id:
                processed_chunk_ids.append(chunk_id)
                done_set.add(chunk_id)
            self._persist_extract_progress(
                batch_id, counts, candidate_ids, rule_candidate_ids, llm_candidate_ids, processed_chunk_ids,
                progress="running",
            )

        # Missing-chunk diagnostics only on first pass (not resume).
        if not resume_batch_id:
            for missing_chunk_id in missing_chunk_ids or []:
                payload = {"code": "missing_chunk", "message": f"chunk not found: {missing_chunk_id}", "chunk_id": missing_chunk_id}
                fingerprint = hashlib.sha256(
                    json.dumps(["diagnostic", payload], ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()
                candidate_id = self.db.add_extraction_candidate(
                    batch_id, "diagnostic", fingerprint, payload, missing_chunk_id, payload["message"]
                )
                self.db.review_extraction_candidates(batch_id, [candidate_id], "rejected", payload["message"])
                candidate_ids["diagnostic"].add(candidate_id)
                rule_candidate_ids.add(candidate_id)

        self._persist_extract_progress(
            batch_id, counts, candidate_ids, rule_candidate_ids, llm_candidate_ids, processed_chunk_ids,
            progress="completed",
        )
        return BuildBatchResult(batch_id, counts)

    def _persist_extract_progress(
        self,
        batch_id: str,
        counts: dict,
        candidate_ids: dict[str, set[str]],
        rule_candidate_ids: set[str],
        llm_candidate_ids: set[str],
        processed_chunk_ids: list[str],
        *,
        progress: str,
    ) -> None:
        for kind, ids in candidate_ids.items():
            counts[kind] = len(ids)
        counts["rule_candidates"] = len(rule_candidate_ids)
        counts["llm_candidates"] = len(llm_candidate_ids)
        counts["processed_chunk_ids"] = list(processed_chunk_ids)
        counts["extract_progress"] = progress
        self.db.update_extraction_batch_stats(batch_id, counts)

    def _load_existing_candidate_sets(
        self, batch_id: str
    ) -> tuple[dict[str, set[str]], set[str], set[str]]:
        candidate_ids: dict[str, set[str]] = {
            kind: set() for kind in ("entity", "relation", "field", "link", "diagnostic", "alias")
        }
        rule_candidate_ids: set[str] = set()
        llm_candidate_ids: set[str] = set()
        for item in self.db.list_extraction_candidates(batch_id):
            kind = str(item.get("candidate_kind") or "")
            cid = str(item.get("id") or "")
            if not cid:
                continue
            if kind in candidate_ids:
                candidate_ids[kind].add(cid)
            payload = item.get("payload") or {}
            if self._is_llm_created(payload):
                llm_candidate_ids.add(cid)
            else:
                rule_candidate_ids.add(cid)
        return candidate_ids, rule_candidate_ids, llm_candidate_ids

    @staticmethod
    def _is_llm_created(payload: dict) -> bool:
        created_by = str(payload.get("created_by") or "")
        if created_by.startswith("llm:"):
            return True
        props = payload.get("properties") or {}
        return str(props.get("created_by") or "").startswith("llm:")

    def _reject_backbone_conflict(
        self,
        batch_id: str,
        kind: str,
        candidate_id: str,
        payload: dict,
        constraints: dict,
        candidate_ids: dict[str, set[str]],
    ) -> None:
        reason = describe_conflict(kind, payload, constraints)
        if not reason:
            return
        self.db.review_extraction_candidates(batch_id, [candidate_id], "rejected", CONFLICT_REASON)
        chunk_id = str(payload.get("source_chunk_id") or payload.get("chunk_id") or "")
        diagnostic_payload = {
            "code": CONFLICT_REASON,
            "message": reason,
            "chunk_id": chunk_id,
        }
        fingerprint = hashlib.sha256(
            json.dumps(["diagnostic", diagnostic_payload], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        diagnostic_id = self.db.add_extraction_candidate(
            batch_id, "diagnostic", fingerprint, diagnostic_payload, chunk_id, reason
        )
        self.db.review_extraction_candidates(batch_id, [diagnostic_id], "rejected", reason)
        candidate_ids["diagnostic"].add(diagnostic_id)

    def _load_type_index(self, batch_id: str) -> dict[str, str]:
        type_index: dict[str, str] = {}
        for item in self.db.list_extraction_candidates(batch_id):
            if item.get("candidate_kind") != "entity":
                continue
            if item.get("status") == "rejected":
                continue
            self._note_entity_type(type_index, item.get("payload") or {})
        return type_index

    @staticmethod
    def _apply_identity_resolution(
        payload: dict,
        resolution,
        batch_type_index: dict[str, str],
        counts: dict,
    ) -> None:
        payload["resolution_action"] = resolution.action
        payload["resolved_entity_id"] = resolution.target_id
        payload["identity_outcome"] = resolution.outcome
        payload["identity_canonical"] = resolution.canonical_name
        if resolution.resolved_type:
            payload["entity_type"] = resolution.resolved_type
            key = normalize_identity_key(str(payload.get("name") or ""))
            if key and key in batch_type_index:
                batch_type_index[key] = resolution.resolved_type
        if any(
            d.code in {"type_arbiter_prefer_existing", "type_arbiter_prefer_candidate"}
            for d in resolution.diagnostics
        ):
            counts["entity_type_arbiter_resolved"] = (
                int(counts.get("entity_type_arbiter_resolved") or 0) + 1
            )

    @staticmethod
    def _note_entity_type(type_index: dict[str, str], payload: dict) -> None:
        name = normalize_entity_name(str(payload.get("name") or ""))
        entity_type = str(payload.get("entity_type") or "").strip()
        if name and entity_type:
            type_index[name] = entity_type

    def _lookup_entity_type(self, type_index: dict[str, str], name: str) -> str | None:
        key = normalize_entity_name(name)
        if not key:
            return None
        if key in type_index:
            return type_index[key]
        entity = self.db.get_entity_by_name(key)
        if entity:
            entity_type = str(entity.get("entity_type") or "")
            if entity_type:
                type_index[key] = entity_type
                return entity_type
        return None

    def _prepare_relation_payload(
        self,
        payload: dict,
        type_index: dict[str, str],
        relation_type_service: RelationTypeService | None,
        direction_service: RelationDirectionService | None,
        counts: dict,
        *,
        belonging_service: RelationBelongingService | None = None,
        in_neighborhood: bool = False,
        section_path: str = "",
        backbone_constraints: dict | None = None,
    ) -> dict:
        """Type-label → direction → belongs_to parent attachment (neighborhood)."""
        payload = self._canonicalize_relation_type(
            payload, type_index, relation_type_service, counts
        )
        payload, flipped = self._canonicalize_relation_direction(
            payload, type_index, direction_service
        )
        if flipped:
            counts["relation_direction_flipped"] = int(counts.get("relation_direction_flipped") or 0) + 1
            if (payload.get("properties") or {}).get("direction_flipped_by") == "llm":
                counts["relation_direction_llm_flipped"] = (
                    int(counts.get("relation_direction_llm_flipped") or 0) + 1
                )
        if (payload.get("properties") or {}).get("direction_uncertain"):
            counts["relation_direction_uncertain"] = (
                int(counts.get("relation_direction_uncertain") or 0) + 1
            )
        payload = self._canonicalize_relation_belonging(
            payload,
            type_index,
            belonging_service,
            counts,
            in_neighborhood=in_neighborhood,
            section_path=section_path,
            backbone_constraints=backbone_constraints or {},
        )
        return payload

    def _canonicalize_relation_belonging(
        self,
        payload: dict,
        type_index: dict[str, str],
        belonging_service: RelationBelongingService | None,
        counts: dict,
        *,
        in_neighborhood: bool,
        section_path: str,
        backbone_constraints: dict,
    ) -> dict:
        if belonging_service is None:
            return payload
        if str(payload.get("relation_type") or "") != "belongs_to":
            return payload
        source_name = str(payload.get("source_name") or "")
        target_name = str(payload.get("target_name") or "")
        source_type = self._lookup_entity_type(type_index, source_name) or ""
        target_type = self._lookup_entity_type(type_index, target_name) or ""
        parents = collect_candidate_parents(
            source_type,
            type_index=type_index,
            backbone_types=backbone_constraints.get("entity_type_by_name") or {},
            extra=[target_name],
        )
        decision = belonging_service.decide(
            source_name,
            target_name,
            child_type=source_type,
            parent_type=target_type,
            evidence_text=str(payload.get("evidence_text") or ""),
            section_path=section_path,
            in_neighborhood=in_neighborhood,
            candidate_parents=parents,
        )
        if decision.action == BelongingAction.KEEP and decision.reason in {
            "out_of_neighborhood",
            "missing_endpoint",
            "default_keep",
        }:
            return payload
        annotated = dict(payload)
        props = dict(annotated.get("properties") or {})
        props["belonging_decision_reason"] = decision.reason
        props["belonging_decision_confidence"] = decision.confidence
        if decision.candidates:
            props["belonging_candidates"] = list(decision.candidates)
        if decision.used_llm:
            props["belonging_decided_by"] = "llm"
        elif decision.action == BelongingAction.REPLACE:
            props["belonging_decided_by"] = "catalog_or_path"
        if decision.action == BelongingAction.REPLACE and decision.target_name:
            props["belonging_replaced_from"] = target_name
            annotated["target_name"] = decision.target_name
            annotated["properties"] = props
            counts["relation_belonging_replaced"] = int(counts.get("relation_belonging_replaced") or 0) + 1
            return annotated
        if decision.action == BelongingAction.REJECT:
            props["belonging_rejected"] = True
            annotated["properties"] = props
            counts["relation_belonging_rejected"] = int(counts.get("relation_belonging_rejected") or 0) + 1
            return annotated
        if decision.action == BelongingAction.UNSURE:
            props["belonging_uncertain"] = True
            annotated["properties"] = props
            counts["relation_belonging_uncertain"] = int(counts.get("relation_belonging_uncertain") or 0) + 1
            return annotated
        annotated["properties"] = props
        return annotated

    def _canonicalize_relation_type(
        self,
        payload: dict,
        type_index: dict[str, str],
        relation_type_service: RelationTypeService | None = None,
        counts: dict | None = None,
    ) -> dict:
        """Schema-filtered type label fix; optional LLM among confusable alternatives."""
        source_name = str(payload.get("source_name") or "")
        target_name = str(payload.get("target_name") or "")
        relation_type = str(payload.get("relation_type") or "")
        source_type = self._lookup_entity_type(type_index, source_name) or ""
        target_type = self._lookup_entity_type(type_index, target_name) or ""
        service = relation_type_service or RelationTypeService(arbiter=None)
        evidence = str(payload.get("evidence_text") or "")
        decision = service.decide(
            source_name,
            relation_type,
            target_name,
            source_type=source_type,
            target_type=target_type,
            evidence_text=evidence,
        )
        counts = counts if counts is not None else {}
        if decision.action == TypeLabelAction.KEEP and not (
            decision.reason.startswith("llm_") or decision.reason.startswith("schema_")
        ):
            return payload
        annotated = dict(payload)
        props = dict(annotated.get("properties") or {})
        props["relation_type_decision_reason"] = decision.reason
        props["relation_type_decision_confidence"] = decision.confidence
        if decision.alternatives:
            props["relation_type_alternatives"] = list(decision.alternatives)
        if decision.used_llm:
            props["relation_type_decided_by"] = "llm"
        elif decision.action in {TypeLabelAction.REPLACE, TypeLabelAction.KEEP}:
            props["relation_type_decided_by"] = "schema"
        if decision.action == TypeLabelAction.REPLACE and decision.relation_type:
            props["relation_type_replaced_from"] = relation_type
            annotated["relation_type"] = decision.relation_type
            annotated["properties"] = props
            counts["relation_type_replaced"] = int(counts.get("relation_type_replaced") or 0) + 1
            return annotated
        if decision.action == TypeLabelAction.REJECT:
            props["relation_type_rejected"] = True
            annotated["properties"] = props
            counts["relation_type_rejected"] = int(counts.get("relation_type_rejected") or 0) + 1
            return annotated
        if decision.action == TypeLabelAction.UNSURE:
            props["relation_type_uncertain"] = True
            annotated["properties"] = props
            counts["relation_type_uncertain"] = int(counts.get("relation_type_uncertain") or 0) + 1
            return annotated
        annotated["properties"] = props
        return annotated

    def _canonicalize_relation_direction(
        self,
        payload: dict,
        type_index: dict[str, str],
        direction_service: RelationDirectionService | None = None,
    ) -> tuple[dict, bool]:
        """Schema-first direction fix; optional LLM semantic flip via RelationDirectionService."""
        source_name = str(payload.get("source_name") or "")
        target_name = str(payload.get("target_name") or "")
        relation_type = str(payload.get("relation_type") or "")
        source_type = self._lookup_entity_type(type_index, source_name) or ""
        target_type = self._lookup_entity_type(type_index, target_name) or ""
        service = direction_service or RelationDirectionService(arbiter=None)
        evidence = str(payload.get("evidence_text") or "")
        decision = service.decide(
            source_name,
            relation_type,
            target_name,
            source_type=source_type,
            target_type=target_type,
            evidence_text=evidence,
        )
        if decision.action == DirectionAction.UNSURE:
            annotated = dict(payload)
            props = dict(annotated.get("properties") or {})
            props["direction_uncertain"] = True
            props["direction_decision_reason"] = decision.reason
            props["direction_decision_confidence"] = decision.confidence
            annotated["properties"] = props
            return annotated, False
        if decision.action != DirectionAction.FLIP:
            return payload, False
        flipped = dict(payload)
        flipped["source_name"] = decision.source_name or target_name
        flipped["target_name"] = decision.target_name or source_name
        props = dict(flipped.get("properties") or {})
        props["direction_flipped"] = True
        props["direction_flipped_from"] = f"{source_name}-[{relation_type}]->{target_name}"
        props["direction_decision_reason"] = decision.reason
        props["direction_decision_confidence"] = decision.confidence
        if decision.used_llm:
            props["direction_flipped_by"] = "llm"
        else:
            props["direction_flipped_by"] = "schema"
        flipped["properties"] = props
        return flipped, True

    def _record_direction_flip_diagnostic(
        self,
        batch_id: str,
        payload: dict,
        candidate_ids: dict[str, set[str]],
    ) -> None:
        chunk_id = str(payload.get("source_chunk_id") or payload.get("chunk_id") or "")
        message = str((payload.get("properties") or {}).get("direction_flipped_from") or "relation direction flipped")
        diagnostic_payload = {
            "code": "relation_direction_flipped",
            "message": message,
            "chunk_id": chunk_id,
            "source_name": payload.get("source_name"),
            "relation_type": payload.get("relation_type"),
            "target_name": payload.get("target_name"),
        }
        fingerprint = hashlib.sha256(
            json.dumps(["diagnostic", diagnostic_payload], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        diagnostic_id = self.db.add_extraction_candidate(
            batch_id, "diagnostic", fingerprint, diagnostic_payload, chunk_id, message
        )
        self.db.review_extraction_candidates(batch_id, [diagnostic_id], "rejected", message)
        candidate_ids["diagnostic"].add(diagnostic_id)

    def _reject_illegal_relation(
        self,
        batch_id: str,
        candidate_id: str,
        payload: dict,
        type_index: dict[str, str],
        candidate_ids: dict[str, set[str]],
    ) -> None:
        """Reject relation at staging when both endpoints' types are known and illegal."""
        source_type = self._lookup_entity_type(type_index, str(payload.get("source_name") or ""))
        target_type = self._lookup_entity_type(type_index, str(payload.get("target_name") or ""))
        relation_type = str(payload.get("relation_type") or "")
        if not source_type or not target_type or not relation_type:
            return
        ok, reason = validate_relation(source_type, relation_type, target_type)
        if ok:
            return
        reject_reason = f"illegal_relation:{reason}"
        self.db.review_extraction_candidates(batch_id, [candidate_id], "rejected", reject_reason)
        chunk_id = str(payload.get("source_chunk_id") or payload.get("chunk_id") or "")
        diagnostic_payload = {
            "code": "illegal_relation",
            "message": reason,
            "chunk_id": chunk_id,
            "source_name": payload.get("source_name"),
            "relation_type": relation_type,
            "target_name": payload.get("target_name"),
            "source_type": source_type,
            "target_type": target_type,
        }
        fingerprint = hashlib.sha256(
            json.dumps(["diagnostic", diagnostic_payload], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        diagnostic_id = self.db.add_extraction_candidate(
            batch_id, "diagnostic", fingerprint, diagnostic_payload, chunk_id, reason
        )
        self.db.review_extraction_candidates(batch_id, [diagnostic_id], "rejected", reason)
        candidate_ids["diagnostic"].add(diagnostic_id)

    def _reject_redundant_has_table_relation(
        self,
        batch_id: str,
        candidate_id: str,
        payload: dict,
        type_index: dict[str, str],
        candidate_ids: dict[str, set[str]],
    ) -> None:
        """Reject has_table relation at staging when the DataTable already belongs to a FunctionArea."""
        relation_type = str(payload.get("relation_type") or "")
        if relation_type != "has_table":
            return
        source_name = str(payload.get("source_name") or "")
        target_name = str(payload.get("target_name") or "")
        source_type = self._lookup_entity_type(type_index, source_name) or ""
        target_type = self._lookup_entity_type(type_index, target_name) or ""
        if source_type not in {"Tool", "Service", "Product"} or target_type != "DataTable":
            return

        has_func_area_belonging = False
        import sqlite3
        with self.db._get_conn() as conn:
            # 1. Check if the current batch contains a belongs_to candidate for this DataTable mapping to a FunctionArea
            candidates = conn.execute(
                "SELECT payload_json FROM extraction_candidates WHERE batch_id = ? AND candidate_kind = 'relation' AND status != 'rejected'",
                (batch_id,)
            ).fetchall()
            for cand in candidates:
                try:
                    p = json.loads(cand["payload_json"])
                    if (str(p.get("relation_type") or "") == "belongs_to"
                        and str(p.get("source_name") or "") == target_name):
                        t_parent = str(p.get("target_name") or "")
                        t_parent_type = self._lookup_entity_type(type_index, t_parent) or ""
                        if t_parent_type == "FunctionArea":
                            has_func_area_belonging = True
                            break
                except Exception:
                    continue

            # 2. Check if the main graph relations table already has this belongs_to mapping
            if not has_func_area_belonging:
                row_rel = conn.execute(
                    "SELECT e2.entity_type FROM relations r "
                    "JOIN entities e1 ON r.source_entity_id = e1.id "
                    "JOIN entities e2 ON r.target_entity_id = e2.id "
                    "WHERE e1.name = ? AND r.relation_type = 'belongs_to' AND e2.entity_type = 'FunctionArea'",
                    (target_name,)
                ).fetchone()
                if row_rel:
                    has_func_area_belonging = True

        if has_func_area_belonging:
            self.db.review_extraction_candidates(
                batch_id, [candidate_id], "rejected", "redundant_has_table_relationship"
            )

    @staticmethod
    def _remember_batch_identity(
        batch_type_index: dict[str, str],
        batch_entity_ids: dict[str, str],
        batch_display_names: dict[str, str],
        payload: dict,
        candidate_id: str,
    ) -> None:
        name = str(payload.get("name") or "")
        entity_type = str(payload.get("entity_type") or "")
        key = normalize_identity_key(name)
        if not key or not entity_type:
            return
        batch_type_index[key] = entity_type
        batch_entity_ids[key] = candidate_id
        batch_display_names[key] = name

    def _stage_identity_alias_candidate(
        self,
        batch_id: str,
        entity_payload: dict,
        backbone_constraints: dict,
        candidate_ids: dict[str, set[str]],
        *,
        created_by: str,
    ) -> str:
        """When identity folds a surface name to canonical, stage an alias candidate."""
        canonical = str(entity_payload.get("identity_canonical") or "").strip()
        alias = str(entity_payload.get("name") or "").strip()
        if not canonical or not alias:
            return ""
        if normalize_identity_key(canonical) == normalize_identity_key(alias):
            return ""
        source_chunk_id = str(entity_payload.get("source_chunk_id") or entity_payload.get("chunk_id") or "")
        evidence = str(entity_payload.get("evidence_text") or "")
        alias_payload = {
            "entity_name": canonical,
            "alias": alias,
            "confidence": float(entity_payload.get("confidence") or 1.0),
            "evidence_text": evidence,
            "source_chunk_id": source_chunk_id,
            "created_by": created_by,
            "from_identity_fold": True,
        }
        identity_payload = self._identity_payload("alias", alias_payload)
        fingerprint = hashlib.sha256(
            json.dumps(["alias", identity_payload], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        alias_id = self.db.add_extraction_candidate(
            batch_id, "alias", fingerprint, alias_payload, source_chunk_id, evidence
        )
        self._reject_backbone_conflict(
            batch_id, "alias", alias_id, alias_payload, backbone_constraints, candidate_ids
        )
        candidate_ids["alias"].add(alias_id)
        return alias_id

    @staticmethod
    def _identity_payload(kind: str, payload: dict) -> dict:
        keys_by_kind = {
            "entity": ("name", "entity_type"),
            "alias": ("entity_name", "alias"),
            "relation": ("source_name", "relation_type", "target_name"),
            "field": ("table_name", "field_name"),
            "link": ("entity_name", "chunk_id", "link_type"),
            "diagnostic": ("code", "chunk_id", "message"),
        }
        keys = keys_by_kind.get(kind)
        return {key: payload.get(key) for key in keys} if keys else payload


class GraphCandidateApplier:
    ORDER = {"entity": 0, "alias": 1, "relation": 2, "field": 3, "link": 4}

    def __init__(self, db: RelationalDB | None = None):
        self.db = db or RelationalDB()

    def apply(self, batch_id: str, *, operator: str = "system", backup_path: str = "") -> dict:
        batch = self.db.get_extraction_batch(batch_id)
        if not batch:
            raise ValueError("extraction batch not found")
        if batch["status"] == "applied":
            raise ValueError("batch already applied")
        if batch["status"] != "approved":
            raise ValueError("only approved batches can be applied")
        candidates = sorted(
            self.db.list_extraction_candidates(batch_id, "approved"),
            key=lambda item: self.ORDER.get(item["candidate_kind"], 99),
        )
        if not candidates:
            raise ValueError("approved batch has no approved candidates")
        preflight = GraphQualityService(self.db).inspect_batch(batch_id)
        if not preflight.ok:
            message = "; ".join(preflight.errors)
            self.db.set_extraction_batch_status(batch_id, "failed", message)
            raise ValueError(message)
        counts_before = graph_counts(self.db)
        started_at = utc_now()
        try:
            with self.db._get_conn() as conn:
                for candidate in candidates:
                    target_id = self._apply_one(conn, candidate["candidate_kind"], candidate["payload"])
                    conn.execute(
                        "UPDATE extraction_candidates SET status = 'applied', applied_target_id = ?, applied_at = ? WHERE id = ?",
                        (target_id or "", self.db._now(), candidate["id"]),
                    )
                conn.execute(
                    "UPDATE extraction_batches SET status = 'applied', applied_at = ? WHERE id = ?",
                    (self.db._now(), batch_id),
                )
        except Exception as exc:
            self.db.set_extraction_batch_status(batch_id, "failed", str(exc))
            raise
        counts_after = graph_counts(self.db)
        audit = ApplyAuditRecord(
            batch_id=batch_id,
            mode=str(batch.get("mode") or ""),
            operator=operator,
            started_at=started_at,
            counts_before=counts_before,
            counts_after=counts_after,
            candidate_summary=candidate_summary(candidates),
            backup_path=backup_path,
        )
        append_apply_audit(audit)
        return audit.to_dict()

    def _apply_one(self, conn: sqlite3.Connection, kind: str, payload: dict) -> str:
        if kind == "entity":
            return self._entity(conn, payload)
        if kind == "alias":
            return self._alias(conn, payload)
        if kind == "relation":
            return self._relation(conn, payload)
        if kind == "field":
            return self._field(conn, payload)
        if kind == "link":
            return self._link(conn, payload)
        raise ValueError(f"unsupported candidate kind: {kind}")

    def _entity(self, conn: sqlite3.Connection, payload: dict) -> str:
        from rag_knowledge.services.entity_type_guard import coerce_entity_type

        name = normalize_entity_name(payload["name"])
        entity_type = coerce_entity_type(name, payload.get("entity_type") or "")
        row = self._lookup_entity_or_none(conn, name)
        if row:
            existing_type = str(row["entity_type"] or "")
            if existing_type != entity_type:
                # Allow Tool -> Utility coercion for binary misclassifications already in DB.
                if existing_type == "Tool" and entity_type == "Utility":
                    conn.execute(
                        "UPDATE entities SET entity_type = ?, updated_at = ? WHERE id = ?",
                        (entity_type, self.db._now(), row["id"]),
                    )
                else:
                    raise ValueError(f"entity type conflict: {name}")
            entity_id = str(row["id"])
            if payload.get("source_chunk_id"):
                self._link(conn, {
                    "entity_name": str(row["name"]),
                    "chunk_id": payload["source_chunk_id"],
                    "link_type": "evidence",
                    "evidence_text": payload.get("evidence_text") or "",
                })
            return entity_id
        entity_id = self.db._uid()
        properties = json.dumps(payload.get("properties") or {}, ensure_ascii=False, sort_keys=True)
        now = self.db._now()
        confidence = float(payload.get("confidence", 1.0))
        created_by = payload.get("created_by") or "rule:phase_b"
        conn.execute(
            "INSERT INTO entities (id, name, canonical_name, entity_type, properties_json, doc_category, confidence, review_status, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?, ?)",
            (entity_id, name, name, entity_type, properties, payload.get("doc_category") or "", confidence, created_by, now, now),
        )
        if payload.get("source_chunk_id"):
            self._link(conn, {
                "entity_name": name,
                "chunk_id": payload["source_chunk_id"],
                "link_type": "evidence",
                "evidence_text": payload.get("evidence_text") or "",
            })
        return entity_id

    def _alias(self, conn: sqlite3.Connection, payload: dict) -> str:
        entity = self._lookup_entity(conn, payload["entity_name"])
        alias = normalize_entity_name(payload["alias"])
        existing = conn.execute(
            "SELECT id FROM aliases WHERE entity_id = ? AND alias = ?",
            (entity["id"], alias),
        ).fetchone()
        if existing:
            return str(existing["id"])
        alias_id = self.db._uid()
        conn.execute(
            "INSERT INTO aliases (id, entity_id, alias, confidence, source_chunk_id, evidence_text, review_status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'approved', ?)",
            (
                alias_id,
                entity["id"],
                alias,
                float(payload.get("confidence", 1.0)),
                payload.get("source_chunk_id") or "",
                payload.get("evidence_text") or "",
                self.db._now(),
            ),
        )
        return alias_id

    @classmethod
    def _lookup_entity_or_none(cls, conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
        norm_key = normalize_identity_key(name)
        clean_name = normalize_entity_name(name)
        if not norm_key:
            return None

        # 1. Direct entities name match (case-insensitive / normalized)
        all_entities = conn.execute("SELECT id, name, entity_type, doc_category FROM entities").fetchall()
        for row in all_entities:
            if normalize_identity_key(row["name"]) == norm_key:
                return row

        # 2. Match aliases table
        all_aliases = conn.execute("SELECT entity_id, alias FROM aliases").fetchall()
        for alias_row in all_aliases:
            if normalize_identity_key(alias_row["alias"]) == norm_key:
                e_row = conn.execute("SELECT id, name, entity_type, doc_category FROM entities WHERE id = ?", (alias_row["entity_id"],)).fetchone()
                if e_row:
                    return e_row

        # 3. Match DomainCatalog
        catalog = DomainCatalogLoader()
        resolved = catalog.resolve(clean_name)
        if resolved:
            canonical_name = resolved[0]
            canonical_norm = normalize_identity_key(canonical_name)
            for row in all_entities:
                if normalize_identity_key(row["name"]) == canonical_norm:
                    return row
            for alias_row in all_aliases:
                if normalize_identity_key(alias_row["alias"]) == canonical_norm:
                    e_row = conn.execute("SELECT id, name, entity_type, doc_category FROM entities WHERE id = ?", (alias_row["entity_id"],)).fetchone()
                    if e_row:
                        return e_row

        return None

    @classmethod
    def _lookup_entity(cls, conn: sqlite3.Connection, name: str) -> sqlite3.Row:
        row = cls._lookup_entity_or_none(conn, name)
        if not row:
            raise ValueError(f"missing relation endpoint: {name}")
        return row

    def _relation(self, conn: sqlite3.Connection, payload: dict) -> str:
        relation_type = payload.get("relation_type") or ""
        if relation_type in {"has_section", "defined_in"}:
            return ""
        source = self._lookup_entity_or_none(conn, payload["source_name"])
        target = self._lookup_entity_or_none(conn, payload["target_name"])
        if not source or not target:
            return ""
        typed_payload = {
            **payload,
            "source_entity_type": source["entity_type"],
            "target_entity_type": target["entity_type"],
        }
        conflict_reason = describe_conflict("relation", typed_payload, load_backbone_constraints())
        if conflict_reason:
            raise ValueError(f"backbone relation lock: {conflict_reason}")
        if relation_type == "belongs_to" and (source["entity_type"] in {"Document", "Section"} or target["entity_type"] in {"Document", "Section"}):
            return ""
        from rag_knowledge.services.backbone_ownership import is_architecture_layer_name

        if (
            relation_type == "belongs_to"
            and is_architecture_layer_name(str(target["name"] or ""))
            and source["entity_type"] in {"Tool", "Service", "Product"}
        ):
            return ""
        if (
            relation_type == "belongs_to"
            and source["entity_type"] == "Utility"
            and target["entity_type"] == "Product"
        ):
            return ""
        if relation_type == "alias_of":
            return self._alias(conn, {
                "entity_name": str(target["name"]),
                "alias": str(payload.get("source_name") or source["name"]),
                "source_chunk_id": payload.get("source_chunk_id") or "",
                "evidence_text": payload.get("evidence_text") or "",
                "confidence": payload.get("confidence", 1.0),
            })
        ok, reason = validate_relation(source["entity_type"], relation_type, target["entity_type"])
        if not ok:
            raise ValueError(reason)
        existing = conn.execute(
            "SELECT id FROM relations WHERE source_entity_id = ? AND target_entity_id = ? AND relation_type = ?",
            (source["id"], target["id"], relation_type),
        ).fetchone()
        if existing:
            return str(existing["id"])
        relation_id = self.db._uid()
        confidence = float(payload.get("confidence", 1.0))
        created_by = payload.get("created_by") or "rule:phase_b"
        properties = json.dumps(payload.get("properties") or {}, ensure_ascii=False, sort_keys=True)
        conn.execute(
            "INSERT INTO relations (id, source_entity_id, target_entity_id, relation_type, properties_json, confidence, evidence_text, source_chunk_id, review_status, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?)",
            (relation_id, source["id"], target["id"], relation_type, properties, confidence, payload.get("evidence_text") or "", payload.get("source_chunk_id") or "", created_by, self.db._now()),
        )
        return relation_id

    def _field(self, conn: sqlite3.Connection, payload: dict) -> str:
        table = self._lookup_entity(conn, payload["table_name"])
        if table["entity_type"] != "DataTable":
            raise ValueError(f"field table is not DataTable: {payload['table_name']}")
        scoped_name = make_field_entity_name(payload["table_name"], payload["field_name"])
        field_entity_id = self._entity(conn, {
            "name": scoped_name, "entity_type": "Field", "doc_category": table["doc_category"] or "", "properties": {}
        })
        self._relation(conn, {
            "source_name": payload["table_name"], "target_name": scoped_name,
            "relation_type": "has_field", "source_chunk_id": payload.get("source_chunk_id") or "",
            "evidence_text": payload.get("description") or "",
        })
        existing = conn.execute(
            "SELECT id FROM fields WHERE table_entity_id = ? AND field_name = ?",
            (table["id"], normalize_entity_name(payload["field_name"])),
        ).fetchone()
        if existing:
            field_id = str(existing["id"])
        else:
            field_id = self.db._uid()
            conn.execute(
                "INSERT INTO fields (id, table_entity_id, field_name, description, required, unit, value_range, source_chunk_id, created_at, field_entity_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (field_id, table["id"], normalize_entity_name(payload["field_name"]), payload.get("description") or "",
                 int(bool(payload.get("required"))), payload.get("unit") or "", payload.get("value_range") or "",
                 payload.get("source_chunk_id") or "", self.db._now(), field_entity_id),
            )
        if payload.get("source_chunk_id"):
            self._link(conn, {"entity_name": scoped_name, "chunk_id": payload["source_chunk_id"], "link_type": "evidence"})
        return field_id

    def _link(self, conn: sqlite3.Connection, payload: dict) -> str:
        entity = self._lookup_entity_or_none(conn, payload["entity_name"])
        if not entity:
            return ""
        existing = conn.execute(
            "SELECT id FROM entity_chunk_links WHERE entity_id = ? AND chunk_id = ?",
            (entity["id"], payload["chunk_id"]),
        ).fetchone()
        if existing:
            return str(existing["id"])
        link_id = self.db._uid()
        conn.execute(
            "INSERT INTO entity_chunk_links (id, entity_id, chunk_id, link_type, section_path, evidence_text, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (link_id, entity["id"], payload["chunk_id"], payload.get("link_type") or "evidence",
             payload.get("section_path") or "", payload.get("evidence_text") or "", payload.get("source") or "", self.db._now()),
        )
        return link_id


class GraphQualityService:
    GOLDEN_ENTITIES = {
        "PipelineBuilder": "Tool", "管线点表": "DataTable", "管线发布服务": "Service",
        "PipelinePublishConfig": "ConfigItem", "DOMBuilder": "Tool",
    }
    GOLDEN_RELATIONS = [
        ("PipelineBuilder", "belongs_to", "StampTools"),
        ("PipelineBuilder", "has_table", "管线点表"),
        ("管线发布服务", "belongs_to", "StampServer"),
        ("管线发布服务", "uses_config", "PipelinePublishConfig"),
        ("DOMBuilder", "belongs_to", "TerrainBuilder"),
        ("TerrainBuilder", "belongs_to", "StampTools"),
    ]

    def __init__(self, db: RelationalDB | None = None):
        self.db = db or RelationalDB()

    def inspect_batch(self, batch_id: str) -> QualityReport:
        candidates = self.db.list_extraction_candidates(batch_id)
        report = QualityReport(stats={"candidates": len(candidates)})
        if any(item["status"] == "pending" for item in candidates):
            report.errors.append("pending_candidates")
        approved = [item for item in candidates if item["status"] == "approved"]
        approved_link_names = {
            item["payload"]["entity_name"]
            for item in approved
            if item["candidate_kind"] == "link"
        }
        entity_types: dict[str, str] = {}
        for item in approved:
            if item["candidate_kind"] != "entity":
                continue
            payload = item["payload"]
            has_candidate_evidence = payload["name"] in approved_link_names or bool(payload.get("source_chunk_id"))
            created_by = str(payload.get("created_by") or "")
            if (
                created_by not in {"rule:profile_sync", "seed:domain_catalog", "seed:product_backbone"}
                and not created_by.startswith("seed:")
                and not has_candidate_evidence
            ):
                error = f"missing_evidence:{payload['name']}"
                if error not in report.errors:
                    report.errors.append(error)
            previous = entity_types.get(payload["name"])
            if previous and previous != payload["entity_type"]:
                report.errors.append(f"entity type conflict:{payload['name']}")
            entity_types[payload["name"]] = payload["entity_type"]
        with self.db._get_conn() as conn:
            for row in conn.execute("SELECT name, entity_type FROM entities").fetchall():
                previous = entity_types.get(row["name"])
                if previous and previous != row["entity_type"]:
                    report.errors.append(f"entity type conflict:{row['name']}")
                entity_types.setdefault(row["name"], row["entity_type"])
        for item in approved:
            kind, payload = item["candidate_kind"], item["payload"]
            if kind == "alias":
                if payload["entity_name"] not in entity_types:
                    report.errors.append(f"missing_alias_entity:{payload['entity_name']}")
            elif kind == "relation":
                source_type = entity_types.get(payload["source_name"])
                target_type = entity_types.get(payload["target_name"])
                if not source_type or not target_type:
                    report.errors.append(
                        f"missing_relation_endpoint:{payload['source_name']}:{payload['target_name']}"
                    )
                else:
                    ok, _ = validate_relation(source_type, payload["relation_type"], target_type)
                    if not ok:
                        report.errors.append(
                            f"illegal_relation:{payload['source_name']}:{payload['relation_type']}:{payload['target_name']}"
                        )
            elif kind == "field" and entity_types.get(payload["table_name"]) != "DataTable":
                report.errors.append(f"missing_table_context:{payload['table_name']}")
            elif kind == "link" and payload["entity_name"] not in entity_types:
                report.errors.append(f"missing_link_entity:{payload['entity_name']}")
        return report

    def inspect_llm_batch(self, batch_id: str) -> QualityReport:
        candidates = self.db.list_extraction_candidates(batch_id)
        llm_candidates = [
            item for item in candidates
            if item["payload"].get("created_by") == "llm:schema_extractor"
            or (
                item["candidate_kind"] == "diagnostic"
                and item["payload"].get("code", "").startswith(
                    ("invalid_", "missing_", "low_", "type_conflict", "possible_duplicate", "illegal_relation", "noisy_", "relation_direction_flipped")
                )
            )
        ]
        report = QualityReport(stats={"total_llm_candidates": sum(item["candidate_kind"] != "diagnostic" for item in llm_candidates)})
        diagnostics = [item["payload"].get("code", "") for item in llm_candidates if item["candidate_kind"] == "diagnostic"]
        report.stats.update({
            "valid_schema_count": sum(item["candidate_kind"] in {"entity", "relation"} and item["status"] != "rejected" for item in llm_candidates),
            "invalid_schema_count": sum(code.startswith("invalid_") for code in diagnostics),
            "missing_evidence_count": sum(code == "missing_evidence" for code in diagnostics),
            "evidence_text_not_found_count": sum(code == "invalid_evidence_text" for code in diagnostics),
            "low_confidence_count": sum(code == "low_confidence" for code in diagnostics),
            "duplicate_candidate_count": sum(code == "duplicate_candidate" for code in diagnostics),
            "type_conflict_count": sum(code == "type_conflict" for code in diagnostics),
            "possible_duplicate_count": sum(code == "possible_duplicate" for code in diagnostics),
            "illegal_relation_count": sum(code == "illegal_relation" for code in diagnostics),
            "relation_direction_flipped_count": sum(code == "relation_direction_flipped" for code in diagnostics),
            "high_confidence_candidate_count": sum(float(item["payload"].get("confidence", 0)) >= 0.9 for item in llm_candidates if item["candidate_kind"] != "diagnostic"),
        })
        report.stats["samples"] = {
            "high_confidence": [item["payload"] for item in llm_candidates if item["candidate_kind"] != "diagnostic" and float(item["payload"].get("confidence", 0)) >= 0.9][:5],
            "low_confidence": [item["payload"] for item in llm_candidates if item["payload"].get("code") == "low_confidence"][:5],
            "conflicts": [item["payload"] for item in llm_candidates if item["payload"].get("code") in {"type_conflict", "possible_duplicate", "illegal_relation", "relation_direction_flipped"}][:5],
        }
        return report

    def inspect_graph(self, profile: str = "full") -> QualityReport:
        if profile not in {"partial", "full"}:
            raise ValueError("quality profile must be partial or full")
        report = QualityReport()
        with self.db._get_conn() as conn:
            entities = {row["name"]: dict(row) for row in conn.execute("SELECT * FROM entities").fetchall()}
            relations = conn.execute(
                "SELECT r.*, s.name source_name, s.entity_type source_type, t.name target_name, t.entity_type target_type "
                "FROM relations r JOIN entities s ON s.id=r.source_entity_id JOIN entities t ON t.id=r.target_entity_id"
            ).fetchall()
            links = {row[0] for row in conn.execute("SELECT DISTINCT entity_id FROM entity_chunk_links").fetchall()}
        relation_keys = {(row["source_name"], row["relation_type"], row["target_name"]) for row in relations}

        # --- Quality Gate v1 checks ---
        if profile == "full":
            for name, entity_type in self.GOLDEN_ENTITIES.items():
                if name not in entities:
                    report.errors.append(f"missing_golden_entity:{name}")
                elif entities[name]["entity_type"] != entity_type:
                    report.errors.append(f"wrong_golden_type:{name}")
            for source, relation, target in self.GOLDEN_RELATIONS:
                if (source, relation, target) not in relation_keys:
                    report.errors.append(f"missing_golden_relation:{source}:{relation}:{target}")
        for row in relations:
            ok, _ = validate_relation(row["source_type"], row["relation_type"], row["target_type"])
            if not ok:
                report.errors.append(f"illegal_relation:{row['id']}")

        # --- Catalog ownership (Tool/Service must not hang on architecture layers) ---
        from rag_knowledge.services.backbone_ownership import (
            belongs_to_parents_from_relations,
            find_ownership_gaps,
        )

        entity_types = {name: meta["entity_type"] for name, meta in entities.items()}
        parents = belongs_to_parents_from_relations(
            relations,
            source_key="source_name",
            target_key="target_name",
        )
        ownership_gaps = find_ownership_gaps(
            entity_types=entity_types,
            belongs_to_parents=parents,
        )
        report.stats["catalog_ownership_gap_count"] = len(ownership_gaps)
        for gap in ownership_gaps:
            report.errors.append(gap.code() if gap.reason == "missing_owner_edge" else f"{gap.reason}:{gap.child}:{gap.expected_parent}")

        from rag_knowledge.services.entity_type_guard import looks_like_utility_name

        utility_misclassified = 0
        utility_product_parents = 0
        for name, meta in entities.items():
            etype = meta.get("entity_type") or ""
            if etype == "Tool" and looks_like_utility_name(name):
                utility_misclassified += 1
                report.errors.append(f"utility_misclassified_as_tool:{name}")
            if etype == "Utility":
                for parent in parents.get(name) or []:
                    if entity_types.get(parent) == "Product":
                        utility_product_parents += 1
                        report.errors.append(f"utility_belongs_to_product:{name}:{parent}")
        report.stats["utility_misclassified_as_tool_count"] = utility_misclassified
        report.stats["utility_belongs_to_product_count"] = utility_product_parents

        # --- Orphan entities ---
        connected = {row["source_entity_id"] for row in relations} | {row["target_entity_id"] for row in relations} | links
        for entity in entities.values():
            if entity["id"] not in connected:
                report.warnings.append(f"orphan_entity:{entity['name']}")
            if entity.get("created_by") == "rule:phase_b" and entity["id"] not in links:
                report.errors.append(f"missing_evidence:{entity['name']}")

        # --- Quality Gate v2: Stale link check ---
        stale_link_count = 0
        try:
            from rag_knowledge.repository.vector_store import VectorStore
            store = VectorStore()
            chroma_data = store._get_store()._collection.get(include=[])
            valid_chunk_ids = set(chroma_data.get("ids") or [])
            with self.db._get_conn() as conn:
                all_links_rows = conn.execute("SELECT id, entity_id, chunk_id FROM entity_chunk_links").fetchall()
                stale_links = [dict(r) for r in all_links_rows if r["chunk_id"] not in valid_chunk_ids]
                stale_link_count = len(stale_links)
        except Exception:
            pass  # Chroma not accessible; skip stale link check
        report.stats["stale_link_count"] = stale_link_count
        if stale_link_count > 0:
            report.errors.append(f"stale_links_present:{stale_link_count}")

        # --- Quality Gate v2: Missing evidence count ---
        missing_evidence_count = sum(
            1 for e in entities.values()
            if e.get("created_by") == "rule:phase_b" and e["id"] not in links
        )
        report.stats["missing_evidence_count"] = missing_evidence_count

        # --- Quality Gate v2: Invalid schema count ---
        invalid_schema_count = sum(
            1 for row in relations
            if not validate_relation(row["source_type"], row["relation_type"], row["target_type"])[0]
        )
        report.stats["invalid_schema_count"] = invalid_schema_count

        # --- Quality Gate v2: Type conflict unresolved count ---
        type_conflict_count = 0
        with self.db._get_conn() as conn:
            conflict_rows = conn.execute(
                "SELECT LOWER(TRIM(name)) as norm_name, COUNT(DISTINCT entity_type) as type_cnt "
                "FROM entities GROUP BY LOWER(TRIM(name)) HAVING type_cnt > 1"
            ).fetchall()
            type_conflict_count = len(conflict_rows)
        report.stats["type_conflict_unresolved_count"] = type_conflict_count
        if type_conflict_count > 0:
            report.errors.append(f"unresolved_type_conflicts:{type_conflict_count}")

        # --- Quality Gate v2: High confidence without evidence ---
        high_conf_no_evidence = 0
        for e in entities.values():
            confidence = float(e.get("confidence", 1.0) or 1.0)
            if confidence >= 0.9 and e["id"] not in links and e.get("created_by", "").startswith("llm:"):
                high_conf_no_evidence += 1
        report.stats["high_confidence_without_evidence_count"] = high_conf_no_evidence
        if high_conf_no_evidence > 0:
            report.errors.append(f"high_confidence_without_evidence:{high_conf_no_evidence}")

        # --- Quality Gate v2: Manual fact preserved ---
        manual_count = sum(1 for e in entities.values() if e.get("created_by") in {"admin", "manual"})
        manual_count += sum(
            1 for r in relations if dict(r).get("created_by") in {"admin", "manual"}
        )
        report.stats["manual_fact_count"] = manual_count
        report.stats["manual_fact_preserved"] = True  # Read-only inspection: always true

        # --- Quality Gate v2: Duplicate candidate ratio ---
        dup_canonical_count = 0
        with self.db._get_conn() as conn:
            dup_rows = conn.execute(
                "SELECT LOWER(TRIM(canonical_name)) as norm_name, COUNT(*) as cnt "
                "FROM entities WHERE canonical_name IS NOT NULL AND canonical_name != '' "
                "GROUP BY LOWER(TRIM(canonical_name)) HAVING cnt > 1"
            ).fetchall()
            dup_canonical_count = len(dup_rows)
        total_entities = len(entities)
        dup_ratio = float(dup_canonical_count) / total_entities if total_entities > 0 else 0.0
        report.stats["duplicate_candidate_ratio"] = round(dup_ratio, 4)
        if dup_ratio > 0.20:
            report.errors.append(f"duplicate_candidate_ratio_exceeded:{dup_ratio:.2f}")

        # --- Quality Gate v2: Section ratio and business entity stats ---
        business_types = {
            "Product", "Tool", "Service", "Module", "DataTable", "Field",
            "ConfigItem", "Format", "Procedure", "Step", "Error", "Solution",
            "EnvironmentComponent", "Command",
        }
        entity_type_counts: dict[str, int] = {}
        for e in entities.values():
            entity_type_counts[e["entity_type"]] = entity_type_counts.get(e["entity_type"], 0) + 1
        section_count = entity_type_counts.get("Section", 0)
        business_count = sum(entity_type_counts.get(t, 0) for t in business_types)
        report.stats["section_count"] = section_count
        report.stats["section_ratio"] = round(float(section_count) / total_entities, 4) if total_entities > 0 else 0.0
        report.stats["business_entity_count"] = business_count
        report.stats["total_entities"] = total_entities
        report.stats["total_relations"] = len(relations)

        # --- Extraction coverage contract (catalog top-level tools) ---
        from rag_knowledge.services.extraction_coverage import ExtractionCoverageService

        coverage_products = ["StampTools", "StampServer"]
        coverage_matrix = []
        domain_gap_total = 0
        structure_gap_total = 0
        for product_name in coverage_products:
            product_report = ExtractionCoverageService(self.db).inspect_product(product_name)
            coverage_matrix.append(product_report.as_dict())
            for row in product_report.uncovered_top_level:
                domain_gap_total += 1
                report.warnings.append(
                    f"extraction_coverage_domain_gap:{product_name}:{row.tool}"
                )
            for row in product_report.structure_uncovered_top_level:
                structure_gap_total += 1
                report.warnings.append(
                    f"extraction_coverage_structure_gap:{product_name}:{row.tool}"
                )
            for row in product_report.missing_tools:
                report.warnings.append(
                    f"extraction_coverage_missing_tool:{product_name}:{row.tool}"
                )
        report.stats["extraction_coverage"] = coverage_matrix
        report.stats["extraction_coverage_domain_gap_count"] = domain_gap_total
        report.stats["extraction_coverage_structure_gap_count"] = structure_gap_total
        report.stats["extraction_coverage_gap_count"] = domain_gap_total

        return report
