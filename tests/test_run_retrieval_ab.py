from run_retrieval_ab import build_regression_message, detect_regressions


def test_detect_regressions_returns_empty_when_metrics_hold():
    previous = {"recall@3": 0.6, "mrr": 0.5, "overall_hit_rate": 0.7}
    current = {"recall@3": 0.6, "mrr": 0.52, "overall_hit_rate": 0.72}

    assert detect_regressions(previous, current) == []


def test_detect_regressions_flags_metric_drop():
    previous = {"recall@3": 0.6, "mrr": 0.5, "overall_hit_rate": 0.7}
    current = {"recall@3": 0.58, "mrr": 0.49, "overall_hit_rate": 0.7}

    assert detect_regressions(previous, current) == [
        "recall@3 dropped from 0.6000 to 0.5800",
        "mrr dropped from 0.5000 to 0.4900",
    ]


def test_build_regression_message_adds_stale_dataset_hint():
    previous = {"recall@3": 0.6, "mrr": 0.5, "overall_hit_rate": 0.7}
    current = {"recall@3": 0.0, "mrr": 0.0, "overall_hit_rate": 0.0}
    regressions = detect_regressions(previous, current)

    message = build_regression_message(
        "data/eval_dataset_hardcases.json", "hybrid", regressions, previous, current
    )

    assert "dataset may be stale" in message
    assert "regenerate" in message


def test_detect_regressions_with_threshold():
    previous = {"recall@3": 0.6, "mrr": 0.5, "overall_hit_rate": 0.7}
    current = {"recall@3": 0.585, "mrr": 0.49, "overall_hit_rate": 0.7}
    # With threshold 0.01:
    # recall@3 drop is 0.015 > 0.01 (flagged)
    # mrr drop is 0.01 <= 0.01 (not flagged)
    assert detect_regressions(previous, current, threshold=0.01) == [
        "recall@3 dropped from 0.6000 to 0.5850 (delta: 0.0150 > threshold: 0.0100)"
    ]
