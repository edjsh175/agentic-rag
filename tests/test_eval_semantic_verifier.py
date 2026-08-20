import json
from pathlib import Path

from scripts.eval_semantic_verifier import build_report, evaluate_items
from rag_knowledge.services.evidence_pack import GroundingVerdict


class _SequenceVerifier:
    def __init__(self, labels):
        self._labels = iter(labels)

    def verify(self, _answer, _docs):
        label = next(self._labels)
        if label == "invalid":
            raise ValueError("bad protocol")
        return GroundingVerdict(
            ok=label == "entailed",
            details={
                "semantic_verifier": {
                    "results": [{"id": 1, "label": label}],
                }
            },
        )


def _items():
    # These all deliberately pass deterministic grounding after Phase 10B.
    # They exercise long-tail semantic roles/scope rather than mechanical operators.
    return [
        {
            "id": "e",
            "claim": "调度模块负责任务分发",
            "evidence": ["调度模块负责任务分发。"],
            "expected": "entailed",
        },
        {
            "id": "c",
            "claim": "任务分发负责调度模块",
            "evidence": ["调度模块负责任务分发。"],
            "expected": "contradicted",
        },
        {
            "id": "u",
            "claim": "系统采用旧版协议",
            "evidence": ["启用兼容模式时，系统采用旧版协议。"],
            "expected": "unsupported",
        },
    ]


def _report(items, results, *, min_residual_cases=1):
    return build_report(
        items,
        results,
        model_role="semantic_verifier",
        model="test",
        min_accuracy=0.95,
        max_false_accept_rate=0.0,
        max_invalid_rate=0.02,
        min_residual_cases=min_residual_cases,
    )


def test_eval_semantic_verifier_metrics_make_residual_false_accept_critical():
    items = _items()
    results = evaluate_items(items, _SequenceVerifier(["entailed", "entailed", "unsupported"]))
    report = _report(items, results)

    assert report["residual_metrics"]["case_count"] == 3
    assert report["residual_metrics"]["false_accept_count"] == 1
    assert report["residual_metrics"]["false_accept_rate"] == 0.5
    assert report["activation_gate"]["ready"] is False


def test_eval_semantic_verifier_activation_gate_rejects_tiny_perfect_residual_set():
    items = _items()
    results = evaluate_items(items, _SequenceVerifier(["entailed", "contradicted", "unsupported"]))
    report = _report(items, results, min_residual_cases=20)

    assert report["residual_metrics"]["accuracy"] == 1.0
    assert report["activation_gate"]["ready"] is False
    assert report["activation_gate"]["min_residual_cases"] == 20


def test_eval_semantic_verifier_activation_gate_passes_clean_residual_predictions():
    items = _items()
    results = evaluate_items(items, _SequenceVerifier(["entailed", "contradicted", "unsupported"]))
    report = _report(items, results)

    assert report["residual_metrics"]["accuracy"] == 1.0
    assert report["residual_metrics"]["false_accept_rate"] == 0.0
    assert report["residual_metrics"]["invalid_rate"] == 0.0
    assert report["activation_gate"]["ready"] is True


def test_eval_semantic_verifier_counts_protocol_failure_as_residual_invalid_and_false_reject():
    items = _items()
    results = evaluate_items(items, _SequenceVerifier(["invalid", "contradicted", "unsupported"]))
    report = _report(items, results)

    assert report["residual_metrics"]["invalid_count"] == 1
    assert report["residual_metrics"]["false_reject_count"] == 1
    assert report["activation_gate"]["ready"] is False


def test_eval_semantic_verifier_skips_cases_already_rejected_deterministically():
    item = {
        "id": "deterministic-block",
        "claim": "StampServer 使用 React",
        "evidence": ["StampServer 默认服务端口为 8080。"],
        "expected": "unsupported",
    }

    class _MustNotRun:
        def verify(self, _answer, _docs):
            raise AssertionError("semantic verifier must not run after deterministic reject")

    result = evaluate_items([item], _MustNotRun())[0]
    assert result["deterministic_pass"] is False
    assert result["semantic_evaluated"] is False
    assert result["predicted"] == "deterministic_reject"
    assert result["end_to_end_correct"] is True


def test_incident_gold_covers_both_real_leak_traces_and_positive_controls():
    path = Path("tests/fixtures/semantic_verifier_incident_gold_v1.json")
    items = json.loads(path.read_text(encoding="utf-8"))
    trace_ids = {item["trace_id"] for item in items}
    labels = {item["expected"] for item in items}

    assert "73f6b29736264393b7ffa3415d2079d9" in trace_ids
    assert "bbcd8571861949819dc472474c7a3bb7" in trace_ids
    assert "entailed" in labels
    assert "unsupported" in labels
    assert len(items) >= 10


def test_phase10_hardening_moves_v1_negative_cases_out_of_semantic_residual():
    path = Path("tests/fixtures/semantic_verifier_residual_hard_v1.json")
    items = json.loads(path.read_text(encoding="utf-8"))

    class _AlwaysEntailed:
        def verify(self, _answer, _docs):
            return GroundingVerdict(
                ok=True,
                details={
                    "semantic_verifier": {
                        "results": [{"id": 1, "label": "entailed"}],
                    }
                },
            )

    results = evaluate_items(items, _AlwaysEntailed())
    for item, result in zip(items, results):
        if item["expected"] == "entailed":
            assert result["deterministic_pass"] is True
        else:
            assert result["deterministic_pass"] is False
            assert result["semantic_evaluated"] is False


def test_residual_hard_v2_is_large_balanced_and_reaches_semantic_layer():
    path = Path("tests/fixtures/semantic_verifier_residual_hard_v2.json")
    items = json.loads(path.read_text(encoding="utf-8"))
    labels = [item["expected"] for item in items]

    assert len(items) >= 20
    assert labels.count("entailed") >= 8
    assert sum(label != "entailed" for label in labels) >= 8

    class _AlwaysUnsupported:
        def verify(self, _answer, _docs):
            return GroundingVerdict(
                ok=False,
                details={
                    "semantic_verifier": {
                        "results": [{"id": 1, "label": "unsupported"}],
                    }
                },
            )

    results = evaluate_items(items, _AlwaysUnsupported())
    assert all(result["deterministic_pass"] for result in results)
    assert all(result["semantic_evaluated"] for result in results)
