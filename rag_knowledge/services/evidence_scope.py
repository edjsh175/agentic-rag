"""
证据范围（EvidenceScope）与广义溯源（Provenance）治理模块。

提供系统级抽象：
1. BindingStrength: 主体绑定强度状态机（UNBOUND, INFERRED, CONFIRMED, EXPLICIT）；
2. ProvenancePath: 广义证据溯源链（直属 Chunk、图谱关联、文档归属、共享域）；
3. ScopePolicy: 范围治理与遍历预算策略（有界扩展约束）；
4. EvidenceScope: 证据范围契约对象（仅承担 Structural Eligibility 判定）；
5. ScopeResolver: 范围求解器，计算合法候选实体与溯源路径。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from rag_knowledge.services.backbone_guard import (
    avoid_names_for_anchors,
    hop_relations_for_anchors,
    load_backbone_constraints,
    resolve_canonical,
    soft_match_backbone_entities,
)
from rag_knowledge.services.relation_policy import SCOPE_TRAVERSAL_RELATIONS

logger = logging.getLogger(__name__)


class BindingStrength(str, Enum):
    """主体绑定强度枚举"""
    UNBOUND = "unbound"        # 未识别出明确主体（全库自由检索）
    INFERRED = "inferred"      # 由上下文或规则推断（允许下游软纠偏）
    CONFIRMED = "confirmed"    # 用户多轮确认或澄清选择（锁定主体，禁止语义重绑定）
    EXPLICIT = "explicit"      # 用户本轮显式指定/强命名前缀（绝对锁定主体）


class ProvenanceSourceType(str, Enum):
    """证据来源类型"""
    DIRECT_ENTITY_CHUNK = "direct_entity_chunk"
    GRAPH_RELATION = "graph_relation"
    DOC_OWNERSHIP = "doc_ownership"
    SHARED_FUNCTIONAL_AREA = "shared_functional_area"
    LEGACY_FALLBACK = "legacy_fallback"


@dataclass(frozen=True)
class ProvenancePath:
    """广义证据来源链。"""

    source_type: str
    root_entity: str
    target_entity: str
    relation_type: str = "self"
    hops: int = 0
    confidence: float = 1.0
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "root_entity": self.root_entity,
            "target_entity": self.target_entity,
            "relation_type": self.relation_type,
            "hops": self.hops,
            "confidence": self.confidence,
            "description": self.description,
        }


@dataclass(frozen=True)
class SubjectResolution:
    """Legacy subject-resolution compatibility record.

    Business task semantics no longer live in ScopeResolver. Additional entities
    are represented only as referenced_entities; Agent V1.6 consumes
    SemanticTaskContext instead.
    """

    primary_entities: tuple[str, ...] = ()
    referenced_entities: tuple[str, ...] = ()
    binding_strength: BindingStrength = BindingStrength.UNBOUND
    raw_query: str = ""


@dataclass(frozen=True)
class ScopePolicy:
    """Legacy EvidenceScope graph-expansion budget.

    No comparison/cooperation/dependency task classification is performed here.
    """

    max_hops: int = 1
    max_admissible_entities: int = 15
    allow_legacy_fallback: bool = True
    allowed_relations: frozenset[str] = SCOPE_TRAVERSAL_RELATIONS


@dataclass(frozen=True)
class EvidenceScope:
    """
    证据范围契约对象。
    
    职责边界：仅表达 Structural Eligibility（知识结构上的合法性），
    不预判语义相关性（交给 Reranker），不预判事实支持度（交给 Evidence Guard）。
    """

    scope_id: str
    root_entities: tuple[str, ...] = ()
    binding_strength: BindingStrength = BindingStrength.UNBOUND
    admissible_entities: frozenset[str] = field(default_factory=frozenset)
    provenance_paths: tuple[ProvenancePath, ...] = ()
    excluded_rebindings: frozenset[str] = field(default_factory=frozenset)
    doc_category: str | None = None
    scope_reason: str = ""
    scope_version: str = "v2"
    materialized_chunk_ids: frozenset[str] | None = None

    @property
    def is_identity_locked(self) -> bool:
        """是否禁止被下游改写或模糊消歧重新绑定为其它主体。"""
        return self.binding_strength in (BindingStrength.CONFIRMED, BindingStrength.EXPLICIT)

    @property
    def primary_root(self) -> str | None:
        """主根实体名称（首个实体）。"""
        return self.root_entities[0] if self.root_entities else None

    @property
    def fingerprint(self) -> str:
        """用于缓存隔离的指纹。"""
        root_str = ",".join(self.root_entities)
        admiss_str = ",".join(sorted(self.admissible_entities))
        excl_str = ",".join(sorted(self.excluded_rebindings))
        materialized_str = ",".join(sorted(self.materialized_chunk_ids or ()))
        raw = (
            f"{self.binding_strength.value}:{root_str}:{admiss_str}:{excl_str}:"
            f"{materialized_str}:{self.doc_category or ''}:{self.scope_version}"
        )
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]

    def is_structurally_admissible(
        self,
        chunk_entity: str | None,
        chunk_id: str | None = None,
    ) -> bool:
        """判断 chunk 是否具备进入当前 EvidenceScope 的结构资格。"""
        if not self.is_identity_locked:
            return True

        cid = (chunk_id or "").strip()
        if cid and self.materialized_chunk_ids and cid in self.materialized_chunk_ids:
            return True

        ent = (chunk_entity or "").strip()
        if not ent:
            return False
        if ent in self.excluded_rebindings and ent not in self.admissible_entities:
            return False
        return not self.admissible_entities or ent in self.admissible_entities

    def get_provenance(self, entity: str) -> ProvenancePath | None:
        """获取某个准入实体的溯源路径。"""
        for p in self.provenance_paths:
            if p.target_entity == entity:
                return p
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "root_entities": list(self.root_entities),
            "binding_strength": self.binding_strength.value,
            "is_identity_locked": self.is_identity_locked,
            "admissible_entities": sorted(self.admissible_entities),
            "provenance_paths": [p.to_dict() for p in self.provenance_paths],
            "excluded_rebindings": sorted(self.excluded_rebindings),
            "doc_category": self.doc_category,
            "scope_reason": self.scope_reason,
            "scope_version": self.scope_version,
            "materialized_chunks_count": len(self.materialized_chunk_ids) if self.materialized_chunk_ids is not None else None,
        }


class ScopeResolver:
    """Legacy compatibility resolver without business-task semantics.

    Agent V1.6 does not use this resolver. It remains temporarily for non-Agent
    callers and performs only generic entity extraction plus bounded structural
    traversal.
    """

    @classmethod
    def resolve_subject(
        cls,
        question: str,
        *,
        entity_name: str | None = None,
        clarification_selected: str | None = None,
        constraints: dict | None = None,
    ) -> SubjectResolution:
        """Resolve a legacy primary entity and other explicitly referenced entities."""
        constraints = constraints if constraints is not None else load_backbone_constraints()
        raw_entity = (entity_name or "").strip()
        raw_selected = (clarification_selected or "").strip()

        # 1. 显式指定 entity_name -> EXPLICIT
        if raw_entity:
            canonical = resolve_canonical(raw_entity, constraints) or raw_entity
            return SubjectResolution(
                primary_entities=(canonical,),
                binding_strength=BindingStrength.EXPLICIT,
                raw_query=question,
            )

        # 2. 澄清选择 -> CONFIRMED
        if raw_selected:
            from rag_knowledge.services.sdk_code_job import map_clarification_text
            mapped = map_clarification_text(raw_selected)
            if mapped:
                canonical = resolve_canonical(mapped, constraints) or mapped
                return SubjectResolution(
                    primary_entities=(canonical,),
                    binding_strength=BindingStrength.CONFIRMED,
                    raw_query=question,
                )
            clean = raw_selected.split("（")[0].split("(")[0].strip()
            canonical = resolve_canonical(clean, constraints) or clean
            if canonical:
                return SubjectResolution(
                    primary_entities=(canonical,),
                    binding_strength=BindingStrength.CONFIRMED,
                    raw_query=question,
                )

        # 3. Legacy generic query entity extraction. Correction/meta turns are
        # handled by DialogueUnderstanding and are not reinterpreted here.
        from rag_knowledge.services.query_entity_guard import detect_correction_or_negation

        is_correction, _ = detect_correction_or_negation(question or "")
        if not is_correction and question:
            inferred = soft_match_backbone_entities(question, constraints, max_hits=4)
            if inferred:
                # 按照实体或其 alias 在 question 中首次出现的字符位置排序，保持主次顺序
                aliases_map = constraints.get("canonical_by_alias") or {}
                def _first_pos(entity: str) -> int:
                    q_lower = question.casefold()
                    best_pos = 999999
                    for alias, canonical in aliases_map.items():
                        if canonical == entity:
                            p = q_lower.find(alias.casefold())
                            if 0 <= p < best_pos:
                                best_pos = p
                    p2 = q_lower.find(entity.casefold())
                    if 0 <= p2 < best_pos:
                        best_pos = p2
                    return best_pos

                inferred = sorted(inferred, key=_first_pos)

            if len(inferred) == 1:
                return SubjectResolution(
                    primary_entities=(inferred[0],),
                    binding_strength=BindingStrength.INFERRED,
                    raw_query=question,
                )
            elif len(inferred) >= 2:
                # Scope does not classify the relationship between referenced entities.
                return SubjectResolution(
                    primary_entities=(inferred[0],),
                    referenced_entities=tuple(inferred[1:]),
                    binding_strength=BindingStrength.INFERRED,
                    raw_query=question,
                )

        return SubjectResolution(raw_query=question)

    @classmethod
    def resolve(
        cls,
        question: str,
        *,
        entity_name: str | None = None,
        doc_category: str | None = None,
        clarification_selected: str | None = None,
        intent: str | None = None,
        constraints: dict | None = None,
        policy: ScopePolicy | None = None,
    ) -> EvidenceScope:
        """计算完整的 EvidenceScope。"""
        constraints = constraints if constraints is not None else load_backbone_constraints()
        policy = policy or ScopePolicy()

        subject = cls.resolve_subject(
            question,
            entity_name=entity_name,
            clarification_selected=clarification_selected,
            constraints=constraints,
        )

        roots = list(subject.primary_entities)
        all_roots = tuple(roots)

        if not all_roots:
            # 无锁定主体：开放范围，无排除重绑定
            scope_id = hashlib.md5(f"unbound_{question}_{doc_category}".encode("utf-8")).hexdigest()[:12]
            return EvidenceScope(
                scope_id=scope_id,
                root_entities=(),
                binding_strength=BindingStrength.UNBOUND,
                admissible_entities=frozenset(),
                provenance_paths=(),
                excluded_rebindings=frozenset(),
                doc_category=(doc_category or "").strip() or None,
                scope_reason="unbound_exploration",
            )

        admissible: set[str] = set(roots)
        paths: list[ProvenancePath] = []

        # 1. 根实体直属路径
        for r in roots:
            paths.append(ProvenancePath(
                source_type=ProvenanceSourceType.DIRECT_ENTITY_CHUNK.value,
                root_entity=r,
                target_entity=r,
                relation_type="self",
                hops=0,
                confidence=1.0,
                description=f"Direct entity knowledge for {r}",
            ))

        # 2. 图谱有界 BFS。每一跳只从上一跳 frontier 继续扩张，避免 max_hops 成为虚假配置。
        frontier = set(roots)
        visited = set(roots)
        for hop in range(1, max(0, policy.max_hops) + 1):
            if not frontier or len(admissible) >= policy.max_admissible_entities:
                break
            edges = hop_relations_for_anchors(
                sorted(frontier),
                constraints,
                max_edges=policy.max_admissible_entities * 2,
            )
            next_frontier: set[str] = set()
            for edge in edges:
                rel = edge.get("relation_type") or ""
                src = edge.get("source") or ""
                tgt = edge.get("target") or ""
                if rel not in policy.allowed_relations:
                    continue
                # Ambiguity sibling edges never become evidence authorization in Scope.
                if rel == "different_from":
                    continue

                if src in frontier:
                    other = tgt
                elif tgt in frontier:
                    other = src
                else:
                    continue
                if not other or other in visited:
                    continue
                if len(admissible) >= policy.max_admissible_entities:
                    break

                visited.add(other)
                admissible.add(other)
                next_frontier.add(other)
                paths.append(ProvenancePath(
                    source_type=ProvenanceSourceType.GRAPH_RELATION.value,
                    root_entity=roots[0],
                    target_entity=other,
                    relation_type=rel,
                    hops=hop,
                    confidence=max(0.5, 0.85 - 0.1 * (hop - 1)),
                    description=f"Graph relation {src} --[{rel}]--> {tgt}",
                ))
            frontier = next_frontier

        # 3. 排除歧义重绑定（avoid names）
        avoid_entities = avoid_names_for_anchors(roots, constraints)
        excluded = {e for e in avoid_entities if e not in admissible}

        cat = (doc_category or "").strip() or None
        scope_reason = (
            "user_explicit_selection" if subject.binding_strength == BindingStrength.EXPLICIT
            else "user_clarification_choice" if subject.binding_strength == BindingStrength.CONFIRMED
            else "stage1_inferred_subject"
        )
        scope_id = hashlib.md5(f"{roots}_{sorted(admissible)}_{cat}_{subject.binding_strength}".encode("utf-8")).hexdigest()[:12]

        return EvidenceScope(
            scope_id=scope_id,
            root_entities=all_roots,
            binding_strength=subject.binding_strength,
            admissible_entities=frozenset(admissible),
            provenance_paths=tuple(paths),
            excluded_rebindings=frozenset(excluded),
            doc_category=cat,
            scope_reason=scope_reason,
        )
