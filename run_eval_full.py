"""
全量评估脚本：重建测试集 + 基础策略及 Rerank 对比
"""
import sys
sys.path.insert(0, ".")

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from rag_knowledge.evaluation.test_dataset import TestDatasetBuilder
from rag_knowledge.evaluation.runner import EvaluationRunner

# ============================================================
# 步骤 1: 重建测试数据集
# ============================================================
print("\n" + "=" * 60)
print(" 步骤 1: 重建测试数据集 (max_chunks=100, 每个 chunk 生成 2 个问题)")
print("=" * 60 + "\n")

builder = TestDatasetBuilder(questions_per_chunk=2, max_chunks=100)
dataset = builder.build("./data/eval_dataset.json")
print(f"\n>>> 测试数据集构建完成: {len(dataset)} 条问题\n")

# ============================================================
# 步骤 2: 基础策略 + Rerank 全量对比
# ============================================================
print("=" * 60)
print(" 步骤 2: 五策略全量对比 (mmr / similarity / bm25 / hybrid / hybrid+rerank)")
print("=" * 60 + "\n")

runner = EvaluationRunner("./data/eval_dataset.json")
results = runner.run_ablation(
    methods=["mmr", "similarity", "bm25", "hybrid", "hybrid+rerank"],
    k_values=[3, 5],
)

# ============================================================
# 步骤 3: 输出对比表（与 baseline 对比）
# ============================================================
print("\n" + "=" * 60)
print(" 步骤 3: 结果汇总与 Baseline 对比")
print("=" * 60)

# 之前的 Baseline（14 题）
baseline = {
    "mmr":       {"recall@3": 0.8571, "mrr": 0.7857, "ndcg@3": 0.8044, "hit_rate": 0.8571},
    "similarity":{"recall@3": 0.9286, "mrr": 0.8214, "ndcg@3": 0.8495, "hit_rate": 0.9286},
    "bm25":      {"recall@3": 0.9286, "mrr": 0.8452, "ndcg@3": 0.8665, "hit_rate": 0.9286},
    "hybrid":    {"recall@3": 0.9286, "mrr": 0.8810, "ndcg@3": 0.8929, "hit_rate": 0.9286},
}

print("\n┌──────────────┬──────────────────────────────────┬──────────────────────────────────┬─────────────────────┐")
print("│              │          Recall@3                │             MRR                  │       Hit Rate      │")
print("│   策略       ├──────────┬──────────┬────────────┼──────────┬──────────┬────────────┼──────────┬──────────┤")
print("│              │  旧      │   新     │   变化      │  旧      │   新     │   变化      │  旧      │   新     │")
print("├──────────────┼──────────┼──────────┼────────────┼──────────┼──────────┼────────────┼──────────┼──────────┤")

for r in results:
    method = r["method"]
    bl = baseline.get(method, {})
    old_r3 = bl.get("recall@3", 0)
    new_r3 = r.get("recall@3", 0)
    old_mrr = bl.get("mrr", 0)
    new_mrr = r.get("mrr", 0)
    old_hit = bl.get("hit_rate", 0)
    new_hit = r.get("overall_hit_rate", 0)

    r3_diff = new_r3 - old_r3
    mrr_diff = new_mrr - old_mrr
    hit_diff = new_hit - old_hit

    def arrow(v):
        return f"↑{v:+.4f}" if v > 0 else (f"↓{v:+.4f}" if v < 0 else " 0.0000")

    print(f"│ {method:<12} │ {old_r3:.4f}  │ {new_r3:.4f}  │ {arrow(r3_diff):>10} │ {old_mrr:.4f}  │ {new_mrr:.4f}  │ {arrow(mrr_diff):>10} │ {old_hit:.4f}  │ {new_hit:.4f}  │")

print("└──────────────┴──────────┴──────────┴────────────┴──────────┴──────────┴────────────┴──────────┴──────────┘")

# 延迟对比
print("\n┌──────────────┬──────────┬──────────┐")
print("│   策略       │ 旧延迟    │ 新延迟    │")
print("├──────────────┼──────────┼──────────┤")
old_latency = {"mmr": 204, "similarity": 191, "bm25": 51, "hybrid": 218}
for r in results:
    method = r["method"]
    old_lat = old_latency.get(method, 0)
    new_lat = r.get("avg_latency_ms", 0)
    print(f"│ {method:<12} │ {old_lat:>5}ms  │ {new_lat:>6.1f}ms │")
print("└──────────────┴──────────┴──────────┘")

by_method = {r["method"]: r for r in results}
if "hybrid" in by_method and "hybrid+rerank" in by_method:
    hybrid = by_method["hybrid"]
    reranked = by_method["hybrid+rerank"]
    print("\nHybrid → Hybrid+Rerank 直接对比")
    for metric in ("recall@3", "recall@5", "mrr", "overall_hit_rate", "avg_latency_ms"):
        before = hybrid.get(metric, 0)
        after = reranked.get(metric, 0)
        print(f"  {metric:<17}: {before:.4f} → {after:.4f} ({after - before:+.4f})")

print(f"\n>>> 评估完成！测试集大小: {len(dataset)} 条")
