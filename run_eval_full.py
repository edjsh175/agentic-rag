"""
全量评估脚本：重建测试集 + 基础策略及 Rerank 对比
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_OUTPUT_DIR = Path("data/eval_runs")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="重建评测集并运行多策略检索对比")
    parser.add_argument("--output")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--questions-per-chunk", type=int, default=2)
    parser.add_argument("--max-chunks", type=int, default=100)
    parser.add_argument(
        "--methods",
        nargs="+",
        default=["mmr", "similarity", "bm25", "hybrid", "hybrid+rerank"],
    )
    return parser


def resolve_output_path(output: str | None, *, now: str | None = None) -> Path:
    if output:
        return Path(output)
    timestamp = now or datetime.now().strftime("%Y%m%d-%H%M%S")
    return DEFAULT_OUTPUT_DIR / f"eval_dataset_{timestamp}.json"


def _collect_chunk_ids(dataset: list[dict]) -> list[str]:
    chunk_ids: set[str] = set()
    for item in dataset:
        for key in ("relevant_chunk_ids", "chunk_ids"):
            for chunk_id in item.get(key) or []:
                chunk_ids.add(str(chunk_id))
    return sorted(chunk_ids)


def _write_manifest(output: Path, dataset: list[dict]) -> Path:
    from rag_knowledge.config import Config

    chunk_ids = _collect_chunk_ids(dataset)
    manifest = {
        "created_at": datetime.now().astimezone().isoformat(),
        "dataset": output.as_posix(),
        "embedding_model": Config().embedding_model,
        "collection_name": Config().collection_name,
        "chunk_count": len(chunk_ids),
        "chunk_id_sha256": hashlib.sha256(
            "\n".join(chunk_ids).encode("utf-8")
        ).hexdigest(),
    }
    manifest_path = output.parent / f"{output.name}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def _print_summary(results: list[dict], dataset_size: int) -> None:
    baseline = {
        "mmr": {"recall@3": 0.8571, "mrr": 0.7857, "ndcg@3": 0.8044, "hit_rate": 0.8571},
        "similarity": {"recall@3": 0.9286, "mrr": 0.8214, "ndcg@3": 0.8495, "hit_rate": 0.9286},
        "bm25": {"recall@3": 0.9286, "mrr": 0.8452, "ndcg@3": 0.8665, "hit_rate": 0.9286},
        "hybrid": {"recall@3": 0.9286, "mrr": 0.8810, "ndcg@3": 0.8929, "hit_rate": 0.9286},
    }

    print("\n" + "=" * 60)
    print(" 步骤 3: 结果汇总与 Baseline 对比")
    print("=" * 60)
    print("\n┌──────────────┬──────────────────────────────────┬──────────────────────────────────┬─────────────────────┐")
    print("│              │          Recall@3                │             MRR                  │       Hit Rate      │")
    print("│   策略       ├──────────┬──────────┬────────────┼──────────┬──────────┬────────────┼──────────┬──────────┤")
    print("│              │  旧      │   新     │   变化      │  旧      │   新     │   变化      │  旧      │   新     │")
    print("├──────────────┼──────────┼──────────┼────────────┼──────────┼──────────┼────────────┼──────────┼──────────┤")

    for result in results:
        method = result["method"]
        bl = baseline.get(method, {})
        old_r3 = bl.get("recall@3", 0)
        new_r3 = result.get("recall@3", 0)
        old_mrr = bl.get("mrr", 0)
        new_mrr = result.get("mrr", 0)
        old_hit = bl.get("hit_rate", 0)
        new_hit = result.get("overall_hit_rate", 0)

        def arrow(value: float) -> str:
            return f"↑{value:+.4f}" if value > 0 else (f"↓{value:+.4f}" if value < 0 else " 0.0000")

        print(
            f"│ {method:<12} │ {old_r3:.4f}  │ {new_r3:.4f}  │ {arrow(new_r3 - old_r3):>10} "
            f"│ {old_mrr:.4f}  │ {new_mrr:.4f}  │ {arrow(new_mrr - old_mrr):>10} "
            f"│ {old_hit:.4f}  │ {new_hit:.4f}  │"
        )

    print("└──────────────┴──────────┴──────────┴────────────┴──────────┴──────────┴────────────┴──────────┴──────────┘")

    print("\n┌──────────────┬──────────┬──────────┐")
    print("│   策略       │ 旧延迟    │ 新延迟    │")
    print("├──────────────┼──────────┼──────────┤")
    old_latency = {"mmr": 204, "similarity": 191, "bm25": 51, "hybrid": 218}
    for result in results:
        method = result["method"]
        old_lat = old_latency.get(method, 0)
        new_lat = result.get("avg_latency_ms", 0)
        print(f"│ {method:<12} │ {old_lat:>5}ms  │ {new_lat:>6.1f}ms │")
    print("└──────────────┴──────────┴──────────┘")

    by_method = {result["method"]: result for result in results}
    if "hybrid" in by_method and "hybrid+rerank" in by_method:
        hybrid = by_method["hybrid"]
        reranked = by_method["hybrid+rerank"]
        print("\nHybrid → Hybrid+Rerank 直接对比")
        for metric in ("recall@3", "recall@5", "mrr", "overall_hit_rate", "avg_latency_ms"):
            before = hybrid.get(metric, 0)
            after = reranked.get(metric, 0)
            print(f"  {metric:<17}: {before:.4f} → {after:.4f} ({after - before:+.4f})")

    print(f"\n>>> 评估完成！测试集大小: {dataset_size} 条")


def main(argv: list[str] | None = None) -> int:
    if str(Path.cwd()) not in sys.path:
        sys.path.insert(0, str(Path.cwd()))

    args = build_parser().parse_args(argv)
    output = resolve_output_path(args.output)
    if output.exists() and not args.overwrite:
        raise SystemExit(f"dataset already exists: {output}; pass --overwrite to replace it")
    output.parent.mkdir(parents=True, exist_ok=True)

    from rag_knowledge.evaluation.runner import EvaluationRunner
    from rag_knowledge.evaluation.test_dataset import TestDatasetBuilder

    print("\n" + "=" * 60)
    print(f" 步骤 1: 重建测试数据集 (max_chunks={args.max_chunks}, 每个 chunk 生成 {args.questions_per_chunk} 个问题)")
    print("=" * 60 + "\n")

    builder = TestDatasetBuilder(
        questions_per_chunk=args.questions_per_chunk,
        max_chunks=args.max_chunks,
    )
    dataset = builder.build(output)
    manifest_path = _write_manifest(output, dataset)
    print(f"\n>>> 测试数据集构建完成: {len(dataset)} 条问题")
    print(f">>> Manifest: {manifest_path}\n")

    print("=" * 60)
    print(f" 步骤 2: 策略对比 ({' / '.join(args.methods)})")
    print("=" * 60 + "\n")

    runner = EvaluationRunner(output)
    results = runner.run_ablation(methods=args.methods, k_values=[3, 5])
    _print_summary(results, len(dataset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
