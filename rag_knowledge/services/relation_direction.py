"""Relation direction authority — schema-first, optional LLM semantic arbiter."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from rag_knowledge.models.graph_schema import normalize_entity_name, validate_relation


# Relations with an intended semantic subject→object direction.
SEMANTIC_DIRECTION_RELATIONS = frozenset({
    "belongs_to",
    "depends_on",
    "requires",
    "runs_command",
    "configured_by",
    "uses_config",
    "has_procedure",
    "has_step",
    "solved_by",
    "causes",
    "defined_in",
    "has_table",
    "has_field",
})

# Prefer deterministic flip when reverse is uniquely schema-legal.
DETERMINISTIC_FLIP_RELATIONS = frozenset({
    "runs_command",
    "configured_by",
    "uses_config",
    "has_procedure",
    "has_step",
    "solved_by",
    "belongs_to",
    "has_table",
    "has_field",
    "defined_in",
    "causes",
})


class DirectionAction:
    KEEP = "keep"
    FLIP = "flip"
    ILLEGAL = "illegal"
    UNSURE = "unsure"


@dataclass(frozen=True)
class DirectionDecision:
    action: str
    confidence: float = 1.0
    reason: str = ""
    source_name: str = ""
    target_name: str = ""
    used_llm: bool = False


class RelationDirectionArbiterProtocol(Protocol):
    def arbitrate(
        self,
        source_name: str,
        relation_type: str,
        target_name: str,
        *,
        source_type: str = "",
        target_type: str = "",
        evidence_text: str = "",
    ) -> tuple[str, float]:
        ...


class RelationDirectionService:
    """
    Single authority for relation endpoint order.

    Order:
    1. Deterministic schema: illegal forward + legal reverse → flip
    2. Unique legal forward → keep
    3. Both legal / types unknown on semantic relations → optional LLM arbiter
    4. Otherwise keep or illegal
    """

    def __init__(self, arbiter: RelationDirectionArbiterProtocol | None = None):
        self.arbiter = arbiter
        self._min_confidence = 0.80
        try:
            from rag_knowledge.config import Config

            self._min_confidence = float(
                Config().graph_extraction_llm.relation_direction_min_confidence
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
    ) -> DirectionDecision:
        src = normalize_entity_name(source_name)
        tgt = normalize_entity_name(target_name)
        rtype = str(relation_type or "").strip()
        if not src or not tgt or not rtype:
            return DirectionDecision(
                action=DirectionAction.ILLEGAL,
                reason="missing_endpoint_or_type",
                source_name=source_name,
                target_name=target_name,
            )

        st = str(source_type or "").strip()
        tt = str(target_type or "").strip()

        if st and tt:
            ok_fwd, _ = validate_relation(st, rtype, tt)
            ok_rev, _ = validate_relation(tt, rtype, st)

            if ok_fwd and not ok_rev:
                return DirectionDecision(
                    action=DirectionAction.KEEP,
                    reason="schema_forward_unique",
                    source_name=source_name,
                    target_name=target_name,
                )
            if (not ok_fwd) and ok_rev and rtype in DETERMINISTIC_FLIP_RELATIONS:
                return DirectionDecision(
                    action=DirectionAction.FLIP,
                    reason="schema_reverse_unique",
                    source_name=target_name,
                    target_name=source_name,
                )
            if (not ok_fwd) and (not ok_rev):
                return DirectionDecision(
                    action=DirectionAction.ILLEGAL,
                    reason="schema_both_illegal",
                    source_name=source_name,
                    target_name=target_name,
                )
            # both legal (or forward illegal but relation not in deterministic flip set)
            if (not ok_fwd) and ok_rev:
                # e.g. depends_on not listed — still prefer unique reverse
                return DirectionDecision(
                    action=DirectionAction.FLIP,
                    reason="schema_reverse_only",
                    source_name=target_name,
                    target_name=source_name,
                )
            needs_llm = rtype in SEMANTIC_DIRECTION_RELATIONS and ok_fwd and ok_rev
        else:
            needs_llm = rtype in SEMANTIC_DIRECTION_RELATIONS

        if needs_llm and self.arbiter:
            verdict, confidence = self.arbiter.arbitrate(
                source_name,
                rtype,
                target_name,
                source_type=st,
                target_type=tt,
                evidence_text=evidence_text,
            )
            if verdict == "flip" and confidence >= self._min_confidence:
                return DirectionDecision(
                    action=DirectionAction.FLIP,
                    confidence=confidence,
                    reason="llm_semantic_flip",
                    source_name=target_name,
                    target_name=source_name,
                    used_llm=True,
                )
            if verdict == "keep" and confidence >= self._min_confidence:
                return DirectionDecision(
                    action=DirectionAction.KEEP,
                    confidence=confidence,
                    reason="llm_semantic_keep",
                    source_name=source_name,
                    target_name=target_name,
                    used_llm=True,
                )
            return DirectionDecision(
                action=DirectionAction.UNSURE,
                confidence=confidence,
                reason="llm_semantic_unsure",
                source_name=source_name,
                target_name=target_name,
                used_llm=True,
            )

        return DirectionDecision(
            action=DirectionAction.KEEP,
            reason="default_keep",
            source_name=source_name,
            target_name=target_name,
        )


class LLMRelationDirectionArbiter:
    """LLM backend: decide whether source-[rel]->target should keep or flip."""

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
    ) -> tuple[str, float]:
        if not self.llm_client and not self.use_graph_endpoint:
            return ("unsure", 0.0)
        try:
            type_hint = ""
            if source_type or target_type:
                type_hint = (
                    f"类型提示: {source_name}={source_type or '?'} ; "
                    f"{target_name}={target_type or '?'}\n"
                )
            evidence = (evidence_text or "").strip()
            evidence_block = f"证据摘录: {evidence}\n" if evidence else ""
            prompt = (
                "你是知识图谱关系方向裁决器。判断下面这条有向关系语义上是否方向正确。\n"
                f"候选: {source_name} -[{relation_type}]-> {target_name}\n"
                f"{type_hint}{evidence_block}\n"
                "方向约定简述:\n"
                "- belongs_to: 子 → 父\n"
                "- depends_on / requires: 依赖方 → 被依赖方\n"
                "- runs_command: 执行者 → Command\n"
                "- uses_config / configured_by: 使用者/被配置对象 → Config\n"
                "- has_procedure / has_step: 容器 → 内容\n"
                "- solved_by: Error → Solution\n"
                "- causes: 原因 → 结果\n"
                "- defined_in: 实体 → 文档/章节\n\n"
                "只回答 JSON:\n"
                '{"verdict":"keep"|"flip"|"unsure","confidence":0.0}\n'
                "keep=保持当前方向; flip=应改为反向; unsure=无法判断。"
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
            if verdict not in {"keep", "flip", "unsure"}:
                verdict = "unsure"
            confidence = float(data.get("confidence") or 0.0)
            return (verdict, confidence)
        except Exception:
            return ("unsure", 0.0)

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
