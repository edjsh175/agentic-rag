"""Check evaluation dataset health against the current knowledge base."""
import argparse
import sys

from rag_knowledge.evaluation.dataset_health import check_eval_dataset_health


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluation dataset health check")
    parser.add_argument(
        "dataset",
        nargs="?",
        default="data/eval_dataset_hardcases.json",
        help="Path to the evaluation dataset JSON file",
    )
    args = parser.parse_args()

    try:
        report = check_eval_dataset_health(args.dataset)
    except Exception as exc:
        print(f"Error checking dataset health: {exc}", file=sys.stderr)
        sys.exit(2)

    print(f"dataset:        {report.dataset}")
    print(f"questions:      {report.total_questions}")
    print(f"chunk ids:      {report.existing_chunk_ids}/{report.total_chunk_ids}")
    print(f"chunk health:   {report.chunk_health:.0%}")
    print(f"target health:  {report.target_health:.0%}")
    print(f"source health:  {report.source_health:.0%}")
    print(f"section health: {report.section_health:.0%}")
    print(f"invalid:        {report.invalid_questions}")
    print(f"status:         {report.status}")
    for w in report.warnings:
        print(f"  warning: {w}")

    if report.status == "BLOCK":
        sys.exit(2)
    elif report.warnings:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
