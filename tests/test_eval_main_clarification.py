from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "eval_main_clarification.py"
SPEC = importlib.util.spec_from_file_location("eval_main_clarification", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_gold_set_is_balanced_and_has_required_regressions():
    version, cases = MODULE.load_gold(MODULE.DEFAULT_GOLD)

    assert version == "v1"
    assert len(cases) == 16
    assert sum(case["expected_clarification"] for case in cases) == 8
    assert {case["id"] for case in cases}.issuperset({
        "ambiguous_typo_pipelien",
        "ambiguous_pipeline_family",
        "clear_pipeline_webgl",
        "clear_pipeline_comparison",
    })


def test_decision_classifier_only_accepts_clarify_tool_call():
    assert MODULE.is_clarification_decision(
        SimpleNamespace(action="tool_call", tool="clarify"),
    )
    assert not MODULE.is_clarification_decision(
        SimpleNamespace(action="tool_call", tool="retrieve_kb"),
    )
    assert not MODULE.is_clarification_decision(
        SimpleNamespace(action="finalize", tool=None),
    )


def test_metrics_enforce_both_prd_thresholds_and_controller_errors():
    cases = [
        {"id": "a", "question": "ambiguous", "expected_clarification": True},
        {"id": "b", "question": "clear", "expected_clarification": False},
    ]

    perfect, perfect_metrics = MODULE.evaluate_cases(
        cases,
        lambda question: SimpleNamespace(
            action="tool_call",
            tool="clarify" if question == "ambiguous" else "retrieve_kb",
        ),
    )
    assert all(row["passed"] for row in perfect)
    assert perfect_metrics["passed"] is True

    _, failed_metrics = MODULE.evaluate_cases(
        cases,
        lambda _question: SimpleNamespace(action="tool_call", tool="clarify"),
    )
    assert failed_metrics["false_clarification_rate"] == 1.0
    assert failed_metrics["passed"] is False

    def unavailable(_question):
        raise RuntimeError("controller unavailable")

    _, error_metrics = MODULE.evaluate_cases(cases, unavailable)
    assert error_metrics["error_count"] == 2
    assert error_metrics["passed"] is False
