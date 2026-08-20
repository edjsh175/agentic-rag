"""Offline activation evaluation for the optional claim-level semantic verifier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_knowledge.config import Config
from rag_knowledge.llm_http import chat_role
from rag_knowledge.services.evidence_pack import (
    DETERMINISTIC_GROUNDING_POLICY_VERSION,
    verify_grounding,
)
from rag_knowledge.services.semantic_entailment import (
    SemanticEntailmentVerifier,
    semantic_verifier_policy_fingerprint,
)


_ALLOWED_EXPECTED = {"entailed", "contradicted", "unsupported"}


def _docs(item: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = item.get("evidence") or []
    if isinstance(evidence, str):
        evidence = [evidence]
    return [
        {
            "content": str(content),
            "metadata": {"citation_id": index + 1, "source": f"gold:{item['id']}:{index + 1}"},
        }
        for index, content in enumerate(evidence)
    ]


def _answer(item: dict[str, Any]) -> str:
    evidence = item.get("evidence") or []
    if isinstance(evidence, str):
        evidence = [evidence]
    cites = "".join(f"[{index + 1}]" for index in range(len(evidence)))
    return f"{str(item.get('claim') or '').strip()}。{cites}"


def _predicted_label(verdict) -> str:
    semantic = (verdict.details or {}).get("semantic_verifier") or {}
    rows = semantic.get("results") or []
    if not rows:
        raise ValueError("semantic verifier returned no per-claim result")
    return str(rows[0].get("label") or "").strip().lower()


def evaluate_items(
    items: list[dict[str, Any]],
    verifier: SemanticEntailmentVerifier,
) -> list[dict[str, Any]]:
    """Evaluate the real two-stage publication gate.

    The semantic verifier is invoked only when the deterministic grounding gate
    would publish the claim. This isolates the residual risk surface that the
    semantic layer is actually responsible for in production.
    """
    results: list[dict[str, Any]] = []
    for item in items:
        expected = str(item.get("expected") or "").strip().lower()
        if expected not in _ALLOWED_EXPECTED:
            raise ValueError(f"invalid expected label for {item.get('id')}: {expected!r}")

        answer = _answer(item)
        docs = _docs(item)
        deterministic = verify_grounding(answer, docs)
        deterministic_pass = bool(deterministic.ok)
        semantic_evaluated = deterministic_pass
        predicted = "deterministic_reject"
        error = None

        if deterministic_pass:
            try:
                verdict = verifier.verify(answer, docs)
                predicted = _predicted_label(verdict)
            except Exception as exc:  # protocol/provider failures are explicit fail-closed outcomes
                predicted = "invalid"
                error = f"{type(exc).__name__}: {exc}"

        accepted = deterministic_pass and predicted == "entailed"
        false_accept = expected != "entailed" and accepted
        false_reject = expected == "entailed" and not accepted
        label_correct = semantic_evaluated and predicted == expected
        end_to_end_correct = accepted if expected == "entailed" else not accepted

        results.append({
            "id": item.get("id"),
            "category": item.get("category"),
            "expected": expected,
            "deterministic_pass": deterministic_pass,
            "deterministic_reasons": list(deterministic.reasons),
            "semantic_evaluated": semantic_evaluated,
            "predicted": predicted,
            "label_correct": label_correct,
            "end_to_end_correct": end_to_end_correct,
            "false_accept": false_accept,
            "false_reject": false_reject,
            "invalid": semantic_evaluated and predicted == "invalid",
            "error": error,
        })
    return results


def build_report(
    items: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    model_role: str,
    model: str,
    min_accuracy: float,
    max_false_accept_rate: float,
    max_invalid_rate: float,
    min_residual_cases: int,
    provider: str = "",
) -> dict[str, Any]:
    total = len(results)
    residual = [item for item in results if item["semantic_evaluated"]]
    residual_total = len(residual)

    end_to_end_correct = sum(bool(item["end_to_end_correct"]) for item in results)
    deterministic_rejects = total - residual_total
    deterministic_false_rejects = sum(
        item["expected"] == "entailed" and not item["deterministic_pass"]
        for item in results
    )

    residual_correct = sum(bool(item["label_correct"]) for item in residual)
    false_accepts = sum(bool(item["false_accept"]) for item in residual)
    false_rejects = sum(bool(item["false_reject"]) for item in residual)
    invalid = sum(bool(item["invalid"]) for item in residual)
    residual_non_entailed = sum(item["expected"] != "entailed" for item in residual)
    residual_entailed = sum(item["expected"] == "entailed" for item in residual)

    residual_accuracy = residual_correct / residual_total if residual_total else 0.0
    false_accept_rate = false_accepts / residual_non_entailed if residual_non_entailed else 0.0
    false_reject_rate = false_rejects / residual_entailed if residual_entailed else 0.0
    invalid_rate = invalid / residual_total if residual_total else 0.0
    end_to_end_accuracy = end_to_end_correct / total if total else 0.0

    activation_ready = bool(
        total == len(items)
        and residual_total >= min_residual_cases
        and residual_accuracy >= min_accuracy
        and false_accept_rate <= max_false_accept_rate
        and invalid_rate <= max_invalid_rate
    )
    return {
        "scope": "semantic_grounding_verifier",
        "model_role": model_role,
        "provider": (provider or "").strip().lower(),
        "model": model,
        "semantic_policy_fingerprint": semantic_verifier_policy_fingerprint(),
        "deterministic_policy_version": DETERMINISTIC_GROUNDING_POLICY_VERSION,
        "total": len(items),
        "completed": total,
        "pipeline_metrics": {
            "end_to_end_accuracy": end_to_end_accuracy,
            "deterministic_reject_count": deterministic_rejects,
            "deterministic_false_reject_count": deterministic_false_rejects,
        },
        "residual_metrics": {
            "case_count": residual_total,
            "accuracy": residual_accuracy,
            "false_accept_count": false_accepts,
            "false_accept_rate": false_accept_rate,
            "false_reject_count": false_rejects,
            "false_reject_rate": false_reject_rate,
            "invalid_count": invalid,
            "invalid_rate": invalid_rate,
        },
        "activation_gate": {
            "min_residual_cases": min_residual_cases,
            "min_accuracy": min_accuracy,
            "max_false_accept_rate": max_false_accept_rate,
            "max_invalid_rate": max_invalid_rate,
            "ready": activation_ready,
        },
        "results": results,
        "interpretation": (
            "Activation is based only on residual cases that already pass deterministic grounding, because only those "
            "reach the semantic verifier in production. Residual False Accept is the critical metric and must remain zero."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True, action="append")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--role", default="semantic_verifier")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-claims", type=int, default=1)
    parser.add_argument("--max-evidence-chars", type=int, default=1800)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--min-residual-cases", "--min-cases", dest="min_residual_cases", type=int, default=20)
    parser.add_argument("--min-accuracy", type=float, default=0.95)
    parser.add_argument("--max-false-accept-rate", type=float, default=0.0)
    parser.add_argument("--max-invalid-rate", type=float, default=0.02)
    parser.add_argument("--strict-exit", action="store_true")
    args = parser.parse_args()

    cfg = Config()
    endpoint = cfg.endpoint_for(args.role)
    items: list[dict[str, Any]] = []
    for gold_path in args.gold:
        loaded = json.loads(gold_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ValueError(f"gold file must contain a JSON array: {gold_path}")
        items.extend(loaded)
    if args.limit is not None:
        items = items[: max(0, args.limit)]

    def caller(prompt: str):
        return chat_role(
            cfg,
            args.role,
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            format_json=True,
            num_predict=512,
            timeout=float(args.timeout),
            think=False,
        )

    verifier = SemanticEntailmentVerifier(
        caller,
        max_claims=max(1, int(args.max_claims)),
        max_evidence_chars=max(200, int(args.max_evidence_chars)),
    )
    results = evaluate_items(items, verifier)
    report = build_report(
        items,
        results,
        model_role=args.role,
        model=endpoint.model,
        provider=endpoint.normalized_provider(),
        min_accuracy=float(args.min_accuracy),
        max_false_accept_rate=float(args.max_false_accept_rate),
        max_invalid_rate=float(args.max_invalid_rate),
        min_residual_cases=max(1, int(args.min_residual_cases)),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metrics = report["residual_metrics"]
    print(
        "semantic verifier residual eval | "
        f"cases={metrics['case_count']} "
        f"accuracy={metrics['accuracy']:.1%} "
        f"false_accept={metrics['false_accept_rate']:.1%} "
        f"false_reject={metrics['false_reject_rate']:.1%} "
        f"invalid={metrics['invalid_rate']:.1%} "
        f"ready={report['activation_gate']['ready']}",
        flush=True,
    )
    if args.strict_exit and not report["activation_gate"]["ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
