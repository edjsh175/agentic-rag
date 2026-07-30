"""Service class for managing knowledge graph admin operations."""
from __future__ import annotations

import logging
from typing import Optional

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.chunk_admin import ChunkAdminService
from rag_knowledge.services.product_backbone_live_sync import ProductBackboneLiveSyncService
from rag_knowledge.models.api import (
    GraphDataResponse,
    GraphNode,
    GraphEdge,
    EntityTypeEnum,
    RelationTypeEnum,
    LinkTypeEnum,
    DocCategoryEnum,
    EntityCreateResponse,
    EntityResponse,
    RelationResponse,
    EntityChunkLinkResponse,
    EntityChunkDetailResponse,
    GraphAliasItem,
)

logger = logging.getLogger(__name__)


class KnowledgeGraphService:
    """Business logic for Knowledge Graph administration."""

    def __init__(self):
        self.db = RelationalDB()
        self._vector_store = None
        self._backbone_live_sync = ProductBackboneLiveSyncService(db=self.db)

    def _sync_backbone_json(self, summary: dict | None) -> None:
        if summary:
            logger.info(
                "backbone JSON synced from live | entities=%s relations=%s",
                summary.get("entities"),
                summary.get("relations"),
            )

    @property
    def vector_store(self):
        if self._vector_store is None:
            self._vector_store = VectorStore()
        return self._vector_store

    def list_graph_data(self, doc_category: Optional[str] = None) -> GraphDataResponse:
        """Fetch all entities and relations, filter orphans if doc_category is applied."""
        entities = self.db.list_entities()
        relations = self.db.list_relations()

        backbone_names = self._backbone_live_sync.backbone_entity_names()

        if doc_category:
            entities = [e for e in entities if e["doc_category"] == doc_category]

        nodes = []
        for e in entities:
            created_by = e.get("created_by") or None
            if e.get("name") in backbone_names and not (created_by and created_by.startswith("seed:product_backbone")):
                created_by = "seed:product_backbone"
            nodes.append(
                GraphNode(
                    id=e["id"],
                    label=e["name"],
                    type=e["entity_type"],
                    doc_category=e.get("doc_category") or None,
                    canonical_name=e.get("canonical_name") or None,
                    description=e.get("description") or None,
                    properties_json=e.get("properties_json") or None,
                    confidence=e.get("confidence"),
                    review_status=e.get("review_status") or None,
                    created_by=created_by,
                )
            )

        allowed_ids = {n.id for n in nodes}
        edges = []
        for r in relations:
            # Prevent orphan edges
            if r["source_entity_id"] in allowed_ids and r["target_entity_id"] in allowed_ids:
                edges.append(
                    GraphEdge(
                        id=r["id"],
                        source=r["source_entity_id"],
                        target=r["target_entity_id"],
                        label=r["relation_type"],
                        confidence=r.get("confidence"),
                        review_status=r.get("review_status") or None,
                        source_chunk_id=r.get("source_chunk_id") or None,
                        evidence_text=r.get("evidence_text") or None,
                    )
                )

        return GraphDataResponse(nodes=nodes, edges=edges)

    def create_entity(
        self,
        name: str,
        entity_type: EntityTypeEnum,
        doc_category: Optional[DocCategoryEnum] = None,
        canonical_name: Optional[str] = None,
        description: Optional[str] = None,
        properties_json: Optional[str] = None,
        confidence: Optional[float] = None,
        review_status: Optional[str] = None,
    ) -> EntityCreateResponse:
        name_stripped = name.strip()
        if not name_stripped:
            raise ValueError("Entity name cannot be empty")

        existing = self.db.get_entity_by_name(name_stripped)
        if existing:
            return EntityCreateResponse(
                id=existing["id"],
                name=existing["name"],
                entity_type=existing["entity_type"],
                doc_category=existing.get("doc_category") or None,
                canonical_name=existing.get("canonical_name") or None,
                description=existing.get("description") or None,
                properties_json=existing.get("properties_json") or None,
                confidence=existing.get("confidence"),
                review_status=existing.get("review_status") or None,
                created_by=existing["created_by"],
                created_at=existing["created_at"],
                created=False,
            )

        doc_cat_str = doc_category.value if doc_category else ""
        eid = self.db.create_entity(
            name=name_stripped,
            entity_type=entity_type.value,
            doc_category=doc_cat_str,
            canonical_name=canonical_name or "",
            description=description or "",
            properties_json=properties_json or "{}",
            confidence=confidence if confidence is not None else 1.0,
            review_status=review_status or "approved",
            created_by="admin",
        )

        created = self.db.get_entity(eid)
        if not created:
            raise RuntimeError("Failed to retrieve created entity")

        try:
            self._sync_backbone_json(self._backbone_live_sync.sync_after_entity_change(eid))
        except Exception as exc:
            logger.warning("backbone JSON sync after create_entity failed: %s", exc)

        return EntityCreateResponse(
            id=created["id"],
            name=created["name"],
            entity_type=created["entity_type"],
            doc_category=created.get("doc_category") or None,
            canonical_name=created.get("canonical_name") or None,
            description=created.get("description") or None,
            properties_json=created.get("properties_json") or None,
            confidence=created.get("confidence"),
            review_status=created.get("review_status") or None,
            created_by=created["created_by"],
            created_at=created["created_at"],
            created=True,
        )

    def update_entity(
        self,
        entity_id: str,
        name: Optional[str] = None,
        entity_type: Optional[EntityTypeEnum] = None,
        doc_category: Optional[DocCategoryEnum] = None,
        canonical_name: Optional[str] = None,
        description: Optional[str] = None,
        properties_json: Optional[str] = None,
        confidence: Optional[float] = None,
        review_status: Optional[str] = None,
    ) -> EntityResponse:
        existing = self.db.get_entity(entity_id)
        if not existing:
            raise KeyError("Entity not found")

        name_stripped = name.strip() if name is not None else None
        if name_stripped:
            conflict = self.db.get_entity_by_name(name_stripped)
            if conflict and conflict["id"] != entity_id:
                raise ValueError("Entity name already exists")

        self.db.update_entity(
            entity_id=entity_id,
            name=name_stripped or "",
            entity_type=entity_type.value if entity_type else "",
            doc_category=doc_category.value if doc_category else "",
            canonical_name=canonical_name or "",
            description=description or "",
            properties_json=properties_json or "",
            confidence=confidence,
            review_status=review_status or "",
        )

        updated = self.db.get_entity(entity_id)
        if not updated:
            raise RuntimeError("Failed to retrieve updated entity")

        try:
            self._sync_backbone_json(self._backbone_live_sync.sync_after_entity_change(entity_id))
        except Exception as exc:
            logger.warning("backbone JSON sync after update_entity failed: %s", exc)

        return EntityResponse(
            id=updated["id"],
            name=updated["name"],
            entity_type=updated["entity_type"],
            doc_category=updated.get("doc_category") or None,
            canonical_name=updated.get("canonical_name") or None,
            description=updated.get("description") or None,
            properties_json=updated.get("properties_json") or None,
            confidence=updated.get("confidence"),
            review_status=updated.get("review_status") or None,
            created_by=updated["created_by"],
            created_at=updated["created_at"],
        )

    def delete_entity(self, entity_id: str) -> bool:
        """Cascade deletes an entity."""
        existing = self.db.get_entity(entity_id)
        touched_backbone = False
        if existing:
            names = self._backbone_live_sync.backbone_entity_names()
            touched_backbone = self._backbone_live_sync.entity_is_backbone(existing, names=names)
        self.db.delete_entity(entity_id)
        if touched_backbone:
            try:
                self._sync_backbone_json(self._backbone_live_sync.export_from_live())
            except Exception as exc:
                logger.warning("backbone JSON sync after delete_entity failed: %s", exc)
        return True

    def create_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: RelationTypeEnum,
        properties_json: Optional[str] = None,
        confidence: Optional[float] = None,
        evidence_text: Optional[str] = None,
        source_chunk_id: Optional[str] = None,
        review_status: Optional[str] = None,
    ) -> RelationResponse:
        if source_id == target_id:
            raise ValueError("Self-loop relations are not allowed")

        source = self.db.get_entity(source_id)
        target = self.db.get_entity(target_id)
        if not source or not target:
            raise KeyError("Source or target entity not found")

        existing = self.db.get_relation_by_details(source_id, target_id, relation_type.value)
        if existing:
            return RelationResponse(
                id=existing["id"],
                source_id=existing["source_entity_id"],
                target_id=existing["target_entity_id"],
                relation_type=existing["relation_type"],
                properties_json=existing.get("properties_json") or None,
                confidence=existing.get("confidence"),
                evidence_text=existing.get("evidence_text") or None,
                source_chunk_id=existing.get("source_chunk_id") or None,
                review_status=existing.get("review_status") or None,
                created_by=existing["created_by"],
                created_at=existing["created_at"],
                created=False,
            )

        rid = self.db.create_relation(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type.value,
            properties_json=properties_json or "{}",
            confidence=confidence if confidence is not None else 1.0,
            evidence_text=evidence_text or "",
            source_chunk_id=source_chunk_id or "",
            review_status=review_status or "approved",
            created_by="admin",
        )
        if not rid:
            raise RuntimeError("Failed to create relation")

        with self.db._get_conn() as conn:
            row = conn.execute("SELECT * FROM relations WHERE id = ?", (rid,)).fetchone()
            if not row:
                raise RuntimeError("Failed to retrieve created relation")
            created_relation = dict(row)

        try:
            self._sync_backbone_json(
                self._backbone_live_sync.sync_after_relation_change(
                    relation_id=rid,
                    source_id=source_id,
                    target_id=target_id,
                )
            )
        except Exception as exc:
            logger.warning("backbone JSON sync after create_relation failed: %s", exc)

        return RelationResponse(
            id=created_relation["id"],
            source_id=created_relation["source_entity_id"],
            target_id=created_relation["target_entity_id"],
            relation_type=created_relation["relation_type"],
            properties_json=created_relation.get("properties_json") or None,
            confidence=created_relation.get("confidence"),
            evidence_text=created_relation.get("evidence_text") or None,
            source_chunk_id=created_relation.get("source_chunk_id") or None,
            review_status=created_relation.get("review_status") or None,
            created_by=created_relation["created_by"],
            created_at=created_relation["created_at"],
            created=True,
        )

    def delete_relation(self, relation_id: str) -> bool:
        """Idempotent relation delete."""
        before = None
        with self.db._get_conn() as conn:
            row = conn.execute("SELECT * FROM relations WHERE id = ?", (relation_id,)).fetchone()
            if row:
                before = dict(row)
        self.db.delete_relation(relation_id)
        try:
            self._sync_backbone_json(
                self._backbone_live_sync.sync_after_relation_change(
                    relation_id=None,
                    relation_before=before,
                )
            )
        except Exception as exc:
            logger.warning("backbone JSON sync after delete_relation failed: %s", exc)
        return True

    def link_entity_chunk(self, entity_id: str, chunk_id: str, link_type: LinkTypeEnum) -> EntityChunkLinkResponse:
        # Verify entity exists
        entity = self.db.get_entity(entity_id)
        if not entity:
            raise KeyError("Entity not found")

        # Verify chunk exists
        files = ChunkAdminService()._file_lookup()
        if chunk_id not in files:
            raise KeyError("Chunk not found")

        existing = self.db.get_link_by_entity_chunk(entity_id, chunk_id)
        if existing:
            return EntityChunkLinkResponse(
                id=existing["id"],
                entity_id=existing["entity_id"],
                chunk_id=existing["chunk_id"],
                link_type=existing["link_type"],
                created_at=existing["created_at"],
                created=False,
            )

        lid = self.db.create_link(entity_id, chunk_id, link_type.value)
        if not lid:
            raise RuntimeError("Failed to create link")

        created = self.db.get_link_by_entity_chunk(entity_id, chunk_id)
        if not created:
            raise RuntimeError("Failed to retrieve created link")

        return EntityChunkLinkResponse(
            id=created["id"],
            entity_id=created["entity_id"],
            chunk_id=created["chunk_id"],
            link_type=created["link_type"],
            created_at=created["created_at"],
            created=True,
        )

    def unlink_entity_chunk(self, entity_id: str, chunk_id: str) -> bool:
        self.db.delete_link_by_entity_chunk(entity_id, chunk_id)
        return True

    def list_entity_aliases(self, entity_id: str) -> list[GraphAliasItem]:
        entity = self.db.get_entity(entity_id)
        if not entity:
            raise KeyError("Entity not found")
        return [
            GraphAliasItem(
                id=row["id"],
                entity_id=row["entity_id"],
                alias=row["alias"],
                confidence=row.get("confidence"),
                source_chunk_id=row.get("source_chunk_id") or None,
                evidence_text=row.get("evidence_text") or None,
                review_status=row.get("review_status") or None,
                created_at=row["created_at"],
            )
            for row in self.db.list_aliases(entity_id)
        ]

    def create_entity_alias(
        self,
        entity_id: str,
        alias: str,
        confidence: Optional[float] = None,
        evidence_text: Optional[str] = None,
        source_chunk_id: Optional[str] = None,
        review_status: Optional[str] = None,
    ) -> GraphAliasItem:
        entity = self.db.get_entity(entity_id)
        if not entity:
            raise KeyError("Entity not found")

        alias_value = alias.strip()
        if not alias_value:
            raise ValueError("Alias cannot be empty")

        for row in self.db.list_aliases(entity_id):
            if row["alias"] == alias_value:
                return GraphAliasItem(
                    id=row["id"],
                    entity_id=row["entity_id"],
                    alias=row["alias"],
                    confidence=row.get("confidence"),
                    source_chunk_id=row.get("source_chunk_id") or None,
                    evidence_text=row.get("evidence_text") or None,
                    review_status=row.get("review_status") or None,
                    created_at=row["created_at"],
                    created=False,
                )

        alias_id = self.db.create_alias(
            entity_id=entity_id,
            alias=alias_value,
            confidence=confidence if confidence is not None else 1.0,
            source_chunk_id=source_chunk_id or "",
            evidence_text=evidence_text or "",
            review_status=review_status or "approved",
        )
        if not alias_id:
            raise RuntimeError("Failed to create alias")

        created = next((row for row in self.db.list_aliases(entity_id) if row["id"] == alias_id), None)
        if not created:
            raise RuntimeError("Failed to retrieve created alias")

        try:
            self._sync_backbone_json(self._backbone_live_sync.sync_after_alias_change(entity_id))
        except Exception as exc:
            logger.warning("backbone JSON sync after create_entity_alias failed: %s", exc)

        return GraphAliasItem(
            id=created["id"],
            entity_id=created["entity_id"],
            alias=created["alias"],
            confidence=created.get("confidence"),
            source_chunk_id=created.get("source_chunk_id") or None,
            evidence_text=created.get("evidence_text") or None,
            review_status=created.get("review_status") or None,
            created_at=created["created_at"],
            created=True,
        )

    def delete_alias(self, alias_id: str) -> bool:
        entity_id = ""
        with self.db._get_conn() as conn:
            row = conn.execute("SELECT entity_id FROM aliases WHERE id = ?", (alias_id,)).fetchone()
            if row:
                entity_id = str(row["entity_id"] or "")
        self.db.delete_alias(alias_id)
        if entity_id:
            try:
                self._sync_backbone_json(self._backbone_live_sync.sync_after_alias_change(entity_id))
            except Exception as exc:
                logger.warning("backbone JSON sync after delete_alias failed: %s", exc)
        return True

    def list_entity_chunks(self, entity_id: str) -> list[EntityChunkDetailResponse]:
        entity = self.db.get_entity(entity_id)
        if not entity:
            raise KeyError("Entity not found")

        links = self.db.list_links(entity_id=entity_id)
        if not links:
            return []

        chunk_ids = [l["chunk_id"] for l in links]
        try:
            collection = self.vector_store.get_chroma()._collection
            res = collection.get(ids=chunk_ids, include=["documents", "metadatas"])
        except Exception as exc:
            logger.warning("Failed to query chunks from ChromaDB: %s", exc)
            res = {}

        ids = res.get("ids") or []
        documents = res.get("documents") or []
        metadatas = res.get("metadatas") or []
        chroma_chunks = {
            chunk_id: {"content": content, "metadata": meta or {}}
            for chunk_id, content, meta in zip(ids, documents, metadatas)
        }

        files = ChunkAdminService()._file_lookup()
        details = []
        for link in links:
            chunk_id = link["chunk_id"]
            chroma_data = chroma_chunks.get(chunk_id, {})
            meta = chroma_data.get("metadata", {})
            file_data = files.get(chunk_id, {})

            source_name = str(meta.get("source") or file_data.get("file_name") or "")
            file_name = str(file_data.get("file_name") or source_name or "Unknown File")
            section_title = str(meta.get("section_title") or "")
            content = str(chroma_data.get("content") or "")

            details.append(
                EntityChunkDetailResponse(
                    chunk_id=chunk_id,
                    file_name=file_name,
                    section_title=section_title,
                    link_type=link["link_type"],
                    content_preview=content[:80],
                    content=content,
                )
            )

        return details
