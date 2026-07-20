# FR-10 Offline Baseline Report

- generated_at: `2026-07-20T03:41:00.092569+00:00`
- mode: `retrieval`
- gold: `docs\3_待办清单\切块基石治理\已完成-第0B轮-并行准备与预研\评测基线与黄金集\multi_chunk_qa_gold_v3.json`
- total: **120**
- pass_rate: **32.50%**
- mean_completeness: **59.34%**
- mean_evidence_recall: **66.67%**
- forbidden_rate: **0.00%**

## Notes

Baseline on current production chunks. Conflict/completeness may be low until Round 0C–0F and FR-08 prompt wiring. Retrieval mode scores concatenated retrieved content as a proxy answer (not LLM generation).

## By category

| category | total | passed | pass_rate | mean_completeness | mean_evidence_recall |
|---|---:|---:|---:|---:|---:|
| conflict | 10 | 0 | 0.00% | 31.67% | 55.00% |
| cross_section | 20 | 6 | 30.00% | 76.25% | 60.00% |
| fact | 30 | 15 | 50.00% | 83.33% | 63.33% |
| none | 10 | 0 | 0.00% | 0.00% | 40.00% |
| ocr | 10 | 0 | 0.00% | 0.00% | 60.00% |
| procedure | 30 | 11 | 36.67% | 60.95% | 86.67% |
| table | 10 | 7 | 70.00% | 95.00% | 75.00% |

## By answerability

| answerability | total | passed | pass_rate | mean_completeness |
|---|---:|---:|---:|---:|
| conflict | 10 | 0 | 0.00% | 31.67% |
| full | 88 | 39 | 44.32% | 76.75% |
| none | 10 | 0 | 0.00% | 0.00% |
| partial | 12 | 0 | 0.00% | 4.17% |

## Fail sample (first 20)

- `mq-016` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-018` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-020` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `mq-022` [fact/partial] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'partial_or_refusal_ok': True}
- `mq-031` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-035` [fact/full] completeness=1.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': True}
- `mq-041` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `mq-042` [fact/full] completeness=1.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': True}
- `mq-043` [fact/full] completeness=1.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': True}
- `mq-044` [fact/full] completeness=1.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': True}
- `mq-045` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `mq-046` [fact/full] completeness=1.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': True}
- `mq-047` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `mq-050` [fact/full] completeness=1.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': True}
- `mq-051` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-002` [procedure/full] completeness=0.29 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-003` [procedure/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `mq-013` [procedure/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-019` [procedure/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-023` [procedure/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
