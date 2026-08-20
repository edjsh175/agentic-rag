import json
from types import SimpleNamespace
from unittest.mock import patch

from rag_knowledge.services.answer_finalizer import AnswerFinalizer
from rag_knowledge.services.evidence_pack import (
    DETERMINISTIC_GROUNDING_POLICY_VERSION,
    GroundingVerdict,
)
from rag_knowledge.services.rag import RagChain
from rag_knowledge.services.semantic_entailment import (
    SemanticEntailmentVerifier,
    build_claim_evidence_units,
    semantic_verifier_policy_fingerprint,
    validate_activation_report,
)


def _source(index: int, content: str):
    return {
        "content": content,
        "metadata": {"citation_id": index, "source": f"doc-{index}.md"},
    }


def test_claim_evidence_units_preserve_local_citation_scope():
    docs = [
        _source(1, "WebGL 用于前端渲染。"),
        _source(2, "WebRTC 用于实时通信。"),
    ]
    units = build_claim_evidence_units(
        "WebGL 用于前端渲染。[1] WebRTC 用于实时通信。[2]",
        docs,
    )

    assert len(units) == 2
    assert units[0].citation_ids == (1,)
    assert units[0].evidence == ("WebGL 用于前端渲染。",)
    assert units[1].citation_ids == (2,)
    assert units[1].evidence == ("WebRTC 用于实时通信。",)


def test_semantic_verifier_accepts_entailed_claims():
    docs = [_source(1, "StampServer 支持在线发布。")]
    verifier = SemanticEntailmentVerifier(
        lambda _prompt: {
            "claims": [{"id": 1, "label": "entailed", "reason": "证据直接说明支持在线发布"}]
        }
    )

    verdict = verifier.verify("StampServer 支持在线发布。[1]", docs)

    assert verdict.ok
    results = verdict.details["semantic_verifier"]["results"]
    assert results[0]["label"] == "entailed"
    assert results[0]["citations"] == [1]


def test_semantic_verifier_accepts_flattenable_top_level_list_payload():
    docs = [_source(1, "StampServer 支持在线发布。")]
    verifier = SemanticEntailmentVerifier(
        lambda _prompt: '[{"claims":[{"id":1,"label":"entailed","reason":"直接支持"}]}]'
    )

    verdict = verifier.verify("StampServer 支持在线发布。[1]", docs)

    assert verdict.ok


def test_semantic_verifier_rejects_contradicted_claim():
    docs = [_source(1, "StampServer 不支持离线发布。")]
    verifier = SemanticEntailmentVerifier(
        lambda _prompt: {
            "claims": [{"id": 1, "label": "contradicted", "reason": "证据明确否定离线发布"}]
        }
    )

    verdict = verifier.verify("StampServer 支持离线发布。[1]", docs)

    assert not verdict.ok
    assert "semantic_contradiction" in verdict.reasons


def test_deterministic_failure_never_calls_semantic_verifier():
    docs = [_source(1, "StampServer 默认服务端口为 8080。")]
    called = []

    def semantic_verify(_answer, _docs):
        called.append(True)
        return GroundingVerdict(ok=True)

    finalized = AnswerFinalizer().finalize(
        "StampServer 使用 React 管理端口 [1]。",
        "StampServer 的端口是什么？",
        docs,
        allow_general_knowledge=False,
        semantic_verify=semantic_verify,
    )

    assert called == []
    assert "React" not in finalized.answer
    assert finalized.grounding["final_mode"] == "deterministic_fallback"


def test_semantic_failure_can_only_veto_verified_candidate():
    docs = [_source(1, "StampServer 支持在线发布。")]

    def semantic_verify(_answer, _docs):
        return GroundingVerdict(
            ok=False,
            reasons=["semantic_unsupported"],
            unsupported_segments=["在线发布关系需要进一步验证"],
            details={"semantic_verifier": {"claim_count": 1}},
        )

    finalized = AnswerFinalizer().finalize(
        "StampServer 支持在线发布。[1]",
        "StampServer 支持什么？",
        docs,
        allow_general_knowledge=False,
        semantic_verify=semantic_verify,
    )

    assert finalized.grounding["verdict"] == "fail"
    assert finalized.grounding["final_mode"] == "deterministic_fallback"
    assert finalized.grounding["fallback_used"] is True


def test_semantic_pass_preserves_verified_candidate_and_trace_details():
    docs = [_source(1, "StampServer 支持在线发布。")]
    candidate = "StampServer 支持在线发布。[1]"

    def semantic_verify(_answer, _docs):
        return GroundingVerdict(
            ok=True,
            details={
                "semantic_verifier": {
                    "claim_count": 1,
                    "results": [{"id": 1, "label": "entailed"}],
                }
            },
        )

    finalized = AnswerFinalizer().finalize(
        candidate,
        "StampServer 支持什么？",
        docs,
        allow_general_knowledge=False,
        semantic_verify=semantic_verify,
    )

    assert finalized.answer == candidate
    assert finalized.grounding["verdict"] == "pass"
    assert finalized.grounding["details"]["semantic"]["semantic_verifier"]["claim_count"] == 1


def test_semantic_failure_then_grounded_retry_is_reverified():
    docs = [_source(1, "StampServer 支持在线发布，不支持离线发布。")]
    semantic_calls = []

    def semantic_verify(answer, _docs):
        semantic_calls.append(answer)
        if "在线发布" in answer:
            return GroundingVerdict(
                ok=False,
                reasons=["semantic_unsupported"],
                unsupported_segments=["第一版关系表达需要收紧"],
            )
        return GroundingVerdict(
            ok=True,
            details={"semantic_verifier": {"claim_count": 1}},
        )

    finalized = AnswerFinalizer().finalize(
        "StampServer 支持在线发布。[1]",
        "StampServer 发布能力是什么？",
        docs,
        allow_general_knowledge=False,
        semantic_verify=semantic_verify,
        retry_candidate=lambda _verdict: "StampServer 不支持离线发布。[1]",
    )

    assert len(semantic_calls) == 2
    assert finalized.answer == "StampServer 不支持离线发布。[1]"
    assert finalized.grounding["final_mode"] == "grounded_retry"
    assert [item["verdict"] for item in finalized.grounding["attempts"]] == ["fail", "pass"]


def test_semantic_verifier_claim_budget_fails_closed_without_partial_check():
    docs = [
        _source(1, "A 支持功能一。"),
        _source(2, "B 支持功能二。"),
    ]
    calls = []
    verifier = SemanticEntailmentVerifier(
        lambda _prompt: calls.append(True),
        max_claims=1,
    )

    verdict = verifier.verify("A 支持功能一。[1] B 支持功能二。[2]", docs)

    assert not verdict.ok
    assert verdict.reasons == ["semantic_verifier_claim_limit_exceeded"]
    assert calls == []


def _activation_cfg(report_path, *, model="verifier-test"):
    endpoint = SimpleNamespace(
        model=model,
        normalized_provider=lambda: "ollama",
    )
    return SimpleNamespace(
        semantic_verifier_enabled=True,
        semantic_verifier_timeout=9.0,
        semantic_verifier_max_claims=4,
        semantic_verifier_max_evidence_chars=600,
        semantic_verifier_activation_report=report_path,
        semantic_verifier_activation_min_residual_cases=20,
        semantic_verifier_activation_min_accuracy=0.95,
        semantic_verifier_activation_max_false_accept_rate=0.0,
        semantic_verifier_activation_max_invalid_rate=0.02,
        endpoint_for=lambda role: endpoint,
    )


def _write_ready_activation_report(path, *, model="verifier-test", false_accept_rate=0.0):
    path.write_text(
        json.dumps({
            "scope": "semantic_grounding_verifier",
            "model_role": "semantic_verifier",
            "provider": "ollama",
            "model": model,
            "semantic_policy_fingerprint": semantic_verifier_policy_fingerprint(),
            "deterministic_policy_version": DETERMINISTIC_GROUNDING_POLICY_VERSION,
            "residual_metrics": {
                "case_count": 24,
                "accuracy": 1.0,
                "false_accept_rate": false_accept_rate,
                "invalid_rate": 0.0,
            },
            "activation_gate": {"ready": True},
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def test_rag_runtime_semantic_callback_uses_qualified_semantic_endpoint(tmp_path):
    report_path = tmp_path / "activation.json"
    _write_ready_activation_report(report_path)
    chain = object.__new__(RagChain)
    chain._cfg = _activation_cfg(report_path)
    docs = [_source(1, "StampServer 支持在线发布。")]
    response = '{"claims":[{"id":1,"label":"entailed","reason":"证据直接支持"}]}'

    with patch("rag_knowledge.llm_http.chat_role", return_value=response) as mocked:
        callback = chain._semantic_verify_callback()
        verdict = callback("StampServer 支持在线发布。[1]", docs)

    assert verdict.ok
    assert mocked.call_args.args[1] == "semantic_verifier"
    assert mocked.call_args.kwargs["timeout"] == 9.0
    assert mocked.call_args.kwargs["format_json"] is True
    assert mocked.call_args.kwargs["think"] is False


def test_rag_runtime_semantic_callback_blocks_unqualified_model_without_calling_llm(tmp_path):
    chain = object.__new__(RagChain)
    chain._cfg = _activation_cfg(tmp_path / "missing.json")

    with patch("rag_knowledge.llm_http.chat_role") as mocked:
        callback = chain._semantic_verify_callback()
        verdict = callback("StampServer 支持在线发布。[1]", [_source(1, "StampServer 支持在线发布。")])

    assert not verdict.ok
    assert "semantic_verifier_activation_blocked" in verdict.reasons
    assert "activation_report_missing" in verdict.reasons
    mocked.assert_not_called()


def test_activation_report_rejects_stale_policy_fingerprint(tmp_path):
    report_path = tmp_path / "activation.json"
    _write_ready_activation_report(report_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["semantic_policy_fingerprint"] = "stale-policy"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    status = validate_activation_report(
        report_path,
        provider="ollama",
        model="verifier-test",
    )

    assert status["ready"] is False
    assert "activation_report_semantic_policy_mismatch" in status["reasons"]


def test_activation_report_rejects_model_mismatch_and_false_accept(tmp_path):
    report_path = tmp_path / "activation.json"
    _write_ready_activation_report(report_path, model="other-model", false_accept_rate=0.1)

    status = validate_activation_report(
        report_path,
        provider="ollama",
        model="verifier-test",
        min_residual_cases=20,
        min_accuracy=0.95,
        max_false_accept_rate=0.0,
        max_invalid_rate=0.02,
    )

    assert status["ready"] is False
    assert "activation_report_model_mismatch" in status["reasons"]
    assert "activation_false_accept_above_threshold" in status["reasons"]


def test_rag_runtime_semantic_callback_is_absent_when_disabled():
    chain = object.__new__(RagChain)
    chain._cfg = SimpleNamespace(semantic_verifier_enabled=False)
    assert chain._semantic_verify_callback() is None


def test_config_exposes_dedicated_semantic_verifier_endpoint(isolated_storage):
    cfg, *_ = isolated_storage()
    endpoint = cfg.endpoint_for("semantic_verifier")
    assert endpoint.role == "semantic_verifier"
    assert endpoint.model == cfg.semantic_verifier_model
    assert endpoint.model


def test_semantic_verifier_error_fails_closed():
    docs = [_source(1, "StampServer 支持在线发布。")]

    def semantic_verify(_answer, _docs):
        raise RuntimeError("verifier unavailable")

    finalized = AnswerFinalizer().finalize(
        "StampServer 支持在线发布。[1]",
        "StampServer 支持什么？",
        docs,
        allow_general_knowledge=False,
        semantic_verify=semantic_verify,
    )

    assert finalized.grounding["verdict"] == "fail"
    assert finalized.grounding["final_mode"] == "deterministic_fallback"
    assert any("semantic_verifier_error" in reason for reason in finalized.grounding["reasons"])
