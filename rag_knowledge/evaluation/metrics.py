"""
检索质量指标计算

指标：
  - Recall@K      — 前 K 个结果中找回了多少相关文档
  - Precision@K    — 前 K 个结果中有多少是相关的
  - MRR            — 第一个相关文档排名的倒数均值
  - Hit Rate       — 是否至少命中一个相关文档
  - NDCG@K         — 归一化折损累计增益
"""
import math
from typing import List, Set, Dict


def recall_at_k(retrieved_ids: List[str], relevant_ids: Set[str], k: int) -> float:
    """前 K 个结果中相关文档的占比（相对于全部相关文档）"""
    if not relevant_ids:
        return 1.0
    top_k = set(retrieved_ids[:k])
    return len(top_k & relevant_ids) / len(relevant_ids)


def precision_at_k(retrieved_ids: List[str], relevant_ids: Set[str], k: int) -> float:
    """前 K 个结果中相关文档的占比（相对于 K）"""
    if k <= 0:
        return 0.0
    top_k = set(retrieved_ids[:k])
    return len(top_k & relevant_ids) / k


def mrr(retrieved_ids: List[str], relevant_ids: Set[str]) -> float:
    """Mean Reciprocal Rank：第一个相关文档排名的倒数"""
    for i, rid in enumerate(retrieved_ids, start=1):
        if rid in relevant_ids:
            return 1.0 / i
    return 0.0


def hit_rate(retrieved_ids: List[str], relevant_ids: Set[str], k: int) -> float:
    """前 K 个结果中是否至少命中一个相关文档（返回 0 或 1）"""
    top_k = set(retrieved_ids[:k])
    return 1.0 if (top_k & relevant_ids) else 0.0


def dcg_at_k(relevance_scores: List[float], k: int) -> float:
    """Discounted Cumulative Gain"""
    dcg = 0.0
    for i, rel in enumerate(relevance_scores[:k], start=1):
        dcg += (2 ** rel - 1) / math.log2(i + 1)
    return dcg


def ndcg_at_k(
    retrieved_ids: List[str],
    relevant_scores: Dict[str, float],
    k: int,
) -> float:
    """
    Normalized DCG@K

    relevant_scores: {chunk_id: relevance_score}，分数越高越相关（通常 0-3）
    """
    if not relevant_scores:
        return 1.0
    # 当前排序的 relevance 序列
    retrieved_scores = [relevant_scores.get(rid, 0.0) for rid in retrieved_ids[:k]]
    dcg = dcg_at_k(retrieved_scores, k)
    # 理想排序（所有相关文档按分数降序排在最前面）
    ideal_scores = sorted(relevant_scores.values(), reverse=True)[:k]
    idcg = dcg_at_k(ideal_scores, k)
    return dcg / idcg if idcg > 0 else 0.0


def compute_all(
    retrieved_ids: List[str],
    relevant_ids: Set[str],
    ks: List[int] = None,
) -> dict:
    """一次计算所有指标，返回字典"""
    if ks is None:
        ks = [3, 5, 10]
    result = {"mrr": mrr(retrieved_ids, relevant_ids)}
    relevant_scores = {rid: 1.0 for rid in relevant_ids}
    for k in ks:
        result[f"recall@{k}"] = recall_at_k(retrieved_ids, relevant_ids, k)
        result[f"precision@{k}"] = precision_at_k(retrieved_ids, relevant_ids, k)
        result[f"hit@{k}"] = hit_rate(retrieved_ids, relevant_ids, k)
        result[f"ndcg@{k}"] = ndcg_at_k(retrieved_ids, relevant_scores, k)
    return result


def compute_batch(
    all_retrieved: List[List[str]],
    all_relevant: List[Set[str]],
    ks: List[int] = None,
) -> dict:
    """
    批量计算平均指标

    all_retrieved: 每条查询返回的 chunk_id 列表
    all_relevant:  每条查询对应的相关 chunk_id 集合
    """
    if ks is None:
        ks = [3, 5, 10]
    n = len(all_retrieved)
    if n == 0:
        return {}
    totals = {}
    for retrieved_ids, relevant_ids in zip(all_retrieved, all_relevant):
        item = compute_all(retrieved_ids, relevant_ids, ks)
        for key, val in item.items():
            totals[key] = totals.get(key, 0.0) + val
    return {key: val / n for key, val in totals.items()}


def content_match(
    retrieved_metadata: dict,
    retrieved_content: str,
    expected_target: dict,
) -> bool:
    """chunk_id 不匹配时的降级判断。

    匹配优先级：
    1. content_fingerprint 精确匹配
    2. source + section_path 双匹配
    3. keywords 子集命中率 >= 60%
    """
    import hashlib

    # 1. fingerprint
    fp = expected_target.get("content_fingerprint", "")
    if fp and retrieved_content:
        actual_fp = hashlib.sha256(retrieved_content.encode("utf-8")).hexdigest()[:16]
        if actual_fp == fp:
            return True

    # 2. source + section_path
    exp_source = (expected_target.get("source") or "").replace("\\", "/")
    exp_section = expected_target.get("section_path") or ""
    if exp_source and exp_section:
        actual_source = (retrieved_metadata.get("source") or "").replace("\\", "/")
        actual_section = (
            retrieved_metadata.get("section_path")
            or retrieved_metadata.get("section_title")
            or ""
        )
        source_ok = actual_source == exp_source or actual_source.endswith(f"/{exp_source}")
        section_ok = actual_section == exp_section or exp_section in actual_section
        if source_ok and section_ok:
            keywords = expected_target.get("keywords") or []
            if keywords and retrieved_content:
                content_lower = retrieved_content.casefold()
                hits = sum(1 for kw in keywords if kw.casefold() in content_lower)
                return len(keywords) > 0 and (hits / len(keywords) >= 0.6)
            return True

    # 3. keywords
    keywords = expected_target.get("keywords") or []
    if keywords and retrieved_content:
        content_lower = retrieved_content.casefold()
        hits = sum(1 for kw in keywords if kw.casefold() in content_lower)
        if len(keywords) > 0 and hits / len(keywords) >= 0.6:
            return True

    return False


def is_match_v2(
    retrieved_id: str,
    retrieved_metadata: dict,
    retrieved_content: str,
    relevant_ids: set[str],
    expected_targets: list[dict],
) -> bool:
    """chunk_id 优先匹配，失败则尝试 content_match 降级。"""
    if retrieved_id in relevant_ids:
        return True
    return any(
        content_match(retrieved_metadata, retrieved_content, target)
        for target in expected_targets
    )

