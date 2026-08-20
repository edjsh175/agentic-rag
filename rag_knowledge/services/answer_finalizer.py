from dataclasses import dataclass, field
import re
from typing import Any, Callable

from rag_knowledge.services.evidence_pack import (
    NO_KNOWLEDGE_ANSWER,
    GroundingVerdict,
    synthesize_grounded_fallback,
    verify_grounding,
)


_GENERAL_HEADING = "## 通用知识补充"
_KB_CITATION_TOKEN_RE = re.compile(r"\[(?:\d+)\]|\((?:\d+)\)")


@dataclass(frozen=True)
class FinalizedAnswer:
    answer: str
    grounding: dict[str, Any] = field(default_factory=dict)


class AnswerFinalizer:
    """Single publication gate for generated RAG answers.

    In strict knowledge-base mode, model output is only a candidate. It may be
    published only after grounding verification; failed candidates are replaced
    by deterministic evidence text. In mixed mode, unsupported model knowledge
    is allowed only after it is explicitly separated from knowledge-base evidence.
    """

    def finalize(
        self,
        candidate: str,
        question: str,
        context_docs: list[dict[str, Any]],
        *,
        allow_general_knowledge: bool,
        is_direct_chat: bool = False,
        retry_candidate: Callable[[GroundingVerdict], str] | None = None,
        semantic_verify: Callable[[str, list[dict[str, Any]]], GroundingVerdict] | None = None,
    ) -> FinalizedAnswer:
        text = (candidate or "").strip()

        if is_direct_chat:
            return FinalizedAnswer(
                answer=text,
                grounding={
                    "policy": "direct_chat",
                    "verdict": "not_required",
                    "final_mode": "generated",
                    "fallback_used": False,
                },
            )

        if allow_general_knowledge:
            return self._finalize_mixed(
                text,
                question,
                context_docs,
                semantic_verify=semantic_verify,
            )

        if not text or text == NO_KNOWLEDGE_ANSWER:
            fallback = synthesize_grounded_fallback(context_docs, question)
            return FinalizedAnswer(
                answer=fallback,
                grounding={
                    "policy": "strict_kb",
                    "verdict": "fallback",
                    "reasons": ["empty_or_no_knowledge_candidate"],
                    "unsupported_segments": [],
                    "final_mode": "deterministic_fallback",
                    "fallback_used": True,
                },
            )

        verdict = self._verify_candidate(text, context_docs, semantic_verify)
        attempts = [self._attempt_payload(verdict)]
        if verdict.ok:
            return FinalizedAnswer(
                answer=text,
                grounding=self._grounding_payload(
                    verdict,
                    final_mode="generated",
                    fallback_used=False,
                    attempts=attempts,
                ),
            )

        if retry_candidate is not None:
            try:
                retried = (retry_candidate(verdict) or "").strip()
            except Exception as exc:  # retry failure must degrade, never reopen publication
                verdict = GroundingVerdict(
                    ok=False,
                    reasons=list(dict.fromkeys([
                        *verdict.reasons,
                        f"grounded_retry_error:{type(exc).__name__}",
                    ])),
                    unsupported_segments=list(verdict.unsupported_segments),
                    valid_citation_ids=set(verdict.valid_citation_ids),
                    details=dict(verdict.details),
                )
                attempts.append({
                    "verdict": "error",
                    "reasons": [f"grounded_retry_error:{type(exc).__name__}"],
                    "unsupported_segments": [],
                })
                retried = ""
            if retried and retried != NO_KNOWLEDGE_ANSWER:
                retry_verdict = self._verify_candidate(
                    retried,
                    context_docs,
                    semantic_verify,
                )
                attempts.append(self._attempt_payload(retry_verdict))
                if retry_verdict.ok:
                    return FinalizedAnswer(
                        answer=retried,
                        grounding=self._grounding_payload(
                            retry_verdict,
                            final_mode="grounded_retry",
                            fallback_used=False,
                            attempts=attempts,
                        ),
                    )
                verdict = retry_verdict

        fallback = synthesize_grounded_fallback(context_docs, question)
        return FinalizedAnswer(
            answer=fallback,
            grounding=self._grounding_payload(
                verdict,
                final_mode="deterministic_fallback",
                fallback_used=True,
                attempts=attempts,
            ),
        )

    def _finalize_mixed(
        self,
        text: str,
        question: str,
        context_docs: list[dict[str, Any]],
        *,
        semantic_verify: Callable[[str, list[dict[str, Any]]], GroundingVerdict] | None = None,
    ) -> FinalizedAnswer:
        """Keep general knowledge explicit while preserving citation semantics."""
        if not text or text == NO_KNOWLEDGE_ANSWER:
            return FinalizedAnswer(
                answer=synthesize_grounded_fallback(context_docs, question),
                grounding={
                    "policy": "mixed",
                    "verdict": "fallback",
                    "final_mode": "deterministic_fallback",
                    "fallback_used": True,
                },
            )

        if _GENERAL_HEADING in text:
            kb_part, general_part = text.split(_GENERAL_HEADING, 1)
            kb_part = kb_part.strip()
            general_part = self._strip_kb_citations(general_part).strip()
            kb_answer, kb_verdict, fallback_used = self._safe_grounded_or_fallback(
                kb_part,
                question,
                context_docs,
                semantic_verify=semantic_verify,
            )
            parts = [part for part in (kb_answer, self._general_section(general_part)) if part]
            return FinalizedAnswer(
                answer="\n\n".join(parts),
                grounding={
                    "policy": "mixed",
                    "verdict": "pass" if kb_verdict is not None and kb_verdict.ok else "partial",
                    "reasons": list(kb_verdict.reasons) if kb_verdict is not None else [],
                    "unsupported_segments": (
                        list(kb_verdict.unsupported_segments) if kb_verdict is not None else []
                    ),
                    "final_mode": "mixed_separated",
                    "fallback_used": fallback_used,
                },
            )

        verdict = self._verify_candidate(text, context_docs, semantic_verify)
        if verdict.ok:
            return FinalizedAnswer(
                answer=text,
                grounding={
                    "policy": "mixed",
                    "verdict": "pass",
                    "reasons": [],
                    "unsupported_segments": [],
                    "final_mode": "grounded",
                    "fallback_used": False,
                },
            )

        kb_fallback = synthesize_grounded_fallback(context_docs, question)
        general_body = self._strip_kb_citations(text).strip()
        parts = [kb_fallback]
        general_section = self._general_section(general_body)
        if general_section:
            parts.append(general_section)
        return FinalizedAnswer(
            answer="\n\n".join(part for part in parts if part),
            grounding={
                "policy": "mixed",
                "verdict": "partial",
                "reasons": list(verdict.reasons),
                "unsupported_segments": list(verdict.unsupported_segments),
                "final_mode": "mixed_relabel",
                "fallback_used": True,
            },
        )

    def _safe_grounded_or_fallback(
        self,
        text: str,
        question: str,
        context_docs: list[dict[str, Any]],
        *,
        semantic_verify: Callable[[str, list[dict[str, Any]]], GroundingVerdict] | None = None,
    ) -> tuple[str, GroundingVerdict | None, bool]:
        if not text or text == NO_KNOWLEDGE_ANSWER:
            return synthesize_grounded_fallback(context_docs, question), None, True
        verdict = self._verify_candidate(text, context_docs, semantic_verify)
        if verdict.ok:
            return text, verdict, False
        return synthesize_grounded_fallback(context_docs, question), verdict, True

    @staticmethod
    def _safe_verify(text: str, context_docs: list[dict[str, Any]]) -> GroundingVerdict:
        try:
            return verify_grounding(text, context_docs)
        except Exception as exc:
            return GroundingVerdict(
                ok=False,
                reasons=[f"grounding_verifier_error:{type(exc).__name__}"],
                unsupported_segments=[text[:120]],
            )

    def _verify_candidate(
        self,
        text: str,
        context_docs: list[dict[str, Any]],
        semantic_verify: Callable[[str, list[dict[str, Any]]], GroundingVerdict] | None,
    ) -> GroundingVerdict:
        deterministic = self._safe_verify(text, context_docs)
        if not deterministic.ok or semantic_verify is None:
            return deterministic
        try:
            semantic = semantic_verify(text, context_docs)
            if not isinstance(semantic, GroundingVerdict):
                raise TypeError("semantic verifier must return GroundingVerdict")
        except Exception as exc:
            return GroundingVerdict(
                ok=False,
                reasons=[f"semantic_verifier_error:{type(exc).__name__}"],
                unsupported_segments=[text[:120]],
                valid_citation_ids=set(deterministic.valid_citation_ids),
                details={"deterministic": dict(deterministic.details)},
            )
        if not semantic.ok:
            return GroundingVerdict(
                ok=False,
                reasons=list(dict.fromkeys(semantic.reasons)),
                unsupported_segments=list(semantic.unsupported_segments),
                valid_citation_ids=set(deterministic.valid_citation_ids),
                details={
                    "deterministic": dict(deterministic.details),
                    "semantic": dict(semantic.details),
                },
            )
        return GroundingVerdict(
            ok=True,
            valid_citation_ids=set(deterministic.valid_citation_ids),
            details={
                "deterministic": dict(deterministic.details),
                "semantic": dict(semantic.details),
            },
        )

    @staticmethod
    def _strip_kb_citations(text: str) -> str:
        cleaned = _KB_CITATION_TOKEN_RE.sub("", text or "")
        return re.sub(r"[ \t]+", " ", cleaned).strip()

    @staticmethod
    def _general_section(text: str) -> str:
        body = (text or "").strip()
        if not body:
            return ""
        return (
            f"{_GENERAL_HEADING}\n"
            "（以下内容来自模型通用知识，不属于知识库检索证据。）\n"
            f"{body}"
        )

    @staticmethod
    def _grounding_payload(
        verdict: GroundingVerdict,
        *,
        final_mode: str,
        fallback_used: bool,
        attempts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "policy": "strict_kb",
            "verdict": "pass" if verdict.ok else "fail",
            "reasons": list(verdict.reasons),
            "unsupported_segments": list(verdict.unsupported_segments),
            "details": dict(verdict.details),
            "candidate_attempts": len(attempts or []),
            "attempts": list(attempts or []),
            "final_mode": final_mode,
            "fallback_used": fallback_used,
        }

    @staticmethod
    def _attempt_payload(verdict: GroundingVerdict) -> dict[str, Any]:
        return {
            "verdict": "pass" if verdict.ok else "fail",
            "reasons": list(verdict.reasons),
            "unsupported_segments": list(verdict.unsupported_segments),
        }
