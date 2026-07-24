# 覆盖型黄金集 v5（改写 / 筛选调参）

- **状态**：`frozen_for_coverage_tuning`
- **与 v4 关系**：独立集合；**不修改** `multi_chunk_qa_gold_v4.json` / FR-10 冻结基线
- **覆盖口径**：source 文件 × `section_path` 一/二级；约 100 槽 → 自动核验后冻结 **90** 题

## 产物

| 文件 | 说明 |
|------|------|
| `v5_coverage_matrix.json` / `.md` | 覆盖矩阵 |
| `multi_chunk_qa_gold_v5_coverage_candidate.json` | 候选题 |
| `multi_chunk_qa_gold_v5_coverage.json` | 冻结集 |
| `multi_chunk_qa_gold_v5_coverage.manifest.json` | 冻结清单 |
| `multi_chunk_qa_gold_v5_coverage.review_ledger.json` | 自动核验台账 |
| `fr10_live_2537_v5_coverage_baseline/` | 首轮离线基线 + 失败分类 |

## 脚本

```powershell
.\venv\Scripts\python.exe scripts\build_v5_coverage_matrix.py
.\venv\Scripts\python.exe scripts\build_v5_coverage_gold_candidates.py
.\venv\Scripts\python.exe scripts\freeze_fr10_gold_v5_coverage.py
.\venv\Scripts\python.exe scripts\eval_multi_evidence_offline.py `
  --gold docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集/multi_chunk_qa_gold_v5_coverage.json `
  --out-dir docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集/fr10_live_2537_v5_coverage_baseline `
  --mode retrieval --production-path
.\venv\Scripts\python.exe scripts\classify_v5_coverage_failures.py
```

## 首轮基线（2026-07-24）

- pass_rate：**53.33%**（48/90）
- mean_completeness：**61.39%**
- mean_evidence_recall：**71.11%**
- 失败主因：`not_retrieved` 21、`partial_evidence` 12、`rerank_drop` 5、`no_evidence` 4

调参时优先看 `fr10_live_2537_v5_coverage_baseline/v5_failure_taxonomy.md`；不要把本分数与 v4 的 86.67% 直接比绝对值。
