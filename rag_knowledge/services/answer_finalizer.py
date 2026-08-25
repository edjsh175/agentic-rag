from __future__ import annotations

from dataclasses import dataclass, field, replace
import logging
import re
from typing import Any, Callable

from rag_knowledge.services.helper_grounding_reviewer import (
    HelperGroundingReviewResult,
    HelperGroundingReviewer,
)

logger = logging.getLogger(__name__)

NO_KNOWLEDGE_ANSWER = "当前知识库中未查询到相关内容。"
REVIEW_BLOCKED_ANSWER = "当前生成答案无法通过知识库证据审查，暂不发布未经证据支持的结论。"
REVIEWER_ERROR_ANSWER = "当前知识库证据审查服务异常，暂不发布未经证据支持的结论。"

_GENERAL_HEADING = "## 通用知识补充"
_KB_CITATION_TOKEN_RE = re.compile(r"\[(?:\d+)\]|\((?:\d+)\)")


@dataclass(frozen=True)
class FinalizedAnswer:
    answer: str
    grounding: dict[str, Any] = field(default_factory=dict)

    @property
    def final_mode(self) -> str:
        return self.grounding.get("final_mode", "")


class AnswerFinalizer:
    """Single publication gate for generated RAG answers (PRD V1.2 double-dimension + atomic claim protocol).

    In strict knowledge-base mode, model output is only a candidate. It is
    evaluated against evidence by the Helper LLM Grounding Reviewer.
    If the candidate passes with FULL coverage, it is published as 'generated'.
    If the candidate passes with PARTIAL coverage, it is published as 'grounded_partial'.
    If revisions are requested (REVISE), Main LLM is given exactly one chance to rewrite
    according to atomic claim rewrite actions, followed by a second review.
    Candidates that fail review are blocked with a controlled message (no chunk fallback dump).
    """

    def finalize(
        self,
        candidate: str,
        question: str,
        context_docs: list[dict[str, Any]],
        *,
        allow_general_knowledge: bool = False,
        is_direct_chat: bool = False,
        retry_candidate: Callable[[HelperGroundingReviewResult], str] | None = None,
        helper_reviewer: HelperGroundingReviewer | Callable[..., HelperGroundingReviewResult] | None = None,
        on_lifecycle_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> FinalizedAnswer:
        text = (candidate or "").strip()

        def _emit(evt: dict[str, Any]) -> None:
            if on_lifecycle_event is not None:
                try:
                    on_lifecycle_event(evt)
                except Exception as exc:
                    logger.debug("on_lifecycle_event callback failed: %s", exc)

        if is_direct_chat:
            _emit({
                "type": "publication",
                "data": {
                    "final_mode": "direct_chat",
                    "review_verdict": "PASS",
                    "coverage": "FULL",
                    "published_candidate_attempt": 1,
                    "message": "直接会话模式，发布答案。",
                },
            })
            return FinalizedAnswer(
                answer=text,
                grounding={
                    "policy": "direct_chat",
                    "verdict": "not_required",
                    "coverage": "FULL",
                    "final_mode": "direct_chat",
                    "fallback_used": False,
                    "candidate_attempts": 1,
                    "review_count": 0,
                    "review_attempts": 0,
                },
            )

        reviewer = helper_reviewer

        if allow_general_knowledge:
            _emit({
                "type": "candidate_status",
                "data": {
                    "version": 1,
                    "status": "generated",
                    "message": "Candidate V1 已生成，正在区分知识库证据与通用知识。",
                },
            })
            res = self._finalize_mixed(
                text,
                question,
                context_docs,
                reviewer=reviewer,
                emit=_emit,
            )
            _emit({
                "type": "publication",
                "data": {
                    "final_mode": res.final_mode,
                    "review_verdict": res.grounding.get("review_verdict", "PASS"),
                    "coverage": res.grounding.get("coverage", "PARTIAL"),
                    "published_candidate_attempt": 1,
                    "message": "混合知识模式，发布答案。",
                },
            })
            return res

        if not text or text == NO_KNOWLEDGE_ANSWER:
            _emit({
                "type": "publication",
                "data": {
                    "final_mode": "no_knowledge",
                    "review_verdict": "NONE",
                    "coverage": "NONE",
                    "published_candidate_attempt": 0,
                    "message": "未检索到相关内容，发布无知识回答。",
                },
            })
            return FinalizedAnswer(
                answer=NO_KNOWLEDGE_ANSWER,
                grounding={
                    "policy": "strict_kb",
                    "verdict": "empty",
                    "coverage": "NONE",
                    "reasons": ["empty_or_no_knowledge_candidate"],
                    "unsupported_segments": [],
                    "final_mode": "no_knowledge",
                    "fallback_used": False,
                    "candidate_attempts": 0,
                    "review_count": 0,
                    "review_attempts": 0,
                    "attempts": [],
                },
            )

        _emit({
            "type": "candidate_status",
            "data": {
                "version": 1,
                "status": "generated",
                "message": "Candidate V1 已生成，正在进行 Grounding Review 审查。",
            },
        })

        if reviewer is None:
            logger.warning("Helper Grounding Reviewer is not configured; failing closed.")
            _emit({
                "type": "error",
                "data": {
                    "code": "reviewer_not_configured",
                    "stage": "review",
                    "message": "证据审查服务未就绪，当前候选答案不会发布。",
                    "recoverable": False,
                },
            })
            _emit({
                "type": "publication",
                "data": {
                    "final_mode": "reviewer_error",
                    "review_verdict": "ERROR",
                    "coverage": "NONE",
                    "published_candidate_attempt": None,
                    "message": "证据审查服务未就绪，阻断发布。",
                },
            })
            return FinalizedAnswer(
                answer=REVIEWER_ERROR_ANSWER,
                grounding={
                    "policy": "strict_kb",
                    "verdict": "error",
                    "coverage": "NONE",
                    "reasons": ["reviewer_not_configured"],
                    "unsupported_segments": [],
                    "final_mode": "reviewer_error",
                    "fallback_used": False,
                    "candidate_attempts": 1,
                    "review_count": 0,
                    "review_attempts": 0,
                    "attempts": [{"attempt": 1, "verdict": "error", "reasons": ["reviewer_not_configured"]}],
                },
            )

        # 1. First review pass
        _emit({
            "type": "helper_grounding_review_started",
            "data": {
                "review_count": 1,
                "candidate_version": 1,
                "message": "正在核对 Candidate V1 与冻结证据快照。",
            },
        })
        review1 = self._invoke_reviewer(reviewer, question, context_docs, text)
        _emit(self._review_status_event(review1, review_count=1))
        if review1.error or review1.verdict == "ERROR":
            _emit({
                "type": "error",
                "data": {
                    "code": review1.error or "reviewer_error",
                    "stage": "review",
                    "message": "审核模型调用失败，当前候选答案不会发布。",
                    "recoverable": False,
                },
            })
            _emit({
                "type": "publication",
                "data": {
                    "final_mode": "reviewer_error",
                    "review_verdict": "ERROR",
                    "coverage": review1.coverage,
                    "published_candidate_attempt": None,
                    "message": "审查服务异常，阻断发布。",
                },
            })
            return FinalizedAnswer(
                answer=REVIEWER_ERROR_ANSWER,
                grounding={
                    "policy": "strict_kb",
                    "verdict": "error",
                    "coverage": review1.coverage,
                    "review_verdict": review1.verdict,
                    "review_count": 1,
                    "review_attempts": 1,
                    "reasons": [review1.error or "reviewer_error"],
                    "details": review1.to_dict(),
                    "final_mode": "reviewer_error",
                    "fallback_used": False,
                    "candidate_attempts": 1,
                    "attempts": [{"verdict": "error", "reasons": [review1.error or "reviewer_error"]}],
                },
            )

        attempt1_payload = {
            "attempt": 1,
            "verdict": review1.verdict.lower(),
            "coverage": review1.coverage,
            "summary": review1.summary,
            "unsupported_claims": [
                c.claim for c in review1.claim_reviews if c.status != "supported"
            ],
        }
        attempts = [attempt1_payload]
        last_review = review1
        review_count = 1
        candidate_attempts = 1

        if review1.verdict == "PASS":
            final_mode = "grounded_partial" if review1.is_partial else "generated"
            _emit({
                "type": "publication",
                "data": {
                    "final_mode": final_mode,
                    "review_verdict": "PASS",
                    "coverage": review1.coverage,
                    "published_candidate_attempt": 1,
                    "message": "初始答案已通过证据审核，正在发布。",
                },
            })
            return FinalizedAnswer(
                answer=text,
                grounding={
                    "policy": "strict_kb",
                    "verdict": "pass",
                    "coverage": review1.coverage,
                    "review_verdict": "PASS",
                    "review_count": 1,
                    "review_attempts": 1,
                    "reasons": [],
                    "unsupported_segments": [],
                    "claim_count": len(review1.claim_reviews),
                    "unsupported_count": len(review1.unsupported_claims),
                    "contradicted_count": len(review1.contradicted_claims),
                    "details": review1.to_dict(),
                    "candidate_attempts": 1,
                    "attempts": attempts,
                    "final_mode": final_mode,
                    "fallback_used": False,
                },
            )

        # 2. Directed atomic claim rewrite (at most 1 retry) if REVISE
        if review1.verdict == "REVISE" and retry_candidate is not None:
            candidate_attempts = 2
            _emit({
                "type": "rewrite_status",
                "data": {
                    "status": "started",
                    "mode": "atomic_claim_rewrite",
                    "message": "Candidate V1 未完全通过审核，正在保留受支持事实并修正未受支持内容。",
                },
            })
            try:
                retried = (retry_candidate(review1) or "").strip()
            except Exception as exc:
                logger.error("Grounded rewrite error: %s", exc)
                attempts.append({
                    "attempt": 2,
                    "verdict": "error",
                    "reasons": [f"grounded_retry_error:{type(exc).__name__}"],
                })
                _emit({
                    "type": "rewrite_status",
                    "data": {
                        "status": "failed",
                        "error": f"grounded_retry_error:{type(exc).__name__}",
                        "message": "重写过程异常，无法生成安全候选。",
                    },
                })
                _emit({
                    "type": "error",
                    "data": {
                        "code": f"grounded_retry_error:{type(exc).__name__}",
                        "stage": "rewrite",
                        "message": "答案重写失败，当前候选答案不会发布。",
                        "recoverable": False,
                    },
                })
                _emit({
                    "type": "publication",
                    "data": {
                        "final_mode": "review_blocked",
                        "review_verdict": "REVISE",
                        "coverage": "NONE",
                        "published_candidate_attempt": None,
                        "message": "重写失败，阻断发布。",
                    },
                })
                return FinalizedAnswer(
                    answer=REVIEW_BLOCKED_ANSWER,
                    grounding={
                        "policy": "strict_kb",
                        "verdict": "fail",
                        "coverage": "NONE",
                        "review_verdict": "REVISE",
                        "review_count": 1,
                        "review_attempts": 1,
                        "reasons": [f"grounded_retry_error:{type(exc).__name__}"],
                        "unsupported_segments": [],
                        "details": review1.to_dict(),
                        "candidate_attempts": candidate_attempts,
                        "attempts": attempts,
                        "final_mode": "review_blocked",
                        "fallback_used": False,
                    },
                )

            if retried and retried != NO_KNOWLEDGE_ANSWER:
                _emit({
                    "type": "rewrite_status",
                    "data": {
                        "status": "completed",
                        "candidate_version": 2,
                        "message": "重写完成，已生成 Candidate V2。",
                    },
                })
                _emit({
                    "type": "candidate_status",
                    "data": {
                        "version": 2,
                        "status": "generated",
                        "message": "Candidate V2 已生成，正在进行二审 Grounding Review。",
                    },
                })
                _emit({
                    "type": "helper_grounding_review_started",
                    "data": {
                        "review_count": 2,
                        "candidate_version": 2,
                        "message": "正在二次核对 Candidate V2 与同一冻结证据快照。",
                    },
                })
                review2 = self._invoke_reviewer(reviewer, question, context_docs, retried)
                review2 = self._freeze_review_coverage(review2, review1.coverage)
                last_review = review2
                review_count = 2
                _emit(self._review_status_event(review2, review_count=2))
                if review2.error or review2.verdict == "ERROR":
                    attempts.append({
                        "attempt": 2,
                        "verdict": "error",
                        "reasons": [review2.error or "reviewer_error"],
                    })
                    _emit({
                        "type": "error",
                        "data": {
                            "code": review2.error or "reviewer_error",
                            "stage": "review",
                            "message": "二审模型调用失败，当前候选答案不会发布。",
                            "recoverable": False,
                        },
                    })
                    _emit({
                        "type": "publication",
                        "data": {
                            "final_mode": "reviewer_error",
                            "review_verdict": review2.verdict,
                            "coverage": review2.coverage,
                            "published_candidate_attempt": None,
                            "message": "二审服务异常，阻断发布。",
                        },
                    })
                    return FinalizedAnswer(
                        answer=REVIEWER_ERROR_ANSWER,
                        grounding={
                            "policy": "strict_kb",
                            "verdict": "error",
                            "coverage": review2.coverage,
                            "review_verdict": review2.verdict,
                            "review_count": 2,
                            "review_attempts": 2,
                            "reasons": [review2.error or "reviewer_error"],
                            "details": review2.to_dict(),
                            "candidate_attempts": 2,
                            "attempts": attempts,
                            "final_mode": "reviewer_error",
                            "fallback_used": False,
                        },
                    )

                attempt2_payload = {
                    "attempt": 2,
                    "verdict": review2.verdict.lower(),
                    "coverage": review2.coverage,
                    "summary": review2.summary,
                    "unsupported_claims": [
                        c.claim for c in review2.claim_reviews if c.status != "supported"
                    ],
                }
                attempts.append(attempt2_payload)

                if review2.verdict == "PASS":
                    final_mode = "grounded_partial" if review2.is_partial else "grounded_rewrite"
                    _emit({
                        "type": "publication",
                        "data": {
                            "final_mode": final_mode,
                            "review_verdict": "PASS",
                            "coverage": review2.coverage,
                            "published_candidate_attempt": 2,
                            "message": "修正后的 Candidate V2 已通过审核，正在发布。",
                        },
                    })
                    return FinalizedAnswer(
                        answer=retried,
                        grounding={
                            "policy": "strict_kb",
                            "verdict": "pass",
                            "coverage": review2.coverage,
                            "review_verdict": "PASS",
                            "review_count": 2,
                            "review_attempts": 2,
                            "reasons": [],
                            "unsupported_segments": [],
                            "claim_count": len(review2.claim_reviews),
                            "unsupported_count": len(review2.unsupported_claims),
                            "contradicted_count": len(review2.contradicted_claims),
                            "details": review2.to_dict(),
                            "candidate_attempts": 2,
                            "attempts": attempts,
                            "final_mode": final_mode,
                            "fallback_used": False,
                        },
                    )

            else:
                attempts.append({
                    "attempt": 2,
                    "verdict": "error",
                    "reasons": ["rewrite_empty_candidate"],
                })
                _emit({
                    "type": "rewrite_status",
                    "data": {
                        "status": "failed",
                        "error": "rewrite_empty_candidate",
                        "message": "重写未生成有效 Candidate V2。",
                    },
                })
                _emit({
                    "type": "error",
                    "data": {
                        "code": "rewrite_empty_candidate",
                        "stage": "rewrite",
                        "message": "答案重写未产生有效内容，当前候选答案不会发布。",
                        "recoverable": False,
                    },
                })

        elif review1.verdict == "REVISE":
            _emit({
                "type": "rewrite_status",
                "data": {
                    "status": "failed",
                    "error": "rewrite_unavailable",
                    "message": "当前未配置答案重写能力，无法生成安全候选。",
                },
            })
            _emit({
                "type": "error",
                "data": {
                    "code": "rewrite_unavailable",
                    "stage": "rewrite",
                    "message": "答案重写服务不可用，当前候选答案不会发布。",
                    "recoverable": False,
                },
            })

        # 3. Blocked from publication
        last_verdict = last_review.verdict
        last_coverage = last_review.coverage
        _emit({
            "type": "publication",
            "data": {
                "final_mode": "review_blocked",
                "review_verdict": last_verdict,
                "coverage": last_coverage,
                "published_candidate_attempt": None,
                "message": "候选答案未能通过证据审核，已阻断发布。",
            },
        })
        return FinalizedAnswer(
            answer=REVIEW_BLOCKED_ANSWER,
            grounding={
                "policy": "strict_kb",
                "verdict": "blocked",
                "coverage": last_coverage,
                "review_verdict": last_verdict,
                "review_count": review_count,
                "review_attempts": review_count,
                "reasons": [
                    f"grounding_review_{last_verdict.lower()}",
                    f"unsupported_count_{len(last_review.unsupported_claims)}",
                ],
                "unsupported_segments": [c.claim for c in last_review.unsupported_claims],
                "details": last_review.to_dict(),
                "candidate_attempts": candidate_attempts,
                "attempts": attempts,
                "final_mode": "review_blocked",
                "fallback_used": False,
            },
        )

    @staticmethod
    def _freeze_review_coverage(
        result: HelperGroundingReviewResult,
        coverage: str,
    ) -> HelperGroundingReviewResult:
        """Keep Question × Frozen Evidence coverage invariant across Candidate rewrites."""
        if result.error or result.verdict == "ERROR":
            return result
        problem_claims = [
            claim
            for claim in result.claim_reviews
            if claim.status in {"unsupported", "contradicted"}
        ]
        if coverage == "NONE":
            verdict = "NO_SAFE_ANSWER"
        elif problem_claims:
            verdict = "REVISE"
        else:
            verdict = "PASS"
        if result.coverage == coverage and result.verdict == verdict:
            return result
        return replace(result, coverage=coverage, verdict=verdict)

    @staticmethod
    def _review_status_event(
        result: HelperGroundingReviewResult,
        *,
        review_count: int,
    ) -> dict[str, Any]:
        claims = list(getattr(result, "claim_reviews", []) or [])
        return {
            "type": "review_status",
            "data": {
                "reviewer_role": "helper_llm",
                "review_count": review_count,
                "verdict": result.verdict,
                "coverage": result.coverage,
                "claim_count": len(claims),
                "unsupported_count": sum(c.status == "unsupported" for c in claims),
                "contradicted_count": sum(c.status == "contradicted" for c in claims),
                "message": f"证据审核结果：{result.verdict} ({result.coverage})",
                "claim_reviews": [
                    {
                        "claim_id": claim.claim_id,
                        "claim": claim.claim,
                        "claim_type": claim.claim_type,
                        "status": claim.status,
                        "evidence_ids": list(claim.evidence_ids),
                    }
                    for claim in claims
                ],
                "rewrite_actions": [
                    {
                        "claim_id": action.claim_id,
                        "action": action.action,
                    }
                    for action in (getattr(result, "rewrite_actions", []) or [])
                ],
                "error": result.error,
            },
        }

    def _finalize_mixed(
        self,
        text: str,
        question: str,
        context_docs: list[dict[str, Any]],
        *,
        reviewer: HelperGroundingReviewer | Callable[..., HelperGroundingReviewResult] | None = None,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> FinalizedAnswer:
        """Keep general knowledge explicit while preserving citation semantics in mixed mode."""
        def _review(candidate_text: str) -> HelperGroundingReviewResult:
            if emit is not None:
                emit({
                    "type": "helper_grounding_review_started",
                    "data": {
                        "review_count": 1,
                        "candidate_version": 1,
                        "message": "正在核对知识库部分与冻结证据快照。",
                    },
                })
            result = self._invoke_reviewer(reviewer, question, context_docs, candidate_text)
            if emit is not None:
                emit(self._review_status_event(result, review_count=1))
                if result.error or result.verdict == "ERROR":
                    emit({
                        "type": "error",
                        "data": {
                            "code": result.error or "reviewer_error",
                            "stage": "review",
                            "message": "知识库部分审核失败，该部分不会作为已审查事实发布。",
                            "recoverable": False,
                        },
                    })
            return result

        if not text or text == NO_KNOWLEDGE_ANSWER:
            return FinalizedAnswer(
                answer=NO_KNOWLEDGE_ANSWER,
                grounding={
                    "policy": "mixed",
                    "verdict": "empty",
                    "coverage": "NONE",
                    "final_mode": "no_knowledge",
                    "fallback_used": False,
                },
            )

        if _GENERAL_HEADING in text:
            kb_part, general_part = text.split(_GENERAL_HEADING, 1)
            kb_part = kb_part.strip()
            general_part = self._strip_kb_citations(general_part).strip()

            kb_ok = False
            kb_details: dict[str, Any] = {}
            if kb_part and reviewer is not None:
                review = _review(kb_part)
                kb_ok = review.ok
                kb_details = review.to_dict()
            elif kb_part and reviewer is None:
                kb_ok = True

            parts: list[str] = []
            if kb_ok and kb_part:
                parts.append(kb_part)
            else:
                parts.append(NO_KNOWLEDGE_ANSWER)

            general_section = self._general_section(general_part)
            if general_section:
                parts.append(general_section)

            return FinalizedAnswer(
                answer="\n\n".join(parts),
                grounding={
                    "policy": "mixed",
                    "verdict": "pass" if kb_ok else "partial",
                    "details": kb_details,
                    "final_mode": "mixed_separated",
                    "fallback_used": False,
                },
            )

        # No general heading in text
        if reviewer is not None:
            review = _review(text)
            if review.ok:
                return FinalizedAnswer(
                    answer=text,
                    grounding={
                        "policy": "mixed",
                        "verdict": "pass",
                        "reasons": [],
                        "details": review.to_dict(),
                        "final_mode": "grounded",
                        "fallback_used": False,
                    },
                )
            # Re-label candidate text as general knowledge
            general_body = self._strip_kb_citations(text).strip()
            parts = [NO_KNOWLEDGE_ANSWER]
            general_section = self._general_section(general_body)
            if general_section:
                parts.append(general_section)
            return FinalizedAnswer(
                answer="\n\n".join(parts),
                grounding={
                    "policy": "mixed",
                    "verdict": "partial",
                    "reasons": [review.verdict.lower()],
                    "details": review.to_dict(),
                    "final_mode": "mixed_relabel",
                    "fallback_used": False,
                },
            )

        return FinalizedAnswer(
            answer=text,
            grounding={
                "policy": "mixed",
                "verdict": "pass",
                "final_mode": "grounded",
                "fallback_used": False,
            },
        )

    @staticmethod
    def _invoke_reviewer(
        reviewer: HelperGroundingReviewer | Callable[..., HelperGroundingReviewResult],
        question: str,
        context_docs: list[dict[str, Any]],
        candidate: str,
    ) -> HelperGroundingReviewResult:
        try:
            if hasattr(reviewer, "review"):
                return reviewer.review(question, context_docs, candidate)
            return reviewer(question, context_docs, candidate)
        except Exception as exc:
            logger.error("Reviewer invocation exception: %s", exc)
            return HelperGroundingReviewResult(
                verdict="ERROR",
                coverage="NONE",
                summary=f"审查异常: {type(exc).__name__}",
                error=f"reviewer_error:{type(exc).__name__}",
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
