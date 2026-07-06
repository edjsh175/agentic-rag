"""Knowledge Graph service layer to encapsulate DB & vector store interactions."""
import logging
from typing import Optional

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.chunk_admin import ChunkAdminService
from rag_knowledge.models.api import (
    EntityTypeEnum,
    RelationTypeEnum,
    LinkTypeEnum,
    DocCategoryEnum,
    EntityCreateResponse,
    EntityResponse,
    RelationResponse,
    EntityChunkLinkResponse,
    GraphNode,
    GraphEdge,
    GraphDataResponse,
    EntityChunkDetailResponse,
)

logger = logging.getLogger(__name__)


class KnowledgeGraphService:
    def __init__(self):
        self.db = RelationalDB()
        self.vector_store = VectorStore()

    def list_graph_data(self, doc_category: Optional[str] = None) -> GraphDataResponse:
        """获取图谱数据（节点与边），支持按分类过滤节点，自动去除孤儿边。"""
        # Fetch all entities
        raw_nodes = self.db.list_entities()
        
        # Filter nodes if doc_category is provided
        nodes = []
        node_ids = set()
        for e in raw_nodes:
            # Skip if doc_category filter is active and doesn't match
            if doc_category and e.get("doc_category") != doc_category:
                continue
            
            nodes.append(GraphNode(
                id=e["id"],
                label=e["name"],
                type=e["entity_type"],
                doc_category=e.get("doc_category")
            ))
            node_ids.add(e["id"])

        # Fetch all relations
        raw_edges = self.db.list_relations()
        
        # Filter edges to prevent orphan edges
        edges = []
        for r in raw_edges:
            if r["source_id"] in node_ids and r["target_id"] in node_ids:
                edges.append(GraphEdge(
                    id=r["id"],
                    source=r["source_id"],
                    target=r["target_id"],
                    label=r["relation_type"]
                ))

        return GraphDataResponse(nodes=nodes, edges=edges)

    def create_entity(self, name: str, entity_type: EntityTypeEnum, 
                      doc_category: Optional[DocCategoryEnum] = None) -> EntityCreateResponse:
        """创建实体。同名实体已存在时，不重复创建，直接返回已有实体信息，并且 created=False。"""
        name_stripped = name.strip()
        if not name_stripped:
            raise ValueError("Entity name cannot be empty")
            
        existing = self.db.get_entity_by_name(name_stripped)
        if existing:
            return EntityCreateResponse(
                id=existing["id"],
                name=existing["name"],
                entity_type=existing["entity_type"],
                doc_category=existing.get("doc_category"),
                created_by=existing["created_by"],
                created_at=existing["created_at"],
                created=False
            )

        # Create new entity
        doc_cat_str = doc_category.value if doc_category else ""
        eid = self.db.create_entity(
            name=name_stripped,
            entity_type=entity_type.value,
            doc_category=doc_cat_str,
            created_by="admin"
        )
        
        created = self.db.get_entity(eid)
        if not created:
            raise RuntimeError("Failed to retrieve created entity")
            
        return EntityCreateResponse(
            id=created["id"],
            name=created["name"],
            entity_type=created["entity_type"],
            doc_category=created.get("doc_category"),
            created_by=created["created_by"],
            created_at=created["created_at"],
            created=True
        )

    def update_entity(self, entity_id: str, name: Optional[str] = None, 
                      entity_type: Optional[EntityTypeEnum] = None, 
                      doc_category: Optional[DocCategoryEnum] = None) -> EntityResponse:
        """更新实体属性。如果改名冲突，抛出 ValueError。"""
        existing = self.db.get_entity(entity_id)
        if not existing:
            raise KeyError("Entity not found")

        # Validate name conflict if changing name
        name_stripped = name.strip() if name is not None else None
        if name_stripped:
            conflict = self.db.get_entity_by_name(name_stripped)
            if conflict and conflict["id"] != entity_id:
                raise ValueError("Entity name already exists")

        # Perform update
        # db.update_entity only updates non-empty strings.
        self.db.update_entity(
            entity_id=entity_id,
            name=name_stripped or "",
            entity_type=entity_type.value if entity_type else "",
            doc_category=doc_category.value if doc_category else ""
        )

        updated = self.db.get_entity(entity_id)
        if not updated:
            raise RuntimeError("Failed to retrieve updated entity")

        return EntityResponse(
            id=updated["id"],
            name=updated["name"],
            entity_type=updated["entity_type"],
            doc_category=updated.get("doc_category"),
            created_by=updated["created_by"],
            created_at=updated["created_at"]
        )

    def delete_entity(self, entity_id: str) -> bool:
        """级联删除实体，返回 True 表示操作完成 (幂等)。"""
        # RelationalDB handles cascade deleting of relations and links
        self.db.delete_entity(entity_id)
        return True

    def create_relation(self, source_id: str, target_id: str, 
                        relation_type: RelationTypeEnum) -> RelationResponse:
        """创建关系。若源或目标实体不存在返回 KeyError；若自环返回 ValueError；若重复返回 created=False。"""
        if source_id == target_id:
            raise ValueError("Self-loop relations are not allowed")

        # Verify source and target exist
        source = self.db.get_entity(source_id)
        target = self.db.get_entity(target_id)
        if not source or not target:
            raise KeyError("Source or target entity not found")

        # Check duplicate
        existing = self.db.get_relation_by_details(source_id, target_id, relation_type.value)
        if existing:
            return RelationResponse(
                id=existing["id"],
                source_id=existing["source_id"],
                target_id=existing["target_id"],
                relation_type=existing["relation_type"],
                created_by=existing["created_by"],
                created_at=existing["created_at"],
                created=False
            )

        rid = self.db.create_relation(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type.value,
            created_by="admin"
        )
        if not rid:
            raise RuntimeError("Failed to create relation")

        with self.db._get_conn() as conn:
            row = conn.execute("SELECT * FROM relations WHERE id = ?", (rid,)).fetchone()
            if not row:
                raise RuntimeError("Failed to retrieve created relation")
            created_relation = dict(row)

        return RelationResponse(
            id=created_relation["id"],
            source_id=created_relation["source_id"],
            target_id=created_relation["target_id"],
            relation_type=created_relation["relation_type"],
            created_by=created_relation["created_by"],
            created_at=created_relation["created_at"],
            created=True
        )

    def delete_relation(self, relation_id: str) -> bool:
        """删除关系，返回 True 表示操作完成 (幂等)。"""
        self.db.delete_relation(relation_id)
        return True

    def link_entity_chunk(self, entity_id: str, chunk_id: str, 
                          link_type: LinkTypeEnum) -> EntityChunkLinkResponse:
        """关联实体与知识块。验证实体和知识块是否存在。"""
        # Verify entity exists
        entity = self.db.get_entity(entity_id)
        if not entity:
            raise KeyError("Entity not found")

        # Verify chunk exists using file index lookup
        files = ChunkAdminService()._file_lookup()
        if chunk_id not in files:
            raise KeyError("Chunk not found")

        # Check if duplicate link exists
        existing = self.db.get_link_by_entity_chunk(entity_id, chunk_id)
        if existing:
            return EntityChunkLinkResponse(
                id=existing["id"],
                entity_id=existing["entity_id"],
                chunk_id=existing["chunk_id"],
                link_type=existing["link_type"],
                created_at=existing["created_at"],
                created=False
            )

        # Create new link
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
            created=True
        )

    def unlink_entity_chunk(self, entity_id: str, chunk_id: str) -> bool:
        """移除实体与知识块的关联，返回 True 表示操作完成 (幂等)。"""
        self.db.delete_link_by_entity_chunk(entity_id, chunk_id)
        return True

    def list_entity_chunks(self, entity_id: str) -> list[EntityChunkDetailResponse]:
        """查询关联特定实体的所有知识块列表。"""
        entity = self.db.get_entity(entity_id)
        if not entity:
            raise KeyError("Entity not found")

        links = self.db.list_links(entity_id=entity_id)
        if not links:
            return []

        # Get chunk details from vector store
        chunk_ids = [l["chunk_id"] for l in links]
        try:
            collection = self.vector_store.get_chroma()._collection
            res = collection.get(ids=chunk_ids, include=["documents", "metadatas"])
        except Exception as exc:
            logger.warning("Failed to query chunks from ChromaDB: %s", exc)
            res = {}

        # Build mappings
        ids = res.get("ids") or []
        documents = res.get("documents") or []
        metadatas = res.get("metadatas") or []
        chroma_chunks = {
            chunk_id: {"content": content, "metadata": meta or {}}
            for chunk_id, content, meta in zip(ids, documents, metadatas)
        }

        # Look up file names and metadata using existing index helper
        files = ChunkAdminService()._file_lookup()
        
        details = []
        for link in links:
            chunk_id = link["chunk_id"]
            chroma_data = chroma_chunks.get(chunk_id, {})
            meta = chroma_data.get("metadata", {})
            file_data = files.get(chunk_id, {})

            # Replicate metadata lookup logic
            source_name = str(meta.get("source") or file_data.get("file_name") or "")
            file_name = str(file_data.get("file_name") or source_name or "Unknown File")
            section_title = str(meta.get("section_title") or "")
            content = str(chroma_data.get("content") or "")

            details.append(EntityChunkDetailResponse(
                chunk_id=chunk_id,
                file_name=file_name,
                section_title=section_title,
                link_type=link["link_type"],
                content_preview=content[:80],
                content=content
            ))

        return details
