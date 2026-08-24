from scripts.eval_grounding_reviewer import (
    build_report,
    evaluate_items,
    evaluate_publication_contract,
)
from rag_knowledge.services.helper_grounding_reviewer import (
    ClaimReview,
    HelperGroundingReviewResult,
)


class _MockReviewer:
    def __init__(self, results):
        self._results = iter(results)

    def review(self, _question, _docs, _candidate):
        return next(self._results)


def _result(verdict, coverage="FULL", status="supported", error=None):
    evidence_ids = (1,) if status == "supported" else ()
    claim = ClaimReview(
        claim_id="c1",
        claim="mock claim",
        claim_type="knowledge_claim",
        status=status,
        evidence_ids=evidence_ids,
        reason="mock",
    )
    return HelperGroundingReviewResult(
        verdict=verdict,
        coverage=coverage,
        summary="mock",
        claim_reviews=[claim],
        error=error,
    )


def _items():
    return [
        {
            "id": "grounded",
            "question": "端口是多少？",
            "candidate": "端口是 8080 [1]。",
            "evidence": ["端口为 8080。"],
            "expected_verdict": "PASS",
            "expected_coverage": "FULL",
            "historical_false_reject": True,
        },
        {
            "id": "partial",
            "question": "有哪些端口？",
            "candidate": "当前资料只确认 8080 [1]，未说明其他端口。",
            "evidence": ["端口为 8080。"],
            "expected_verdict": "PASS",
            "expected_coverage": "PARTIAL",
            "historical_false_reject": True,
            "has_supported_candidate_content": True,
        },
        {
            "id": "unsupported",
            "question": "端口是多少？",
            "candidate": "端口是 9999 [1]。",
            "evidence": ["端口为 8080。"],
            "expected_verdict": "REVISE",
            "expected_coverage": "PARTIAL",
            "expected_claim_status": "unsupported",
            "adversarial": True,
        },
        {
            "id": "contradicted",
            "question": "A 和 B 谁依赖谁？",
            "candidate": "B 依赖 A [1]。",
            "evidence": ["A 依赖 B。"],
            "expected_verdict": "REVISE",
            "expected_coverage": "PARTIAL",
            "expected_claim_status": "contradicted",
            "adversarial": True,
        },
    ]


def test_eval_grounding_reviewer_builds_all_prd_metrics():
    items = _items()
    results = evaluate_items(
        items,
        _MockReviewer([
            _result("PASS", "FULL"),
            _result("PASS", "PARTIAL"),
            _result("REVISE", "PARTIAL", "unsupported"),
            _result("REVISE", "PARTIAL", "contradicted"),
        ]),
    )
    report = build_report(items, results, model_role="helper_llm", model="test")

    assert report["total"] == 4
    assert report["contract_match_rate"] == 1.0
    assert report["false_accept_count"] == 0
    assert report["false_reject_count"] == 0
    assert set(report["metrics"]) == {
        "grounded_candidate_correct_release_rate",
        "unsupported_candidate_block_rate",
        "contradicted_candidate_block_rate",
        "incident_false_reject_rate",
        "gold_false_accept_rate",
        "reviewer_json_protocol_success_rate",
        "strict_kb_candidate_reviewer_coverage_rate",
        "pass_partial_correct_publish_rate",
        "supported_content_no_safe_answer_rate",
        "deterministic_fallback_publication_rate",
    }
    assert report["all_thresholds_passed"] is True


def test_eval_grounding_reviewer_counts_false_accept_and_false_reject():
    items = _items()
    results = evaluate_items(
        items,
        _MockReviewer([
            _result("REVISE", "PARTIAL", "unsupported"),
            _result("PASS", "PARTIAL"),
            _result("PASS", "FULL"),
            _result("REVISE", "PARTIAL", "contradicted"),
        ]),
    )
    report = build_report(items, results, model_role="helper_llm", model="test")

    assert report["false_accept_count"] == 1
    assert report["false_reject_count"] == 1
    assert report["metrics"]["gold_false_accept_rate"]["passed"] is False
    assert report["metrics"]["incident_false_reject_rate"]["passed"] is False
    assert report["all_thresholds_passed"] is False


def test_eval_grounding_reviewer_protocol_error_does_not_count_as_semantic_block():
    items = [_items()[2]]
    results = evaluate_items(
        items,
        _MockReviewer([HelperGroundingReviewResult(
            verdict="ERROR",
            coverage="NONE",
            error="invalid_review_protocol",
        )]),
    )
    report = build_report(items, results, model_role="helper_llm", model="test")

    assert report["metrics"]["reviewer_json_protocol_success_rate"]["value"] == 0.0
    assert report["metrics"]["unsupported_candidate_block_rate"]["value"] == 0.0


def test_publication_contract_has_full_reviewer_coverage_and_no_deterministic_fallback():
    contract = evaluate_publication_contract()

    assert contract["strict_kb_candidate_reviewer_coverage_rate"] == 1.0
    assert contract["deterministic_fallback_publication_rate"] == 0.0
    assert "generated" in contract["final_modes"]
    assert "grounded_partial" in contract["final_modes"]
    assert "grounded_rewrite" in contract["final_modes"]
    assert "review_blocked" in contract["final_modes"]
