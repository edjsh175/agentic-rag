"""Multi-root GraphWorkingSet, GraphEntityState, GraphRelationCandidate and GraphBudget (PRD 2026-08-26)."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GraphEntityState:
    """State tracking for a single graph node in the multi-root working set."""

    entity_id: str
    canonical_name: str
    entity_type: str = ""
    depth_from_root: int = 0  # Local depth relative to origin_root
    origin_root: str = ""
    is_root: bool = False
    is_frontier: bool = True
    first_seen_via_relation_id: str = ""
    source: str = "bootstrap"
    evidence_status: str = "PENDING"

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "canonical_name": self.canonical_name,
            "entity_type": self.entity_type,
            "depth_from_root": self.depth_from_root,
            "origin_root": self.origin_root,
            "is_root": self.is_root,
            "is_frontier": self.is_frontier,
            "first_seen_via_relation_id": self.first_seen_via_relation_id,
            "source": self.source,
            "evidence_status": self.evidence_status,
        }


@dataclass
class GraphRelationCandidate:
    """Graph edge candidate in the working set, pending or passed relation admission."""

    relation_id: str
    source_name: str
    target_name: str
    relation_type: str
    source_entity_id: str = ""
    source_type: str = "Product"
    target_entity_id: str = ""
    target_type: str = "Product"
    review_status: str = "approved"
    confidence: float = 1.0
    graph_revision: str = "rev_v1"
    depth_from_root: int = 1
    origin_root: str = ""
    discovery_source: str = "bootstrap"  # bootstrap | depth_expansion | root_expansion
    discovery_path: tuple[str, ...] = ()
    evidence_status: str = "PENDING"
    evidence_reason: str = ""

    @property
    def relation_key(self) -> str:
        return f"{self.source_name} -[{self.relation_type}]-> {self.target_name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "relation_key": self.relation_key,
            "source_entity_id": self.source_entity_id,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "relation_type": self.relation_type,
            "target_entity_id": self.target_entity_id,
            "target_name": self.target_name,
            "target_type": self.target_type,
            "review_status": self.review_status,
            "confidence": self.confidence,
            "graph_revision": self.graph_revision,
            "depth_from_root": self.depth_from_root,
            "origin_root": self.origin_root,
            "discovery_source": self.discovery_source,
            "discovery_path": list(self.discovery_path),
            "evidence_status": self.evidence_status,
            "evidence_reason": self.evidence_reason,
        }


@dataclass
class GraphPathCandidate:
    """Multi-hop path candidate in the working set for candidate ranking and tracing."""

    path_id: str
    nodes: tuple[str, ...]
    edges: tuple[str, ...]
    length: int = 0
    origin_root: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_id": self.path_id,
            "nodes": list(self.nodes),
            "edges": list(self.edges),
            "length": self.length,
            "origin_root": self.origin_root,
        }


@dataclass
class GraphBudget:
    """Query-scoped graph execution budget independent from text retrieval budget."""

    bootstrap_calls: int = 0
    expansion_calls: int = 0
    entities_seen: int = 0
    relations_seen: int = 0
    max_hops_per_expansion: int = 2
    max_expansion_calls: int = 2
    max_entities_total: int = 24
    max_relations_total: int = 64
    max_total_depth: int = 3  # Per-root local depth cap

    @property
    def expansion_calls_used(self) -> int:
        return self.expansion_calls

    @property
    def entities_discovered(self) -> int:
        return self.entities_seen

    @property
    def relations_discovered(self) -> int:
        return self.relations_seen

    def remaining_expansion_calls(self) -> int:
        return max(0, self.max_expansion_calls - self.expansion_calls)

    def can_expand(self, hops: int = 1, target_depth: int = 1) -> bool:
        return (
            hops <= self.max_hops_per_expansion
            and target_depth <= self.max_total_depth
            and self.expansion_calls < self.max_expansion_calls
            and self.entities_seen < self.max_entities_total
            and self.relations_seen < self.max_relations_total
        )

    def consume_expansion(self, hops: int = 1, entities_discovered: int = 0, relations_discovered: int = 0) -> bool:
        if not self.can_expand(hops=hops):
            return False
        self.expansion_calls += 1
        self.entities_seen += entities_discovered
        self.relations_seen += relations_discovered
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "bootstrap_calls": self.bootstrap_calls,
            "expansion_calls": self.expansion_calls,
            "remaining_expansion_calls": self.remaining_expansion_calls(),
            "expansion_allowed": self.can_expand(),
            "entities_seen": self.entities_seen,
            "relations_seen": self.relations_seen,
            "max_expansion_calls": self.max_expansion_calls,
            "max_entities_total": self.max_entities_total,
            "max_relations_total": self.max_relations_total,
            "max_total_depth": self.max_total_depth,
        }


@dataclass
class GraphWorkingSet:
    """Agent Query 级一等多根局部图谱世界状态对象（Multi-root GraphWorkingSet）."""

    graph_scope_id: str = field(default_factory=lambda: f"gws_{uuid.uuid4().hex[:12]}")
    question_id: str = ""
    graph_revision: str = "rev_v1"

    exploration_roots: tuple[str, ...] = ()  # 探索起点集合（含 anchor 及后续授权扩展的 root）
    anchor_entities: tuple[str, ...] = ()    # 原始锚定实体集合

    entities: dict[str, GraphEntityState] = field(default_factory=dict)
    relations: dict[str, GraphRelationCandidate] = field(default_factory=dict)
    entity_chunk_links: dict[str, tuple[str, ...]] = field(default_factory=dict)
    paths: list[GraphPathCandidate] = field(default_factory=list)

    frontier_entity_ids: tuple[str, ...] = ()
    visited_entity_ids: set[str] = field(default_factory=set)
    visited_relation_ids: set[str] = field(default_factory=set)

    max_depth_reached: int = 0
    expansion_signatures: set[str] = field(default_factory=set)
    expansion_calls: int = 0
    bootstrap_status: str = "NOT_STARTED"  # NOT_STARTED | COMPLETE | EMPTY | UNAVAILABLE | DISABLED
    last_graph_status: str = "PROGRESS"    # PROGRESS | NO_PROGRESS | DENIED | ERROR

    budget: GraphBudget = field(default_factory=GraphBudget)
    admitted_relation_ids: set[str] = field(default_factory=set)

    def add_root(
        self,
        root_name: str,
        *,
        entity_id: str | None = None,
        entity_type: str = "Product",
    ) -> GraphEntityState:
        """Register a new exploration root into the working set (local depth = 0)."""
        norm_name = str(root_name or "").strip()
        if not norm_name:
            raise ValueError("root_name cannot be empty")
        if norm_name not in self.exploration_roots:
            self.exploration_roots = (*self.exploration_roots, norm_name)
        eid = str(entity_id or f"ent_{hashlib.sha256(norm_name.encode()).hexdigest()[:12]}")
        key = norm_name.casefold()
        existing = self.entities.get(key)
        if existing is not None:
            existing.is_root = True
            existing.origin_root = norm_name
            existing.depth_from_root = 0
            self.visited_entity_ids.add(existing.entity_id)
            return existing

        state = GraphEntityState(
            entity_id=eid,
            canonical_name=norm_name,
            entity_type=entity_type,
            depth_from_root=0,
            origin_root=norm_name,
            is_root=True,
            is_frontier=True,
        )
        self.entities[key] = state
        self.visited_entity_ids.add(eid)
        self.budget.entities_seen = len(self.entities)
        return state

    def add_entity(self, state: GraphEntityState) -> bool:
        """Add or update an entity state. Returns True if this is a newly discovered entity."""
        key = str(state.canonical_name or state.entity_id).strip().casefold()
        if not key:
            return False
        is_new = key not in self.entities
        if is_new:
            self.entities[key] = state
            self.budget.entities_seen = len(self.entities)
            if state.depth_from_root > self.max_depth_reached:
                self.max_depth_reached = state.depth_from_root
        else:
            existing = self.entities[key]
            # Update to shorter depth if discovered via a shorter path from a root
            if state.depth_from_root < existing.depth_from_root:
                existing.depth_from_root = state.depth_from_root
                existing.origin_root = state.origin_root
        return is_new

    def add_relation(self, rel: GraphRelationCandidate) -> bool:
        """Add a relation candidate. Returns True if newly added."""
        key = str(rel.relation_id or rel.relation_key).strip().casefold()
        if not key:
            return False
        is_new = key not in self.relations
        if is_new:
            self.relations[key] = rel
            self.visited_relation_ids.add(rel.relation_id)
            self.budget.relations_seen = len(self.relations)
        return is_new

    def recalculate_frontier(self) -> tuple[str, ...]:
        """Recalculate frontier entities (entities not yet fully expanded at max local depth)."""
        frontier: list[str] = []
        for state in self.entities.values():
            if state.depth_from_root < self.budget.max_total_depth and state.is_frontier:
                frontier.append(state.canonical_name)
        self.frontier_entity_ids = tuple(sorted(set(frontier)))
        return self.frontier_entity_ids

    compute_expansion_signature = None  # defined below
    def make_expansion_signature(
        self,
        start_entities: list[str] | tuple[str, ...],
        relation_types: list[str] | tuple[str, ...],
        direction: str,
        additional_hops: int,
    ) -> str:
        payload = {
            "start_entities": sorted(str(s).strip().casefold() for s in (start_entities or [])),
            "relation_types": sorted(str(r).strip().casefold() for r in (relation_types or [])),
            "direction": str(direction or "both").strip().lower(),
            "additional_hops": int(additional_hops or 1),
            "graph_revision": self.graph_revision,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def is_duplicate_expansion(self, signature: str) -> bool:
        return signature in self.expansion_signatures

    def record_expansion_signature(self, signature: str) -> None:
        self.expansion_signatures.add(signature)

    def is_signature_attempted(self, signature: str) -> bool:
        return self.is_duplicate_expansion(signature)

    def record_attempted_signature(self, signature: str) -> None:
        self.record_expansion_signature(signature)

    def record_relation_evidence(self, relation_id: str, verdict: str, reason: str = "") -> None:
        """Persist the graph evidence decision on its candidate; the ID set is only an index."""
        key = str(relation_id or "").strip()
        candidate = self.relations.get(key.casefold())
        if candidate is None:
            candidate = next(
                (item for item in self.relations.values() if item.relation_id == key),
                None,
            )
        if candidate is None:
            return
        candidate.evidence_status = str(verdict or "PENDING").strip().upper() or "PENDING"
        candidate.evidence_reason = str(reason or "").strip()
        if candidate.evidence_status == "PASS":
            self.admitted_relation_ids.add(candidate.relation_id)
        else:
            self.admitted_relation_ids.discard(candidate.relation_id)

    def mark_relation_admitted(self, relation_id: str) -> None:
        """Mark a relation as passed graph evidence."""
        self.record_relation_evidence(relation_id, "PASS")

    def add_entity_chunk_links(self, entity_name: str, chunk_ids: list[str] | tuple[str, ...]) -> None:
        key = str(entity_name or "").strip().casefold()
        values = tuple(dict.fromkeys(str(item or "").strip() for item in chunk_ids if str(item or "").strip()))
        if key and values:
            self.entity_chunk_links[key] = values

    def to_controller_state(self) -> dict[str, Any]:
        """Compact graph summary for Main ControllerState injection."""
        self.recalculate_frontier()
        return {
            "bootstrap_status": self.bootstrap_status,
            "roots": list(self.exploration_roots),
            "max_depth_reached": self.max_depth_reached,
            "frontier_entities": list(self.frontier_entity_ids),
            "entity_count": len(self.entities),
            "relation_count": len(self.relations),
            "admitted_relation_evidence_count": len(self.admitted_relation_ids),
            "remaining_expansion_calls": self.budget.remaining_expansion_calls(),
            "max_total_depth": self.budget.max_total_depth,
            "expansion_allowed": self.budget.can_expand(),
            "last_graph_status": self.last_graph_status,
        }

    @classmethod
    def from_trace(cls, data: dict[str, Any] | None) -> "GraphWorkingSet":
        """Restore query-scoped graph Working state across Reviewer resumes."""
        if not isinstance(data, dict):
            return cls()
        ws = cls(
            graph_scope_id=str(data.get("graph_scope_id") or f"gws_{uuid.uuid4().hex[:12]}"),
            question_id=str(data.get("question_id") or ""),
            graph_revision=str(data.get("graph_revision") or "rev_v1"),
            exploration_roots=tuple(data.get("exploration_roots") or ()),
            anchor_entities=tuple(data.get("anchor_entities") or ()),
            frontier_entity_ids=tuple(data.get("frontier_entities") or ()),
            visited_entity_ids=set(data.get("visited_entity_ids") or ()),
            visited_relation_ids=set(data.get("visited_relation_ids") or ()),
            admitted_relation_ids=set(data.get("admitted_relation_ids") or ()),
            max_depth_reached=int(data.get("max_depth_reached") or 0),
            expansion_calls=int(data.get("expansion_calls") or 0),
            bootstrap_status=str(data.get("bootstrap_status") or "NOT_STARTED"),
            last_graph_status=str(data.get("last_graph_status") or "PROGRESS"),
        )
        ws.expansion_signatures = set(data.get("expansion_signatures") or ())
        for key, raw in (data.get("entities") or {}).items():
            if not isinstance(raw, dict):
                continue
            ws.entities[str(key)] = GraphEntityState(
                entity_id=str(raw.get("entity_id") or ""),
                canonical_name=str(raw.get("canonical_name") or key),
                entity_type=str(raw.get("entity_type") or ""),
                depth_from_root=int(raw.get("depth_from_root") or 0),
                origin_root=str(raw.get("origin_root") or ""),
                is_root=bool(raw.get("is_root")),
                is_frontier=bool(raw.get("is_frontier", True)),
                first_seen_via_relation_id=str(raw.get("first_seen_via_relation_id") or ""),
                source=str(raw.get("source") or "bootstrap"),
                evidence_status=str(raw.get("evidence_status") or "PENDING"),
            )
        for key, raw in (data.get("relations") or {}).items():
            if not isinstance(raw, dict):
                continue
            ws.relations[str(key)] = GraphRelationCandidate(
                relation_id=str(raw.get("relation_id") or key),
                source_name=str(raw.get("source_name") or ""),
                target_name=str(raw.get("target_name") or ""),
                relation_type=str(raw.get("relation_type") or ""),
                source_entity_id=str(raw.get("source_entity_id") or ""),
                source_type=str(raw.get("source_type") or "Product"),
                target_entity_id=str(raw.get("target_entity_id") or ""),
                target_type=str(raw.get("target_type") or "Product"),
                review_status=str(raw.get("review_status") or "approved"),
                confidence=float(raw.get("confidence") or 1.0),
                graph_revision=str(raw.get("graph_revision") or ws.graph_revision),
                depth_from_root=int(raw.get("depth_from_root") or 1),
                origin_root=str(raw.get("origin_root") or ""),
                discovery_source=str(raw.get("discovery_source") or "bootstrap"),
                discovery_path=tuple(raw.get("discovery_path") or ()),
                evidence_status=str(raw.get("evidence_status") or "PENDING"),
                evidence_reason=str(raw.get("evidence_reason") or ""),
            )
        ws.entity_chunk_links = {
            str(key): tuple(value or ())
            for key, value in (data.get("entity_chunk_links") or {}).items()
        }
        ws.paths = [
            GraphPathCandidate(
                path_id=str(item.get("path_id") or ""),
                nodes=tuple(item.get("nodes") or ()),
                edges=tuple(item.get("edges") or ()),
                length=int(item.get("length") or 0),
                origin_root=str(item.get("origin_root") or ""),
            )
            for item in (data.get("paths") or []) if isinstance(item, dict)
        ]
        budget = data.get("budget") or {}
        if isinstance(budget, dict):
            ws.budget.bootstrap_calls = int(budget.get("bootstrap_calls") or 0)
            ws.budget.expansion_calls = int(budget.get("expansion_calls") or ws.expansion_calls)
            ws.budget.entities_seen = int(budget.get("entities_seen") or len(ws.entities))
            ws.budget.relations_seen = int(budget.get("relations_seen") or len(ws.relations))
            ws.budget.max_expansion_calls = int(budget.get("max_expansion_calls") or ws.budget.max_expansion_calls)
            ws.budget.max_entities_total = int(budget.get("max_entities_total") or ws.budget.max_entities_total)
            ws.budget.max_relations_total = int(budget.get("max_relations_total") or ws.budget.max_relations_total)
            ws.budget.max_total_depth = int(budget.get("max_total_depth") or ws.budget.max_total_depth)
        return ws

    def to_trace(self) -> dict[str, Any]:
        """Comprehensive trace serialization."""
        return {
            "graph_scope_id": self.graph_scope_id,
            "question_id": self.question_id,
            "graph_revision": self.graph_revision,
            "exploration_roots": list(self.exploration_roots),
            "anchor_entities": list(self.anchor_entities),
            "entities": {k: v.to_dict() for k, v in self.entities.items()},
            "relations": {k: v.to_dict() for k, v in self.relations.items()},
            "entity_chunk_links": {k: list(v) for k, v in self.entity_chunk_links.items()},
            "paths": [p.to_dict() for p in self.paths],
            "frontier_entities": list(self.frontier_entity_ids),
            "visited_entity_ids": list(self.visited_entity_ids),
            "visited_relation_ids": list(self.visited_relation_ids),
            "admitted_relation_ids": list(self.admitted_relation_ids),
            "max_depth_reached": self.max_depth_reached,
            "expansion_signatures": list(self.expansion_signatures),
            "expansion_calls": self.expansion_calls,
            "bootstrap_status": self.bootstrap_status,
            "last_graph_status": self.last_graph_status,
            "budget": self.budget.to_dict(),
        }


GraphWorkingSet.compute_expansion_signature = GraphWorkingSet.make_expansion_signature
GraphWorkingSet.is_signature_attempted = GraphWorkingSet.is_duplicate_expansion
GraphWorkingSet.record_attempted_signature = GraphWorkingSet.record_expansion_signature
