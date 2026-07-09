"""Run resumable retrieval A/B evaluations on fixed datasets."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from rag_knowledge.evaluation.runner import EvaluationRunner, DatasetStaleError


DEFAULT_METHODS = [
    "hybrid",
    "hybrid+rerank",
    "hybrid+quality",
    "hybrid+rerank+quality",
]


def detect_regressions(
    previous: dict | None,
    current: dict,
    metrics: tuple[str, ...] = ("recall@3", "mrr", "overall_hit_rate"),
    threshold: float = 0.0,
) -> list[str]:
    if not previous:
        return []
    regressions = []
    for metric in metrics:
        old = previous.get(metric)
        new = current.get(metric)
        if old is None or new is None:
            continue
        delta = round(float(old) - float(new), 4)
        if delta > threshold:
            msg = f"{metric} dropped from {float(old):.4f} to {float(new):.4f}"
            if threshold > 0.0:
                msg += f" (delta: {delta:.4f} > threshold: {threshold:.4f})"
            regressions.append(msg)
    return regressions


def build_regression_message(
    dataset_key: str,
    method: str,
    regressions: list[str],
    previous: dict | None,
    current: dict,
) -> str:
    message = f"Regression detected for {dataset_key} {method}: " + "; ".join(regressions)
    previous_hit = float((previous or {}).get("overall_hit_rate", 0.0) or 0.0)
    current_hit = float((current or {}).get("overall_hit_rate", 0.0) or 0.0)
    if previous_hit > 0 and current_hit == 0:
        message += (
            ". The dataset may be stale relative to current chunk IDs; "
            "regenerate eval_dataset_hardcases.json before trusting this comparison."
        )
    return message


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset")
    parser.add_argument("--methods", nargs="+", default=DEFAULT_METHODS)
    parser.add_argument("--output", default="data/retrieval_ab_results.json")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--regression-threshold", type=float, default=0.01, help="Regression tolerance threshold")
    args = parser.parse_args()

    output = Path(args.output)
    if output.exists():
        report = json.loads(output.read_text(encoding="utf-8"))
    else:
        report = {"results": {}}

    dataset_key = str(Path(args.dataset).as_posix())
    dataset_results = report["results"].setdefault(dataset_key, {})
    runner = EvaluationRunner(args.dataset)
    runner.load()  # triggers dataset health check; BLOCK -> DatasetStaleError

    for method in args.methods:
        if method in dataset_results and not args.force:
            print(f"SKIP {dataset_key} {method} (already complete)", flush=True)
            continue
        print(f"RUN {dataset_key} {method}", flush=True)
        previous_result = dataset_results.get(method)
        result = runner.run_ablation(methods=[method], k_values=[3, 5])[0]
        regressions = detect_regressions(previous_result, result, threshold=args.regression_threshold)
        if regressions and args.fail_on_regression:
            raise SystemExit(
                build_regression_message(
                    dataset_key, method, regressions, previous_result, result
                )
            )
        dataset_results[method] = result
        report["updated_at"] = datetime.now().astimezone().isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except DatasetStaleError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        sys.exit(2)
