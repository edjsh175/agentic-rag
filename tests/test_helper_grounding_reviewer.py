import json
import pytest

from rag_knowledge.services.helper_grounding_reviewer import (
    HelperGroundingReviewer,
    HelperGroundingReviewResult,
    ClaimReview,
    RewriteAction,
    format_evidence_snapshot,
)


def _source(index: int, content: str, source: str = ""):
    return {
        "content": content,
        "metadata": {
            "citation_id": index,
            "source": source or f"doc-{index}.md",
            "section_path": f"第{index}章",
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
        "summary": "回答内容受证据支持",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "StampServer 支持服务发布。",
                "claim_type": "knowledge_claim",
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
        "summary": "候选回答包含不受支持的事实",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "StampServer 支持服务发布。",
                "claim_type": "knowledge_claim",
                "status": "supported",
                "evidence_ids": [1],
                "reason": "证据 1 明确支持",
            },
            {
                "claim_id": "c2",
                "claim": "StampServer 默认开放 9999 端口。",
                "claim_type": "knowledge_claim",
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
        "summary": "当前证据无法形成安全回答",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "系统默认开放 9999 端口。",
                "claim_type": "knowledge_claim",
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
        "summary": "回答内容完全受证据支持",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "StampServer 支持服务发布。",
                "claim_type": "knowledge_claim",
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


def test_reviewer_pass_partial():
    mock_response = json.dumps({
        "verdict": "PASS",
        "coverage": "PARTIAL",
        "summary": "回答受支持，但证据仅覆盖部分问题",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "StampWebRTC 示例使用 31443 端口。",
                "claim_type": "knowledge_claim",
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
        "summary": "部分断言未在证据中体现",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "StampServer 支持在线发布。",
                "claim_type": "knowledge_claim",
                "evidence_ids": [1],
                "status": "supported",
                "reason": "证据 1 明确支持",
            },
            {
                "claim_id": "c2",
                "claim": "StampServer 支持基于 Redis 的缓存集群。",
                "claim_type": "knowledge_claim",
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
        "summary": "证据完全无法回答该问题",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "系统使用 Java 编写。",
                "claim_type": "knowledge_claim",
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
            "summary": "ok",
            "claim_reviews": [{
                "claim_id": "c1",
                "claim": "候选答案。",
                "claim_type": "knowledge_claim",
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
    assert "原子事实拆分是审核的前置条件" in captured_msgs[0]["content"]
    assert "每个 claim_reviews 项只能表达一个可独立判定真假的事实" in captured_msgs[0]["content"]
    assert "在部署过程中" in captured_msgs[0]["content"]
    assert "Claim 是否 supported 只看该 Claim 自身是否被 Evidence 支持" in captured_msgs[0]["content"]
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

    _assert_protocol_error(result, "revise_requires_rewrite_actions")


def test_unsupported_non_knowledge_claim_also_requires_rewrite_action():
    payload = _pass_payload()
    payload["claim_reviews"][0]["claim_type"] = "limitation_statement"
    payload["claim_reviews"][0]["status"] = "unsupported"
    payload["claim_reviews"][0]["evidence_ids"] = []

    result = _review_payload(payload)

    _assert_protocol_error(result, "revise_requires_rewrite_actions")


def test_review_requires_at_least_one_claim_review():
    payload = _pass_payload()
    payload["claim_reviews"] = []

    result = _review_payload(payload)

    _assert_protocol_error(result, "review_requires_claim_reviews")


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


def test_protocol_repair_retries_once_and_can_recover():
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
    assert "协议修复" in calls[1][0]["content"]
    assert calls[1][1]["role"] == "user"


def test_protocol_repair_rejects_semantic_drift():
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

    assert result.verdict == "ERROR"
    assert result.error == "invalid_review_protocol:protocol_repair_semantic_drift"
    assert len(result.protocol_attempts) == 2
    assert result.protocol_attempts[1]["error"] == result.error


def test_structured_output_schema_has_single_semantic_source():
    from rag_knowledge.services.helper_grounding_reviewer import review_response_json_schema

    schema = review_response_json_schema()

    assert "verdict" not in schema["properties"]
    assert "verdict" not in schema["required"]
    action_enum = schema["properties"]["rewrite_actions"]["items"]["properties"]["action"]["enum"]
    assert "preserve" not in action_enum
