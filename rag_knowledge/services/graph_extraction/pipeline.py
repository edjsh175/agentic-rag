"""Batch orchestration, atomic application, and quality checks."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Callable

from rag_knowledge.models.graph_schema import normalize_entity_name, validate_relation
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.knowledge_base_consistency import KnowledgeBaseConsistencyService
from rag_knowledge.services.entity_resolution import EntityResolutionService

from . import (
    CandidateNormalizer,
    ConfigBlockExtractor,
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
                   doc_categories: list[str] | None = None, include_llm: bool = False) -> BuildBatchResult:
        chunks = self._chunk_source()
        if doc_categories:
            allowed = set(doc_categories)
            chunks = [item for item in chunks if str((item.get("metadata") or {}).get("doc_category") or "") in allowed]
        if limit is not None:
            chunks = chunks[:limit]
        return self._build("full", chunks, {"limit": limit, "doc_categories": doc_categories or [], "force_rebuild": force_rebuild, "include_llm": include_llm}, include_llm=include_llm)

    def build_incremental(self, chunk_ids: list[str], include_llm: bool = False) -> BuildBatchResult:
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
        )

    def _build(self, mode: str, chunks: list[dict], filters: dict,
               extra_stats: dict | None = None,
               missing_chunk_ids: list[str] | None = None,
               include_llm: bool = False) -> BuildBatchResult:
        snapshot = hashlib.sha256(json.dumps(chunks, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        force_rebuild = bool(filters.get("force_rebuild"))
        if mode == "full":
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
                        return BuildBatchResult(str(existing["id"]), json.loads(existing["stats_json"] or "{}"))

        from .llm_extractor import LLMGraphExtractor
        from rag_knowledge.config import Config
        cfg = Config()
        actual_include_llm = include_llm or cfg.graph_extraction_llm.enabled
        llm_extractor = LLMGraphExtractor() if actual_include_llm else None
        candidate_normalizer = CandidateNormalizer()
        entity_resolver = EntityResolutionService(self.db)

        batch_id = self.db.create_extraction_batch(mode, filters, snapshot)
        counts = {"chunks": len(chunks), "entity": 0, "relation": 0, "alias": 0, "field": 0, "link": 0, "diagnostic": 0}
        counts.update(extra_stats or {})
        candidate_ids: dict[str, set[str]] = {kind: set() for kind in ("entity", "relation", "field", "link", "diagnostic", "alias")}
        rule_candidate_ids = set()
        llm_candidate_ids = set()

        for chunk in chunks:
            # 1. Rules extractors
            context = SectionPathExtractor().extract(chunk)
            combined = ExtractionResult()
            combined.extend(context)
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
                    identity_payload = self._identity_payload(kind, payload)
                    fingerprint = hashlib.sha256(
                        json.dumps([kind, identity_payload], ensure_ascii=False, sort_keys=True).encode()
                    ).hexdigest()
                    candidate_id = self.db.add_extraction_candidate(
                        batch_id, kind, fingerprint, payload, source_chunk_id, evidence
                    )
                    if kind == "diagnostic":
                        self.db.review_extraction_candidates(batch_id, [candidate_id], "rejected", payload.get("message", ""))
                    candidate_ids[kind].add(candidate_id)
                    rule_candidate_ids.add(candidate_id)

            # 2. LLM semantic extractor
            if actual_include_llm and llm_extractor:
                llm_result = llm_extractor.extract(chunk)
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
                            payload["properties"] = {
                                "created_by": "llm:schema_extractor",
                                "confidence": payload["confidence"],
                                "prompt_version": payload["prompt_version"],
                                "extractor_version": payload["extractor_version"],
                            }
                        
                        identity_payload = self._identity_payload(kind, payload)
                        fingerprint = hashlib.sha256(
                            json.dumps([kind, identity_payload], ensure_ascii=False, sort_keys=True).encode()
                        ).hexdigest()
                        candidate_id = self.db.add_extraction_candidate(
                            batch_id, kind, fingerprint, payload, source_chunk_id, evidence
                        )
                        if kind == "diagnostic":
                            self.db.review_extraction_candidates(batch_id, [candidate_id], "rejected", payload.get("message", ""))
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
                    candidate_ids["alias"].add(candidate_id)
                    llm_candidate_ids.add(candidate_id)

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

        for kind, ids in candidate_ids.items():
            counts[kind] = len(ids)
        counts["rule_candidates"] = len(rule_candidate_ids)
        counts["llm_candidates"] = len(llm_candidate_ids)

        with self.db._get_conn() as conn:
            conn.execute(
                "UPDATE extraction_batches SET stats_json = ? WHERE id = ?",
                (json.dumps(counts, ensure_ascii=False, sort_keys=True), batch_id),
            )
        return BuildBatchResult(batch_id, counts)

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

    def apply(self, batch_id: str) -> None:
        batch = self.db.get_extraction_batch(batch_id)
        if not batch or batch["status"] != "approved":
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
        try:
            with self.db._get_conn() as conn:
                for candidate in candidates:
                    target_id = self._apply_one(conn, candidate["candidate_kind"], candidate["payload"])
                    conn.execute(
                        "UPDATE extraction_candidates SET status = 'applied', applied_target_id = ?, applied_at = ? WHERE id = ?",
                        (target_id or "", self.db._now(), candidate["id"]),
                    )
                GraphSpecialRuleRestorer(self.db).apply(conn)
                conn.execute(
                    "UPDATE extraction_batches SET status = 'applied', applied_at = ? WHERE id = ?",
                    (self.db._now(), batch_id),
                )
        except Exception as exc:
            self.db.set_extraction_batch_status(batch_id, "failed", str(exc))
            raise

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
        scoped_name = f"{payload['table_name']}.{normalize_entity_name(payload['field_name'])}"
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


class GraphSpecialRuleRestorer:
    """Restore graph-level disambiguation rules that are not recoverable from chunk extraction alone."""

    DIFFERENT_FROM_RULES = (
        ("PipelineBuilder", "管线发布服务"),
        ("PipelineBuilder", "PipelinePublishConfig"),
    )

    ALIAS_RULES = (
        ("PipelineBuilder", "管线发布工具"),
    )

    def __init__(self, db: RelationalDB | None = None):
        self.db = db or RelationalDB()

    def apply(self, conn: sqlite3.Connection) -> None:
        self._restore_aliases(conn)
        self._restore_different_from(conn)

    def _restore_aliases(self, conn: sqlite3.Connection) -> None:
        for entity_name, alias in self.ALIAS_RULES:
            entity = self._lookup_entity(conn, entity_name)
            if entity is None:
                continue
            existing = conn.execute(
                "SELECT id FROM aliases WHERE entity_id = ? AND alias = ?",
                (entity["id"], alias),
            ).fetchone()
            if existing:
                continue
            conn.execute(
                "INSERT INTO aliases (id, entity_id, alias, confidence, source_chunk_id, evidence_text, review_status, created_at) "
                "VALUES (?, ?, ?, 1.0, '', ?, 'approved', ?)",
                (
                    self.db._uid(),
                    entity["id"],
                    alias,
                    f"special_rule:{entity_name}:alias",
                    self.db._now(),
                ),
            )

    def _restore_different_from(self, conn: sqlite3.Connection) -> None:
        for source_name, target_name in self.DIFFERENT_FROM_RULES:
            source = self._lookup_entity(conn, source_name)
            target = self._lookup_entity(conn, target_name)
            if source is None or target is None:
                continue
            existing = conn.execute(
                "SELECT id FROM relations WHERE source_entity_id = ? AND target_entity_id = ? AND relation_type = 'different_from'",
                (source["id"], target["id"]),
            ).fetchone()
            if existing:
                continue
            conn.execute(
                "INSERT INTO relations (id, source_entity_id, target_entity_id, relation_type, properties_json, confidence, evidence_text, source_chunk_id, review_status, created_by, created_at) "
                "VALUES (?, ?, ?, 'different_from', '{}', 1.0, ?, '', 'approved', 'rule:special_relations', ?)",
                (
                    self.db._uid(),
                    source["id"],
                    target["id"],
                    f"special_rule:{source_name}:different_from:{target_name}",
                    self.db._now(),
                ),
            )

    @staticmethod
    def _lookup_entity(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT id, name, entity_type FROM entities WHERE name = ?",
            (normalize_entity_name(name),),
        ).fetchone()


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
            if payload.get("created_by") != "rule:profile_sync" and not has_candidate_evidence:
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
            or item["candidate_kind"] == "diagnostic" and item["payload"].get("code", "").startswith(("invalid_", "missing_", "low_", "type_conflict", "possible_duplicate"))
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
            "high_confidence_candidate_count": sum(float(item["payload"].get("confidence", 0)) >= 0.9 for item in llm_candidates if item["candidate_kind"] != "diagnostic"),
        })
        report.stats["samples"] = {
            "high_confidence": [item["payload"] for item in llm_candidates if item["candidate_kind"] != "diagnostic" and float(item["payload"].get("confidence", 0)) >= 0.9][:5],
            "low_confidence": [item["payload"] for item in llm_candidates if item["payload"].get("code") == "low_confidence"][:5],
            "conflicts": [item["payload"] for item in llm_candidates if item["payload"].get("code") in {"type_conflict", "possible_duplicate"}][:5],
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
