from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from rag_knowledge.config import Config
from rag_knowledge.llm_http import chat
from rag_knowledge.services.answer_finalizer import AnswerFinalizer
from rag_knowledge.services.helper_grounding_reviewer import (
    ClaimReview,
    HelperGroundingReviewer,
    HelperGroundingReviewResult,
    RewriteAction,
    review_response_json_schema,
)
from rag_knowledge.services.model_routing import ModelRoutePolicy


DEFAULT_GOLD = Path("tests/fixtures/grounding_reviewer_gold_v1.json")
DEFAULT_REPORT = Path("data/eval_grounding_reviewer_gold_v1_report.json")

THRESHOLDS = {
    "grounded_candidate_correct_release_rate": (">=", 0.95),
    "unsupported_candidate_block_rate": (">=", 0.95),
    "contradicted_candidate_block_rate": (">=", 0.98),
    "incident_false_reject_rate": ("<=", 0.05),
    "gold_false_accept_rate": ("<=", 0.02),
    "reviewer_json_protocol_success_rate": (">=", 0.99),
    "strict_kb_candidate_reviewer_coverage_rate": (">=", 1.0),
    "pass_partial_correct_publish_rate": (">=", 0.95),
    "supported_content_no_safe_answer_rate": ("<=", 0.02),
    "deterministic_fallback_publication_rate": ("<=", 0.0),
}


def _normalize_verdict(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    aliases = {
        "PASS": "PASS",
        "REVISE": "REVISE",
        "UNSUPPORTED": "REVISE",
        "CONTRADICTED": "REVISE",
        "NO_SAFE_ANSWER": "NO_SAFE_ANSWER",
        "NO_SAFE": "NO_SAFE_ANSWER",
    }
    if normalized not in aliases:
        raise ValueError(f"invalid expected verdict: {value!r}")
    return aliases[normalized]


def _expected_coverages(item: dict[str, Any], expected_verdict: str) -> tuple[str, ...]:
    raw = item.get("expected_coverage")
    if raw is None:
        return ("NONE",) if expected_verdict == "NO_SAFE_ANSWER" else ("FULL", "PARTIAL")
    values = raw if isinstance(raw, list) else [raw]
    normalized = tuple(str(v).strip().upper() for v in values)
    allowed = {"FULL", "PARTIAL", "NONE"}
    if not normalized or any(v not in allowed for v in normalized):
        raise ValueError(f"invalid expected coverage for {item.get('id')}: {raw!r}")
    return normalized


def _context_docs(item: dict[str, Any]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for index, evidence in enumerate(item.get("evidence") or [], start=1):
        if isinstance(evidence, dict):
            content = str(evidence.get("content") or "")
            source = str(evidence.get("source") or f"gold-{index}.md")
            section = str(evidence.get("section") or "")
            evidence_id = int(evidence.get("evidence_id", index))
        else:
            content = str(evidence)
            source = f"gold-{index}.md"
            section = ""
            evidence_id = index
        docs.append({
            "content": content,
            "metadata": {
                "citation_id": evidence_id,
                "source": source,
                "section_path": section,
            },
        })
    return docs


def evaluate_items(
    items: list[dict[str, Any]],
    reviewer: HelperGroundingReviewer | Any,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in items:
        expected_verdict = _normalize_verdict(item.get("expected_verdict", item.get("expected")))
        expected_coverages = _expected_coverages(item, expected_verdict)
        try:
            review = reviewer.review(
                str(item.get("question") or ""),
                _context_docs(item),
                str(item.get("candidate") or item.get("claim") or ""),
            )
        except Exception as exc:  # fail closed in the evaluator too
            review = HelperGroundingReviewResult(
                verdict="ERROR",
                coverage="NONE",
                error=f"evaluator_reviewer_exception:{type(exc).__name__}",
            )

        predicted = str(review.verdict or "ERROR").upper()
        predicted_coverage = str(review.coverage or "NONE").upper()
        unsafe_expected = expected_verdict != "PASS"
        false_accept = unsafe_expected and predicted == "PASS"
        false_reject = expected_verdict == "PASS" and predicted != "PASS"
        protocol_success = predicted != "ERROR" and not review.error
        contract_match = (
            predicted == expected_verdict
            and predicted_coverage in expected_coverages
            and protocol_success
        )
        results.append({
            "id": item.get("id"),
            "category": item.get("category", ""),
            "expected_verdict": expected_verdict,
            "expected_coverages": list(expected_coverages),
            "expected_claim_status": item.get("expected_claim_status"),
            "predicted_verdict": predicted,
            "predicted_coverage": predicted_coverage,
            "contract_match": contract_match,
            "false_accept": false_accept,
            "false_reject": false_reject,
            "protocol_success": protocol_success,
            "historical_false_reject": bool(item.get("historical_false_reject", False)),
            "adversarial": bool(item.get("adversarial", False)),
            "has_supported_candidate_content": bool(item.get("has_supported_candidate_content", False)),
            "error": review.error,
            "summary": review.summary,
            "claim_reviews": [c.to_dict() for c in review.claim_reviews],
        })
    return results


def _rate(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _metric(
    name: str,
    numerator: int,
    denominator: int,
) -> dict[str, Any]:
    value = _rate(numerator, denominator)
    op, target = THRESHOLDS[name]
    passed = denominator > 0 and (value >= target if op == ">=" else value <= target)
    return {
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "target": f"{op} {target:.0%}",
        "passed": passed,
        "measurable": denominator > 0,
    }


def evaluate_publication_contract() -> dict[str, Any]:
    docs = [{"content": "事实 A。", "metadata": {"citation_id": 1, "source": "probe.md"}}]
    claim = ClaimReview("c1", "事实 A。", "knowledge_claim", "supported", (1,), "证据支持")
    unsupported = ClaimReview("c2", "事实 B。", "knowledge_claim", "unsupported", (), "证据不支持")

    pass_full = HelperGroundingReviewResult("PASS", "FULL", claim_reviews=[claim])
    pass_partial = HelperGroundingReviewResult("PASS", "PARTIAL", claim_reviews=[claim])
    no_safe = HelperGroundingReviewResult("NO_SAFE_ANSWER", "NONE", claim_reviews=[unsupported])
    revise = HelperGroundingReviewResult(
        "REVISE",
        "PARTIAL",
        claim_reviews=[claim, unsupported],
        rewrite_actions=[
            RewriteAction("c1", "preserve", "保留事实 A"),
            RewriteAction("c2", "rewrite_to_supported_scope_or_remove", "删除事实 B"),
        ],
    )

    cases = [
        (pass_full, None),
        (pass_partial, None),
        (no_safe, None),
        (revise, pass_full),
    ]
    review_calls = 0
    final_modes: list[str] = []
    for first, second in cases:
        queue = [first] + ([second] if second is not None else [])

        class _Reviewer:
            def review(self, _question, _docs, _candidate):
                nonlocal review_calls
                review_calls += 1
                return queue.pop(0)

        retry = (lambda _review: "事实 A。[1]") if second is not None else None
        finalized = AnswerFinalizer().finalize(
            "事实 A。[1]" if first.verdict != "REVISE" else "事实 A。[1] 事实 B。",
            "事实是什么？",
            docs,
            helper_reviewer=_Reviewer(),
            retry_candidate=retry,
        )
        final_modes.append(finalized.final_mode)

    expected_review_calls = 5
    return {
        "strict_kb_candidate_reviewer_coverage_rate": min(review_calls / expected_review_calls, 1.0),
        "deterministic_fallback_publication_rate": (
            sum(mode == "deterministic_fallback" for mode in final_modes) / len(final_modes)
        ),
        "review_calls": review_calls,
        "expected_review_calls": expected_review_calls,
        "final_modes": final_modes,
    }


def build_report(
    items: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    model_role: str,
    model: str,
    provider: str = "",
    publication_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    grounded = [r for r in results if r["expected_verdict"] == "PASS"]
    unsupported = [r for r in results if r.get("expected_claim_status") == "unsupported"]
    contradicted = [r for r in results if r.get("expected_claim_status") == "contradicted"]
    unsafe = [r for r in results if r["expected_verdict"] != "PASS"]
    incidents = [r for r in results if r["historical_false_reject"] and r["expected_verdict"] == "PASS"]
    partial = [
        r for r in results
        if r["expected_verdict"] == "PASS" and r["expected_coverages"] == ["PARTIAL"]
    ]
    supported_content = [r for r in results if r["has_supported_candidate_content"]]

    publication_contract = publication_contract or evaluate_publication_contract()
    metrics = {
        "grounded_candidate_correct_release_rate": _metric(
            "grounded_candidate_correct_release_rate",
            sum(r["predicted_verdict"] == "PASS" for r in grounded),
            len(grounded),
        ),
        "unsupported_candidate_block_rate": _metric(
            "unsupported_candidate_block_rate",
            sum(r["predicted_verdict"] in {"REVISE", "NO_SAFE_ANSWER"} for r in unsupported),
            len(unsupported),
        ),
        "contradicted_candidate_block_rate": _metric(
            "contradicted_candidate_block_rate",
            sum(r["predicted_verdict"] in {"REVISE", "NO_SAFE_ANSWER"} for r in contradicted),
            len(contradicted),
        ),
        "incident_false_reject_rate": _metric(
            "incident_false_reject_rate",
            sum(r["false_reject"] for r in incidents),
            len(incidents),
        ),
        "gold_false_accept_rate": _metric(
            "gold_false_accept_rate",
            sum(r["false_accept"] for r in unsafe),
            len(unsafe),
        ),
        "reviewer_json_protocol_success_rate": _metric(
            "reviewer_json_protocol_success_rate",
            sum(r["protocol_success"] for r in results),
            len(results),
        ),
        "strict_kb_candidate_reviewer_coverage_rate": _metric(
            "strict_kb_candidate_reviewer_coverage_rate",
            round(publication_contract["strict_kb_candidate_reviewer_coverage_rate"] * 1000000),
            1000000,
        ),
        "pass_partial_correct_publish_rate": _metric(
            "pass_partial_correct_publish_rate",
            sum(
                r["predicted_verdict"] == "PASS" and r["predicted_coverage"] == "PARTIAL"
                for r in partial
            ),
            len(partial),
        ),
        "supported_content_no_safe_answer_rate": _metric(
            "supported_content_no_safe_answer_rate",
            sum(
                r["predicted_verdict"] == "NO_SAFE_ANSWER" and r["predicted_coverage"] == "NONE"
                for r in supported_content
            ),
            len(supported_content),
        ),
        "deterministic_fallback_publication_rate": _metric(
            "deterministic_fallback_publication_rate",
            round(publication_contract["deterministic_fallback_publication_rate"] * 1000000),
            1000000,
        ),
    }

    report = {
        "scope": "helper_grounding_reviewer",
        "model_role": model_role,
        "provider": provider,
        "model": model,
        "total": len(items),
        "completed": len(results),
        "protocol_success_count": sum(r["protocol_success"] for r in results),
        "contract_match_count": sum(r["contract_match"] for r in results),
        "contract_match_rate": _rate(sum(r["contract_match"] for r in results), len(results)),
        "false_accept_count": sum(r["false_accept"] for r in results),
        "false_reject_count": sum(r["false_reject"] for r in results),
        "historical_false_reject_case_count": len(incidents),
        "adversarial_case_count": sum(r["adversarial"] for r in results),
        "metrics": metrics,
        "all_thresholds_passed": all(metric["passed"] for metric in metrics.values()),
        "publication_contract": publication_contract,
        "results": results,
    }
    return report


def _load_items(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("gold set root must be a JSON array")
    return payload


def _build_real_reviewer(cfg: Config) -> tuple[HelperGroundingReviewer, str, str, str]:
    role = ModelRoutePolicy(cfg).grounding_reviewer_role()
    if role != "helper_llm":
        raise RuntimeError(f"grounding reviewer must route to helper_llm, got {role}")
    endpoint = cfg.endpoint_for(role)

    def _caller(messages: list[dict[str, str]]) -> str:
        return chat(
            endpoint,
            messages,
            default_ollama=cfg.ollama_base_url,
            temperature=0.0,
            format_json=True,
            json_schema=review_response_json_schema(),
            timeout=cfg.grounding_reviewer_timeout,
            num_predict=4096,
            think=False,
        )

    return HelperGroundingReviewer(_caller), role, endpoint.provider, endpoint.model


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Helper LLM Grounding Reviewer against the PRD Gold Set.")
    parser.add_argument("--gold", default=str(DEFAULT_GOLD))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--config", default="config-local.ini")
    args = parser.parse_args()

    os.environ["RAG_CONFIG"] = args.config
    Config._instance = None
    cfg = Config()
    items = _load_items(Path(args.gold))
    reviewer, role, provider, model = _build_real_reviewer(cfg)
    results = evaluate_items(items, reviewer)
    report = build_report(
        items,
        results,
        model_role=role,
        provider=provider,
        model=model,
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "total": report["total"],
        "model_role": role,
        "provider": provider,
        "model": model,
        "all_thresholds_passed": report["all_thresholds_passed"],
        "metrics": report["metrics"],
        "report": str(report_path),
    }, ensure_ascii=False, indent=2))
    return 0 if report["all_thresholds_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
