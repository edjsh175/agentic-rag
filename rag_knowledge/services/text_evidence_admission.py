"""Text Evidence Admission Service (PRD 2026-08-27).

Authoritative qualification of text candidates into:
- TARGET_DIRECT (PASS, TARGET_SPECIFIC)
- RELATED_CONTEXT (PASS, CONTEXT_ONLY)
- CONFLICT (REJECT, NONE)
- IRRELEVANT (REJECT, NONE)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Literal, Sequence

from langchain_core.documents import Document

from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.services.agent_candidate_pipeline import CandidateResult, _chunk_key

logger = logging.getLogger(__name__)

_OVERVIEW_TERMS = ("主要功能", "功能", "用途", "作用", "是什么", "概览", "能力")
_OVERVIEW_EVIDENCE_TERMS = ("用于", "功能", "支持", "作用", "提供", "实现", "能力")
_DEPLOYMENT_TERMS = ("部署", "安装", "上传", "目录", "路径", "配置位置")
_DIRECT_ATTRIBUTION_CANDIDATE_SIGNALS = frozenset({
    "target_text_mention",
    "document_entity_match",
    "mentioned_entity_match",
    "entity_chunk_link",
    "section_target_match",
})


def _norm(value: Any) -> str:
    return normalize_entity_name(str(value or "")).casefold()


def _same(left: Any, right: Any) -> bool:
    return bool(_norm(left) and _norm(left) == _norm(right))


@dataclass(frozen=True)
class TextEvidenceQualification:
    """Canonical qualification result for one candidate text item."""

    verdict: Literal["PASS", "REJECT"]
    evidence_class: Literal[
        "TARGET_DIRECT",
        "RELATED_CONTEXT",
        "CONFLICT",
        "IRRELEVANT",
    ]
    support_scope: Literal[
        "TARGET_SPECIFIC",
        "CONTEXT_ONLY",
        "NONE",
    ]
    intent_relevance: Literal[
        "HIGH",
        "MEDIUM",
        "LOW",
        "NONE",
    ]
    reason_code: str
    reason: str
    signals: tuple[str, ...] = ()
    canonical_question: str = ""
    answer_intent: str = "general_qa"

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "evidence_class": self.evidence_class,
            "support_scope": self.support_scope,
            "intent_relevance": self.intent_relevance,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "admission_signals": list(self.signals),
            "signals": list(self.signals),
            "canonical_question": self.canonical_question,
            "answer_intent": self.answer_intent,
            # Legacy compatibility fields
            "entity_relevance": "HIGH" if self.evidence_class == "TARGET_DIRECT" else ("MEDIUM" if self.evidence_class == "RELATED_CONTEXT" else "LOW"),
        }


_VALID_QUALIFICATION_COMBINATIONS = frozenset({
    ("TARGET_DIRECT", "PASS", "TARGET_SPECIFIC"),
    ("RELATED_CONTEXT", "PASS", "CONTEXT_ONLY"),
    ("CONFLICT", "REJECT", "NONE"),
    ("IRRELEVANT", "REJECT", "NONE"),
})


def valid_text_qualification_protocol(
    qualification: TextEvidenceQualification,
    *,
    target_entity: str = "",
) -> bool:
    """Validate qualification output against the protocol matrix."""
    triple = (qualification.evidence_class, qualification.verdict, qualification.support_scope)
    if triple not in _VALID_QUALIFICATION_COMBINATIONS:
        return False
    if qualification.verdict == "PASS":
        if qualification.intent_relevance not in {"HIGH", "MEDIUM"}:
            return False
    elif qualification.support_scope != "NONE":
        return False
    return True


def text_qualification_response_json_schema(
    *,
    direct_attribution_eligible: bool = True,
) -> dict[str, Any]:
    """JSON Schema for semantic text evidence qualification."""
    evidence_classes = ["TARGET_DIRECT", "RELATED_CONTEXT", "CONFLICT", "IRRELEVANT"]
    support_scopes = ["TARGET_SPECIFIC", "CONTEXT_ONLY", "NONE"]
    if not direct_attribution_eligible:
        # This is a one-way protocol constraint, not an authorization rule.
        # Without an attribution candidate, Helper may still judge the text as
        # contextual or irrelevant, but it cannot grant target-specific scope.
        evidence_classes.remove("TARGET_DIRECT")
        support_scopes.remove("TARGET_SPECIFIC")
    return {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["PASS", "REJECT"],
            },
            "evidence_class": {
                "type": "string",
                "enum": evidence_classes,
            },
            "support_scope": {
                "type": "string",
                "enum": support_scopes,
            },
            "intent_relevance": {
                "type": "string",
                "enum": ["HIGH", "MEDIUM", "LOW", "NONE"],
            },
            "reason_code": {"type": "string"},
            "reason": {"type": "string"},
            "signals": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "verdict",
            "evidence_class",
            "support_scope",
            "intent_relevance",
            "reason_code",
            "reason",
            "signals",
        ],
        "additionalProperties": False,
    }


def resolve_entity_conflict(
    candidate: CandidateResult,
    *,
    target_entity: str,
    graph_working_set: Any = None,
) -> tuple[Literal["NO_CONFLICT", "EXPLICIT_CONFLICT"], list[str]]:
    """Determine whether a candidate explicitly conflicts with the target entity.

    Only an approved different_from relation against the candidate's owning entity
    creates a hard conflict. Mentions, section labels, graph paths, and generic
    entity-chunk links are candidate signals and cannot override that hard fact.
    """
    target = str(target_entity or "").strip()
    if not target:
        return "NO_CONFLICT", ["unbound_no_entity_guard"]

    sibling_names: set[str] = set()
    for relation in getattr(graph_working_set, "relations", {}).values():
        if str(getattr(relation, "relation_type", "")) != "different_from":
            continue
        source_name = getattr(relation, "source_name", "")
        target_name = getattr(relation, "target_name", "")
        if _same(source_name, target):
            sibling_names.add(str(target_name))
        elif _same(target_name, target):
            sibling_names.add(str(source_name))

    if not sibling_names:
        return "NO_CONFLICT", ["no_sibling_conflicts"]

    meta = candidate.document.metadata or {}
    document_entity = str(meta.get("document_entity") or meta.get("entity_name") or "")

    # A target mention/section/link is only an attribution candidate signal. It
    # cannot negate an approved different_from fact about the candidate's owning
    # entity. Mixed chunks are handled later by semantic qualification.
    is_sibling_doc = any(_same(document_entity, sibling) for sibling in sibling_names)
    if is_sibling_doc:
        return "EXPLICIT_CONFLICT", [f"explicit_sibling_conflict:{document_entity}"]

    return "NO_CONFLICT", ["no_explicit_conflict"]


class TextEvidenceAdmissionService:
    """Sole authoritative service for text candidate evidence qualification."""

    def __init__(self, cfg: Any = None) -> None:
        self._cfg = cfg

    def qualify(
        self,
        candidate: CandidateResult,
        *,
        semantic_task: Any = None,
        target_entity: str = "",
        graph_working_set: Any = None,
        retrieval_query: str = "",
        semantic_admitter: Callable[[str, CandidateResult, TextEvidenceQualification], TextEvidenceQualification | None] | None = None,
    ) -> TextEvidenceQualification:
        """Qualify one candidate without turning retrieval/metadata signals into evidence authority."""
        if semantic_task is None:
            from rag_knowledge.services.query_surface import infer_answer_intent

            canonical_question = str(retrieval_query or "").strip()
            answer_intent, requested_facets, _source = infer_answer_intent(canonical_question)
            semantic_target = ""
        else:
            canonical_question = str(getattr(semantic_task, "resolved_question", "") or "").strip()
            answer_intent = str(getattr(semantic_task, "answer_intent", "") or "general_qa").strip().lower()
            requested_facets = tuple(getattr(semantic_task, "requested_facets", ()) or ())
            semantic_target = str(getattr(semantic_task, "primary_entity", "") or "").strip()

        requested_target = str(target_entity or "").strip()
        if semantic_target and requested_target and not _same(semantic_target, requested_target):
            return TextEvidenceQualification(
                verdict="REJECT",
                evidence_class="IRRELEVANT",
                support_scope="NONE",
                intent_relevance="NONE",
                reason_code="semantic_task_target_mismatch",
                reason="Admission target disagrees with the frozen SemanticTaskContext primary entity.",
                signals=("semantic_task_authority_violation",),
                canonical_question=canonical_question,
                answer_intent=answer_intent,
            )
        target = semantic_target or requested_target

        conflict_status, conflict_signals = resolve_entity_conflict(
            candidate, target_entity=target, graph_working_set=graph_working_set
        )
        if conflict_status == "EXPLICIT_CONFLICT":
            candidate.structural_flags.append("REJECT:explicit_sibling_conflict")
            return TextEvidenceQualification(
                verdict="REJECT",
                evidence_class="CONFLICT",
                support_scope="NONE",
                intent_relevance="NONE",
                reason_code="explicit_entity_conflict",
                reason=f"Candidate has an approved different_from conflict with the frozen target: {conflict_signals}",
                signals=tuple(conflict_signals),
                canonical_question=canonical_question,
                answer_intent=answer_intent,
            )
        candidate.structural_flags.append("PASS:no_explicit_conflict")

        meta = candidate.document.metadata or {}
        content = candidate.document.page_content
        document_entity = str(meta.get("document_entity") or meta.get("entity_name") or "")
        mentioned = meta.get("mentioned_entities") or []
        section_path = str(meta.get("section_path") or meta.get("section_title") or "")

        candidate_signals: list[str] = []
        if target and target.casefold() in content.casefold():
            candidate_signals.append("target_text_mention")
        if target and _same(document_entity, target):
            candidate_signals.append("document_entity_match")
        if target and any(_same(item, target) for item in mentioned):
            candidate_signals.append("mentioned_entity_match")
        if target and any(
            item.entity_link and item.linked_entity and _same(item.linked_entity, target)
            for item in candidate.provenance
        ):
            candidate_signals.append("entity_chunk_link")
        if target and target.casefold() in section_path.casefold():
            candidate_signals.append("section_target_match")
        if any(item.graph_path for item in candidate.provenance):
            candidate_signals.append("graph_provenance")
        if any(item.exact_lexical for item in candidate.provenance):
            candidate_signals.append("exact_lexical")
        candidate_signals.extend(f"retrieval_source:{source}" for source in candidate.source_generators)
        if not target:
            candidate_signals.append("unbound_query")

        direct_attribution_eligible = bool(
            _DIRECT_ATTRIBUTION_CANDIDATE_SIGNALS.intersection(candidate_signals)
        )

        # None of the signals above proves proposition-level attribution or intent relevance.
        # They only explain why this candidate deserves semantic inspection.
        deterministic_pending = TextEvidenceQualification(
            verdict="REJECT",
            evidence_class="IRRELEVANT",
            support_scope="NONE",
            intent_relevance="LOW",
            reason_code="semantic_admission_required",
            reason="Candidate/metadata signals are not sufficient to authorize evidence without semantic qualification.",
            signals=tuple(dict.fromkeys(candidate_signals or ["no_authoritative_attribution_signal"])),
            canonical_question=canonical_question,
            answer_intent=answer_intent,
        )

        if semantic_admitter is not None:
            sem = semantic_admitter(canonical_question, candidate, deterministic_pending)
            sem = self._apply_direct_attribution_ceiling(
                sem,
                direct_attribution_eligible=direct_attribution_eligible,
            )
            if sem is not None and valid_text_qualification_protocol(sem, target_entity=target):
                return sem

        if self._cfg is not None:
            sem = self._semantic_qualify_via_llm(
                candidate,
                target_entity=target,
                canonical_question=canonical_question,
                answer_intent=answer_intent,
                requested_facets=requested_facets,
                conflict_signals=conflict_signals,
                direct_signals=candidate_signals,
                direct_attribution_eligible=direct_attribution_eligible,
                semantic_task=semantic_task,
            )
            if sem is not None and valid_text_qualification_protocol(sem, target_entity=target):
                return sem

        return deterministic_pending

    @staticmethod
    def _apply_direct_attribution_ceiling(
        qualification: TextEvidenceQualification | None,
        *,
        direct_attribution_eligible: bool,
    ) -> TextEvidenceQualification | None:
        """Apply the one-way target-specific scope ceiling to every semantic provider."""
        if qualification is None or qualification.evidence_class != "TARGET_DIRECT":
            return qualification
        if direct_attribution_eligible:
            return qualification
        if qualification.verdict != "PASS" or qualification.intent_relevance not in {"HIGH", "MEDIUM"}:
            logger.warning("semantic candidate qualification attempted invalid direct attribution")
            return None

        logger.warning(
            "downgrading TARGET_DIRECT without an attribution candidate to RELATED_CONTEXT"
        )
        return replace(
            qualification,
            evidence_class="RELATED_CONTEXT",
            support_scope="CONTEXT_ONLY",
            reason_code="direct_scope_downgraded_without_attribution_candidate",
            reason=(
                "The helper found the candidate relevant, but it lacks a direct attribution "
                "candidate and cannot support a target-specific claim."
            ),
            signals=(*qualification.signals, "target_direct_scope_downgraded"),
        )

    def _semantic_qualify_via_llm(
        self,
        candidate: CandidateResult,
        *,
        target_entity: str,
        canonical_question: str,
        answer_intent: str,
        requested_facets: Sequence[str],
        conflict_signals: Sequence[str],
        direct_signals: Sequence[str],
        direct_attribution_eligible: bool,
        semantic_task: Any = None,
    ) -> TextEvidenceQualification | None:
        """Call configured helper LLM to qualify an ambiguous candidate."""
        from rag_knowledge.llm_http import chat_role

        meta = candidate.document.metadata or {}
        payload = {
            "target_entity": target_entity,
            "canonical_question": canonical_question,
            "answer_intent": answer_intent,
            "requested_facets": list(requested_facets),
            "candidate_text": candidate.document.page_content[:1800],
            "section_path": str(meta.get("section_path") or meta.get("section_title") or ""),
            "document_entity": str(meta.get("document_entity") or meta.get("entity_name") or ""),
            "mentioned_entities": list(meta.get("mentioned_entities") or []),
            "candidate_sources": candidate.source_generators,
            "graph_conflicts": list(conflict_signals),
            "graph_relatedness": [list(item.graph_path) for item in candidate.provenance if item.graph_path],
            "deterministic_signals": list(direct_signals),
            "target_direct_eligibility": direct_attribution_eligible,
        }
        prompt = (
            "判断候选片段能否作为当前问题的证据。不得扩大检索范围或改写主体。\n"
            f"目标实体已冻结：{target_entity or '（无）'}\n"
            f"解析问题已冻结：{canonical_question}\n"
            "证据分类规则：\n"
            "- 先从候选原文识别命题的主语，再判断该主语是否被原文明确标为目标实体、目标的无歧义别名，或与目标存在原文明确的归属关系。不得根据名称相近、同一领域、文档来源、检索命中或常识补全这种关系。\n"
            "- TARGET_DIRECT: 仅当上一步能从候选原文证明命题直接归属于目标实体时选择（verdict: PASS, support_scope: TARGET_SPECIFIC）。\n"
            "- RELATED_CONTEXT: 候选与目标所属领域/系统高度相关，但不能确认直接归属于目标模块（verdict: PASS, support_scope: CONTEXT_ONLY）。\n"
            "- CONFLICT: 明确属于与目标冲突的其他实体（verdict: REJECT, support_scope: NONE）。\n"
            "- IRRELEVANT: 与目标及当前问题均无证据价值（verdict: REJECT, support_scope: NONE）。\n\n"
            "归属反例：目标是“模块 A”，候选仅说“系统 B 支持功能 X”。即使 A 与 B 名称相近、属于同一系统或检索命中了 A，原文未证明 A 是 B 或功能 X 属于 A 时，必须是 RELATED_CONTEXT，不能是 TARGET_DIRECT。\n\n"
            "RELATED_CONTEXT 不要求候选写出目标的完整名称，也不要求已存在实体等同或图谱关系。只要候选描述的是目标同一核心对象/领域的系统能力，且能为当前问题提供限定性上下文，就应保留为 RELATED_CONTEXT。示例：目标“三维管线管理”，候选“管线系统支持碰撞分析和智能排管”，应为 RELATED_CONTEXT；它不能证明功能属于目标模块，因此绝不是 TARGET_DIRECT。\n\n"
            "target_direct_eligibility 是代码提供的单向安全约束：若为 false，表示候选没有任何可供检查的直接归属线索；此时 TARGET_DIRECT 非法，你只能在 RELATED_CONTEXT 和 IRRELEVANT 中选择。该约束不代表候选自动相关。\n\n"
            "只返回 JSON 格式：\n"
            "{\n"
            '  "verdict": "PASS" | "REJECT",\n'
            '  "evidence_class": "TARGET_DIRECT" | "RELATED_CONTEXT" | "CONFLICT" | "IRRELEVANT",\n'
            '  "support_scope": "TARGET_SPECIFIC" | "CONTEXT_ONLY" | "NONE",\n'
            '  "intent_relevance": "HIGH" | "MEDIUM" | "LOW" | "NONE",\n'
            '  "reason_code": "string",\n'
            '  "reason": "string",\n'
            '  "signals": ["string"]\n'
            "}\n\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        try:
            raw = chat_role(
                self._cfg,
                "helper_llm",
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                timeout=20.0,
                num_predict=256,
                format_json=True,
                json_schema=text_qualification_response_json_schema(
                    direct_attribution_eligible=direct_attribution_eligible,
                ),
            )
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.IGNORECASE)
            data = json.loads(cleaned)
            verdict = str(data.get("verdict") or "").upper()
            e_class = str(data.get("evidence_class") or "").upper()
            scope = str(data.get("support_scope") or "").upper()
            intent = str(data.get("intent_relevance") or "").upper()
            reason_code = str(data.get("reason_code") or "semantic_qualification")
            reason = str(data.get("reason") or "semantic_qualification_evaluated")
            signals = tuple(str(s) for s in (data.get("signals") or data.get("admission_signals") or ()) if str(s).strip())

            if verdict not in {"PASS", "REJECT"}:
                return None
            if e_class not in {"TARGET_DIRECT", "RELATED_CONTEXT", "CONFLICT", "IRRELEVANT"}:
                return None
            if scope not in {"TARGET_SPECIFIC", "CONTEXT_ONLY", "NONE"}:
                return None
            if intent not in {"HIGH", "MEDIUM", "LOW", "NONE"}:
                return None
            qual = TextEvidenceQualification(
                verdict=verdict,  # type: ignore[arg-type]
                evidence_class=e_class,  # type: ignore[arg-type]
                support_scope=scope,  # type: ignore[arg-type]
                intent_relevance=intent,  # type: ignore[arg-type]
                reason_code=reason_code,
                reason=reason,
                signals=signals,
                canonical_question=canonical_question,
                answer_intent=answer_intent,
            )
            qual = self._apply_direct_attribution_ceiling(
                qual,
                direct_attribution_eligible=direct_attribution_eligible,
            )
            return (
                qual
                if qual is not None and valid_text_qualification_protocol(qual, target_entity=target_entity)
                else None
            )
        except Exception as exc:
            logger.warning("semantic candidate qualification failed; failing closed: %s", exc)
            return None

    @staticmethod
    def admitted_documents(
        candidates: list[CandidateResult],
        qualifications: dict[str, TextEvidenceQualification],
    ) -> list[Document]:
        """Convert admitted candidates into Documents carrying qualification metadata."""
        docs: list[Document] = []
        for candidate in candidates:
            qual = qualifications.get(candidate.chunk_id)
            if qual is None or qual.verdict != "PASS":
                continue
            meta = dict(candidate.document.metadata or {})
            meta["candidate_sources"] = candidate.source_generators
            meta["candidate_provenance"] = [item.to_dict() for item in candidate.provenance]
            meta["candidate_fusion_score"] = candidate.fusion_score
            meta["structural_guard"] = list(candidate.structural_flags)
            meta["text_evidence_class"] = qual.evidence_class
            meta["support_scope"] = qual.support_scope
            meta["intent_relevance"] = qual.intent_relevance
            meta["admission_reason_code"] = qual.reason_code
            meta["admission_signals"] = list(qual.signals)
            meta["admission"] = qual.to_dict()
            meta["admission_verdict"] = qual.verdict
            docs.append(Document(page_content=candidate.document.page_content, metadata=meta))
        return docs
