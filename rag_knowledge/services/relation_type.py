"""Relation type label authority — schema-filtered, optional LLM semantic arbiter."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from rag_knowledge.models.graph_schema import normalize_entity_name, validate_relation


# Easy-to-confuse labels; LLM only runs inside these groups.
RELATION_TYPE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"requires", "depends_on"}),
    frozenset({"uses_config", "configured_by"}),
    frozenset({"has_procedure", "belongs_to"}),
    frozenset({"has_step", "has_procedure"}),
)


class TypeLabelAction:
    KEEP = "keep"
    REPLACE = "replace"
    REJECT = "reject"
    UNSURE = "unsure"


@dataclass(frozen=True)
class TypeLabelDecision:
    action: str
    relation_type: str = ""
    confidence: float = 1.0
    reason: str = ""
    used_llm: bool = False
    alternatives: tuple[str, ...] = ()


class RelationTypeArbiterProtocol(Protocol):
    def arbitrate(
        self,
        source_name: str,
        relation_type: str,
        target_name: str,
        *,
        source_type: str = "",
        target_type: str = "",
        evidence_text: str = "",
        alternatives: list[str] | None = None,
    ) -> tuple[str, str, float]:
        """Return (verdict, chosen_type, confidence). verdict: keep|replace|reject|unsure."""
        ...


def confusable_group(relation_type: str) -> frozenset[str] | None:
    """Union of all confusable groups containing this type (types may appear in multiple)."""
    rtype = str(relation_type or "").strip()
    if not rtype:
        return None
    union: set[str] = set()
    for group in RELATION_TYPE_GROUPS:
        if rtype in group:
            union |= set(group)
    return frozenset(union) if union else None


def schema_legal_in_group(
    source_type: str,
    target_type: str,
    group: frozenset[str],
) -> list[str]:
    st = str(source_type or "").strip()
    tt = str(target_type or "").strip()
    if not st or not tt:
        return sorted(group)
    legal: list[str] = []
    for rtype in sorted(group):
        ok, _ = validate_relation(st, rtype, tt)
        if ok:
            legal.append(rtype)
    return legal


class RelationTypeService:
    """
    Single authority for confusable relation-type labels.

    Order:
    1. Outside confusable groups → keep
    2. Types known: unique schema-legal alternative → deterministic keep/replace
    3. Multiple legal / types unknown → optional LLM among alternatives
    4. LLM replace must remain in alternatives; reject marks for staging drop
    """

    def __init__(self, arbiter: RelationTypeArbiterProtocol | None = None):
        self.arbiter = arbiter
        self._min_confidence = 0.80
        try:
            from rag_knowledge.config import Config

            self._min_confidence = float(
                Config().graph_extraction_llm.relation_type_min_confidence
            )
        except Exception:
            pass

    def decide(
        self,
        source_name: str,
        relation_type: str,
        target_name: str,
        *,
        source_type: str = "",
        target_type: str = "",
        evidence_text: str = "",
    ) -> TypeLabelDecision:
        src = normalize_entity_name(source_name)
        tgt = normalize_entity_name(target_name)
        rtype = str(relation_type or "").strip()
        if not src or not tgt or not rtype:
            return TypeLabelDecision(
                action=TypeLabelAction.KEEP,
                relation_type=rtype,
                reason="missing_endpoint_or_type",
            )

        group = confusable_group(rtype)
        if not group:
            return TypeLabelDecision(
                action=TypeLabelAction.KEEP,
                relation_type=rtype,
                reason="not_confusable",
            )

        alternatives = schema_legal_in_group(source_type, target_type, group)
        if not alternatives:
            return TypeLabelDecision(
                action=TypeLabelAction.KEEP,
                relation_type=rtype,
                reason="no_schema_legal_alternative",
                alternatives=tuple(sorted(group)),
            )

        if len(alternatives) == 1:
            only = alternatives[0]
            if only == rtype:
                return TypeLabelDecision(
                    action=TypeLabelAction.KEEP,
                    relation_type=rtype,
                    reason="schema_unique_keep",
                    alternatives=tuple(alternatives),
                )
            return TypeLabelDecision(
                action=TypeLabelAction.REPLACE,
                relation_type=only,
                reason="schema_unique_replace",
                alternatives=tuple(alternatives),
            )

        # Multiple schema-legal labels in the confusable group
        if rtype not in alternatives:
            # Current illegal among multi-legal set → prefer deterministic first alt
            # still allow LLM to choose among alternatives when enabled
            if not self.arbiter:
                return TypeLabelDecision(
                    action=TypeLabelAction.REPLACE,
                    relation_type=alternatives[0],
                    reason="schema_current_illegal_replace",
                    alternatives=tuple(alternatives),
                )

        if not self.arbiter:
            if rtype in alternatives:
                return TypeLabelDecision(
                    action=TypeLabelAction.KEEP,
                    relation_type=rtype,
                    reason="default_keep",
                    alternatives=tuple(alternatives),
                )
            return TypeLabelDecision(
                action=TypeLabelAction.REPLACE,
                relation_type=alternatives[0],
                reason="schema_current_illegal_replace",
                alternatives=tuple(alternatives),
            )

        verdict, chosen, confidence = self.arbiter.arbitrate(
            source_name,
            rtype,
            target_name,
            source_type=source_type,
            target_type=target_type,
            evidence_text=evidence_text,
            alternatives=alternatives,
        )
        chosen = str(chosen or "").strip() or rtype
        if confidence < self._min_confidence or verdict == "unsure":
            return TypeLabelDecision(
                action=TypeLabelAction.UNSURE,
                relation_type=rtype,
                confidence=confidence,
                reason="llm_type_unsure",
                used_llm=True,
                alternatives=tuple(alternatives),
            )
        if verdict == "reject":
            return TypeLabelDecision(
                action=TypeLabelAction.REJECT,
                relation_type=rtype,
                confidence=confidence,
                reason="llm_type_reject",
                used_llm=True,
                alternatives=tuple(alternatives),
            )
        if verdict == "keep" and rtype in alternatives:
            return TypeLabelDecision(
                action=TypeLabelAction.KEEP,
                relation_type=rtype,
                confidence=confidence,
                reason="llm_type_keep",
                used_llm=True,
                alternatives=tuple(alternatives),
            )
        if verdict in {"replace", "keep"} and chosen in alternatives:
            if chosen == rtype:
                return TypeLabelDecision(
                    action=TypeLabelAction.KEEP,
                    relation_type=rtype,
                    confidence=confidence,
                    reason="llm_type_keep",
                    used_llm=True,
                    alternatives=tuple(alternatives),
                )
            return TypeLabelDecision(
                action=TypeLabelAction.REPLACE,
                relation_type=chosen,
                confidence=confidence,
                reason="llm_type_replace",
                used_llm=True,
                alternatives=tuple(alternatives),
            )
        return TypeLabelDecision(
            action=TypeLabelAction.UNSURE,
            relation_type=rtype,
            confidence=confidence,
            reason="llm_type_invalid_choice",
            used_llm=True,
            alternatives=tuple(alternatives),
        )


class LLMRelationTypeArbiter:
    """LLM backend: choose among confusable relation-type labels."""

    def __init__(self, llm_client: Any = None, *, use_graph_endpoint: bool = False):
        self.llm_client = llm_client
        self.use_graph_endpoint = use_graph_endpoint

    def arbitrate(
        self,
        source_name: str,
        relation_type: str,
        target_name: str,
        *,
        source_type: str = "",
        target_type: str = "",
        evidence_text: str = "",
        alternatives: list[str] | None = None,
    ) -> tuple[str, str, float]:
        if not self.llm_client and not self.use_graph_endpoint:
            return ("unsure", relation_type, 0.0)
        alts = list(alternatives or [])
        if not alts:
            return ("unsure", relation_type, 0.0)
        try:
            type_hint = ""
            if source_type or target_type:
                type_hint = (
                    f"端点类型: {source_name}={source_type or '?'} ; "
                    f"{target_name}={target_type or '?'}\n"
                )
            evidence = (evidence_text or "").strip()
            evidence_block = f"证据摘录: {evidence}\n" if evidence else ""
            alt_text = ", ".join(alts)
            prompt = (
                "你是知识图谱关系类型裁决器。候选关系的标签可能属于易混集合，请选最贴切的类型。\n"
                f"候选: {source_name} -[{relation_type}]-> {target_name}\n"
                f"可选类型(必须从中选择，或 reject): {alt_text}\n"
                f"{type_hint}{evidence_block}\n"
                "类型约定:\n"
                "- depends_on: 运行/部署依赖（常环境组件、服务）\n"
                "- requires: 更泛的前置条件依赖\n"
                "- uses_config: 主体使用某配置项\n"
                "- configured_by: 主体被某配置项配置\n"
                "- has_procedure: 工具/服务/能力域拥有流程\n"
                "- belongs_to: 子归属父\n"
                "- has_step: 流程拥有步骤\n\n"
                "只回答 JSON:\n"
                '{"verdict":"keep"|"replace"|"reject"|"unsure","relation_type":"<one of alternatives>","confidence":0.0}\n'
                "keep=保留当前类型; replace=换成 relation_type 字段; reject=整条关系应丢弃。"
            )
            if self.llm_client is not None:
                raw_response = self.llm_client.invoke(prompt)
                raw_text = raw_response.content if hasattr(raw_response, "content") else str(raw_response)
            else:
                raw_text = self._call_graph_llm(prompt)
            if "```" in raw_text:
                match = re.search(r"```(?:json)?\s*({.*?})\s*```", raw_text, re.DOTALL)
                if match:
                    raw_text = match.group(1)
            data = json.loads(raw_text.strip())
            verdict = str(data.get("verdict") or "unsure").lower()
            if verdict not in {"keep", "replace", "reject", "unsure"}:
                verdict = "unsure"
            chosen = str(data.get("relation_type") or relation_type).strip()
            confidence = float(data.get("confidence") or 0.0)
            return (verdict, chosen, confidence)
        except Exception:
            return ("unsure", relation_type, 0.0)

    def _call_graph_llm(self, prompt: str) -> str:
        from rag_knowledge.config import Config
        from rag_knowledge.llm_http import chat

        cfg = Config()
        llm_cfg = cfg.graph_extraction_llm
        return chat(
            cfg.graph_extraction_endpoint,
            [{"role": "user", "content": prompt}],
            default_ollama=cfg.ollama_base_url,
            temperature=llm_cfg.temperature,
            format_json=True,
            timeout=120.0,
            think=False,
        )
