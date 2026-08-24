"""Evaluate the real Main Controller's first-action clarification decision.

This acceptance runner calls ``AgentLoop._decide_via_llm`` with the production
controller prompt, registry and configured Main model. It neither mocks the
decision nor executes retrieval tools.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_GOLD = ROOT / "tests" / "fixtures" / "main_clarification_gold_v1.json"
MAX_MISSED_CLARIFICATION_RATE = 0.05
MAX_FALSE_CLARIFICATION_RATE = 0.03


def load_gold(path: Path) -> tuple[str, list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("gold dataset must be an object containing a cases list")

    cases = payload["cases"]
    ids: set[str] = set()
    expected_values: set[bool] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"case {index} must be an object")
        case_id = str(case.get("id") or "").strip()
        question = str(case.get("question") or "").strip()
        expected = case.get("expected_clarification")
        if not case_id or not question or not isinstance(expected, bool):
            raise ValueError(f"case {index} requires id, question and boolean expectation")
        if case_id in ids:
            raise ValueError(f"duplicate gold case id: {case_id}")
        ids.add(case_id)
        expected_values.add(expected)

    if len(cases) < 10 or expected_values != {False, True}:
        raise ValueError("gold dataset must contain at least 10 cases across both classes")
    return str(payload.get("version") or path.stem), cases


def is_clarification_decision(decision: Any) -> bool:
    return (
        str(getattr(decision, "action", "") or "").strip().casefold() == "tool_call"
        and str(getattr(decision, "tool", "") or "").strip().casefold() == "clarify"
    )


def calculate_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    should_clarify = [row for row in results if row["expected_clarification"]]
    should_not_clarify = [row for row in results if not row["expected_clarification"]]
    missed = [row for row in should_clarify if row.get("predicted_clarification") is not True]
    false_clarifications = [
        row for row in should_not_clarify if row.get("predicted_clarification") is True
    ]
    errors = [row for row in results if row.get("error")]
    missed_rate = len(missed) / len(should_clarify) if should_clarify else 0.0
    false_rate = (
        len(false_clarifications) / len(should_not_clarify)
        if should_not_clarify
        else 0.0
    )
    passed = (
        not errors
        and missed_rate <= MAX_MISSED_CLARIFICATION_RATE
        and false_rate <= MAX_FALSE_CLARIFICATION_RATE
    )
    return {
        "total": len(results),
        "should_clarify": len(should_clarify),
        "should_not_clarify": len(should_not_clarify),
        "missed_clarification_count": len(missed),
        "missed_clarification_rate": missed_rate,
        "false_clarification_count": len(false_clarifications),
        "false_clarification_rate": false_rate,
        "error_count": len(errors),
        "passed": passed,
    }


def evaluate_cases(
    cases: list[dict[str, Any]],
    decide_first_action: Callable[[str], Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        row = {
            "id": case["id"],
            "question": case["question"],
            "category": case.get("category"),
            "expected_clarification": case["expected_clarification"],
        }
        try:
            decision = decide_first_action(case["question"])
            predicted = is_clarification_decision(decision)
            row.update({
                "predicted_clarification": predicted,
                "passed": predicted == case["expected_clarification"],
                "decision": (
                    decision.to_dict()
                    if callable(getattr(decision, "to_dict", None))
                    else {
                        "action": getattr(decision, "action", None),
                        "tool": getattr(decision, "tool", None),
                    }
                ),
                "error": None,
            })
        except Exception as exc:  # noqa: BLE001 - per-case failures belong in the report
            row.update({
                "predicted_clarification": None,
                "passed": False,
                "decision": None,
                "error": f"{type(exc).__name__}: {exc}",
            })
        row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        results.append(row)
    return results, calculate_metrics(results)


def run_real_main(config_path: Path, gold_path: Path) -> dict[str, Any]:
    os.environ["RAG_CONFIG"] = str(config_path.resolve())

    from rag_knowledge.config import Config
    from rag_knowledge.services.agent_orchestration.models import (
        AgentBudget,
        ConversationContext,
        EvidencePool,
    )
    from rag_knowledge.services.agent_orchestration.runtime import (
        AgentLoop,
        build_agent_registry,
    )
    from rag_knowledge.services.dialogue_understanding import DialogueUnderstanding
    from rag_knowledge.services.model_routing import ModelRoutePolicy

    cfg = Config()
    role = ModelRoutePolicy(cfg).agent_controller_role()
    endpoint = getattr(cfg, f"{role}_endpoint")
    registry = build_agent_registry()
    understanding_service = DialogueUnderstanding(cfg)

    def decide_first_action(question: str):
        understanding = understanding_service.analyze(
            question,
            history=[],
            run_clarify=False,
        )
        conversation = ConversationContext.from_request(
            question,
            [],
            understanding=understanding,
        )
        loop = AgentLoop(
            conversation=conversation,
            evidence=EvidencePool(question_id="main-clarification-gold"),
            budget=AgentBudget(max_steps=1),
            registry=registry,
            handlers={},
            cfg=cfg,
        )
        return loop._decide_via_llm()

    version, cases = load_gold(gold_path)
    started = time.perf_counter()
    results, metrics = evaluate_cases(cases, decide_first_action)
    return {
        "evaluation": "main_controller_first_action_clarification",
        "decision_source": "real_main_controller",
        "gold_version": version,
        "gold_path": str(gold_path.resolve()),
        "config_path": str(config_path.resolve()),
        "controller": {
            "role": role,
            "provider": endpoint.provider,
            "model": endpoint.model,
            "base_url": endpoint.base_url or cfg.ollama_base_url,
        },
        "thresholds": {
            "max_missed_clarification_rate": MAX_MISSED_CLARIFICATION_RATE,
            "max_false_clarification_rate": MAX_FALSE_CLARIFICATION_RATE,
            "max_errors": 0,
        },
        "metrics": metrics,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "cases": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the real configured Main Controller against the clarification Gold Set",
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config-local.ini")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_real_main(args.config, args.gold)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["metrics"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
