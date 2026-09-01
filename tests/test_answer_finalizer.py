import pytest

from rag_knowledge.services.answer_finalizer import (
    AnswerFinalizer,
    NO_KNOWLEDGE_ANSWER,
    REVIEW_BLOCKED_ANSWER,
    REVIEWER_ERROR_ANSWER,
)
from rag_knowledge.services.helper_grounding_reviewer import (
    HelperGroundingReviewer,
    HelperGroundingReviewResult,
    ClaimReview,
    RetrievalFeedback,
    RewriteAction,
)


def _source(index: int, content: str):
    # 通过 Text Admission 的 KB 文本必须携带协议字段（evidence_class + support_scope）。
    return {
        "content": content,
        "metadata": {
            "citation_id": index,
            "source": f"doc-{index}.md",
            "evidence_class": "TARGET_DIRECT",
            "support_scope": "TARGET_SPECIFIC",
        },
    }


def _pass_reviewer(coverage: str = "FULL"):
    return HelperGroundingReviewer(lambda _msgs: f"""{{
        "verdict": "PASS",
        "coverage": "{coverage}",
        "summary": "通过",
        "repair_mode": "NONE",
        "claim_reviews": [{{"claim_id": "c1", "claim": "测试", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [1], "status": "supported", "reason": "支持"}}],
        "rewrite_actions": []
    }}""")


def _revise_reviewer():
    return HelperGroundingReviewer(lambda _msgs: """{
        "verdict": "REVISE",
        "coverage": "PARTIAL",
        "repair_mode": "REWRITE",
        "summary": "需要修改",
        "repair_mode": "REWRITE",
        "claim_reviews": [
            {"claim_id": "c1", "claim": "受支持断言", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [1], "status": "supported", "reason": "依据充分"},
            {"claim_id": "c2", "claim": "未支持断言", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [], "status": "unsupported", "reason": "无依据"}
        ],
        "rewrite_actions": [
            {"claim_id": "c2", "action": "rewrite_to_supported_scope_or_remove", "instruction": "删除未支持断言"}
        ]
    }""")


def _no_safe_answer_reviewer():
    return HelperGroundingReviewer(lambda _msgs: """{
        "verdict": "NO_SAFE_ANSWER",
        "coverage": "NONE",
        "repair_mode": "NONE",
        "summary": "无法安全回答",
        "repair_mode": "NONE",
        "claim_reviews": [
            {"claim_id": "c1", "claim": "第一版回答中的事实主张", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [], "status": "unsupported", "reason": "当前证据无法支持该主张"}
        ],
        "rewrite_actions": []
    }""")


def _error_reviewer():
    def _raise(_msgs):
        raise TimeoutError("timeout")
    return HelperGroundingReviewer(_raise)


def test_review_status_event_projects_evidence_support_scopes():
    result = HelperGroundingReviewResult(
        verdict="PASS",
        coverage="PARTIAL",
        summary="ok",
        claim_reviews=[
            ClaimReview(
                claim_id="c1",
                claim="相关系统资料涉及碰撞分析。",
                claim_type="knowledge_claim",
                status="supported",
                evidence_ids=(1,),
                reason="supported",
            )
        ],
    )
    event = AnswerFinalizer._review_status_event(
        result,
        review_count=1,
        context_docs=[{
            "content": "管线系统支持碰撞分析。",
            "metadata": {"citation_id": 1, "support_scope": "CONTEXT_ONLY"},
        }],
    )

    claim = event["data"]["claim_reviews"][0]
    assert claim["evidence_ids"] == [1]
    assert claim["evidence_support_scopes"] == ["CONTEXT_ONLY"]


def test_direct_chat_passes_through():
    finalizer = AnswerFinalizer()
    res = finalizer.finalize(
        "你好！我是智能助手。",
        "你好",
        [],
        is_direct_chat=True,
    )
    assert res.answer == "你好！我是智能助手。"
    assert res.final_mode == "direct_chat"


def test_no_knowledge_candidate():
    finalizer = AnswerFinalizer()
    res = finalizer.finalize(
        NO_KNOWLEDGE_ANSWER,
        "测试问题",
        [_source(1, "文档内容")],
    )
    assert res.answer == NO_KNOWLEDGE_ANSWER
    assert res.final_mode == "no_safe_answer"


def test_candidate_v1_pass_full():
    finalizer = AnswerFinalizer()
    docs = [_source(1, "StampServer 支持发布服务。")]
    res = finalizer.finalize(
        "StampServer 支持发布服务。[1]",
        "StampServer 支持发布吗？",
        docs,
        helper_reviewer=_pass_reviewer("FULL"),
    )
    assert "StampServer 支持发布服务" in res.answer
    assert res.final_mode == "generated"
    assert res.grounding["review_verdict"] == "PASS"
    assert res.grounding["coverage"] == "FULL"


def test_candidate_v1_pass_partial_publishes_grounded_partial():
    finalizer = AnswerFinalizer()
    docs = [_source(1, "StampWebRTC 示例使用 31443 端口。")]
    res = finalizer.finalize(
        "StampWebRTC 示例使用 31443 端口 [1]。当前资料未说明其他 UDP 端口。",
        "StampWebRTC UDP 端口有哪些？",
        docs,
        helper_reviewer=_pass_reviewer("PARTIAL"),
    )
    assert "31443" in res.answer
    assert res.final_mode == "grounded_partial"
    assert res.grounding["review_verdict"] == "PASS"
    assert res.grounding["coverage"] == "PARTIAL"


def test_candidate_v1_revise_and_v2_pass_keeps_frozen_partial_coverage():
    finalizer = AnswerFinalizer()
    docs = [_source(1, "StampServer 支持在线发布。")]

    review_count = 0

    def _caller(_msgs):
        nonlocal review_count
        review_count += 1
        if review_count == 1:
            return """{
                "verdict": "REVISE",
                "coverage": "PARTIAL",
        "repair_mode": "REWRITE",
                "summary": "请删除离线发布",
                "claim_reviews": [
                    {"claim_id": "c1", "claim": "支持在线发布", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [1], "status": "supported", "reason": "支持"},
                    {"claim_id": "c2", "claim": "支持离线发布", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [], "status": "unsupported", "reason": "无依据"}
                ],
                "rewrite_actions": [
                    {"claim_id": "c2", "action": "rewrite_to_supported_scope_or_remove", "instruction": "删除离线发布"}
                ]
            }"""
        else:
            return """{
                "verdict": "PASS",
                "coverage": "FULL",
        "repair_mode": "NONE",
                "summary": "修改后通过",
                "claim_reviews": [{"claim_id": "c1", "claim": "支持在线发布", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [1], "status": "supported", "reason": "支持"}],
                "rewrite_actions": []
            }"""

    reviewer = HelperGroundingReviewer(_caller)

    retry_called = []

    def _retry(review_result):
        retry_called.append(review_result)
        assert len(review_result.rewrite_actions) == 1
        assert [action.claim_id for action in review_result.rewrite_actions] == ["c2"]
        return "StampServer 支持在线发布。[1]"

    res = finalizer.finalize(
        "StampServer 支持在线发布 [1]，也支持离线发布。",
        "StampServer 发布能力？",
        docs,
        retry_candidate=_retry,
        helper_reviewer=reviewer,
    )

    assert len(retry_called) == 1
    assert res.answer == "StampServer 支持在线发布。[1]"
    assert res.final_mode == "grounded_partial"
    assert res.grounding["review_verdict"] == "PASS"
    assert res.grounding["review_attempts"] == 2
    assert res.grounding["coverage"] == "PARTIAL"


def test_candidate_v1_revise_and_v2_pass_partial():
    finalizer = AnswerFinalizer()
    docs = [_source(1, "StampWebRTC 示例使用 31443 端口。")]

    review_count = 0

    def _caller(_msgs):
        nonlocal review_count
        review_count += 1
        if review_count == 1:
            return """{
                "verdict": "REVISE",
                "coverage": "PARTIAL",
        "repair_mode": "REWRITE",
                "summary": "请删除 3478 端口",
                "claim_reviews": [
                    {"claim_id": "c1", "claim": "访问使用 31443 端口", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [1], "status": "supported", "reason": "支持"},
                    {"claim_id": "c2", "claim": "必须开放 3478", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [], "status": "unsupported", "reason": "无依据"}
                ],
                "rewrite_actions": [
                    {"claim_id": "c2", "action": "rewrite_to_supported_scope_or_remove", "instruction": "删除 3478，若无其他端口说明资料未确认"}
                ]
            }"""
        else:
            return """{
                "verdict": "PASS",
                "coverage": "PARTIAL",
        "repair_mode": "NONE",
                "summary": "部分通过",
                "claim_reviews": [
                    {"claim_id": "c1", "claim": "访问使用 31443 端口", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [1], "status": "supported", "reason": "支持"},
                    {"claim_id": "c3", "claim": "当前资料未说明其他 UDP 端口", "evidence_ids": [], "status": "supported", "claim_type": "limitation_statement", "reason": "合理边界"}
                ],
                "rewrite_actions": []
            }"""

    reviewer = HelperGroundingReviewer(_caller)

    def _retry(review_result):
        return "StampWebRTC 访问示例使用 31443 端口 [1]。当前资料未确认其他 UDP 端口。"

    res = finalizer.finalize(
        "StampWebRTC 访问使用 31443 端口 [1]，必须开放 3478 端口。",
        "StampWebRTC UDP 部署需要配置哪些端口？",
        docs,
        retry_candidate=_retry,
        helper_reviewer=reviewer,
    )

    assert res.answer == "StampWebRTC 访问示例使用 31443 端口 [1]。当前资料未确认其他 UDP 端口。"
    assert res.final_mode == "grounded_partial"
    assert res.grounding["review_verdict"] == "PASS"
    assert res.grounding["coverage"] == "PARTIAL"


def test_candidate_v2_cannot_change_frozen_coverage_to_none():
    finalizer = AnswerFinalizer()
    docs = [_source(1, "StampServer 提供授权服务。")]
    review_count = 0

    def _caller(_msgs):
        nonlocal review_count
        review_count += 1
        if review_count == 1:
            return """{
                "coverage": "PARTIAL",
        "repair_mode": "REWRITE",
                "summary": "需要修改",
                "claim_reviews": [
                    {"claim_id": "c1", "claim": "提供授权服务", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [1], "status": "supported", "reason": "支持"},
                    {"claim_id": "c2", "claim": "负责模型处理", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [], "status": "unsupported", "reason": "无依据"}
                ],
                "rewrite_actions": [
                    {"claim_id": "c2", "action": "rewrite_to_supported_scope_or_remove", "instruction": "删除模型处理"}
                ]
            }"""
        return """{
            "coverage": "NONE",
        "repair_mode": "NONE",
            "summary": "错误地改变 coverage",
            "claim_reviews": [
                {"claim_id": "c1", "claim": "提供授权服务", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [1], "status": "supported", "reason": "支持"},
                {"claim_id": "c2", "claim": "仍有未支持内容", "claim_type": "knowledge_claim", "claim_scope": "TARGET_ATTRIBUTION", "evidence_ids": [], "status": "unsupported", "reason": "无依据"}
            ],
            "rewrite_actions": []
        }"""

    res = finalizer.finalize(
        "StampServer 提供授权服务，同时负责模型处理。",
        "StampServer 的主要用途是什么？",
        docs,
        retry_candidate=lambda _review: "StampServer 提供授权服务，但仍有未支持内容。",
        helper_reviewer=HelperGroundingReviewer(_caller),
    )

    assert res.final_mode == "no_safe_answer"
    assert res.grounding["review_verdict"] == "REVISE"
    assert res.grounding["coverage"] == "PARTIAL"
    assert res.grounding["attempts"][1]["coverage"] == "PARTIAL"


def test_candidate_v1_revise_and_v2_fail_blocks():
    finalizer = AnswerFinalizer()
    docs = [_source(1, "StampServer 资料。")]

    reviewer = _revise_reviewer()

    def _retry(review_result):
        return "依然未受支持的回答。"

    res = finalizer.finalize(
        "第一版回答。",
        "测试问题",
        docs,
        retry_candidate=_retry,
        helper_reviewer=reviewer,
    )

    assert res.answer == REVIEW_BLOCKED_ANSWER
    assert res.final_mode == "no_safe_answer"
    assert res.grounding["review_verdict"] == "REVISE"
    assert res.grounding["coverage"] == "PARTIAL"
    assert res.grounding["review_attempts"] == 2


def test_candidate_no_safe_answer_blocks_immediately():
    finalizer = AnswerFinalizer()
    docs = [_source(1, "StampServer 资料。")]

    res = finalizer.finalize(
        "第一版回答。",
        "测试问题",
        docs,
        helper_reviewer=_no_safe_answer_reviewer(),
    )

    assert res.answer == REVIEW_BLOCKED_ANSWER
    assert res.final_mode == "no_safe_answer"
    assert res.grounding["review_verdict"] == "NO_SAFE_ANSWER"
    assert res.grounding["review_attempts"] == 1
    assert res.retrieval_feedback is None


def test_retrieval_feedback_lifecycle_never_selects_or_runs_a_tool():
    events = []

    reviewer = lambda *_args: HelperGroundingReviewResult(
        verdict="REVISE",
        coverage="PARTIAL",
        summary="缺直接证据",
        claim_reviews=[ClaimReview(
            claim_id="c1", claim="端口", claim_type="knowledge_claim",
            claim_scope="TARGET_ATTRIBUTION", status="unsupported",
            evidence_ids=(), reason="未找到直接证据",
        )],
        repair_mode="RETRIEVE",
        retrieval_feedback=RetrievalFeedback(
            gap_id="gap-port", affected_claim_ids=("c1",), missing_fact="默认端口",
            subject_entity_ids=("stamp-server",), deficiency_type="NO_DIRECT_EVIDENCE",
            reason="快照没有直接证据",
        ),
    )

    result = AnswerFinalizer().finalize(
        "第一版回答。",
        "测试问题",
        [_source(1, "StampServer 资料。")],
        helper_reviewer=reviewer,
        on_lifecycle_event=events.append,
    )

    feedback = next(event for event in events if event["type"] == "retrieval_feedback")
    assert feedback["data"] == {
        "status": "requested",
        "gap_id": "gap-port",
        "affected_claim_ids": ["c1"],
        "message": "当前冻结证据不足，已形成检索缺口反馈，等待 Main Controller 决定是否补检。",
    }
    assert result.grounding["review_verdict"] == "REVISE"
    assert result.grounding["repair_mode"] == "RETRIEVE"


def test_reviewer_error_fails_closed():
    finalizer = AnswerFinalizer()
    docs = [_source(1, "StampServer 资料。")]

    res = finalizer.finalize(
        "候选答案。[1]",
        "测试问题",
        docs,
        helper_reviewer=_error_reviewer(),
    )

    assert res.answer == REVIEWER_ERROR_ANSWER
    assert res.final_mode == "reviewer_error"
    assert res.grounding["review_verdict"] == "ERROR"


def test_protocol_error_cannot_publish_candidate():
    reviewer = HelperGroundingReviewer(lambda _msgs: '"verdict": "PASS"')
    res = AnswerFinalizer().finalize(
        "UNSUPPORTED CANDIDATE",
        "测试问题",
        [_source(1, "无关证据")],
        helper_reviewer=reviewer,
    )

    assert res.answer == REVIEWER_ERROR_ANSWER
    assert res.final_mode == "reviewer_error"


def test_reviewer_not_configured_records_generated_candidate_before_blocking():
    events = []
    res = AnswerFinalizer().finalize(
        "候选答案。[1]",
        "测试问题",
        [_source(1, "文档内容")],
        on_lifecycle_event=events.append,
    )

    assert [event["type"] for event in events] == ["candidate_status", "error", "publication"]
    assert res.grounding["candidate_attempts"] == 1
    assert res.final_mode == "reviewer_error"


def test_rewrite_error_reports_one_completed_review_and_two_candidate_attempts():
    def _raise(_review_result):
        raise RuntimeError("rewrite failed")

    res = AnswerFinalizer().finalize(
        "第一版回答。",
        "测试问题",
        [_source(1, "受支持断言")],
        retry_candidate=_raise,
        helper_reviewer=_revise_reviewer(),
    )

    assert res.final_mode == "review_blocked"
    assert res.grounding["review_attempts"] == 1
    assert res.grounding["candidate_attempts"] == 2


def test_pass_lifecycle_includes_review_start_and_publication():
    events = []
    res = AnswerFinalizer().finalize(
        "候选答案。[1]",
        "测试问题",
        [_source(1, "候选答案")],
        helper_reviewer=_pass_reviewer(),
        on_lifecycle_event=events.append,
    )

    assert res.final_mode == "generated"
    assert [event["type"] for event in events] == [
        "candidate_status",
        "helper_grounding_review_started",
        "review_status",
        "publication",
    ]


def test_review_status_exposes_counts_without_private_reviewer_reasoning():
    events = []

    AnswerFinalizer().finalize(
        "受支持断言与未支持断言。[1]",
        "测试问题",
        [_source(1, "受支持断言")],
        helper_reviewer=_revise_reviewer(),
        on_lifecycle_event=events.append,
    )

    data = next(event["data"] for event in events if event["type"] == "review_status")
    assert data["reviewer_role"] == "helper_llm"
    assert data["claim_count"] == 2
    assert data["unsupported_count"] == 1
    assert data["contradicted_count"] == 0
    assert "summary" not in data
    assert all("reason" not in claim for claim in data["claim_reviews"])
    assert all("instruction" not in action for action in data["rewrite_actions"])


def test_callable_reviewer_exception_is_reported_as_error_verdict():
    def _raise(*_args):
        raise RuntimeError("boom")

    res = AnswerFinalizer().finalize(
        "候选答案。[1]",
        "测试问题",
        [_source(1, "候选答案")],
        helper_reviewer=_raise,
    )

    assert res.final_mode == "reviewer_error"
    assert res.grounding["review_verdict"] == "ERROR"
    assert res.grounding["review_count"] == 1
