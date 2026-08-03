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
from rag_knowledge.services.backbone_guard import (
    CONFLICT_REASON,
    chunk_in_backbone_neighborhood,
    describe_conflict,
    load_backbone_constraints,
    rule_result_hits_backbone,
)
from rag_knowledge.services.ollama_health import assert_ollama_reachable

from .chunk_mentions_extractor import ChunkMentionsExtractor
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
                   doc_categories: list[str] | None = None, include_llm: bool = False,
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
            {"limit": limit, "doc_categories": doc_categories or [], "force_rebuild": force_rebuild, "include_llm": include_llm},
            include_llm=include_llm,
            resume_batch_id=resume_batch_id,
        )

    def build_incremental(self, chunk_ids: list[str], include_llm: bool = False,
                          resume_batch_id: str | None = None) -> BuildBatchResult:
        wanted = set(chunk_ids)
        chunks = [item for item in self._chunk_source() if str(item.get("chunk_id") or "") in wanted]
        matched = {str(item.get("chunk_id") or "") for item in chunks}
        missing = sorted(wanted - matched)
        return self._build(
            "incremental",
            chunks,
            {"chunk_ids": sorted(wanted), "include_llm": include_llm},
            extra_stats={
                "requested_chunks": len(wanted),
                "matched_chunks": len(matched),
                "missing_chunks": missing,
            },
            missing_chunk_ids=missing,
            include_llm=include_llm,
            resume_batch_id=resume_batch_id,
        )

    def resume_batch(self, batch_id: str, *, include_llm: bool | None = None) -> BuildBatchResult:
        """Continue an interrupted extract batch; skips processed_chunk_ids."""
        batch = self.db.get_extraction_batch(batch_id)
        if not batch:
            raise KeyError(f"extraction batch not found: {batch_id}")
        status = str(batch.get("status") or "")
        if status in {"applied", "approved", "rejected", "superseded", "failed"}:
            raise ValueError(f"cannot resume batch in status={status}")

        filters = json.loads(batch.get("filters_json") or "{}")
        mode = str(batch.get("mode") or "full")
        want_llm = bool(filters.get("include_llm")) if include_llm is None else bool(include_llm)

        if mode == "incremental":
            chunk_ids = list(filters.get("chunk_ids") or [])
            return self.build_incremental(chunk_ids, include_llm=want_llm, resume_batch_id=batch_id)

        limit = filters.get("limit")
        return self.build_full(
            force_rebuild=bool(filters.get("force_rebuild")),
            limit=int(limit) if limit is not None else None,
            doc_categories=list(filters.get("doc_categories") or []) or None,
            include_llm=want_llm,
            resume_batch_id=batch_id,
        )

    def _build(self, mode: str, chunks: list[dict], filters: dict,
               extra_stats: dict | None = None,
               missing_chunk_ids: list[str] | None = None,
               include_llm: bool = False,
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
        cfg = Config()
        actual_include_llm = include_llm or cfg.graph_extraction_llm.enabled
        backbone_constraints = load_backbone_constraints()
        if actual_include_llm:
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
        catalog = DomainCatalogLoader()
        section_extractor = SectionPathExtractor(catalog=catalog)
        candidate_normalizer = CandidateNormalizer(catalog=catalog)
        entity_resolver = EntityResolutionService(self.db)

        if batch_id is None:
            batch_id = self.db.create_extraction_batch(mode, filters, snapshot)
            self.db.update_extraction_batch_stats(batch_id, counts)

        for chunk in chunks:
            chunk_id = str(chunk.get("chunk_id") or "")
            if chunk_id in done_set:
                continue

            # 1. Rules extractors
            context = section_extractor.extract(chunk)
            combined = ExtractionResult()
            combined.extend(context)
            combined.extend(DataSpecTableRelationExtractor().extract(chunk, context))
            combined.extend(TableFieldExtractor().extract(chunk, context))
            combined.extend(ConfigBlockExtractor().extract(chunk, context))
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
                        resolution = entity_resolver.resolve(item)
                        payload["resolution_action"] = resolution.action
                        payload["resolved_entity_id"] = resolution.target_id
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
                        payload, flipped = self._canonicalize_relation_direction(payload, type_index)
                        if flipped:
                            counts["relation_direction_flipped"] = int(counts.get("relation_direction_flipped") or 0) + 1
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
                    elif kind == "relation":
                        if (payload.get("properties") or {}).get("direction_flipped"):
                            self._record_direction_flip_diagnostic(
                                batch_id, payload, candidate_ids
                            )
                        self._reject_backbone_conflict(
                            batch_id, kind, candidate_id, payload, backbone_constraints, candidate_ids
                        )
                        self._reject_illegal_relation(
                            batch_id, candidate_id, payload, type_index, candidate_ids
                        )
                    candidate_ids[kind].add(candidate_id)
                    rule_candidate_ids.add(candidate_id)

            # Collect FunctionAreas from rule extractors for this chunk
            fa_list = [
                e.name for e in combined.entities if e.entity_type == "FunctionArea"
            ]

            # 2. LLM semantic extractor
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
                    if cfg.graph_extraction_llm.rate_limit_delay > 0:
                        import time
                        time.sleep(cfg.graph_extraction_llm.rate_limit_delay)
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
                                resolution = entity_resolver.resolve(item)
                                payload["resolution_action"] = resolution.action
                                payload["resolved_entity_id"] = resolution.target_id
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
                                payload, flipped = self._canonicalize_relation_direction(payload, type_index)
                                if flipped:
                                    counts["relation_direction_flipped"] = int(counts.get("relation_direction_flipped") or 0) + 1

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
                            elif kind == "relation":
                                if (payload.get("properties") or {}).get("direction_flipped"):
                                    self._record_direction_flip_diagnostic(
                                        batch_id, payload, candidate_ids
                                    )
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

    _DIRECTION_FLIP_RELATIONS = frozenset({
        "runs_command",
        "configured_by",
        "uses_config",
        "has_procedure",
        "has_step",
        "solved_by",
    })

    def _canonicalize_relation_direction(
        self, payload: dict, type_index: dict[str, str]
    ) -> tuple[dict, bool]:
        """If direction is illegal but reverse is legal, swap endpoints (audited)."""
        source_name = str(payload.get("source_name") or "")
        target_name = str(payload.get("target_name") or "")
        relation_type = str(payload.get("relation_type") or "")
        if relation_type not in self._DIRECTION_FLIP_RELATIONS:
            return payload, False
        source_type = self._lookup_entity_type(type_index, source_name)
        target_type = self._lookup_entity_type(type_index, target_name)
        if not source_type or not target_type:
            return payload, False
        ok, _ = validate_relation(source_type, relation_type, target_type)
        if ok:
            return payload, False
        ok_rev, _ = validate_relation(target_type, relation_type, source_type)
        if not ok_rev:
            return payload, False
        flipped = dict(payload)
        flipped["source_name"] = target_name
        flipped["target_name"] = source_name
        props = dict(flipped.get("properties") or {})
        props["direction_flipped"] = True
        props["direction_flipped_from"] = f"{source_name}-[{relation_type}]->{target_name}"
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
        name = normalize_entity_name(payload["name"])
        row = conn.execute("SELECT id, entity_type FROM entities WHERE name = ?", (name,)).fetchone()
        if row:
            if row["entity_type"] != payload["entity_type"]:
                raise ValueError(f"entity type conflict: {name}")
            entity_id = str(row["id"])
            if payload.get("source_chunk_id"):
                self._link(conn, {
                    "entity_name": name,
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
            (entity_id, name, name, payload["entity_type"], properties, payload.get("doc_category") or "", confidence, created_by, now, now),
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

    @staticmethod
    def _lookup_entity(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
        row = conn.execute("SELECT id, entity_type, doc_category FROM entities WHERE name = ?", (normalize_entity_name(name),)).fetchone()
        if not row:
            raise ValueError(f"missing relation endpoint: {name}")
        return row

    def _relation(self, conn: sqlite3.Connection, payload: dict) -> str:
        source = self._lookup_entity(conn, payload["source_name"])
        target = self._lookup_entity(conn, payload["target_name"])
        relation_type = payload["relation_type"]
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
        entity = self._lookup_entity(conn, payload["entity_name"])
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
        ("DOMBuilder", "belongs_to", "StampTools"),
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

        return report
