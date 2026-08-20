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
import re
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
    """主体解析结果。"""

    primary_entities: tuple[str, ...] = ()
    referenced_entities: tuple[str, ...] = ()
    comparison_entities: tuple[str, ...] = ()
    binding_strength: BindingStrength = BindingStrength.UNBOUND
    raw_query: str = ""


@dataclass(frozen=True)
class ScopePolicy:
    """范围扩展策略与预算。"""

    max_hops: int = 1
    max_admissible_entities: int = 15
    allow_comparison_expansion: bool = True
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
    """证据范围求解器。负责计算合法证据范围与溯源路径。"""

    @classmethod
    def _extract_comparison_from_query(
        cls,
        question: str,
        primary_entity: str,
        constraints: dict,
    ) -> tuple[str, ...]:
        """提取 query 中的对比目标（如问 'PipelineWebGL 和 PipelineBuilder 的区别'）。"""
        if not question:
            return ()
        is_comparison = bool(re.search(
            r"区别|对比|不同|差异|相比|比较|哪个好|孰优|versus|\bvs\.?\b",
            question,
            re.IGNORECASE,
        ))
        if not is_comparison:
            return ()
        inferred = soft_match_backbone_entities(question, constraints, max_hits=4)
        comp = [e for e in inferred if e != primary_entity]
        return tuple(comp)

    @classmethod
    def resolve_subject(
        cls,
        question: str,
        *,
        entity_name: str | None = None,
        clarification_selected: str | None = None,
        constraints: dict | None = None,
    ) -> SubjectResolution:
        """解析问题主体、对比目标及绑定强度。"""
        constraints = constraints if constraints is not None else load_backbone_constraints()
        raw_entity = (entity_name or "").strip()
        raw_selected = (clarification_selected or "").strip()

        # 1. 显式指定 entity_name -> EXPLICIT
        if raw_entity:
            canonical = resolve_canonical(raw_entity, constraints) or raw_entity
            comp = cls._extract_comparison_from_query(question, canonical, constraints)
            return SubjectResolution(
                primary_entities=(canonical,),
                comparison_entities=comp,
                binding_strength=BindingStrength.EXPLICIT,
                raw_query=question,
            )

        # 2. 澄清选择 -> CONFIRMED
        if raw_selected:
            from rag_knowledge.services.sdk_code_job import map_clarification_text
            mapped = map_clarification_text(raw_selected)
            if mapped:
                canonical = resolve_canonical(mapped, constraints) or mapped
                comp = cls._extract_comparison_from_query(question, canonical, constraints)
                return SubjectResolution(
                    primary_entities=(canonical,),
                    comparison_entities=comp,
                    binding_strength=BindingStrength.CONFIRMED,
                    raw_query=question,
                )
            clean = raw_selected.split("（")[0].split("(")[0].strip()
            canonical = resolve_canonical(clean, constraints) or clean
            if canonical:
                comp = cls._extract_comparison_from_query(question, canonical, constraints)
                return SubjectResolution(
                    primary_entities=(canonical,),
                    comparison_entities=comp,
                    binding_strength=BindingStrength.CONFIRMED,
                    raw_query=question,
                )

        # 3. 从 query 中识别主体与对比目标
        is_meta_question = bool(re.search(
            r"刚才|上一轮|前面|对话|啥时候|什么时候|说过|提过|没问|没说|没有说",
            question or "",
        ))
        if not is_meta_question and question:
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

            # 只有明确比较语义才进入 comparison；单独的“和/与”只是并列或协作关系。
            is_comparison = bool(re.search(
                r"区别|对比|不同|差异|相比|比较|哪个好|孰优|versus|\bvs\.?\b",
                question,
                re.IGNORECASE,
            ))
            if len(inferred) == 1:
                return SubjectResolution(
                    primary_entities=(inferred[0],),
                    binding_strength=BindingStrength.INFERRED,
                    raw_query=question,
                )
            elif len(inferred) >= 2 and is_comparison:
                return SubjectResolution(
                    primary_entities=(inferred[0],),
                    comparison_entities=tuple(inferred[1:]),
                    binding_strength=BindingStrength.INFERRED,
                    raw_query=question,
                )
            elif len(inferred) >= 2:
                # 多个提及实体，默认首个为主实体，其余为引用实体
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
        comparison_targets = list(subject.comparison_entities)
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

        # 2. 比较目标实体（若是对比问题且 policy 允许）
        if policy.allow_comparison_expansion and comparison_targets:
            for comp in comparison_targets:
                admissible.add(comp)
                paths.append(ProvenancePath(
                    source_type=ProvenanceSourceType.GRAPH_RELATION.value,
                    root_entity=roots[0],
                    target_entity=comp,
                    relation_type="comparison_target",
                    hops=1,
                    confidence=0.9,
                    description=f"Comparison target in query: {comp}",
                ))

        # 3. 图谱有界 BFS。每一跳只从上一跳 frontier 继续扩张，避免 max_hops 成为虚假配置。
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
                if rel == "different_from" and not comparison_targets and not (intent and "compare" in intent):
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

        # 4. 排除歧义重绑定（avoid names）
        avoid_entities = avoid_names_for_anchors(roots, constraints)
        # 如果比较目标显式合法，则不将其移入 excluded_rebindings
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
