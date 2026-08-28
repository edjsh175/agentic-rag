"""Graph explorer engine: Runtime Bootstrap and expand_graph_scope (PRD 2026-08-26)."""
from __future__ import annotations

import logging
from typing import Any

from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.services.agent_orchestration.graph_admission import (
    GraphRelationAdmissionResult,
    GraphRelationAdmissionService,
)
from rag_knowledge.services.agent_orchestration.graph_working_set import (
    GraphBudget,
    GraphEntityState,
    GraphRelationCandidate,
    GraphWorkingSet,
)
from rag_knowledge.services.agent_orchestration.models import (
    EvidenceDelta,
    ToolObservation,
    ToolProgressStatus,
)

logger = logging.getLogger(__name__)


def _norm(name: Any) -> str:
    return normalize_entity_name(str(name or "")).casefold()


class GraphExplorer:
    """Manages Runtime 1-hop Bootstrap and Agent autonomous expand_graph_scope."""

    def __init__(self, db: Any = None, *, graph_db: Any = None, config: Any = None, admission_service: Any = None):
        self.db = graph_db if graph_db is not None else db
        self.config = config
        self.admission_service = admission_service

    def _find_entity(self, name: str) -> dict[str, Any] | None:
        if self.db is None:
            return None
        target_norm = _norm(name)
        if not target_norm:
            return None
        try:
            for item in self.db.list_entities(review_status="approved"):
                if _norm(item.get("canonical_name") or item.get("name")) == target_norm:
                    return item
        except Exception as exc:  # noqa: BLE001
            logger.debug("graph entity lookup failed: %s", exc)
        return None

    def bootstrap_anchor_graph(
        self,
        confirmed_roots: tuple[str, ...] | list[str] | None = None,
        *,
        question: str = "",
        question_id: str = "",
        confirmed_entities: tuple[str, ...] | list[str] | None = None,
        task_type: str | None = None,
        max_hops: int = 1,
        budget: GraphBudget | None = None,
        semantic_task: Any = None,
    ) -> tuple[GraphWorkingSet, list[GraphRelationCandidate], dict[str, GraphRelationAdmissionResult]]:
        roots = tuple(confirmed_roots or confirmed_entities or ())
        task_type = task_type or getattr(semantic_task, "task_type", None)
        """Runtime-owned default 1-hop Bootstrap across all confirmed entities (Multi-root)."""
        working_set = GraphWorkingSet(
            question_id=question_id,
            exploration_roots=roots,
            anchor_entities=roots,
            budget=budget or GraphBudget(),
        )
        working_set.budget.bootstrap_calls += 1

        if self.db is None:
            working_set.bootstrap_status = "UNAVAILABLE"
            return working_set, [], {}

        if not roots:
            working_set.bootstrap_status = "EMPTY"
            return working_set, [], {}

        working_set.bootstrap_status = "IN_PROGRESS"
        new_relations: list[GraphRelationCandidate] = []

        for root_name in roots:
            root_entity = self._find_entity(root_name)
            eid = str(root_entity.get("id") or "") if root_entity else None
            etype = str(root_entity.get("entity_type") or "Product") if root_entity else "Product"
            working_set.add_root(root_name, entity_id=eid, entity_type=etype)

            try:
                links = self.db.list_links(entity_id=eid)
                working_set.add_entity_chunk_links(
                    root_name,
                    [item.get("chunk_id") for item in links if isinstance(item, dict)],
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("failed to materialize entity chunk links for %s: %s", root_name, exc)

            if not eid:
                continue

            # Query 1-hop approved relations
            try:
                relations = self.db.list_relations(entity_id=eid, review_status="approved")
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to list relations for %s: %s", root_name, exc)
                relations = []

            for rel in relations:
                rid = str(rel.get("id") or "")
                rtype = str(rel.get("relation_type") or "")
                s_id = str(rel.get("source_entity_id") or "")
                s_name = str(rel.get("source_name") or rel.get("source_canonical_name") or s_id)
                s_type = str(rel.get("source_type") or "")
                t_id = str(rel.get("target_entity_id") or "")
                t_name = str(rel.get("target_name") or rel.get("target_canonical_name") or t_id)
                t_type = str(rel.get("target_type") or "")
                conf = float(rel.get("confidence", 1.0) or 1.0)

                # Determine neighbor
                is_source = (s_id == eid or _norm(s_name) == _norm(root_name))
                neighbor_id = t_id if is_source else s_id
                neighbor_name = t_name if is_source else s_name
                neighbor_type = t_type if is_source else s_type

                # Register neighbor entity state
                neighbor_state = GraphEntityState(
                    entity_id=neighbor_id,
                    canonical_name=neighbor_name,
                    entity_type=neighbor_type,
                    depth_from_root=1,
                    origin_root=root_name,
                    is_root=False,
                    is_frontier=True,
                    first_seen_via_relation_id=rid,
                )
                working_set.add_entity(neighbor_state)
                try:
                    links = self.db.list_links(entity_id=neighbor_id)
                    working_set.add_entity_chunk_links(
                        neighbor_name,
                        [item.get("chunk_id") for item in links if isinstance(item, dict)],
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("failed to materialize entity chunk links for %s: %s", neighbor_name, exc)

                rel_candidate = GraphRelationCandidate(
                    relation_id=rid,
                    source_entity_id=s_id,
                    source_name=s_name,
                    source_type=s_type,
                    relation_type=rtype,
                    target_entity_id=t_id,
                    target_name=t_name,
                    target_type=t_type,
                    review_status="approved",
                    confidence=conf,
                    graph_revision=working_set.graph_revision,
                    depth_from_root=1,
                    origin_root=root_name,
                    discovery_source="bootstrap",
                    discovery_path=(f"{root_name} --{rtype}--> {neighbor_name}",),
                )
                if working_set.add_relation(rel_candidate):
                    new_relations.append(rel_candidate)

        # Multi-root priority sorting: direct A <-> B link > matching task > strong candidate > other
        def _sort_key(r: GraphRelationCandidate) -> tuple[int, str]:
            s_norm, t_norm = _norm(r.source_name), _norm(r.target_name)
            roots_norm = {_norm(rt) for rt in roots}
            if s_norm in roots_norm and t_norm in roots_norm:
                return (0, r.relation_key)
            return (1, r.relation_key)

        new_relations.sort(key=_sort_key)

        # Run Relation Admission
        admissions = GraphRelationAdmissionService.admit_batch(
            question,
            new_relations,
            semantic_task=semantic_task,
            working_set=working_set,
            target_entities=list(roots),
            task_type=task_type,
        )
        for r in new_relations:
            adm = admissions.get(str(r.relation_id or r.relation_key))
            if adm:
                working_set.record_relation_evidence(r.relation_id, adm.verdict, adm.reason)

        working_set.bootstrap_status = "COMPLETE" if working_set.entities else "EMPTY"
        working_set.recalculate_frontier()
        admitted = [
            r for r in new_relations
            if r.relation_id in working_set.admitted_relation_ids
            or r.relation_key.casefold() in working_set.admitted_relation_ids
        ]
        return working_set, admitted, admissions

    def expand_graph_scope(
        self,
        working_set: GraphWorkingSet | None = None,
        start_entities: list[str] | tuple[str, ...] | None = None,
        *,
        relation_types: list[str] | tuple[str, ...] | None = None,
        direction: str = "both",
        additional_hops: int = 1,
        goal_entities: list[str] | tuple[str, ...] | None = None,
        admitted_text_entities: set[str] | tuple[str, ...] | None = None,
        stage1_confirmed_entities: set[str] | tuple[str, ...] | None = None,
        user_mentioned_entities: set[str] | tuple[str, ...] | list[str] | None = None,
        user_mentions: set[str] | tuple[str, ...] | list[str] | None = None,
        question: str = "",
        task_type: str | None = None,
        conversation_context: Any = None,
        admission_service: Any = None,
        semantic_task: Any = None,
    ) -> ToolObservation:
        """Agent-directed graph scope expansion supporting Depth and Root Expansion with 4-source authorization."""
        task_type = task_type or getattr(semantic_task, "task_type", None)
        if working_set is None:
            working_set = GraphWorkingSet()
        if start_entities is None:
            start_entities = []
        if not start_entities:
            return ToolObservation(
                tool="expand_graph_scope",
                ok=False,
                summary="缺少必填参数 start_entities",
                error="tool_missing_arg:start_entities",
                status=ToolProgressStatus.DENIED,
            )

        if self.db is None:
            return ToolObservation(
                tool="expand_graph_scope",
                ok=False,
                summary="知识图谱数据库不可用",
                error="graph_db_unavailable",
                status=ToolProgressStatus.ERROR,
            )

        # 1. Start Entity 4-Source Authorization Gate (PRD Section 22)
        valid_stage1 = {_norm(e) for e in (stage1_confirmed_entities or ()) if _norm(e)}
        valid_stage1.update(_norm(e) for e in working_set.anchor_entities if _norm(e))
        valid_graph = {_norm(e) for e in working_set.entities.keys() if _norm(e)}
        valid_text = {_norm(e) for e in (admitted_text_entities or ()) if _norm(e)}
        valid_user = {_norm(e) for e in (user_mentioned_entities or user_mentions or ()) if _norm(e)}

        all_authorized = valid_stage1 | valid_graph | valid_text | valid_user

        for start_ent in start_entities:
            if _norm(start_ent) not in all_authorized:
                return ToolObservation(
                    tool="expand_graph_scope",
                    ok=False,
                    summary=f"起点实体 '{start_ent}' 未通过合法来源授权校验",
                    error="graph_root_not_authorized",
                    status=ToolProgressStatus.DENIED,
                )

        # 2. Budget & Duplicate Call Check
        hops = max(1, min(int(additional_hops or 1), working_set.budget.max_total_depth))
        signature = working_set.make_expansion_signature(
            start_entities=start_entities,
            relation_types=relation_types or (),
            direction=direction,
            additional_hops=hops,
            goal_entities=goal_entities,
        )

        if working_set.is_duplicate_expansion(signature):
            working_set.last_graph_status = ToolProgressStatus.DENIED
            return ToolObservation(
                tool="expand_graph_scope",
                ok=False,
                summary="检测到完全重复的图谱扩展调用（相同起点、关系类型与跳数）",
                error="duplicate_graph_expansion",
                status=ToolProgressStatus.DENIED,
            )

        if not working_set.budget.can_expand():
            working_set.last_graph_status = ToolProgressStatus.DENIED
            return ToolObservation(
                tool="expand_graph_scope",
                ok=False,
                summary="图谱扩展预算已耗尽（达到最大调用次数或实体/关系统计上限）",
                error="graph_budget_exhausted",
                status=ToolProgressStatus.DENIED,
            )

        working_set.budget.consume_expansion()
        working_set.record_expansion_signature(signature)

        # 3. Perform Expansion Traversal (Multi-hop BFS)
        new_entities_count = 0
        new_relations_count = 0
        new_candidates: list[GraphRelationCandidate] = []
        filter_types = {str(r).strip().lower() for r in (relation_types or ()) if str(r).strip()}

        current_frontier_names: list[str] = list(start_entities)
        visited_in_call: set[str] = set()

        for hop_idx in range(1, hops + 1):
            next_frontier_names: list[str] = []
            for node_name in current_frontier_names:
                node_norm = _norm(node_name)
                if not node_norm:
                    continue
                is_existing_node = node_norm in working_set.entities

                if is_existing_node:
                    current_state = working_set.entities[node_norm]
                    root_origin = current_state.origin_root
                    base_depth = current_state.depth_from_root
                    discovery_source = "depth_expansion"
                else:
                    # Root Expansion: Create new local root with local depth = 0
                    root_origin = node_name
                    root_ent = self._find_entity(node_name)
                    eid = str(root_ent.get("id") or "") if root_ent else None
                    etype = str(root_ent.get("entity_type") or "Product") if root_ent else "Product"
                    current_state = working_set.add_root(node_name, entity_id=eid, entity_type=etype)
                    base_depth = 0
                    discovery_source = "root_expansion"
                    new_entities_count += 1

                if not current_state.entity_id:
                    root_ent = self._find_entity(node_name)
                    if root_ent:
                        current_state.entity_id = str(root_ent.get("id") or "")
                        current_state.entity_type = str(root_ent.get("entity_type") or current_state.entity_type or "Product")

                if not current_state.entity_id or current_state.entity_id in visited_in_call:
                    continue
                visited_in_call.add(current_state.entity_id)

                try:
                    relations = self.db.list_relations(
                        entity_id=current_state.entity_id,
                        review_status="approved",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("expand_graph_scope failed for %s: %s", node_name, exc)
                    relations = []

                for rel in relations:
                    rid = str(rel.get("id") or "")
                    rtype = str(rel.get("relation_type") or "")
                    if filter_types and rtype.lower() not in filter_types:
                        continue

                    s_id = str(rel.get("source_entity_id") or "")
                    s_name = str(rel.get("source_name") or rel.get("source_canonical_name") or s_id)
                    s_type = str(rel.get("source_type") or "")
                    t_id = str(rel.get("target_entity_id") or "")
                    t_name = str(rel.get("target_name") or rel.get("target_canonical_name") or t_id)
                    t_type = str(rel.get("target_type") or "")
                    conf = float(rel.get("confidence", 1.0) or 1.0)

                    is_source = (s_id == current_state.entity_id or _norm(s_name) == node_norm)
                    if direction == "out" and not is_source:
                        continue
                    if direction == "in" and is_source:
                        continue

                    neighbor_id = t_id if is_source else s_id
                    neighbor_name = t_name if is_source else s_name
                    neighbor_type = t_type if is_source else s_type
                    next_depth = base_depth + 1

                    if next_depth > working_set.budget.max_total_depth:
                        continue

                    neighbor_state = GraphEntityState(
                        entity_id=neighbor_id,
                        canonical_name=neighbor_name,
                        entity_type=neighbor_type,
                        depth_from_root=next_depth,
                        origin_root=root_origin,
                        is_root=False,
                        is_frontier=True,
                        first_seen_via_relation_id=rid,
                    )
                    is_new_ent = working_set.add_entity(neighbor_state)
                    if is_new_ent:
                        new_entities_count += 1
                        next_frontier_names.append(neighbor_name)
                    try:
                        links = self.db.list_links(entity_id=neighbor_id)
                        working_set.add_entity_chunk_links(
                            neighbor_name,
                            [item.get("chunk_id") for item in links if isinstance(item, dict)],
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("failed to materialize entity chunk links for %s: %s", neighbor_name, exc)

                    rel_candidate = GraphRelationCandidate(
                        relation_id=rid,
                        source_entity_id=s_id,
                        source_name=s_name,
                        source_type=s_type,
                        relation_type=rtype,
                        target_entity_id=t_id,
                        target_name=t_name,
                        target_type=t_type,
                        review_status="approved",
                        confidence=conf,
                        depth_from_root=next_depth,
                        origin_root=root_origin,
                        discovery_source=discovery_source,
                        discovery_path=(f"{node_name} --{rtype}--> {neighbor_name}",),
                    )
                    if working_set.add_relation(rel_candidate):
                        new_relations_count += 1
                        new_candidates.append(rel_candidate)

            current_frontier_names = next_frontier_names
            if not current_frontier_names:
                break

        working_set.recalculate_frontier()

        # 4. Check for Progress
        if new_entities_count == 0 and new_relations_count == 0:
            working_set.last_graph_status = ToolProgressStatus.NO_PROGRESS
            return ToolObservation(
                tool="expand_graph_scope",
                ok=True,
                summary="图谱扩展未发现任何新实体或已审核关系（NO_PROGRESS）",
                status=ToolProgressStatus.NO_PROGRESS,
                data={
                    "new_entities": 0,
                    "new_relations": 0,
                    "new_graph_evidence": 0,
                    "budget": working_set.budget.to_dict(),
                },
                evidence_delta=EvidenceDelta(status=ToolProgressStatus.NO_PROGRESS),
            )

        # 5. Admission on new relations
        admissions = GraphRelationAdmissionService.admit_batch(
            question,
            new_candidates,
            semantic_task=semantic_task,
            working_set=working_set,
            target_entities=list(working_set.exploration_roots),
            task_type=task_type,
        )
        new_evidence_count = 0
        admitted_keys: list[str] = []
        for r in new_candidates:
            adm = admissions.get(str(r.relation_id or r.relation_key))
            if adm:
                working_set.record_relation_evidence(r.relation_id, adm.verdict, adm.reason)
            if adm and adm.verdict == "PASS":
                new_evidence_count += 1
                admitted_keys.append(r.relation_key)

        working_set.last_graph_status = ToolProgressStatus.PROGRESS
        summary = f"图谱扩展发现 {new_entities_count} 个新实体、{new_relations_count} 条关系，其中 {new_evidence_count} 条准入为事实证据。"

        return ToolObservation(
            tool="expand_graph_scope",
            ok=True,
            summary=summary,
            status=ToolProgressStatus.PROGRESS,
            data={
                "new_entities": new_entities_count,
                "new_relations": new_relations_count,
                "new_graph_evidence": new_evidence_count,
                "max_depth_reached": working_set.max_depth_reached,
                "frontier_entities": list(working_set.frontier_entity_ids),
                "relation_summaries": [r.relation_key for r in new_candidates[:8]],
                "admitted_evidence_keys": admitted_keys,
                "admitted_relation_ids": list(working_set.admitted_relation_ids),
                "budget": working_set.budget.to_dict(),
            },
            evidence_delta=EvidenceDelta(
                new_entities=new_entities_count,
                new_relations=new_evidence_count,
                status=ToolProgressStatus.PROGRESS,
            ),
        )
