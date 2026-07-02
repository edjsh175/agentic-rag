"""Run resumable retrieval A/B evaluations on fixed datasets."""
import argparse
import json
from datetime import datetime
from pathlib import Path

from rag_knowledge.evaluation.runner import EvaluationRunner


DEFAULT_METHODS = [
    "hybrid",
    "hybrid+rerank",
    "hybrid+quality",
    "hybrid+rerank+quality",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset")
    parser.add_argument("--methods", nargs="+", default=DEFAULT_METHODS)
    parser.add_argument("--output", default="data/retrieval_ab_results.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output = Path(args.output)
    if output.exists():
        report = json.loads(output.read_text(encoding="utf-8"))
    else:
        report = {"results": {}}

    dataset_key = str(Path(args.dataset).as_posix())
    dataset_results = report["results"].setdefault(dataset_key, {})
    runner = EvaluationRunner(args.dataset)

    for method in args.methods:
        if method in dataset_results and not args.force:
            print(f"SKIP {dataset_key} {method} (already complete)", flush=True)
            continue
        print(f"RUN {dataset_key} {method}", flush=True)
        result = runner.run_ablation(methods=[method], k_values=[3, 5])[0]
        dataset_results[method] = result
        report["updated_at"] = datetime.now().astimezone().isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
