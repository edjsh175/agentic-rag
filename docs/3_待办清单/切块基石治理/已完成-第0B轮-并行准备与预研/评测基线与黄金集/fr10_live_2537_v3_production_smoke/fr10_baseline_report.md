# FR-10 Offline Baseline Report

- generated_at: `2026-07-20T05:47:03.114086+00:00`
- mode: `retrieval`
- gold: `docs\3_待办清单\切块基石治理\已完成-第0B轮-并行准备与预研\评测基线与黄金集\multi_chunk_qa_gold_v3.json`
- total: **1**
- pass_rate: **100.00%**
- mean_completeness: **100.00%**
- mean_evidence_recall: **100.00%**
- forbidden_rate: **0.00%**

## Notes

Baseline on current production chunks. Conflict/completeness may be low until Round 0C–0F and FR-08 prompt wiring. Retrieval mode scores concatenated retrieved content as a proxy answer (not LLM generation). Retrieval candidates use the production query planner and graph plan.

## By category

| category | total | passed | pass_rate | mean_completeness | mean_evidence_recall |
|---|---:|---:|---:|---:|---:|
| fact | 1 | 1 | 100.00% | 100.00% | 100.00% |

## By answerability

| answerability | total | passed | pass_rate | mean_completeness |
|---|---:|---:|---:|---:|
| full | 1 | 1 | 100.00% | 100.00% |

## Fail sample (first 20)

_none_
