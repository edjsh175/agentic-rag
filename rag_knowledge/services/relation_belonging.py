"""belongs_to parent-attachment authority — catalog/path first, optional LLM in backbone neighborhood."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from rag_knowledge.models.graph_schema import normalize_entity_name, validate_relation
from rag_knowledge.services.backbone_guard import resolve_canonical
from rag_knowledge.services.domain_catalog import DomainCatalogLoader


PARENT_ENTITY_TYPES = frozenset({
    "Product",
    "Module",
    "Tool",
    "Service",
    "FunctionArea",
    "Feature",
})


class BelongingAction:
    KEEP = "keep"
    REPLACE = "replace"
    REJECT = "reject"
    UNSURE = "unsure"


@dataclass(frozen=True)
class BelongingDecision:
    action: str
    target_name: str = ""
    confidence: float = 1.0
    reason: str = ""
    used_llm: bool = False
    candidates: tuple[str, ...] = ()


class BelongingArbiterProtocol(Protocol):
    def arbitrate(
        self,
        child_name: str,
        parent_name: str,
        *,
        child_type: str = "",
        parent_type: str = "",
        evidence_text: str = "",
        section_path: str = "",
        candidate_parents: list[str] | None = None,
    ) -> tuple[str, str, float]:
        """Return (accept|reject|replace|unsure, chosen_parent, confidence)."""
        ...


def _norm_key(name: str) -> str:
    return normalize_entity_name(name).casefold()


def preferred_parent_from_section_path(
    section_path: str,
    candidate_parents: list[str],
    current_parent: str,
) -> str | None:
    """Prefer the most specific candidate that appears in section_path over a coarse parent."""
    path = str(section_path or "").strip()
    if not path or not candidate_parents:
        return None
    current = normalize_entity_name(current_parent)
    ranked: list[tuple[int, str]] = []
    for cand in candidate_parents:
        name = normalize_entity_name(cand)
        if not name or name == current:
            continue
        if name in path or any(
            seg.strip() and (seg.strip() == name or name.endswith(seg.strip()) or name.endswith(f"::{seg.strip()}"))
            for seg in path.replace(">", "/").split("/")
        ):
            ranked.append((len(name), name))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    best = ranked[0][1]
    # Only upgrade when current is a coarser ancestor-like name of the preferred parent.
    if current and (best.startswith(f"{current}::") or current in best and current != best):
        return best
    if current and current in path and best in path and len(best) > len(current):
        # Both in path; prefer longer/more specific
        return best
    return None


def collect_candidate_parents(
    child_type: str,
    *,
    type_index: dict[str, str] | None = None,
    backbone_types: dict[str, str] | None = None,
    extra: list[str] | None = None,
    limit: int = 10,
) -> list[str]:
    """Schema-legal parent names near the extract context."""
    st = str(child_type or "").strip()
    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str, et: str) -> None:
        n = normalize_entity_name(name)
        if not n or n in seen:
            return
        if et and et not in PARENT_ENTITY_TYPES:
            return
        if st and et:
            ok, _ = validate_relation(st, "belongs_to", et)
            if not ok:
                return
        seen.add(n)
        names.append(n)

    for name, et in (type_index or {}).items():
        _add(name, str(et or ""))
    for name, et in (backbone_types or {}).items():
        _add(name, str(et or ""))
    for name in extra or []:
        # Unknown type: keep if already schema-checked via type_index/backbone, else include raw for LLM
        n = normalize_entity_name(name)
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    return names[:limit]


class RelationBelongingService:
    """
    Single authority for belongs_to parent attachment (non-backbone residual).

    Order:
    1. Non-belongs_to / out of neighborhood → keep
    2. Catalog owner mismatch → deterministic replace
    3. Section-path more-specific parent → deterministic replace
    4. Optional LLM among candidate parents (neighborhood only)
    """

    def __init__(
        self,
        arbiter: BelongingArbiterProtocol | None = None,
        catalog: DomainCatalogLoader | None = None,
        backbone_constraints: dict | None = None,
    ):
        self.arbiter = arbiter
        self.catalog = catalog
        self.backbone_constraints = backbone_constraints or {}
        self._min_confidence = 0.80
        try:
            from rag_knowledge.config import Config

            self._min_confidence = float(
                Config().graph_extraction_llm.relation_belonging_min_confidence
            )
        except Exception:
            pass
        if self.catalog is None:
            try:
                self.catalog = DomainCatalogLoader()
            except Exception:
                self.catalog = None

    def decide(
        self,
        child_name: str,
        parent_name: str,
        *,
        child_type: str = "",
        parent_type: str = "",
        evidence_text: str = "",
        section_path: str = "",
        in_neighborhood: bool = False,
        candidate_parents: list[str] | None = None,
    ) -> BelongingDecision:
        child = normalize_entity_name(child_name)
        parent = normalize_entity_name(parent_name)
        if not child or not parent:
            return BelongingDecision(action=BelongingAction.KEEP, target_name=parent_name, reason="missing_endpoint")

        if not in_neighborhood:
            return BelongingDecision(
                action=BelongingAction.KEEP,
                target_name=parent,
                reason="out_of_neighborhood",
            )

        # Catalog gold owner (non-backbone seeds also)
        if self.catalog is not None:
            owner = self.catalog.owner_for(child)
            if owner:
                owner_n = normalize_entity_name(owner)
                parent_canon = resolve_canonical(parent, self.backbone_constraints) or parent
                owner_canon = resolve_canonical(owner_n, self.backbone_constraints) or owner_n
                if _norm_key(parent_canon) != _norm_key(owner_canon):
                    return BelongingDecision(
                        action=BelongingAction.REPLACE,
                        target_name=owner_n,
                        reason="catalog_owner_replace",
                        candidates=(owner_n,),
                    )

        candidates = list(candidate_parents or [])
        if parent not in candidates:
            candidates = [parent] + candidates
        # Dedupe preserve order
        seen: set[str] = set()
        ordered: list[str] = []
        for name in candidates:
            n = normalize_entity_name(name)
            if n and n not in seen:
                seen.add(n)
                ordered.append(n)
        candidates = ordered[:10]

        preferred = preferred_parent_from_section_path(section_path, candidates, parent)
        if preferred and _norm_key(preferred) != _norm_key(parent):
            if _norm_key(preferred) == _norm_key(child_name):
                # Prevent belongs_to self-loop (e.g. PipelineBuilder::数据规范 -> PipelineBuilder::数据规范)
                pass
            else:
                return BelongingDecision(
                    action=BelongingAction.REPLACE,
                    target_name=preferred,
                    reason="section_path_prefer_parent",
                    candidates=tuple(candidates),
                )

        if not self.arbiter or len(candidates) <= 1:
            return BelongingDecision(
                action=BelongingAction.KEEP,
                target_name=parent,
                reason="default_keep",
                candidates=tuple(candidates),
            )

        verdict, chosen, confidence = self.arbiter.arbitrate(
            child,
            parent,
            child_type=child_type,
            parent_type=parent_type,
            evidence_text=evidence_text,
            section_path=section_path,
            candidate_parents=candidates,
        )
        chosen_n = normalize_entity_name(chosen) or parent
        if confidence < self._min_confidence or verdict == "unsure":
            return BelongingDecision(
                action=BelongingAction.UNSURE,
                target_name=parent,
                confidence=confidence,
                reason="llm_belonging_unsure",
                used_llm=True,
                candidates=tuple(candidates),
            )
        if verdict == "reject":
            return BelongingDecision(
                action=BelongingAction.REJECT,
                target_name=parent,
                confidence=confidence,
                reason="llm_belonging_reject",
                used_llm=True,
                candidates=tuple(candidates),
            )
        if verdict == "accept" or (verdict == "replace" and _norm_key(chosen_n) == _norm_key(parent)):
            return BelongingDecision(
                action=BelongingAction.KEEP,
                target_name=parent,
                confidence=confidence,
                reason="llm_belonging_accept",
                used_llm=True,
                candidates=tuple(candidates),
            )
        if verdict == "replace":
            match = next((c for c in candidates if _norm_key(c) == _norm_key(chosen_n)), None)
            if match and _norm_key(match) != _norm_key(parent):
                return BelongingDecision(
                    action=BelongingAction.REPLACE,
                    target_name=match,
                    confidence=confidence,
                    reason="llm_belonging_replace",
                    used_llm=True,
                    candidates=tuple(candidates),
                )
        return BelongingDecision(
            action=BelongingAction.UNSURE,
            target_name=parent,
            confidence=confidence,
            reason="llm_belonging_invalid_choice",
            used_llm=True,
            candidates=tuple(candidates),
        )


class LLMBelongingArbiter:
    """LLM backend: validate or replace belongs_to parent within candidate set."""

    def __init__(self, llm_client: Any = None, *, use_graph_endpoint: bool = False):
        self.llm_client = llm_client
        self.use_graph_endpoint = use_graph_endpoint

    def arbitrate(
        self,
        child_name: str,
        parent_name: str,
        *,
        child_type: str = "",
        parent_type: str = "",
        evidence_text: str = "",
        section_path: str = "",
        candidate_parents: list[str] | None = None,
    ) -> tuple[str, str, float]:
        if not self.llm_client and not self.use_graph_endpoint:
            return ("unsure", parent_name, 0.0)
        candidates = list(candidate_parents or [parent_name])
        try:
            type_hint = ""
            if child_type or parent_type:
                type_hint = (
                    f"类型: 子={child_name}({child_type or '?'}) ; "
                    f"父={parent_name}({parent_type or '?'})\n"
                )
            evidence = (evidence_text or "").strip()
            evidence_block = f"证据: {evidence}\n" if evidence else ""
            path_block = f"章节路径: {section_path}\n" if section_path else ""
            alt_text = ", ".join(candidates)
            prompt = (
                "你是知识图谱归属关系（belongs_to）裁决器。判断子实体是否挂到了正确的父实体。\n"
                f"候选边: {child_name} -[belongs_to]-> {parent_name}\n"
                f"可选父实体(replace 必须从中选): {alt_text}\n"
                f"{type_hint}{path_block}{evidence_block}\n"
                "约定: belongs_to 是子→父；优先更具体的 FunctionArea/父 Tool；"
                "产品归属以 catalog 为准（Tool→Product / SubTool→Tool）；"
                "禁止把「工具与数据处理层」等架构分层 Module 当作归属父节点（分层是 facet，不是父实体）。\n\n"
                "只回答 JSON:\n"
                '{"verdict":"accept"|"replace"|"reject"|"unsure","parent":"<one of candidates>","confidence":0.0}\n'
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
            if verdict not in {"accept", "replace", "reject", "unsure"}:
                verdict = "unsure"
            chosen = str(data.get("parent") or parent_name).strip()
            confidence = float(data.get("confidence") or 0.0)
            return (verdict, chosen, confidence)
        except Exception:
            return ("unsure", parent_name, 0.0)

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
