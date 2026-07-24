# v5 覆盖集失败分类

- report: `E:\申浩霖实习文件夹\rag_cy\rag\docs\3_待办清单\切块基石治理\已完成-第0B轮-并行准备与预研\评测基线与黄金集\fr10_live_2537_v5_coverage_baseline\fr10_baseline_report.json`
- total: 90
- pass_rate: 0.5333333333333333

## 分桶计数

| bucket | count |
|---|---:|
| no_evidence | 4 |
| not_retrieved | 21 |
| partial_evidence | 12 |
| pass | 48 |
| rerank_drop | 5 |

## 失败题

| id | category | bucket | evidence_recall | question |
|---|---|---|---:|---|
| cv5-003 | fact | not_retrieved | None |  |
| cv5-004 | fact | not_retrieved | None |  |
| cv5-005 | table | not_retrieved | None |  |
| cv5-006 | fact | rerank_drop | None |  |
| cv5-007 | fact | no_evidence | None |  |
| cv5-008 | procedure | partial_evidence | None |  |
| cv5-009 | procedure | partial_evidence | None |  |
| cv5-010 | procedure | partial_evidence | None |  |
| cv5-011 | fact | not_retrieved | None |  |
| cv5-012 | table | rerank_drop | None |  |
| cv5-013 | fact | no_evidence | None |  |
| cv5-014 | procedure | partial_evidence | None |  |
| cv5-015 | table | rerank_drop | None |  |
| cv5-016 | procedure | partial_evidence | None |  |
| cv5-017 | table | partial_evidence | None |  |
| cv5-019 | fact | no_evidence | None |  |
| cv5-021 | table | partial_evidence | None |  |
| cv5-023 | procedure | partial_evidence | None |  |
| cv5-026 | procedure | partial_evidence | None |  |
| cv5-028 | fact | not_retrieved | None |  |
| cv5-031 | fact | not_retrieved | None |  |
| cv5-032 | fact | partial_evidence | None |  |
| cv5-033 | procedure | not_retrieved | None |  |
| cv5-036 | procedure | not_retrieved | None |  |
| cv5-038 | fact | not_retrieved | None |  |
| cv5-039 | fact | not_retrieved | None |  |
| cv5-042 | fact | not_retrieved | None |  |
| cv5-043 | fact | partial_evidence | None |  |
| cv5-044 | procedure | rerank_drop | None |  |
| cv5-047 | procedure | not_retrieved | None |  |
| cv5-048 | fact | not_retrieved | None |  |
| cv5-050 | fact | not_retrieved | None |  |
| cv5-051 | procedure | not_retrieved | None |  |
| cv5-054 | fact | not_retrieved | None |  |
| cv5-056 | fact | not_retrieved | None |  |
| cv5-057 | fact | not_retrieved | None |  |
| cv5-058 | procedure | not_retrieved | None |  |
| cv5-059 | fact | not_retrieved | None |  |
| cv5-068 | table | partial_evidence | None |  |
| cv5-070 | fact | no_evidence | None |  |
| cv5-071 | fact | rerank_drop | None |  |
| cv5-095 | procedure | not_retrieved | None |  |
