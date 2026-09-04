from __future__ import annotations

import pytest

from rag_knowledge.services.agent_orchestration.models import (
    ConversationContext,
    EvidencePool,
)
from rag_knowledge.services.answer_finalizer import (
    AnswerFinalizer,
    REVIEW_BLOCKED_ANSWER,
    REVIEWER_ERROR_ANSWER,
)
from rag_knowledge.services.helper_grounding_reviewer import (
    ClaimReview,
    HelperGroundingReviewResult,
    RewriteAction,
)


def _make_mock_reviewer(result: HelperGroundingReviewResult):
    def _reviewer(q, docs, cand):
        return result
    return _reviewer


def test_zero_evidence_compose_answer_passes_pure_conversation_candidate():
    """PRD 10.1 & 10.5: 即使 0 证据，无事实 Claim 的纯会话 Candidate 也能通过 Reviewer 正常发布。"""
    finalizer = AnswerFinalizer()
    events = []

    # Reviewer 对纯会话（如“好的，请问还有什么我可以帮您？”）返回 PASS 且 claim_reviews 为空
    review_result = HelperGroundingReviewResult(
        verdict="PASS",
        coverage="FULL",
        summary="纯会话回复，无知识事实断言",
        claim_reviews=[],
        rewrite_actions=[],
        repair_mode="NONE",
    )

    finalized = finalizer.finalize(
        candidate="好的，请问还有什么我可以帮您？",
        question="好的",
        context_docs=[],  # 0 证据快照
        allow_general_knowledge=False,
        helper_reviewer=_make_mock_reviewer(review_result),
        on_lifecycle_event=lambda evt: events.append(evt),
    )

    assert finalized.answer == "好的，请问还有什么我可以帮您？"
    assert finalized.grounding["verdict"] == "pass"
    assert finalized.grounding["publication_state"] == "grounded_full"
    assert finalized.grounding["claim_count"] == 0

    pub_events = [e for e in events if e.get("type") == "publication"]
    assert len(pub_events) == 1
    assert pub_events[0]["data"]["publication_state"] == "grounded_full"


def test_zero_evidence_compose_answer_blocks_unsupported_facts():
    """PRD 10.1 & 10.6: 0 证据时，若 Candidate 捏造知识事实，Reviewer 阻断发布并收敛为 No Safe Answer。"""
    finalizer = AnswerFinalizer()
    events = []

    # Reviewer 判定包含未支持事实，且 coverage=NONE
    unsupported_claim = ClaimReview(
        claim_id="c1",
        claim="PipelineBuilder 默认端口是 8080",
        claim_type="knowledge_claim",
        claim_scope="TARGET_ATTRIBUTION",
        status="unsupported",
        evidence_ids=(),
        reason="未在证据快照中找到对应事实",
    )
    review_result = HelperGroundingReviewResult(
        verdict="NO_SAFE_ANSWER",
        coverage="NONE",
        summary="候选断言未获得任何证据支持",
        claim_reviews=[unsupported_claim],
        rewrite_actions=[],
        repair_mode="NONE",
    )

    finalized = finalizer.finalize(
        candidate="PipelineBuilder 默认端口是 8080",
        question="端口是多少？",
        context_docs=[],  # 0 证据快照
        allow_general_knowledge=False,
        helper_reviewer=_make_mock_reviewer(review_result),
        on_lifecycle_event=lambda evt: events.append(evt),
    )

    assert finalized.answer == REVIEW_BLOCKED_ANSWER
    assert finalized.grounding["verdict"] == "blocked"
    assert finalized.grounding["publication_state"] == "no_safe_answer"

    pub_events = [e for e in events if e.get("type") == "publication"]
    assert len(pub_events) == 1
    assert pub_events[0]["data"]["publication_state"] == "no_safe_answer"


def test_direct_candidate_external_rewrite_does_not_emit_premature_publication():
    """PRD 10.2 & 10.3: Direct Candidate 遇到 REVISE 时，支持外部重写，绝不提前发射 publication 或 rewrite_unavailable 错误。"""
    finalizer = AnswerFinalizer()
    events = []

    # Reviewer #1 判定需要重写
    claim1 = ClaimReview(
        claim_id="c1",
        claim="我刚才向你弹出了三次澄清卡片",
        claim_type="knowledge_claim",
        claim_scope="CONTEXTUAL_FACT",
        status="unsupported",
        evidence_ids=(),
        reason="运行事实记载弹出次数为0",
    )
    action1 = RewriteAction(
        claim_id="c1",
        action="correct_to_evidence",
        instruction="运行记录记载仅准备了一次澄清卡片，未真正发布三次",
    )
    review_result_1 = HelperGroundingReviewResult(
        verdict="REVISE",
        coverage="PARTIAL",
        summary="运行事实陈述与记录不符",
        claim_reviews=[claim1],
        rewrite_actions=[action1],
        repair_mode="REWRITE",
    )

    # 第一轮：外部控制器负责重写 (allow_external_rewrite=True)
    finalized_v1 = finalizer.finalize(
        candidate="我刚才向你弹出了三次澄清卡片",
        question="刚才弹卡了吗？",
        context_docs=[],
        allow_general_knowledge=False,
        helper_reviewer=_make_mock_reviewer(review_result_1),
        on_lifecycle_event=lambda evt: events.append(evt),
        candidate_version=1,
        allow_external_rewrite=True,
    )

    # 验证第一轮结果：处于 rewrite_pending，绝无提前发布阻断
    assert finalized_v1.grounding["verdict"] == "revise"
    assert finalized_v1.grounding["repair_mode"] == "REWRITE"
    assert finalized_v1.grounding["publication_state"] == "rewrite_pending"

    # 关键断言：绝对没有发射 rewrite_unavailable 错误，也绝对没有发射 publication 事件！
    assert not any(e.get("type") == "publication" for e in events)
    assert not any(e.get("data", {}).get("code") == "rewrite_unavailable" for e in events)

    # 第二轮：Main Controller 重写生成 Candidate V2 并重新提交审核
    events_v2 = []
    review_result_2 = HelperGroundingReviewResult(
        verdict="PASS",
        coverage="FULL",
        summary="重写后事实已与运行事实一致",
        claim_reviews=[],
        rewrite_actions=[],
        repair_mode="NONE",
    )

    original_coverage = finalized_v1.grounding["coverage"]
    assert original_coverage == "PARTIAL"

    finalized_v2 = finalizer.finalize(
        candidate="我刚才准备了澄清卡片，但没有向你连续弹出三次。",
        question="刚才弹卡了吗？",
        context_docs=[],
        allow_general_knowledge=False,
        helper_reviewer=_make_mock_reviewer(review_result_2),
        on_lifecycle_event=lambda evt: events_v2.append(evt),
        candidate_version=2,
        allow_external_rewrite=False,
        frozen_coverage=original_coverage,
    )

    # 关键断言：Question × Frozen Evidence coverage 不可变，Reviewer #2 即使返回 FULL 也被锁定为 PARTIAL
    assert finalized_v2.grounding["verdict"] == "pass"
    assert finalized_v2.grounding["coverage"] == "PARTIAL"
    assert finalized_v2.grounding["publication_state"] == "grounded_partial"
    assert finalized_v2.grounding["final_mode"] == "grounded_partial"
    pub_events_v2 = [e for e in events_v2 if e.get("type") == "publication"]
    assert len(pub_events_v2) == 1
    assert pub_events_v2[0]["data"]["publication_state"] == "grounded_partial"
    assert pub_events_v2[0]["data"]["coverage"] == "PARTIAL"
    assert pub_events_v2[0]["data"]["published_candidate_attempt"] == 2

    # 关键断言：Candidate V2 审查生命周期透明度，杜绝硬编码 V1
    cand_status_events = [e for e in events_v2 if e.get("type") == "candidate_status"]
    assert len(cand_status_events) == 1
    assert cand_status_events[0]["data"]["version"] == 2
    assert "Candidate V2" in cand_status_events[0]["data"]["message"]

    review_start_events = [e for e in events_v2 if e.get("type") == "helper_grounding_review_started"]
    assert len(review_start_events) == 1
    assert review_start_events[0]["data"]["candidate_version"] == 2
    assert "Candidate V2" in review_start_events[0]["data"]["message"]


def test_direct_candidate_second_review_failure_terminates_as_no_safe_answer():
    """PRD 10.3: Direct Candidate 第二轮审查仍未通过时，终局收敛为 no_safe_answer。"""
    finalizer = AnswerFinalizer()
    events = []

    claim = ClaimReview(
        claim_id="c1",
        claim="依然是未经支持的事实",
        claim_type="knowledge_claim",
        claim_scope="TARGET_ATTRIBUTION",
        status="unsupported",
        evidence_ids=(),
        reason="依然缺乏支持",
    )
    action = RewriteAction(claim_id="c1", action="delete", instruction="删除")
    review_result = HelperGroundingReviewResult(
        verdict="REVISE",
        coverage="PARTIAL",
        summary="重写后依然未通过",
        claim_reviews=[claim],
        rewrite_actions=[action],
        repair_mode="REWRITE",
    )

    # 第二轮 (candidate_version=2, allow_external_rewrite=False)
    finalized = finalizer.finalize(
        candidate="依然包含未经支持事实的 Candidate V2",
        question="测试",
        context_docs=[],
        allow_general_knowledge=False,
        helper_reviewer=_make_mock_reviewer(review_result),
        on_lifecycle_event=lambda evt: events.append(evt),
        candidate_version=2,
        allow_external_rewrite=False,
    )

    assert finalized.answer == REVIEW_BLOCKED_ANSWER
    assert finalized.grounding["verdict"] == "blocked"
    assert finalized.grounding["publication_state"] == "no_safe_answer"

    pub_events = [e for e in events if e.get("type") == "publication"]
    assert len(pub_events) == 1
    assert pub_events[0]["data"]["publication_state"] == "no_safe_answer"


def test_meta_conversation_runtime_evidence_grounding_audit():
    """PRD 10.4: 元对话事实 Claim 对照 Runtime Event Evidence 审核。"""
    finalizer = AnswerFinalizer()

    # 模拟运行时事件证据
    runtime_doc = {
        "content": "系统于上一轮准备了澄清卡片，但未发生用户点击，真实发布次数为 0。",
        "metadata": {
            "source_type": "runtime_event",
            "support_scope": "TARGET_SPECIFIC",
            "citation_id": 1,
            "citable": False,
        },
    }

    # Reviewer 结合运行时事实核对，若 Candidate 与事实一致则 PASS
    review_result = HelperGroundingReviewResult(
        verdict="PASS",
        coverage="FULL",
        summary="陈述与运行事实一致",
        claim_reviews=[
            ClaimReview(
                claim_id="c1",
                claim="上一轮真实发布次数为 0",
                claim_type="knowledge_claim",
                claim_scope="CONTEXTUAL_FACT",
                status="supported",
                evidence_ids=(1,),
                reason="与运行事实记录一致",
            )
        ],
        rewrite_actions=[],
        repair_mode="NONE",
    )

    finalized = finalizer.finalize(
        candidate="根据系统记录，上一轮真实发布次数为 0。",
        question="上一轮弹出了吗？",
        context_docs=[runtime_doc],
        allow_general_knowledge=False,
        helper_reviewer=_make_mock_reviewer(review_result),
    )

    assert finalized.grounding["verdict"] == "pass"
    assert finalized.grounding["publication_state"] == "grounded_full"


def test_strict_evidence_fail_closed_on_reviewer_error():
    """PRD 10.6: 审查服务异常或未就绪时，严格 fail-closed 阻断发布。"""
    finalizer = AnswerFinalizer()
    events = []

    # Reviewer 抛出系统异常
    def _buggy_reviewer(q, docs, cand):
        raise RuntimeError("LLM service unavailable")

    finalized = finalizer.finalize(
        candidate="测试答案",
        question="测试问题",
        context_docs=[],
        allow_general_knowledge=False,
        helper_reviewer=_buggy_reviewer,
        on_lifecycle_event=lambda evt: events.append(evt),
    )

    assert finalized.answer == REVIEWER_ERROR_ANSWER
    assert finalized.grounding["verdict"] == "error"
    assert finalized.grounding["publication_state"] == "reviewer_error"

    pub_events = [e for e in events if e.get("type") == "publication"]
    assert len(pub_events) == 1
    assert pub_events[0]["data"]["publication_state"] == "reviewer_error"


def test_direct_candidate_gap_feedback_returns_retrieval_pending_without_publication():
    """PRD 10.3: Direct Candidate 遇到 GAP 时，产出 retrieval_pending 反馈供 Main 自主规划，绝不提前发布。"""
    from rag_knowledge.services.helper_grounding_reviewer import RetrievalFeedback

    finalizer = AnswerFinalizer()
    events = []

    claim = ClaimReview(
        claim_id="c1",
        claim="该工具在 Linux 下默认端口是 9090",
        claim_type="knowledge_claim",
        claim_scope="CONTEXTUAL_FACT",
        status="unsupported",
        evidence_ids=(),
        reason="未检索到默认端口信息",
    )
    feedback = RetrievalFeedback(
        gap_id="default_port",
        affected_claim_ids=("c1",),
        missing_fact="Linux 默认端口",
        subject_entity_ids=("Tool",),
        deficiency_type="missing_fact",
        reason="缺少默认端口证据",
    )
    review_result = HelperGroundingReviewResult(
        verdict="REVISE",
        coverage="PARTIAL",
        summary="存在检索缺口",
        claim_reviews=[claim],
        rewrite_actions=[],
        repair_mode="RETRIEVE",
        retrieval_feedback=feedback,
    )

    finalized = finalizer.finalize(
        candidate="该工具在 Linux 下默认端口是 9090",
        question="端口是多少？",
        context_docs=[],
        allow_general_knowledge=False,
        helper_reviewer=_make_mock_reviewer(review_result),
        on_lifecycle_event=lambda evt: events.append(evt),
        allow_external_rewrite=True,
    )

    # 验证处于 retrieval_pending 且绝无提前发布事件
    assert finalized.grounding["verdict"] == "revise"
    assert finalized.grounding["repair_mode"] == "RETRIEVE"
    assert finalized.grounding["publication_state"] == "retrieval_pending"
    assert finalized.retrieval_feedback is not None
    assert finalized.retrieval_feedback["gap_id"] == "default_port"

    assert not any(e.get("type") == "publication" for e in events)
    assert any(e.get("type") == "retrieval_feedback" for e in events)


def test_internal_rewrite_exception_emits_clean_no_safe_answer_publication_state():
    """验证内部重写异常分支上 publication_state 统一收敛为 no_safe_answer。"""
    finalizer = AnswerFinalizer()
    events = []

    claim = ClaimReview(
        claim_id="c1",
        claim="待重写事实",
        claim_type="knowledge_claim",
        claim_scope="TARGET_ATTRIBUTION",
        status="unsupported",
        evidence_ids=(),
        reason="需要重写",
    )
    action = RewriteAction(claim_id="c1", action="delete", instruction="删除")
    review_result = HelperGroundingReviewResult(
        verdict="REVISE",
        coverage="PARTIAL",
        summary="待重写",
        claim_reviews=[claim],
        rewrite_actions=[action],
        repair_mode="REWRITE",
    )

    def _broken_rewrite_candidate(review):
        raise RuntimeError("Model service connection broken")

    finalized = finalizer.finalize(
        candidate="测试内容",
        question="测试",
        context_docs=[],
        allow_general_knowledge=False,
        retry_candidate=_broken_rewrite_candidate,
        helper_reviewer=_make_mock_reviewer(review_result),
        on_lifecycle_event=lambda evt: events.append(evt),
    )

    assert finalized.answer == REVIEW_BLOCKED_ANSWER
    assert finalized.grounding["verdict"] == "fail"
    assert finalized.grounding["publication_state"] == "no_safe_answer"

    pub_events = [e for e in events if e.get("type") == "publication"]
    assert len(pub_events) == 1
    assert pub_events[0]["data"]["publication_state"] == "no_safe_answer"
