"""Stage product_relation_backbone.json into reviewable graph candidates."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from rag_knowledge.models.graph_schema import validate_relation
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_extraction.pipeline import BuildBatchResult

SEED_CREATED_BY = "seed:product_backbone"
BATCH_MODE = "product_backbone_seed"


@dataclass
class ProductBackbonePreview:
    entities: list[dict] = field(default_factory=list)
    aliases: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "entities": self.entities,
            "aliases": self.aliases,
            "relations": self.relations,
            "diagnostics": self.diagnostics,
        }


class ProductBackboneGraphSyncService:
    def __init__(self, db: RelationalDB | None = None, path: str | Path | None = None):
        self.db = db
        root = Path(__file__).resolve().parents[2]
        self.path = Path(path) if path else root / "data" / "product_relation_backbone.json"

    def preview(self) -> dict:
        return self._build_preview().to_dict()

    def build_batch(self, review_status: str = "pending") -> BuildBatchResult:
        if review_status not in {"pending", "approved"}:
            raise ValueError("review_status must be pending or approved")
        db = self.db or RelationalDB()
        preview = self._build_preview()
        if preview.diagnostics:
            codes = {item["code"] for item in preview.diagnostics}
            raise ValueError(f"product_relation_backbone validation failed: {sorted(codes)}")
        payload = preview.to_dict()
        snapshot = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        batch_id = db.create_extraction_batch(
            BATCH_MODE,
            {"review_status": review_status, "source": str(self.path)},
            snapshot,
        )
        counts = {"entity": 0, "alias": 0, "relation": 0}
        for entity in preview.entities:
            counts["entity"] += self._stage_candidate(
                db, batch_id, "entity", entity, entity["evidence_text"], review_status
            )
        for alias in preview.aliases:
            counts["alias"] += self._stage_candidate(
                db, batch_id, "alias", alias, alias["evidence_text"], review_status
            )
        for relation in preview.relations:
            counts["relation"] += self._stage_candidate(
                db, batch_id, "relation", relation, relation["evidence_text"], review_status
            )
        with db._get_conn() as conn:
            conn.execute(
                "UPDATE extraction_batches SET stats_json = ? WHERE id = ?",
                (json.dumps(counts, ensure_ascii=False, sort_keys=True), batch_id),
            )
        if review_status == "approved":
            db.set_extraction_batch_status(batch_id, "approved")
        return BuildBatchResult(batch_id, counts)

    def _load(self) -> dict:
        if not self.path.exists():
            raise FileNotFoundError(f"product relation backbone not found: {self.path}")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("product_relation_backbone root must be an object")
        if int(data.get("schema_version") or 0) != 1:
            raise ValueError("product_relation_backbone schema_version must be 1")
        if not isinstance(data.get("entities"), list) or not isinstance(data.get("relations"), list):
            raise ValueError("product_relation_backbone entities/relations must be lists")
        return data

    def _build_preview(self) -> ProductBackbonePreview:
        data = self._load()
        preview = ProductBackbonePreview()
        source_ref = str(data.get("source_ref") or self.path.name)
        entity_types: dict[str, str] = {}

        for item in data["entities"]:
            if not isinstance(item, dict):
                preview.diagnostics.append({"code": "invalid_entity", "message": "entity entry must be object"})
                continue
            name = str(item.get("name") or "").strip()
            entity_type = str(item.get("entity_type") or "").strip()
            if not name or not entity_type:
                preview.diagnostics.append({"code": "invalid_entity", "message": f"missing name/type: {item}"})
                continue
            if name in entity_types and entity_types[name] != entity_type:
                preview.diagnostics.append({
                    "code": "type_conflict",
                    "message": f"duplicate entity with conflicting type: {name}",
                })
                continue
            entity_types[name] = entity_type
            evidence = f"product_backbone:{source_ref}:entity:{name}"
            preview.entities.append({
                "name": name,
                "entity_type": entity_type,
                "doc_category": str(item.get("doc_category") or ""),
                "created_by": SEED_CREATED_BY,
                "confidence": 1.0,
                "evidence_text": evidence,
            })
            aliases = item.get("aliases") or []
            if not isinstance(aliases, list):
                preview.diagnostics.append({"code": "invalid_aliases", "message": f"aliases must be list: {name}"})
                continue
            for alias in aliases:
                alias_name = str(alias or "").strip()
                if not alias_name:
                    continue
                preview.aliases.append({
                    "entity_name": name,
                    "alias": alias_name,
                    "created_by": SEED_CREATED_BY,
                    "confidence": 1.0,
                    "evidence_text": evidence,
                })

        for item in data["relations"]:
            if not isinstance(item, dict):
                preview.diagnostics.append({"code": "invalid_relation", "message": "relation entry must be object"})
                continue
            source = str(item.get("source") or "").strip()
            target = str(item.get("target") or "").strip()
            relation_type = str(item.get("relation_type") or "").strip()
            note = str(item.get("note") or "").strip()
            if not source or not target or not relation_type:
                preview.diagnostics.append({"code": "invalid_relation", "message": f"incomplete relation: {item}"})
                continue
            if source not in entity_types or target not in entity_types:
                preview.diagnostics.append({
                    "code": "missing_endpoint",
                    "message": f"relation endpoint not in entities: {source} --{relation_type}--> {target}",
                })
                continue
            ok, reason = validate_relation(entity_types[source], relation_type, entity_types[target])
            if not ok:
                preview.diagnostics.append({
                    "code": "illegal_relation",
                    "message": reason or f"{source} --{relation_type}--> {target}",
                })
                continue
            evidence = note or f"product_backbone:{source_ref}:relation:{source}:{relation_type}:{target}"
            preview.relations.append({
                "source_name": source,
                "relation_type": relation_type,
                "target_name": target,
                "created_by": SEED_CREATED_BY,
                "confidence": 1.0,
                "evidence_text": evidence,
            })
        return preview

    def _stage_candidate(
        self,
        db: RelationalDB,
        batch_id: str,
        kind: str,
        payload: dict,
        evidence: str,
        review_status: str,
    ) -> int:
        fingerprint = hashlib.sha256(
            json.dumps([kind, self._identity_payload(kind, payload)], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        candidate_id = db.add_extraction_candidate(batch_id, kind, fingerprint, payload, "", evidence)
        if review_status == "approved":
            db.review_extraction_candidates(batch_id, [candidate_id], "approved")
        return 1

    @staticmethod
    def _identity_payload(kind: str, payload: dict) -> dict:
        keys_by_kind = {
            "entity": ("name", "entity_type"),
            "alias": ("entity_name", "alias"),
            "relation": ("source_name", "relation_type", "target_name"),
        }
        return {key: payload.get(key) for key in keys_by_kind[kind]}
