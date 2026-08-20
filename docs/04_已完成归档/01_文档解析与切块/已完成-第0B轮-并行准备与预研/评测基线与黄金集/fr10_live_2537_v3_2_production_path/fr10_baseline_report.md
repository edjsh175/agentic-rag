# FR-10 Offline Baseline Report

- generated_at: `2026-07-20T06:41:26.620899+00:00`
- mode: `retrieval`
- gold: `docs\3_待办清单\切块基石治理\已完成-第0B轮-并行准备与预研\评测基线与黄金集\multi_chunk_qa_gold_v3_2.json`
- total: **120**
- pass_rate: **39.17%**
- mean_completeness: **63.92%**
- mean_evidence_recall: **66.94%**
- forbidden_rate: **0.00%**

## Notes

Baseline on current production chunks. Conflict/completeness may be low until Round 0C–0F and FR-08 prompt wiring. Retrieval mode scores concatenated retrieved content as a proxy answer (not LLM generation). Retrieval candidates use the production query planner and graph plan.

## By category

| category | total | passed | pass_rate | mean_completeness | mean_evidence_recall |
|---|---:|---:|---:|---:|---:|
| conflict | 10 | 0 | 0.00% | 31.67% | 55.00% |
| cross_section | 20 | 9 | 45.00% | 85.42% | 66.67% |
| fact | 30 | 18 | 60.00% | 88.33% | 70.00% |
| none | 10 | 0 | 0.00% | 0.00% | 20.00% |
| ocr | 10 | 0 | 0.00% | 0.00% | 50.00% |
| procedure | 30 | 12 | 40.00% | 68.17% | 83.33% |
| table | 10 | 8 | 80.00% | 95.00% | 85.00% |

## By answerability

| answerability | total | passed | pass_rate | mean_completeness |
|---|---:|---:|---:|---:|
| conflict | 10 | 0 | 0.00% | 31.67% |
| full | 88 | 47 | 53.41% | 82.43% |
| none | 10 | 0 | 0.00% | 0.00% |
| partial | 12 | 0 | 0.00% | 8.33% |

## Fail sample (first 20)

- `mq-016` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `mq-018` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-022` [fact/partial] completeness=1.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'partial_or_refusal_ok': True}
- `mq-031` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-041` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `mq-042` [fact/full] completeness=1.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': True}
- `mq-043` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `mq-044` [fact/full] completeness=1.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': True}
- `mq-045` [fact/full] completeness=1.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': True}
- `mq-047` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `mq-050` [fact/full] completeness=1.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': True}
- `mq-051` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-002` [procedure/full] completeness=0.29 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-003` [procedure/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `mq-019` [procedure/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-023` [procedure/full] completeness=1.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': True}
- `mq-030` [procedure/partial] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'partial_or_refusal_ok': False}
- `mq-056` [procedure/full] completeness=0.33 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-057` [procedure/full] completeness=0.67 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-060` [procedure/full] completeness=0.67 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
