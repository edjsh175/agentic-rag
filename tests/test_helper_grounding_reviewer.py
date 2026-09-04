import json
import pytest

from rag_knowledge.services.helper_grounding_reviewer import (
    HelperGroundingReviewer,
    HelperGroundingReviewResult,
    ClaimReview,
    RewriteAction,
    format_evidence_snapshot,
)


def test_supported_claim_with_wrong_candidate_citation_requires_citation_rewrite():
    reviewer = HelperGroundingReviewer(lambda _messages: {
        "coverage": "FULL",
        "summary": "事实受支持，但候选引用错绑",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "服务端口为 8080",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1],
            "candidate_citation_ids": [2],
            "reason": "事实由证据 1 支持，但候选原文引用的是 2",
        }],
        "rewrite_actions": [],
    })

    result = reviewer.review(
        "默认端口是多少？",
        [
            _source(1, "默认端口为 8080"),
            _source(2, "安装目录为 /opt/app"),
        ],
        "服务端口为 8080 [2]。",
    )

    assert result.error is None
    assert result.verdict == "REVISE"
    assert result.repair_mode == "REWRITE"
    assert result.claim_reviews[0].status == "supported"
    assert result.claim_reviews[0].candidate_citation_ids == (2,)
    assert len(result.rewrite_actions) == 1
    assert result.rewrite_actions[0].action == "fix_citations"
    assert "[2]" in result.rewrite_actions[0].instruction
    assert "[1]" in result.rewrite_actions[0].instruction


def test_legacy_supported_rewrite_with_wrong_citation_becomes_citation_only_revise():
    reviewer = HelperGroundingReviewer(lambda _messages: {
        "coverage": "PARTIAL",
        "summary": "事实均受支持，但引用需要修正",
        "repair_mode": "REWRITE",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "服务端口为 8080",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1],
            "candidate_citation_ids": [2],
            "reason": "事实由证据 1 支持，但候选绑定了证据 2",
        }],
        "rewrite_actions": [{
            "claim_id": "c1",
            "action": "correct_to_evidence",
            "instruction": "把引用 2 改成 1",
        }],
    })

    result = reviewer.review(
        "默认端口是多少？",
        [
            _source(1, "默认端口为 8080"),
            _source(2, "安装目录为 /opt/app"),
        ],
        "服务端口为 8080 [2]。",
    )

    assert result.error is None
    assert result.verdict == "REVISE"
    assert result.coverage == "PARTIAL"
    assert result.repair_mode == "REWRITE"
    assert result.claim_reviews[0].status == "supported"
    assert [action.action for action in result.rewrite_actions] == ["fix_citations"]


def test_supported_claim_without_candidate_citation_requires_citation_rewrite():
    reviewer = HelperGroundingReviewer(lambda _messages: {
        "coverage": "FULL",
        "summary": "事实受支持，但候选缺少引用",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "服务端口为 8080",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1],
            "candidate_citation_ids": [],
            "reason": "证据 1 支持事实，但候选原文没有引用",
        }],
        "rewrite_actions": [],
    })

    result = reviewer.review(
        "默认端口是多少？",
        [_source(1, "默认端口为 8080")],
        "服务端口为 8080。",
    )

    assert result.error is None
    assert result.verdict == "REVISE"
    assert result.repair_mode == "REWRITE"
    assert result.rewrite_actions[0].action == "fix_citations"


def test_explicit_candidate_citation_must_exist_in_candidate_text():
    reviewer = HelperGroundingReviewer(lambda _messages: {
        "coverage": "FULL",
        "summary": "伪造候选引用绑定",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "服务端口为 8080",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1],
            "candidate_citation_ids": [1],
            "reason": "证据 1 支持",
        }],
        "rewrite_actions": [],
    })

    result = reviewer.review(
        "默认端口是多少？",
        [_source(1, "默认端口为 8080")],
        "服务端口为 8080。",
    )

    assert result.verdict == "ERROR"
    assert "candidate_citation_not_in_candidate:1" in (result.error or "")


def test_repair_mode_is_derived_from_claim_state_not_model_output():
    reviewer = HelperGroundingReviewer(lambda _messages: {
        "coverage": "FULL",
        "summary": "全部支持",
        # Legacy models may still emit this field. It must not control the
        # publication state or make a PASS result unrepairable.
        "repair_mode": "REWRITE",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "服务端口为 8080",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1],
            "reason": "证据直接支持",
        }],
        "rewrite_actions": [{
            "claim_id": "c1",
            "action": "correct_to_evidence",
            "instruction": "保持原样",
        }],
    })

    result = reviewer.review(
        "默认端口是多少？",
        [{"content": "默认端口为 8080", "metadata": {"citation_id": 1}}],
        "服务端口为 8080 [1]。",
    )

    assert result.error is None
    assert result.verdict == "PASS"
    assert result.repair_mode == "NONE"
    assert result.rewrite_actions == []


def test_sparse_empty_findings_derives_pass_without_full_claim_report():
    reviewer = HelperGroundingReviewer(lambda _messages: {
        "coverage": "FULL",
        "summary": "未发现阻断问题",
        "findings": [],
        "more_blocking_findings": False,
    })

    result = reviewer.review(
        "默认端口是多少？",
        [_source(1, "默认端口为 8080")],
        "默认端口为 8080 [1]。",
    )

    assert result.error is None
    assert result.verdict == "PASS"
    assert result.findings == []
    assert result.claim_reviews == []
    assert result.rewrite_actions == []


def test_sparse_citation_mismatch_derives_fix_citations():
    reviewer = HelperGroundingReviewer(lambda _messages: {
        "coverage": "FULL",
        "summary": "存在引用错绑",
        "findings": [{
            "finding_id": "f1",
            "issue": "CITATION_MISMATCH",
            "claim": "默认端口为 8080",
            "claim_scope": "TARGET_ATTRIBUTION",
            "candidate_citation_ids": [2],
            "evidence_ids": [1],
            "reason": "事实由 1 支持，但 Candidate 引用 2",
        }],
        "more_blocking_findings": False,
    })

    result = reviewer.review(
        "默认端口是多少？",
        [_source(1, "默认端口为 8080"), _source(2, "安装目录为 /opt/app")],
        "默认端口为 8080 [2]。",
    )

    assert result.error is None
    assert result.verdict == "REVISE"
    assert result.repair_mode == "REWRITE"
    assert result.findings[0].issue == "CITATION_MISMATCH"
    assert result.claim_reviews[0].status == "supported"
    assert [action.action for action in result.rewrite_actions] == ["fix_citations"]


def test_sparse_scope_mismatch_is_rewrite_not_reviewer_error():
    reviewer = HelperGroundingReviewer(lambda _messages: {
        "coverage": "PARTIAL",
        "summary": "存在归属越权",
        "findings": [{
            "finding_id": "f1",
            "issue": "SCOPE_MISMATCH",
            "claim": "DEM 数据自身支持在线实时发布",
            "claim_scope": "TARGET_ATTRIBUTION",
            "candidate_citation_ids": [1],
            "evidence_ids": [1],
            "reason": "证据只授权上下文事实，不能直接归属给 DEM 数据",
        }],
        "more_blocking_findings": False,
    })

    result = reviewer.review(
        "如何发布 DEM 数据？",
        [_source(1, "StampWebGL 提供 DEM 发布入口", support_scope="CONTEXT_ONLY")],
        "DEM 数据自身支持在线实时发布 [1]。",
    )

    assert result.error is None
    assert result.verdict == "REVISE"
    assert result.repair_mode == "REWRITE"
    assert result.claim_reviews[0].status == "unsupported"
    assert result.rewrite_actions[0].action == "rewrite_to_supported_scope_or_remove"


def test_sparse_redundant_citation_mismatch_with_same_ids_is_discarded():
    reviewer = HelperGroundingReviewer(lambda _messages: {
        "coverage": "FULL",
        "summary": "误报引用问题",
        "findings": [{
            "finding_id": "f1",
            "issue": "CITATION_MISMATCH",
            "claim": "默认端口为 8080",
            "claim_scope": "TARGET_ATTRIBUTION",
            "candidate_citation_ids": [1],
            "evidence_ids": [1],
            "reason": "模型误报，但编号完全一致",
        }],
        "more_blocking_findings": False,
    })

    result = reviewer.review(
        "默认端口是多少？",
        [_source(1, "默认端口为 8080")],
        "默认端口为 8080 [1]。",
    )

    assert result.error is None
    assert result.verdict == "PASS"
    assert result.findings == []
    assert result.rewrite_actions == []


def test_sparse_same_ids_with_scope_violation_normalizes_to_scope_mismatch():
    reviewer = HelperGroundingReviewer(lambda _messages: {
        "coverage": "PARTIAL",
        "summary": "错误地标成引用问题",
        "findings": [{
            "finding_id": "f1",
            "issue": "CITATION_MISMATCH",
            "claim": "DEM 数据自身支持在线发布",
            "claim_scope": "TARGET_ATTRIBUTION",
            "candidate_citation_ids": [1],
            "evidence_ids": [1],
            "reason": "实际问题是 scope 越权",
        }],
        "more_blocking_findings": False,
    })

    result = reviewer.review(
        "如何发布 DEM？",
        [_source(1, "StampWebGL 提供 DEM 发布入口", support_scope="CONTEXT_ONLY")],
        "DEM 数据自身支持在线发布 [1]。",
    )

    assert result.error is None
    assert result.verdict == "REVISE"
    assert result.findings[0].issue == "SCOPE_MISMATCH"
    assert result.rewrite_actions[0].action == "rewrite_to_supported_scope_or_remove"
    assert "CONTEX" in result.rewrite_actions[0].instruction
    assert "删除这条直接归属事实" in result.rewrite_actions[0].instruction


def test_sparse_scope_mismatch_without_matrix_violation_is_discarded():
    reviewer = HelperGroundingReviewer(lambda _messages: {
        "coverage": "FULL",
        "summary": "模型误报 scope",
        "findings": [{
            "finding_id": "f1",
            "issue": "SCOPE_MISMATCH",
            "claim": "DEM 发布需要设置空间参考",
            "claim_scope": "TARGET_ATTRIBUTION",
            "candidate_citation_ids": [1],
            "evidence_ids": [1],
            "reason": "模型误报",
        }],
        "more_blocking_findings": False,
    })

    result = reviewer.review(
        "如何发布 DEM？",
        [_source(1, "DEM 发布需要设置空间参考", support_scope="TARGET_SPECIFIC")],
        "DEM 发布需要设置空间参考 [1]。",
    )

    assert result.error is None
    assert result.verdict == "PASS"
    assert result.findings == []
    assert result.rewrite_actions == []


def test_sparse_evidence_gap_derives_retrieve_feedback():
    reviewer = HelperGroundingReviewer(lambda _messages: {
        "coverage": "PARTIAL",
        "summary": "仍缺少发布路径事实",
        "findings": [{
            "finding_id": "f1",
            "issue": "EVIDENCE_GAP",
            "claim": "DEM 发布所需的服务器路径要求",
            "claim_scope": "TARGET_ATTRIBUTION",
            "candidate_citation_ids": [],
            "evidence_ids": [],
            "reason": "当前快照没有直接说明服务器路径要求",
        }],
        "more_blocking_findings": False,
    })

    result = reviewer.review(
        "如何发布 DEM 数据？",
        [_source(1, "DEM 发布需要设置空间参考")],
        "先设置空间参考 [1]。",
    )

    assert result.error is None
    assert result.verdict == "REVISE"
    assert result.repair_mode == "RETRIEVE"
    assert result.rewrite_actions == []
    assert result.retrieval_feedback is not None
    assert result.retrieval_feedback.gap_id.startswith("gap_")
    assert result.retrieval_feedback.gap_id != "f1"


def test_sparse_gap_id_is_stable_when_finding_order_changes():
    findings = [
        {
            "issue": "EVIDENCE_GAP",
            "claim": "DEM 发布所需的服务器路径要求",
            "claim_scope": "TARGET_ATTRIBUTION",
            "candidate_citation_ids": [],
            "evidence_ids": [],
            "reason": "缺少服务器路径证据",
        },
        {
            "issue": "EVIDENCE_GAP",
            "claim": "DEM 发布所需的发布入口要求",
            "claim_scope": "TARGET_ATTRIBUTION",
            "candidate_citation_ids": [],
            "evidence_ids": [],
            "reason": "缺少发布入口证据",
        },
    ]

    def _review_with(items):
        reviewer = HelperGroundingReviewer(lambda _messages: {
            "coverage": "PARTIAL",
            "summary": "仍有证据缺口",
            "findings": items,
            "more_blocking_findings": False,
        })
        return reviewer.review(
            "如何发布 DEM 数据？",
            [_source(1, "DEM 发布需要设置空间参考")],
            "先设置空间参考 [1]。",
        )

    first = _review_with(findings)
    second = _review_with(list(reversed(findings)))

    assert first.retrieval_feedback is not None
    assert second.retrieval_feedback is not None
    assert first.retrieval_feedback.gap_id == second.retrieval_feedback.gap_id
    assert set(first.retrieval_feedback.affected_claim_ids) == set(second.retrieval_feedback.affected_claim_ids)


def test_sparse_more_findings_triggers_bounded_conservative_rewrite():
    reviewer = HelperGroundingReviewer(lambda _messages: {
        "coverage": "FULL",
        "summary": "问题超过输出上限",
        "findings": [{
            "finding_id": "f1",
            "issue": "UNSUPPORTED",
            "claim": "无证据事实",
            "claim_scope": "TARGET_ATTRIBUTION",
            "candidate_citation_ids": [],
            "evidence_ids": [],
            "reason": "无证据支持",
        }],
        "more_blocking_findings": True,
    })

    result = reviewer.review(
        "介绍产品",
        [_source(1, "产品支持 A")],
        "产品支持 A [1]，并支持 B。",
    )

    assert result.error is None
    assert result.verdict == "REVISE"
    assert result.repair_mode == "REWRITE"
    assert result.more_blocking_findings is True
    assert any(action.claim_id == "__candidate_overflow__" for action in result.rewrite_actions)


def _source(index: int, content: str, source: str = "", support_scope: str = "TARGET_SPECIFIC"):
    # 通过 Text Admission 的 KB 文本必须携带协议字段（evidence_class + support_scope）；
    # 有 evidence_class 即属于 Support Scope Protocol，参与 Claim Support Matrix。
    return {
        "content": content,
        "metadata": {
            "citation_id": index,
            "source": source or f"doc-{index}.md",
            "section_path": f"第{index}章",
            "evidence_class": "TARGET_DIRECT",
            "support_scope": support_scope,
        },
    }


def test_evidence_snapshot_preserves_complete_content_without_whitespace_collapse():
    content = "首行  保留双空格\n" + ("完整证据" * 600)

    snapshot = format_evidence_snapshot([_source(1, content)])

    assert snapshot[0]["content"] == content


def _pass_payload() -> dict:
    return {
        "verdict": "PASS",
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "回答内容受证据支持",
        "repair_mode": "NONE",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "StampServer 支持服务发布。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "status": "supported",
                "evidence_ids": [1],
                "reason": "证据 1 明确支持",
            }
        ],
        "rewrite_actions": [],
    }


def _revise_payload() -> dict:
    return {
        "verdict": "REVISE",
        "coverage": "PARTIAL",
        "repair_mode": "REWRITE",
        "summary": "候选回答包含不受支持的事实",
        "repair_mode": "REWRITE",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "StampServer 支持服务发布。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "status": "supported",
                "evidence_ids": [1],
                "reason": "证据 1 明确支持",
            },
            {
                "claim_id": "c2",
                "claim": "StampServer 默认开放 9999 端口。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "status": "unsupported",
                "evidence_ids": [],
                "reason": "证据未提供该端口",
            },
        ],
        "rewrite_actions": [
            {
                "claim_id": "c2",
                "action": "rewrite_to_supported_scope_or_remove",
                "instruction": "删除未受支持的端口信息",
            },
        ],
    }


def _no_safe_answer_payload() -> dict:
    return {
        "verdict": "NO_SAFE_ANSWER",
        "coverage": "NONE",
        "repair_mode": "NONE",
        "summary": "当前证据无法形成安全回答",
        "repair_mode": "NONE",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "系统默认开放 9999 端口。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "status": "unsupported",
                "evidence_ids": [],
                "reason": "证据未提供该端口",
            }
        ],
        "rewrite_actions": [],
    }


def _review_payload(payload: object):
    reviewer = HelperGroundingReviewer(lambda _msgs: payload)
    return reviewer.review(
        "StampServer 支持什么功能？",
        [_source(1, "StampServer 支持服务发布。")],
        "StampServer 支持服务发布。[1]",
    )


def _assert_protocol_error(result, expected_reason: str = "") -> None:
    assert result.verdict == "ERROR"
    assert result.coverage == "NONE"
    assert result.error is not None
    assert result.error.startswith("invalid_review_protocol:")
    if expected_reason:
        assert expected_reason in result.error


def test_reviewer_pass_full():
    mock_response = json.dumps({
        "verdict": "PASS",
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "回答内容完全受证据支持",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "StampServer 支持服务发布。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "evidence_ids": [1],
                "status": "supported",
                "reason": "证据 1 明确说明支持服务发布",
            }
        ],
        "rewrite_actions": [],
    })

    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    docs = [_source(1, "StampServer 支持服务发布。")]
    result = reviewer.review("StampServer 是否支持发布？", docs, "StampServer 支持服务发布。[1]")

    assert result.verdict == "PASS"
    assert result.coverage == "FULL"
    assert result.is_partial is False
    assert len(result.claim_reviews) == 1
    assert result.claim_reviews[0].claim_id == "c1"
    assert result.claim_reviews[0].status == "supported"
    assert result.error is None


def test_non_retrieve_empty_retrieval_feedback_placeholder_is_ignored():
    payload = _pass_payload()
    payload["retrieval_feedback"] = {
        "gap_id": "",
        "affected_claim_ids": [],
        "missing_fact": "",
        "subject_entity_ids": [],
        "deficiency_type": "",
        "reason": "",
    }

    result = _review_payload(payload)

    assert result.verdict == "PASS"
    assert result.retrieval_feedback is None


def test_reviewer_pass_partial():
    mock_response = json.dumps({
        "verdict": "PASS",
        "coverage": "PARTIAL",
        "repair_mode": "NONE",
        "summary": "回答受支持，但证据仅覆盖部分问题",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "StampWebRTC 示例使用 31443 端口。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "evidence_ids": [1],
                "status": "supported",
                "reason": "证据 1 包含 31443 端口",
            },
            {
                "claim_id": "c2",
                "claim": "当前资料未说明其他 UDP 端口。",
                "evidence_ids": [],
                "claim_type": "limitation_statement",
                "status": "supported",
                "reason": "合理边界说明",
            },
        ],
        "rewrite_actions": [],
    })

    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    docs = [_source(1, "StampWebRTC 示例使用 31443 端口。")]
    result = reviewer.review(
        "StampWebRTC UDP 部署需要哪些端口？",
        docs,
        "StampWebRTC 示例使用 31443 端口 [1]。当前资料未说明其他 UDP 端口。",
    )

    assert result.verdict == "PASS"
    assert result.coverage == "PARTIAL"
    assert result.is_partial is True
    assert len(result.claim_reviews) == 2


def test_reviewer_revise_atomic_actions():
    mock_response = json.dumps({
        "verdict": "REVISE",
        "coverage": "PARTIAL",
        "repair_mode": "REWRITE",
        "summary": "部分断言未在证据中体现",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "StampServer 支持在线发布。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "evidence_ids": [1],
                "status": "supported",
                "reason": "证据 1 明确支持",
            },
            {
                "claim_id": "c2",
                "claim": "StampServer 支持基于 Redis 的缓存集群。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "evidence_ids": [],
                "status": "unsupported",
                "reason": "证据中未提及 Redis 缓存",
            },
        ],
        "rewrite_actions": [
            {
                "claim_id": "c2",
                "action": "rewrite_to_supported_scope_or_remove",
                "instruction": "删除关于 Redis 缓存的陈述",
            },
        ],
    })

    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    docs = [_source(1, "StampServer 支持在线发布。")]
    result = reviewer.review(
        "StampServer 功能有哪些？",
        docs,
        "StampServer 支持在线发布 [1]，并支持基于 Redis 的缓存集群。",
    )

    assert result.verdict == "REVISE"
    assert result.coverage == "PARTIAL"
    assert len(result.claim_reviews) == 2
    assert len(result.rewrite_actions) == 1
    assert [action.claim_id for action in result.rewrite_actions] == ["c2"]
    assert result.rewrite_actions[0].claim_id == "c2"
    assert result.rewrite_actions[0].action == "rewrite_to_supported_scope_or_remove"
    assert len(result.unsupported_claims) == 1
    assert result.unsupported_claims[0].claim_id == "c2"


def test_reviewer_no_safe_answer():
    mock_response = json.dumps({
        "verdict": "NO_SAFE_ANSWER",
        "coverage": "NONE",
        "repair_mode": "NONE",
        "summary": "证据完全无法回答该问题",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "系统使用 Java 编写。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "evidence_ids": [],
                "status": "contradicted",
                "reason": "证据中说明系统使用 C++",
            }
        ],
        "rewrite_actions": [],
    })

    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    docs = [_source(1, "系统采用 C++ 编写。")]
    result = reviewer.review("系统采用什么语言？", docs, "系统使用 Java 编写。")

    assert result.verdict == "NO_SAFE_ANSWER"
    assert result.coverage == "NONE"


def test_reviewer_fail_closed_on_invalid_json():
    reviewer = HelperGroundingReviewer(lambda _msgs: "Not a valid JSON")
    docs = [_source(1, "StampServer 支持服务发布。")]
    result = reviewer.review("StampServer 支持发布吗？", docs, "StampServer 支持发布。[1]")

    assert result.verdict == "ERROR"
    assert result.error is not None
    assert "JSONDecodeError" in result.error or "json" in result.error.lower()


def test_reviewer_fail_closed_on_caller_exception():
    def _raise(_msgs):
        raise TimeoutError("LLM call timed out")

    reviewer = HelperGroundingReviewer(_raise)
    docs = [_source(1, "StampServer 支持服务发布。")]
    result = reviewer.review("StampServer 支持发布吗？", docs, "StampServer 支持发布。[1]")

    assert result.verdict == "ERROR"
    assert result.error is not None
    assert "TimeoutError" in result.error


def test_reviewer_evidence_formatting():
    captured_msgs = []

    def _caller(msgs):
        captured_msgs.extend(msgs)
        return json.dumps({
            "verdict": "PASS",
            "coverage": "FULL",
        "repair_mode": "NONE",
            "summary": "ok",
            "claim_reviews": [{
                "claim_id": "c1",
                "claim": "候选答案。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "status": "supported",
                "evidence_ids": [1],
                "reason": "证据支持",
            }],
            "rewrite_actions": [],
        })

    reviewer = HelperGroundingReviewer(_caller)
    docs = [_source(1, "测试文档正文内容", source="manual.pdf")]
    reviewer.review("问题描述？", docs, "候选答案。[1]")

    assert len(captured_msgs) == 2
    assert captured_msgs[0]["role"] == "system"
    assert "只输出存在问题的事实" in captured_msgs[0]["content"]
    assert "正确且引用正确的事实不要输出 Finding" in captured_msgs[0]["content"]
    assert "SCOPE_MISMATCH" in captured_msgs[0]["content"]
    assert "不要输出 verdict、repair_mode、rewrite_actions" in captured_msgs[0]["content"]
    assert captured_msgs[1]["role"] == "user"
    content = captured_msgs[1]["content"]
    assert "manual.pdf" in content
    assert "测试文档正文内容" in content
    assert "问题描述？" in content
    assert "候选答案。[1]" in content


@pytest.mark.parametrize(
    "missing_field",
    ["coverage", "summary", "claim_reviews"],
)
def test_reviewer_fails_closed_when_required_top_level_field_is_missing(missing_field):
    payload = _pass_payload()
    del payload[missing_field]

    result = _review_payload(payload)

    _assert_protocol_error(result, "root_missing_fields")


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    [
        ("coverage", "SOME", "invalid_coverage"),
        ("summary", [], "summary_not_string"),
        ("claim_reviews", {}, "claim_reviews_not_list"),
    ],
)
def test_reviewer_top_level_types_and_enums_are_strict(field, value, expected_reason):
    payload = _pass_payload()
    payload[field] = value

    result = _review_payload(payload)

    _assert_protocol_error(result, expected_reason)


def test_model_declared_verdict_is_ignored_and_state_is_derived():
    pass_payload = _pass_payload()
    pass_payload["verdict"] = "NO_SAFE_ANSWER"
    revise_payload = _revise_payload()
    revise_payload["verdict"] = "PASS"
    none_payload = _pass_payload()
    none_payload["coverage"] = "NONE"

    pass_result = _review_payload(pass_payload)
    revise_result = _review_payload(revise_payload)
    none_result = _review_payload(none_payload)

    assert pass_result.verdict == "PASS"
    assert revise_result.verdict == "REVISE"
    assert none_result.verdict == "NO_SAFE_ANSWER"


def test_reviewer_fails_closed_on_truncated_json():
    raw = json.dumps(_pass_payload(), ensure_ascii=False)[:-1]

    result = _review_payload(raw)

    _assert_protocol_error(result, "invalid_json")


def test_reviewer_accepts_complete_json_fence_only():
    raw_json = json.dumps(_pass_payload(), ensure_ascii=False)

    valid_result = _review_payload(f"```json\n{raw_json}\n```")
    invalid_result = _review_payload(f"审核结果如下：\n```json\n{raw_json}\n```")

    assert valid_result.verdict == "PASS"
    _assert_protocol_error(invalid_result, "invalid_json")


@pytest.mark.parametrize("evidence_ids", [[2], [1, 1], ["1"], [True]])
def test_reviewer_fails_closed_on_invalid_evidence_ids(evidence_ids):
    payload = _pass_payload()
    payload["claim_reviews"][0]["evidence_ids"] = evidence_ids

    result = _review_payload(payload)

    _assert_protocol_error(result, "evidence_id")


def test_reviewer_fails_closed_on_duplicate_claim_id():
    payload = _revise_payload()
    payload["claim_reviews"][1]["claim_id"] = "c1"

    result = _review_payload(payload)

    _assert_protocol_error(result, "duplicate_claim_id")


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    [
        ("claim_id", 1, "claim_id_not_string"),
        ("claim_id", "  ", "claim_id_empty"),
        ("claim", None, "claim_not_string"),
        ("claim_type", "fact", "invalid_claim_type"),
        ("status", "unknown", "invalid_status"),
        ("evidence_ids", 1, "evidence_ids_not_list"),
        ("reason", [], "reason_not_string"),
    ],
)
def test_reviewer_claim_schema_is_strict(field, value, expected_reason):
    payload = _pass_payload()
    payload["claim_reviews"][0][field] = value

    result = _review_payload(payload)

    _assert_protocol_error(result, expected_reason)


def test_reviewer_fails_closed_when_claim_field_is_missing():
    payload = _pass_payload()
    del payload["claim_reviews"][0]["claim_type"]

    result = _review_payload(payload)

    _assert_protocol_error(result, "missing_fields")


@pytest.mark.parametrize("status", ["unsupported", "contradicted"])
def test_problem_claim_without_rewrite_action_fails_protocol_validation(status):
    payload = _pass_payload()
    payload["claim_reviews"][0]["status"] = status
    if status == "unsupported":
        payload["claim_reviews"][0]["evidence_ids"] = []

    result = _review_payload(payload)

    _assert_protocol_error(result, "revise_rewrite_requires_actions")


def test_unsupported_non_knowledge_claim_also_requires_rewrite_action():
    payload = _pass_payload()
    payload["claim_reviews"][0]["claim_type"] = "limitation_statement"
    payload["claim_reviews"][0]["claim_scope"] = "NOT_APPLICABLE"
    payload["claim_reviews"][0]["status"] = "unsupported"
    payload["claim_reviews"][0]["evidence_ids"] = []

    result = _review_payload(payload)

    _assert_protocol_error(result, "revise_rewrite_requires_actions")


def test_review_accepts_no_factual_claims():
    payload = _pass_payload()
    payload["claim_reviews"] = []

    result = _review_payload(payload)

    assert result.verdict == "PASS"
    assert result.claim_reviews == []


def test_model_supplied_rewrite_actions_are_validated():
    payload = _revise_payload()
    payload["rewrite_actions"] = [
        {"claim_id": "missing", "action": "preserve", "instruction": "malicious or stale"}
    ]

    result = _review_payload(payload)

    _assert_protocol_error(result, "unknown_claim_id")


def test_redundant_supported_claim_action_is_discarded():
    payload = _pass_payload()
    payload["rewrite_actions"] = [{
        "claim_id": "c1",
        "action": "add_limitation_statement",
        "instruction": "redundant action that must not override supported status",
    }]

    result = _review_payload(payload)

    assert result.verdict == "PASS"
    assert result.error is None
    assert result.rewrite_actions == []


def test_contradicted_claim_requires_compatible_model_action():
    payload = _revise_payload()
    payload["claim_reviews"][1]["status"] = "contradicted"
    payload["rewrite_actions"] = [
        {"claim_id": "c2", "action": "add_limitation_statement", "instruction": "stale model action"}
    ]

    result = _review_payload(payload)

    _assert_protocol_error(result, "action_status_mismatch")


def test_coverage_none_derives_no_safe_answer_even_if_legacy_verdict_says_pass():
    payload = _pass_payload()
    payload["coverage"] = "NONE"
    payload["verdict"] = "PASS"

    result = _review_payload(payload)

    assert result.verdict == "NO_SAFE_ANSWER"
    assert result.error is None


def test_invalid_protocol_retries_same_semantic_review_once():
    invalid = _revise_payload()
    invalid["rewrite_actions"][0]["claim_id"] = "c1"
    invalid["rewrite_actions"][0]["action"] = "rewrite_to_supported_scope_or_remove"

    repaired = _revise_payload()
    repaired["rewrite_actions"] = [repaired["rewrite_actions"][0]]
    calls = []

    def _caller(messages):
        calls.append(messages)
        return invalid if len(calls) == 1 else repaired

    reviewer = HelperGroundingReviewer(_caller)
    result = reviewer.review(
        "StampServer 支持什么功能？",
        [_source(1, "StampServer 支持服务发布。")],
        "StampServer 支持服务发布，并默认开放 9999 端口。",
    )

    assert result.verdict == "REVISE"
    assert result.error is None
    assert len(calls) == 2
    assert len(result.protocol_attempts) == 2
    assert result.protocol_attempts[0]["error"] is not None
    assert result.protocol_attempts[1]["error"] is None
    assert calls[1] == calls[0]
    retry_payload = json.dumps(calls[1], ensure_ascii=False)
    assert "validation_error" not in retry_payload
    assert "previous_response" not in retry_payload
    assert "immutable_semantics" not in retry_payload


def test_invalid_protocol_retry_does_not_expose_validator_state():
    invalid = _revise_payload()
    invalid["retrieval_feedback"] = {
        "gap_id": "missing-port",
        "affected_claim_ids": ["c2"],
        "missing_fact": "默认端口",
        "subject_entity_ids": [],
        "deficiency_type": "NO_DIRECT_EVIDENCE",
        "reason": "没有端口证据",
    }
    repaired = _revise_payload()
    calls = []

    def _caller(messages):
        calls.append(messages)
        return invalid if len(calls) == 1 else repaired

    reviewer = HelperGroundingReviewer(_caller)
    result = reviewer.review(
        "StampServer 支持什么功能？",
        [_source(1, "StampServer 支持服务发布。")],
        "StampServer 支持服务发布，并默认开放 9999 端口。",
    )

    assert result.verdict == "REVISE"
    assert result.error is None
    assert len(result.protocol_attempts) == 2
    assert calls[1] == calls[0]
    retry_payload = json.dumps(calls[1], ensure_ascii=False)
    assert "validation_error" not in retry_payload
    assert "missing-port" not in retry_payload
    assert "没有端口证据" not in retry_payload


def test_clean_retry_uses_second_valid_semantic_result():
    invalid = _revise_payload()
    invalid["rewrite_actions"][0]["claim_id"] = "c1"
    invalid["rewrite_actions"][0]["action"] = "rewrite_to_supported_scope_or_remove"

    drifted = _revise_payload()
    drifted["claim_reviews"][1]["status"] = "contradicted"
    drifted["rewrite_actions"] = [{
        "claim_id": "c2",
        "action": "correct_to_evidence",
        "instruction": "改成证据支持的内容",
    }]
    calls = 0

    def _caller(_messages):
        nonlocal calls
        calls += 1
        return invalid if calls == 1 else drifted

    reviewer = HelperGroundingReviewer(_caller)
    result = reviewer.review(
        "StampServer 支持什么功能？",
        [_source(1, "StampServer 支持服务发布。")],
        "StampServer 支持服务发布，并默认开放 9999 端口。",
    )

    assert result.verdict == "REVISE"
    assert result.error is None
    assert len(result.protocol_attempts) == 2
    assert result.claim_reviews[1].status == "contradicted"
    assert result.protocol_attempts[1]["error"] is None


def test_structured_output_schema_has_single_semantic_source():
    from rag_knowledge.services.helper_grounding_reviewer import review_response_json_schema

    schema = review_response_json_schema()

    assert set(schema["properties"]) == {
        "coverage", "summary", "findings", "more_blocking_findings"
    }
    assert set(schema["required"]) == set(schema["properties"])
    finding_schema = schema["properties"]["findings"]
    assert finding_schema["maxItems"] == 12
    assert "finding_id" not in finding_schema["items"]["properties"]
    assert set(finding_schema["items"]["properties"]["issue"]["enum"]) == {
        "UNSUPPORTED", "CONTRADICTED", "CITATION_MISMATCH", "SCOPE_MISMATCH", "EVIDENCE_GAP"
    }
    assert "verdict" not in schema["properties"]
    assert "repair_mode" not in schema["properties"]
    assert "rewrite_actions" not in schema["properties"]
    assert "retrieval_feedback" not in schema["properties"]
    assert "claim_reviews" not in schema["properties"]


def test_evidence_snapshot_includes_support_scope():
    doc1 = {
        "content": "管线系统支持碰撞分析。",
        "metadata": {
            "citation_id": 1,
            "source": "pipe.md",
            "section_path": "第1章",
            "support_scope": "CONTEXT_ONLY",
                "evidence_class": "RELATED_CONTEXT",
        },
    }
    snapshot = format_evidence_snapshot([doc1])
    assert snapshot[0]["support_scope"] == "CONTEXT_ONLY"
    assert snapshot[0]["evidence_class"] == "RELATED_CONTEXT"


def test_context_only_supports_contextual_claim():
    mock_response = json.dumps({
        "coverage": "PARTIAL",
        "repair_mode": "NONE",
        "summary": "相关管线系统上下文陈述受支持",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "相关管线系统资料涉及碰撞分析与智能排管。",
                "claim_type": "knowledge_claim",
                "claim_scope": "CONTEXTUAL_FACT",
                "status": "supported",
                "evidence_ids": [1],
                "reason": "证据 1 (CONTEXT_ONLY) 支持相关系统资料的陈述",
            },
            {
                "claim_id": "c2",
                "claim": "现有证据尚未确认这些功能直接归属于三维管线管理模块。",
                "claim_type": "limitation_statement",
                "status": "supported",
                "evidence_ids": [],
                "reason": "快照未提供直接归属",
            },
        ],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = {
        "content": "管线系统支持碰撞分析和智能排管。",
        "metadata": {
            "citation_id": 1,
            "source": "pipe.md",
            "evidence_class": "RELATED_CONTEXT",
            "support_scope": "CONTEXT_ONLY",
        },
    }
    result = reviewer.review(
        "三维管线管理的相关信息",
        [doc],
        "相关管线系统资料涉及碰撞分析与智能排管 [1]。现有证据尚未确认这些功能直接归属于三维管线管理模块。",
    )
    assert result.verdict == "PASS"
    assert result.coverage == "PARTIAL"
    assert result.is_partial is True


def test_context_only_rejects_target_attribute_claim():
    mock_response = json.dumps({
        "coverage": "PARTIAL",
        "repair_mode": "REWRITE",
        "summary": "直接将 CONTEXT_ONLY 资料归属于目标实体不被支持",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "三维管线管理支持碰撞分析。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "status": "unsupported",
                "evidence_ids": [1],
                "reason": "证据 1 仅为 CONTEXT_ONLY 上下文资料，不能支持三维管线管理模块的直接功能归属",
            }
        ],
        "rewrite_actions": [
            {
                "claim_id": "c1",
                "action": "rewrite_to_supported_scope_or_remove",
                "instruction": "修改为'相关管线系统资料涉及碰撞分析，但现有资料未确认直接属于三维管线管理模块'",
            }
        ],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = {
        "content": "管线系统支持碰撞分析。",
        "metadata": {"citation_id": 1, "source": "pipe.md", "support_scope": "CONTEXT_ONLY"},
    }
    result = reviewer.review("三维管线管理支持碰撞分析吗？", [doc], "三维管线管理支持碰撞分析。[1]")
    assert result.verdict == "REVISE"
    assert len(result.unsupported_claims) == 1
    assert result.rewrite_actions[0].claim_id == "c1"


def test_target_specific_supports_target_claim():
    mock_response = json.dumps({
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "目标直接断言受 TARGET_SPECIFIC 证据支持",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "PipelineWebRTC 用于建立实时音视频处理通道。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "status": "supported",
                "evidence_ids": [1],
                "reason": "证据 1 (TARGET_SPECIFIC) 明确支持目标功能",
            }
        ],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = {
        "content": "PipelineWebRTC 用于建立实时音视频处理通道。",
        "metadata": {
            "citation_id": 1,
            "source": "pipe.md",
            "evidence_class": "TARGET_DIRECT",
            "support_scope": "TARGET_SPECIFIC",
        },
    }
    result = reviewer.review("PipelineWebRTC 的功能是什么？", [doc], "PipelineWebRTC 用于建立实时音视频处理通道。[1]")
    assert result.verdict == "PASS"
    assert result.coverage == "FULL"


def test_graph_relation_supports_relation_claim_only():
    mock_response = json.dumps({
        "coverage": "PARTIAL",
        "repair_mode": "NONE",
        "summary": "图谱关系证据支持关系断言，但不支持额外属性",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "三维管线管理在知识图谱中归属于 PipelineWebGL。",
                "claim_type": "knowledge_claim",
                "claim_scope": "RELATION_CLAIM",
                "status": "supported",
                "evidence_ids": [1],
                "reason": "证据 1 明确为 belongs_to 关系证据",
            }
        ],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = {
        "content": "三维管线管理 -[belongs_to]-> PipelineWebGL",
        "metadata": {
            "citation_id": 1,
            "source_type": "graph_relation",
            "relation_key": "三维管线管理 -[belongs_to]-> PipelineWebGL",
            "support_scope": "RELATION_SPECIFIC",
        },
    }
    result = reviewer.review("三维管线管理属于哪个系统？", [doc], "三维管线管理在知识图谱中归属于 PipelineWebGL。[1]")
    assert result.verdict == "PASS"


def test_graph_relation_plus_context_does_not_create_attribute_inheritance():
    # If doc1 is belongs_to relation and doc2 is CONTEXT_ONLY (PipelineWebGL feature),
    # Candidate asserts target entity (三维管线管理) itself has that feature -> Must be rejected (REVISE)!
    mock_response = json.dumps({
        "coverage": "PARTIAL",
        "repair_mode": "REWRITE",
        "summary": "属于关系不能与上下文资料组合成目标实体的自身属性",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "三维管线管理归属于 PipelineWebGL。",
                "claim_type": "knowledge_claim",
                "claim_scope": "RELATION_CLAIM",
                "status": "supported",
                "evidence_ids": [1],
                "reason": "证据 1 支持 belongs_to 关系",
            },
            {
                "claim_id": "c2",
                "claim": "三维管线管理具备 PipelineWebGL 的高并发渲染能力。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "status": "unsupported",
                "evidence_ids": [2],
                "reason": "证据 2 仅为 CONTEXT_ONLY 上下文资料，不能通过 belongs_to 自动继承属性",
            },
        ],
        "rewrite_actions": [
            {
                "claim_id": "c2",
                "action": "rewrite_to_supported_scope_or_remove",
                "instruction": "删除三维管线管理继承高并发渲染能力的断言，改为说明其归属于具备该能力的 PipelineWebGL",
            }
        ],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc1 = {
        "content": "三维管线管理 -[belongs_to]-> PipelineWebGL",
        "metadata": {"citation_id": 1, "source_type": "graph_relation", "support_scope": "RELATION_SPECIFIC"},
    }
    doc2 = {
        "content": "PipelineWebGL 具备高并发渲染能力。",
        "metadata": {"citation_id": 2, "source": "webgl.md", "support_scope": "CONTEXT_ONLY"},
    }
    result = reviewer.review(
        "三维管线管理具备什么渲染能力？",
        [doc1, doc2],
        "三维管线管理归属于 PipelineWebGL [1]，并具备 PipelineWebGL 的高并发渲染能力 [2]。",
    )
    assert result.verdict == "REVISE"
    assert len(result.unsupported_claims) == 1
    assert result.unsupported_claims[0].claim_id == "c2"


def test_reviewer_cannot_upgrade_support_scope():
    # If Candidate asserts target attribution on a CONTEXT_ONLY doc,
    # Reviewer prompt forbids upgrading CONTEXT_ONLY to TARGET_SPECIFIC.
    mock_response = json.dumps({
        "coverage": "PARTIAL",
        "repair_mode": "REWRITE",
        "summary": "CONTEXT_ONLY 证据不能被当成 TARGET_SPECIFIC 支撑目标功能",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "三维管线管理支持智能排管功能。",
                "claim_type": "knowledge_claim",
                "claim_scope": "TARGET_ATTRIBUTION",
                "status": "unsupported",
                "evidence_ids": [1],
                "reason": "证据 1 为 CONTEXT_ONLY，无法升级支持目标实体的直接属性",
            }
        ],
        "rewrite_actions": [
            {
                "claim_id": "c1",
                "action": "rewrite_to_supported_scope_or_remove",
                "instruction": "修改为'相关管线系统资料涉及智能排管功能'",
            }
        ],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = {
        "content": "管线系统支持智能排管功能。",
        "metadata": {"citation_id": 1, "source": "pipe.md", "support_scope": "CONTEXT_ONLY"},
    }
    result = reviewer.review(
        "三维管线管理支持智能排管吗？",
        [doc],
        "三维管线管理支持智能排管功能。[1]",
    )
    assert result.verdict == "REVISE"
    assert len(result.unsupported_claims) == 1


# ---------------------------------------------------------------------------
# Claim Support Matrix：LLM 负责语义分类，代码负责类型兼容性（逐 evidence_id 核对）
# ---------------------------------------------------------------------------


def test_matrix_target_attribution_accepts_target_specific():
    mock_response = json.dumps({
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "TARGET_ATTRIBUTION + TARGET_SPECIFIC 合法组合",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "StampServer 支持服务发布。",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1],
            "reason": "证据 1 为 TARGET_SPECIFIC",
        }],
        "rewrite_actions": [],
    })
    result = _review_payload(mock_response)
    assert result.verdict == "PASS"
    assert result.claim_reviews[0].claim_scope == "TARGET_ATTRIBUTION"


def test_matrix_target_attribution_with_context_only_is_protocol_rejected():
    # 实体防漂移核心反例：LLM 把 CONTEXT_ONLY 证据当成目标实体归属断言的支持，
    # 即使 LLM 判 supported，代码也必须按矩阵拒绝该组合。
    mock_response = json.dumps({
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "非法组合：TARGET_ATTRIBUTION 引用 CONTEXT_ONLY",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "三维管线管理支持碰撞分析。",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1],
            "reason": "证据 1 提及碰撞分析",
        }],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = {
        "content": "管线系统支持碰撞分析。",
        "metadata": {
            "citation_id": 1,
            "source": "pipe.md",
            "evidence_class": "RELATED_CONTEXT",
            "support_scope": "CONTEXT_ONLY",
        },
    }
    result = reviewer.review("三维管线管理支持碰撞分析吗？", [doc], "三维管线管理支持碰撞分析。[1]")

    _assert_protocol_error(result, "claim_support_matrix_violation:TARGET_ATTRIBUTION+CONTEXT_ONLY")


def test_matrix_target_attribution_with_relation_specific_is_protocol_rejected():
    mock_response = json.dumps({
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "非法组合：TARGET_ATTRIBUTION 引用 RELATION_SPECIFIC",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "三维管线管理支持碰撞分析。",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1],
            "reason": "证据 1 提及碰撞分析",
        }],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = {
        "content": "三维管线管理 -[belongs_to]-> PipelineWebGL",
        "metadata": {"citation_id": 1, "source_type": "graph_relation", "support_scope": "RELATION_SPECIFIC"},
    }
    result = reviewer.review("三维管线管理支持碰撞分析吗？", [doc], "三维管线管理支持碰撞分析。[1]")

    _assert_protocol_error(result, "claim_support_matrix_violation:TARGET_ATTRIBUTION+RELATION_SPECIFIC")


def test_matrix_contextual_fact_accepts_target_specific():
    # ✅* 方向：直接证据当然也能支撑更保守的上下文表述；反向不成立。
    mock_response = json.dumps({
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "CONTEXTUAL_FACT 引用 TARGET_SPECIFIC 属于更保守的合法组合",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "相关管线系统资料涉及碰撞分析。",
            "claim_type": "knowledge_claim",
            "claim_scope": "CONTEXTUAL_FACT",
            "status": "supported",
            "evidence_ids": [1],
            "reason": "TARGET_SPECIFIC 证据可支撑更保守的上下文表述",
        }],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = _source(1, "三维管线管理支持碰撞分析。")
    result = reviewer.review(
        "三维管线管理支持碰撞分析吗？",
        [doc],
        "相关管线系统资料涉及碰撞分析。[1]",
    )
    assert result.verdict == "PASS"
    assert result.claim_reviews[0].claim_scope == "CONTEXTUAL_FACT"


def test_matrix_contextual_fact_with_relation_specific_is_protocol_rejected():
    mock_response = json.dumps({
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "非法组合：CONTEXTUAL_FACT 引用 RELATION_SPECIFIC",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "相关系统资料涉及归属关系。",
            "claim_type": "knowledge_claim",
            "claim_scope": "CONTEXTUAL_FACT",
            "status": "supported",
            "evidence_ids": [1],
            "reason": "证据 1 提及归属",
        }],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = {
        "content": "三维管线管理 -[belongs_to]-> PipelineWebGL",
        "metadata": {"citation_id": 1, "source_type": "graph_relation", "support_scope": "RELATION_SPECIFIC"},
    }
    result = reviewer.review("三维管线管理属于哪个系统？", [doc], "相关系统资料涉及归属关系。[1]")

    _assert_protocol_error(result, "claim_support_matrix_violation:CONTEXTUAL_FACT+RELATION_SPECIFIC")


def test_matrix_relation_claim_accepts_relation_specific_only():
    mock_response = json.dumps({
        "coverage": "PARTIAL",
        "repair_mode": "NONE",
        "summary": "RELATION_CLAIM + RELATION_SPECIFIC 合法组合",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "三维管线管理在知识图谱中归属于 PipelineWebGL。",
            "claim_type": "knowledge_claim",
            "claim_scope": "RELATION_CLAIM",
            "status": "supported",
            "evidence_ids": [1],
            "reason": "证据 1 明确为 belongs_to 关系证据",
        }],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = {
        "content": "三维管线管理 -[belongs_to]-> PipelineWebGL",
        "metadata": {"citation_id": 1, "source_type": "graph_relation", "support_scope": "RELATION_SPECIFIC"},
    }
    result = reviewer.review("三维管线管理属于哪个系统？", [doc], "三维管线管理归属于 PipelineWebGL。[1]")
    assert result.verdict == "PASS"


def test_matrix_relation_claim_with_context_only_is_protocol_rejected():
    mock_response = json.dumps({
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "非法组合：RELATION_CLAIM 引用 CONTEXT_ONLY",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "三维管线管理归属于 PipelineWebGL。",
            "claim_type": "knowledge_claim",
            "claim_scope": "RELATION_CLAIM",
            "status": "supported",
            "evidence_ids": [1],
            "reason": "证据 1 提及 PipelineWebGL",
        }],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = {
        "content": "PipelineWebGL 相关系统资料。",
        "metadata": {
            "citation_id": 1,
            "source": "webgl.md",
            "evidence_class": "RELATED_CONTEXT",
            "support_scope": "CONTEXT_ONLY",
        },
    }
    result = reviewer.review("三维管线管理属于哪个系统？", [doc], "三维管线管理归属于 PipelineWebGL。[1]")

    _assert_protocol_error(result, "claim_support_matrix_violation:RELATION_CLAIM+CONTEXT_ONLY")


def test_matrix_graph_relation_plus_context_cannot_merge_into_target_attribution():
    # belongs_to 关系 + CONTEXT_ONLY 上下文，LLM 试图合并成 TARGET_ATTRIBUTION：
    # 属性自动继承必须在代码矩阵处被拒绝。
    mock_response = json.dumps({
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "非法组合：把关系与上下文合并成目标实体自身属性",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "三维管线管理具备 PipelineWebGL 的高并发渲染能力。",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1, 2],
            "reason": "证据 1 为归属关系，证据 2 提及高并发渲染",
        }],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc1 = {
        "content": "三维管线管理 -[belongs_to]-> PipelineWebGL",
        "metadata": {"citation_id": 1, "source_type": "graph_relation", "support_scope": "RELATION_SPECIFIC"},
    }
    doc2 = {
        "content": "PipelineWebGL 具备高并发渲染能力。",
        "metadata": {
            "citation_id": 2,
            "source": "webgl.md",
            "evidence_class": "RELATED_CONTEXT",
            "support_scope": "CONTEXT_ONLY",
        },
    }
    result = reviewer.review(
        "三维管线管理具备什么渲染能力？",
        [doc1, doc2],
        "三维管线管理具备高并发渲染能力。[1][2]",
    )

    _assert_protocol_error(result, "claim_support_matrix_violation:TARGET_ATTRIBUTION+RELATION_SPECIFIC")


def test_matrix_unknown_support_scope_fails_closed():
    # 协议内证据（有 evidence_class）缺失 support_scope = SHOULD_HAVE_SCOPE_BUT_MISSING，
    # 视为协议错误 fail-closed，不得支撑任何 supported knowledge_claim。
    # 注意区分：协议外证据（linear KB / external）缺 scope 是 OUT_OF_SCOPE_PROTOCOL，不裁决。
    mock_response = json.dumps({
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "UNKNOWN scope 不得支撑 supported knowledge_claim",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "StampServer 支持服务发布。",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1],
            "reason": "证据 1 提及服务发布",
        }],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = {
        "content": "StampServer 支持服务发布。",
        "metadata": {"citation_id": 1, "source": "doc.md", "evidence_class": "TARGET_DIRECT"},
    }
    result = reviewer.review("StampServer 支持服务发布吗？", [doc], "StampServer 支持服务发布。[1]")

    _assert_protocol_error(result, "claim_support_matrix_violation:TARGET_ATTRIBUTION+UNKNOWN")


def test_matrix_out_of_protocol_external_evidence_is_not_adjudicated():
    # external 来源明确属于本轮豁免类型：不参与 Claim Support Matrix（不裁决），
    # 继续由既有 Reviewer 语义核对兜底，缺席 scope 不构成协议错误。
    mock_response = json.dumps({
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "外部来源证据不参与矩阵",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "StampServer 支持服务发布。",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1],
            "reason": "外部页面提及服务发布",
        }],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = {
        "content": "官网页面说明服务发布功能。",
        "metadata": {"citation_id": 1, "title": "官网页面", "source_type": "external"},
    }
    result = reviewer.review("StampServer 支持服务发布吗？", [doc], "StampServer 支持服务发布。[1]")

    assert result.verdict == "PASS"
    assert result.error is None


def test_matrix_out_of_protocol_linear_kb_evidence_is_not_adjudicated():
    # Linear KB 文本（source_type=knowledge_base 且无 evidence_class / grant_admitted）
    # 尚未迁移 Support Scope Protocol：本轮不执行 Matrix，继续既有 Reviewer 语义核对。
    mock_response = json.dumps({
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "线性路径 KB 文本不参与矩阵",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "StampServer 支持服务发布。",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1],
            "reason": "KB 文本提及服务发布",
        }],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    doc = {
        "content": "StampServer 支持服务发布。",
        "metadata": {"citation_id": 1, "source": "doc.md", "source_type": "knowledge_base"},
    }
    result = reviewer.review("StampServer 支持服务发布吗？", [doc], "StampServer 支持服务发布。[1]")

    assert result.verdict == "PASS"
    assert result.error is None


def test_matrix_mixed_protocol_and_external_citations_are_judged_per_citation():
    # 逐 citation 判断：[1] 协议内 TARGET_SPECIFIC 逐项核对合法，[2] external 不裁决
    # → 整体 PASS。external citation 只豁免它自己，不得改变其他 citation 的核对。
    mock_response = json.dumps({
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "协议内 citation 合法、external citation 不裁决",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "StampServer 支持服务发布。",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1, 2],
            "reason": "证据 1 支持目标功能，证据 2 为外部补充",
        }],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    docs = [
        _source(1, "StampServer 支持服务发布。"),
        {
            "content": "官网页面说明服务发布功能。",
            "metadata": {"citation_id": 2, "title": "官网页面", "source_type": "external"},
        },
    ]
    result = reviewer.review("StampServer 支持服务发布吗？", docs, "StampServer 支持服务发布。[1][2]")

    assert result.verdict == "PASS"
    assert result.error is None


def test_matrix_external_citation_does_not_mask_illegal_protocol_citation():
    # 禁止旁路：Claim 混合引用协议内非法 citation 与协议外 citation 时，
    # 不得因为 external 不参与矩阵就跳过整个 Claim 的核对；[1] 仍必须按矩阵拒绝。
    mock_response = json.dumps({
        "coverage": "FULL",
        "repair_mode": "NONE",
        "summary": "external citation 不得掩盖协议内非法组合",
        "claim_reviews": [{
            "claim_id": "c1",
            "claim": "三维管线管理支持碰撞分析。",
            "claim_type": "knowledge_claim",
            "claim_scope": "TARGET_ATTRIBUTION",
            "status": "supported",
            "evidence_ids": [1, 2],
            "reason": "证据 1 提及碰撞分析，证据 2 为外部补充",
        }],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_response)
    docs = [
        {
            "content": "管线系统支持碰撞分析。",
            "metadata": {
                "citation_id": 1,
                "source": "pipe.md",
                "evidence_class": "RELATED_CONTEXT",
                "support_scope": "CONTEXT_ONLY",
            },
        },
        {
            "content": "官网页面说明碰撞分析功能。",
            "metadata": {"citation_id": 2, "title": "官网页面", "source_type": "external"},
        },
    ]
    result = reviewer.review("三维管线管理支持碰撞分析吗？", docs, "三维管线管理支持碰撞分析。[1][2]")

    _assert_protocol_error(result, "claim_support_matrix_violation:TARGET_ATTRIBUTION+CONTEXT_ONLY")


def test_matrix_missing_claim_scope_on_knowledge_claim_fails_closed():
    # 语义分类必须来自 LLM：knowledge_claim 缺失 claim_scope 即协议错误。
    payload = _pass_payload()
    del payload["claim_reviews"][0]["claim_scope"]

    result = _review_payload(payload)

    _assert_protocol_error(result, "missing_fields:claim_scope")


def test_matrix_missing_claim_scope_on_non_knowledge_claim_defaults_not_applicable():
    # 非 knowledge_claim 的 scope 是确定性归约（NOT_APPLICABLE），缺失时由代码补齐。
    payload = _pass_payload()
    payload["claim_reviews"][0]["claim_type"] = "limitation_statement"
    payload["claim_reviews"][0]["claim"] = "当前证据未说明其他端口。"
    del payload["claim_reviews"][0]["claim_scope"]

    result = _review_payload(payload)

    assert result.verdict == "PASS"
    assert result.claim_reviews[0].claim_scope == "NOT_APPLICABLE"


def test_matrix_knowledge_claim_with_not_applicable_scope_is_protocol_rejected():
    payload = _pass_payload()
    payload["claim_reviews"][0]["claim_scope"] = "NOT_APPLICABLE"

    result = _review_payload(payload)

    _assert_protocol_error(result, "knowledge_claim_scope_not_applicable")


def test_matrix_non_knowledge_claim_with_fact_scope_is_protocol_rejected():
    payload = _pass_payload()
    payload["claim_reviews"][0]["claim_type"] = "question_context"
    payload["claim_reviews"][0]["claim_scope"] = "CONTEXTUAL_FACT"

    result = _review_payload(payload)

    _assert_protocol_error(result, "non_knowledge_claim_scope_invalid:CONTEXTUAL_FACT")


def test_matrix_unsupported_claim_is_not_subject_to_matrix():
    # 矩阵只约束 supported knowledge_claim；unsupported claim 允许引用任何证据说明判断依据。
    payload = _pass_payload()
    payload["claim_reviews"][0]["status"] = "unsupported"
    payload["claim_reviews"][0]["evidence_ids"] = [1]
    payload["rewrite_actions"] = [{
        "claim_id": "c1",
        "action": "rewrite_to_supported_scope_or_remove",
        "instruction": "删除该断言",
    }]
    payload["repair_mode"] = "REWRITE"
    # _review_payload 的证据是 TARGET_SPECIFIC；再换成 CONTEXT_ONLY 也应放行（unsupported 不核对矩阵）。
    reviewer = HelperGroundingReviewer(lambda _msgs: json.dumps(payload))
    doc = {
        "content": "StampServer 支持服务发布。",
        "metadata": {"citation_id": 1, "source": "doc.md", "support_scope": "CONTEXT_ONLY"},
    }
    result = reviewer.review("StampServer 支持服务发布吗？", [doc], "StampServer 支持服务发布。[1]")

    assert result.verdict == "REVISE"
    assert result.unsupported_claims[0].claim_scope == "TARGET_ATTRIBUTION"


def test_retrieve_repair_keeps_verdict_and_exposes_descriptive_feedback():
    payload = _revise_payload()
    payload["repair_mode"] = "RETRIEVE"
    payload["rewrite_actions"] = []
    payload["retrieval_feedback"] = {
        "gap_id": "stampserver-port",
        "affected_claim_ids": ["c2"],
        "missing_fact": "StampServer 默认端口的直接证据",
        "subject_entity_ids": ["stampserver"],
        "deficiency_type": "NO_DIRECT_EVIDENCE",
        "reason": "当前快照没有端口事实",
    }

    result = _review_payload(payload)

    assert result.verdict == "REVISE"
    assert result.repair_mode == "RETRIEVE"
    assert result.retrieval_feedback is not None
    assert result.retrieval_feedback.gap_id == "stampserver-port"


def test_reviewer_retrieve_feedback_rejects_query_or_tool_directives():
    payload = _revise_payload()
    payload["repair_mode"] = "RETRIEVE"
    payload["rewrite_actions"] = []
    payload["retrieval_feedback"] = {
        "gap_id": "stampserver-port",
        "affected_claim_ids": ["c2"],
        "missing_fact": "默认端口",
        "subject_entity_ids": ["stampserver"],
        "deficiency_type": "NO_DIRECT_EVIDENCE",
        "reason": "缺直接证据",
        "query": "StampServer 默认端口",
    }

    _assert_protocol_error(_review_payload(payload), "forbidden_retrieval_directive:query")


def test_structured_output_schema_requires_claim_scope_only_for_findings():
    from rag_knowledge.services.helper_grounding_reviewer import review_response_json_schema

    schema = review_response_json_schema()
    finding_schema = schema["properties"]["findings"]["items"]

    assert "claim_scope" in finding_schema["properties"]
    assert "claim_scope" in finding_schema["required"]
    assert finding_schema["properties"]["claim_scope"]["enum"] == [
        "CONTEXTUAL_FACT",
        "RELATION_CLAIM",
        "TARGET_ATTRIBUTION",
    ]
