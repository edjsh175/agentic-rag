import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval_answer_governance.py"
SPEC = importlib.util.spec_from_file_location("eval_answer_governance", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_conflict_behavior_requires_signal_and_explicit_values():
    item = {
        "id": "conflict-1",
        "answerability": "conflict",
        "required_facts": ["5439", "5349", "冲突提示"],
        "forbidden_claims": ["仅存在唯一端口"],
    }

    result = MODULE.evaluate_item(item, "存在冲突：5439 [1] 与 5349 [2]，请核对原文。")

    assert result["behavior_pass"] is True
    assert result["citation_count"] == 2


def test_none_behavior_requires_refusal_and_rejects_forbidden_claim():
    item = {
        "id": "none-1",
        "answerability": "none",
        "required_facts": ["未查询到"],
        "forbidden_claims": ["编造端口"],
    }

    assert MODULE.evaluate_item(item, "当前知识库中未查询到相关内容。")["behavior_pass"]
    assert not MODULE.evaluate_item(item, "当前知识库中未查询到，但编造端口。 ")["behavior_pass"]


def test_report_exposes_checkpoint_progress():
    items = [{"id": "one"}, {"id": "two"}]
    report = MODULE.build_report(items, [{"id": "one", "behavior_pass": True}])

    assert report["completed"] == 1
    assert report["remaining"] == 1
    assert report["behavior_pass_rate"] == 1.0
