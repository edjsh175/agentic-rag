"""
评估运行器

功能：
  - 加载测试数据集，逐条执行检索
  - 计算 Recall@K、MRR、Hit Rate 等指标
  - 支持对比不同检索策略（ablations）
  - 支持 RAGAS 端到端质量评估
"""
import json
import time
import logging
from pathlib import Path
from typing import List, Dict, Set, Optional

from rag_knowledge.config import Config
from rag_knowledge.services.rag import RagChain
from rag_knowledge.evaluation.metrics import compute_batch
from rag_knowledge.evaluation.test_dataset import load_dataset, get_dataset_stats

logger = logging.getLogger(__name__)


class EvaluationRunner:
    """评估运行器"""

    def __init__(self, dataset_path: str | Path):
        self._dataset_path = Path(dataset_path)
        self._dataset: List[dict] = []
        self._rag = RagChain()

    # ------------------------------------------------------------------
    # 数据集
    # ------------------------------------------------------------------

    def load(self) -> List[dict]:
        self._dataset = load_dataset(self._dataset_path)
        logger.info("已加载测试数据集: %d 条", len(self._dataset))
        return self._dataset

    @property
    def dataset(self) -> List[dict]:
        return self._dataset

    # ------------------------------------------------------------------
    # 执行评估
    # ------------------------------------------------------------------

    def run_retrieval_eval(
        self,
        k_values: List[int] = None,
        verbose: bool = True,
        method: str | None = None,
        rerank: bool | None = None,
    ) -> dict:
        """
        执行检索质量评估

        对数据集中每条问题：
          1. 调用 RAG 检索（跳过 LLM 生成，仅评估检索环节）
          2. 提取返回的 chunk_id 列表
          3. 与数据集中的 relevant_chunk_ids 对比

        参数：
          method: 检索方式（mmr/similarity/bm25/hybrid），None 则使用配置值
          rerank: 是否启用重排序（None=使用配置）

        返回: {mrr, recall@3, recall@5, ...}
        """
        if k_values is None:
            k_values = [3, 5, 10]
        if not self._dataset:
            self.load()

        all_retrieved: List[List[str]] = []
        all_relevant: List[Set[str]] = []
        total_time = 0.0
        hit_count = 0

        for i, item in enumerate(self._dataset):
            question = item["question"]
            relevant = set(item.get("relevant_chunk_ids", []))
            kb_name = item.get("kb_name") or None

            t0 = time.time()
            try:
                source_docs, _ = self._rag._retrieve(
                    question,
                    kb_name=kb_name,
                    doc_category=item.get("doc_category") or None,
                    review_status=None,  # 评估时不限制审核状态
                    method=method,
                    rerank=rerank,
                )
                elapsed = time.time() - t0
                total_time += elapsed

                # 提取 chunk_id
                retrieved_ids = [doc["metadata"].get("chunk_id", "") for doc in source_docs]
                retrieved_ids = [rid for rid in retrieved_ids if rid]

            except Exception as e:
                logger.warning("检索失败 [%d]: %s — %s", i, question[:40], e)
                retrieved_ids = []
                elapsed = 0

            # 检查是否命中（用于快速反馈）
            is_hit = bool(set(retrieved_ids[:max(k_values)]) & relevant) if k_values else True
            if is_hit:
                hit_count += 1

            all_retrieved.append(retrieved_ids)
            all_relevant.append(relevant)

            if verbose and (i + 1) % 20 == 0:
                logger.info(
                    "进度: %d/%d | 命中率: %d/%d (%.1f%%) | 均耗时: %.2fs",
                    i + 1, len(self._dataset),
                    hit_count, i + 1,
                    hit_count / (i + 1) * 100,
                    total_time / (i + 1),
                )

        metrics = compute_batch(all_retrieved, all_relevant, k_values)
        metrics["avg_latency_ms"] = round(total_time / len(self._dataset) * 1000, 1) if self._dataset else 0
        metrics["total_questions"] = len(self._dataset)
        metrics["overall_hit_rate"] = round(hit_count / len(self._dataset), 4) if self._dataset else 0

        return metrics

    @staticmethod
    def write_summary(metrics: dict, output_path: str | Path | None = None) -> Path:
        cfg = Config()
        path = Path(output_path) if output_path else (cfg.data_dir / "eval_summary.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        recall_at_k = {
            key.split("@", 1)[1]: value
            for key, value in metrics.items()
            if key.startswith("recall@")
        }
        payload = {
            "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sample_count": int(metrics.get("total_questions", 0) or 0),
            "hit_rate": float(metrics.get("overall_hit_rate", 0.0) or 0.0),
            "recall_at_k": recall_at_k,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def run_end_to_end_eval(
        self,
        sample_size: int = 50,
        verbose: bool = True,
    ) -> dict:
        """
        端到端评估（检索 + 生成）

        对随机抽样的 N 条问题，完整执行 RAG query，收集：
          - 检索指标（同上）
          - 生成耗时
          - 答案长度

        返回可用于 ragas 评估的数据结构
        """
        import random

        if not self._dataset:
            self.load()

        sample = random.sample(self._dataset, min(sample_size, len(self._dataset)))

        ragas_data = []
        for i, item in enumerate(sample):
            question = item["question"]
            relevant = set(item.get("relevant_chunk_ids", []))
            kb_name = item.get("kb_name") or None

            t0 = time.time()
            try:
                result = self._rag.query(
                    question,
                    kb_name=kb_name,
                    doc_category=item.get("doc_category") or None,
                )
                elapsed = time.time() - t0

                retrieved_ids = [
                    doc["metadata"].get("chunk_id", "")
                    for doc in result.get("source_documents", [])
                ]
                retrieved_ids = [rid for rid in retrieved_ids if rid]

            except Exception as e:
                logger.warning("端到端评估失败 [%d]: %s — %s", i, question[:40], e)
                result = {"answer": "", "source_documents": []}
                retrieved_ids = []
                elapsed = 0

            ragas_data.append({
                "question": question,
                "answer": result.get("answer", ""),
                "contexts": [
                    doc["content"] for doc in result.get("source_documents", [])
                ],
                "ground_truth": "",  # 人工标注阶段可补充
                "relevant_chunk_ids": list(relevant),
                "retrieved_chunk_ids": retrieved_ids,
                "latency_ms": round(elapsed * 1000, 1),
            })

            if verbose:
                hit = "✓" if (set(retrieved_ids[:5]) & relevant) else "✗"
                logger.info(
                    "[%d/%d] %s %s | 延迟: %.1fs | 答案长度: %d",
                    i + 1, len(sample), hit, question[:50],
                    elapsed, len(result.get("answer", "")),
                )

        # 汇总统计
        hits = sum(
            1 for d in ragas_data
            if set(d["retrieved_chunk_ids"][:5]) & set(d["relevant_chunk_ids"])
        )
        avg_latency = sum(d["latency_ms"] for d in ragas_data) / len(ragas_data) if ragas_data else 0

        return {
            "sample_size": len(sample),
            "hit@5": round(hits / len(sample), 4) if sample else 0,
            "avg_latency_ms": round(avg_latency, 1),
            "avg_answer_len": round(sum(len(d["answer"]) for d in ragas_data) / len(ragas_data), 1) if ragas_data else 0,
            "details": ragas_data,
        }

    # ------------------------------------------------------------------
    # 对比评估（Ablation）
    # ------------------------------------------------------------------

    def run_ablation(
        self,
        methods: List[str] = None,
        k_values: List[int] = None,
    ) -> List[dict]:
        """
        对比不同检索方式的效果

        methods: ["mmr", "similarity", "bm25", "hybrid"]
        以及质量控制的组合：hybrid+score_filter, hybrid+jaccard_dedup,
        hybrid+dynamic_topk, hybrid+quality, hybrid+rerank+quality。
        """
        if methods is None:
            methods = [
                "mmr", "similarity", "bm25", "hybrid", "hybrid+rerank",
                "hybrid+jaccard_dedup", "hybrid+dynamic_topk",
                "hybrid+quality",
            ]
        if k_values is None:
            k_values = [3, 5, 10]

        cfg = Config()
        results = []

        # ---- 保存质量控制原始配置，用于恢复 ----
        orig_qc = cfg.retrieval_quality
        saved_values = {
            key: getattr(orig_qc, key)
            for key in [
                "enabled", "score_threshold_enabled", "jaccard_dedup_enabled",
                "dynamic_topk_enabled",
            ]
        }

        # ---- 质量控制方法映射 ----
        quality_configs = {
            "hybrid+score_filter": {
                "enabled": True,
                "score_threshold_enabled": True,
                "jaccard_dedup_enabled": False,
                "dynamic_topk_enabled": False,
            },
            "hybrid+jaccard_dedup": {
                "enabled": True,
                "score_threshold_enabled": False,
                "jaccard_dedup_enabled": True,
                "dynamic_topk_enabled": False,
            },
            "hybrid+dynamic_topk": {
                "enabled": True,
                "score_threshold_enabled": False,
                "jaccard_dedup_enabled": False,
                "dynamic_topk_enabled": True,
            },
            "hybrid+quality": {
                "enabled": True,
                "score_threshold_enabled": False,
                "jaccard_dedup_enabled": True,
                "dynamic_topk_enabled": True,
            },
            "hybrid+rerank+quality": {
                "enabled": True,
                "score_threshold_enabled": False,
                "jaccard_dedup_enabled": True,
                "dynamic_topk_enabled": True,
            },
        }

        supported = {
            "mmr", "similarity", "bm25", "hybrid", "hybrid+rerank",
        } | set(quality_configs.keys())

        for method in methods:
            logger.info("===== 评估检索方式: %s =====", method)
            if method not in supported:
                logger.warning("跳过不支持的方法: %s（尚未实现）", method)
                continue

            # ---- 恢复默认 ----
            for key, val in saved_values.items():
                setattr(cfg.retrieval_quality, key, val)

            # Baseline methods must not inherit quality controls from the
            # application config; explicit +quality variants enable them below.
            cfg.retrieval_quality.enabled = False

            # ---- 解析方法 ----
            enable_rerank = False
            actual_method = method

            if method == "hybrid+rerank":
                actual_method = "hybrid"
                enable_rerank = True

            # ---- 质量控制配置 ----
            if method in quality_configs:
                actual_method = "hybrid"
                qc = quality_configs[method]
                for key, val in qc.items():
                    setattr(cfg.retrieval_quality, key, val)
                # 如果方法名包含 rerank
                if "rerank" in method:
                    enable_rerank = True

            metrics = self.run_retrieval_eval(
                k_values=k_values, verbose=True,
                method=actual_method, rerank=enable_rerank,
            )
            metrics["method"] = method
            results.append(metrics)

        # ---- 恢复原始配置 ----
        for key, val in saved_values.items():
            setattr(cfg.retrieval_quality, key, val)

        if len(results) > 1:
            logger.info("\n========== 对比结果 ==========")
            header = f"{'方法':<24}" + "".join(f"{k:>10}" for k in k_values) + f"{'MRR':>10}{'Hit':>10}"
            logger.info(header)
            for r in results:
                def _fmt(v):
                    return f"{v:.4f}" if isinstance(v, float) else str(v)
                row = f"{r['method']:<24}" + "".join(
                    _fmt(r.get(f"recall@{k}", "N/A")).rjust(10) for k in k_values
                ) + _fmt(r.get("mrr", "N/A")).rjust(10) + _fmt(r.get("overall_hit_rate", "N/A")).rjust(10)
                logger.info(row)

        return results


# ------------------------------------------------------------------
# 便捷入口
# ------------------------------------------------------------------

def build_and_eval(
    dataset_path: str | Path = "./data/eval_dataset.json",
    build_dataset: bool = True,
    max_chunks: int = 100,
) -> dict:
    """
    一键：构建数据集 → 运行评估

    返回评估指标 dict
    """
    from rag_knowledge.evaluation.test_dataset import TestDatasetBuilder

    if build_dataset:
        logger.info("===== 步骤 1: 构建测试数据集 =====")
        builder = TestDatasetBuilder(questions_per_chunk=2, max_chunks=max_chunks)
        builder.build(dataset_path)

    logger.info("===== 步骤 2: 运行检索评估 =====")
    runner = EvaluationRunner(dataset_path)
    metrics = runner.run_retrieval_eval()

    logger.info("===== 评估结果 =====")
    for key, val in sorted(metrics.items()):
        if isinstance(val, float):
            logger.info("  %s = %.4f", key, val)
        else:
            logger.info("  %s = %s", key, val)

    EvaluationRunner.write_summary(metrics)

    return metrics
