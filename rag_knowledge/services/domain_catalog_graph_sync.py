"""Stage domain catalog seeds into reviewable graph candidates."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.domain_catalog import CatalogSeedEntity, DomainCatalogLoader
from rag_knowledge.services.graph_extraction.pipeline import BuildBatchResult

SEED_CREATED_BY = "seed:domain_catalog"


@dataclass
class CatalogSeedPreview:
    entities: list[CatalogSeedEntity] = field(default_factory=list)
    aliases: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "entities": [asdict(item) for item in self.entities],
            "aliases": self.aliases,
            "relations": self.relations,
        }


class DomainCatalogGraphSyncService:
    def __init__(self, db: RelationalDB | None = None, catalog: DomainCatalogLoader | None = None):
        self.db = db
        self._catalog = catalog or DomainCatalogLoader()

    def preview(self) -> dict:
        return self._build_preview().to_dict()

    def build_batch(self, review_status: str = "pending") -> BuildBatchResult:
        if review_status not in {"pending", "approved"}:
            raise ValueError("review_status must be pending or approved")
        db = self.db or RelationalDB()
        preview = self._build_preview()
        payload = preview.to_dict()
        snapshot = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        batch_id = db.create_extraction_batch(
            "domain_catalog_seed",
            {"review_status": review_status},
            snapshot,
        )
        counts = {"entity": 0, "alias": 0, "relation": 0}
        for entity in preview.entities:
            evidence = f"domain_catalog:{entity.category}:{entity.name}"
            entity_payload = {
                "name": entity.name,
                "entity_type": entity.entity_type,
                "created_by": SEED_CREATED_BY,
                "evidence_text": evidence,
            }
            counts["entity"] += self._stage_candidate(db, batch_id, "entity", entity_payload, evidence, review_status)
            for alias in entity.aliases:
                alias_payload = {
                    "entity_name": entity.name,
                    "alias": alias,
                    "created_by": SEED_CREATED_BY,
                    "evidence_text": evidence,
                }
                counts["alias"] += self._stage_candidate(db, batch_id, "alias", alias_payload, evidence, review_status)
            if entity.belongs_to:
                relation_payload = {
                    "source_name": entity.name,
                    "relation_type": "belongs_to",
                    "target_name": entity.belongs_to,
                    "created_by": SEED_CREATED_BY,
                    "evidence_text": evidence,
                }
                counts["relation"] += self._stage_candidate(db, batch_id, "relation", relation_payload, evidence, review_status)
            for target in entity.different_from:
                relation_payload = {
                    "source_name": entity.name,
                    "relation_type": "different_from",
                    "target_name": target,
                    "created_by": SEED_CREATED_BY,
                    "evidence_text": evidence,
                }
                counts["relation"] += self._stage_candidate(db, batch_id, "relation", relation_payload, evidence, review_status)
        with db._get_conn() as conn:
            conn.execute(
                "UPDATE extraction_batches SET stats_json = ? WHERE id = ?",
                (json.dumps(counts, ensure_ascii=False, sort_keys=True), batch_id),
            )
        if review_status == "approved":
            db.set_extraction_batch_status(batch_id, "approved")
        return BuildBatchResult(batch_id, counts)

    def _build_preview(self) -> CatalogSeedPreview:
        preview = CatalogSeedPreview()
        for entity in self._catalog.seeds():
            preview.entities.append(entity)
            evidence = f"domain_catalog:{entity.category}:{entity.name}"
            for alias in entity.aliases:
                preview.aliases.append({
                    "entity_name": entity.name,
                    "alias": alias,
                    "created_by": SEED_CREATED_BY,
                    "evidence_text": evidence,
                })
            if entity.belongs_to:
                preview.relations.append({
                    "source_name": entity.name,
                    "relation_type": "belongs_to",
                    "target_name": entity.belongs_to,
                    "created_by": SEED_CREATED_BY,
                    "evidence_text": evidence,
                })
            for target in entity.different_from:
                preview.relations.append({
                    "source_name": entity.name,
                    "relation_type": "different_from",
                    "target_name": target,
                    "created_by": SEED_CREATED_BY,
                    "evidence_text": evidence,
                })
        return preview

    def _stage_candidate(self, db: RelationalDB, batch_id: str, kind: str, payload: dict, evidence: str, review_status: str) -> int:
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
