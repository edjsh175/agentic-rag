# FR-10 Offline Baseline Report

- generated_at: `2026-07-24T06:36:23.958357+00:00`
- mode: `retrieval`
- gold: `docs\3_待办清单\切块基石治理\已完成-第0B轮-并行准备与预研\评测基线与黄金集\multi_chunk_qa_gold_v5_coverage.json`
- total: **90**
- pass_rate: **53.33%**
- mean_completeness: **61.39%**
- mean_evidence_recall: **71.11%**
- forbidden_rate: **0.00%**

## Notes

Baseline on current production chunks. Conflict/completeness may be low until Round 0C–0F and FR-08 prompt wiring. Retrieval mode scores concatenated retrieved content as a proxy answer (not LLM generation). Retrieval candidates use the production query planner and graph plan.

## By category

| category | total | passed | pass_rate | mean_completeness | mean_evidence_recall |
|---|---:|---:|---:|---:|---:|
| fact | 34 | 12 | 35.29% | 45.59% | 52.94% |
| procedure | 48 | 34 | 70.83% | 77.08% | 85.42% |
| table | 8 | 2 | 25.00% | 34.38% | 62.50% |

## By answerability

| answerability | total | passed | pass_rate | mean_completeness |
|---|---:|---:|---:|---:|
| full | 90 | 48 | 53.33% | 61.39% |

## Fail sample (first 20)

- `cv5-003` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `cv5-004` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `cv5-005` [table/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `cv5-006` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `cv5-007` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `cv5-008` [procedure/full] completeness=0.25 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `cv5-009` [procedure/full] completeness=0.25 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `cv5-010` [procedure/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `cv5-011` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `cv5-012` [table/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `cv5-013` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `cv5-014` [procedure/full] completeness=0.25 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `cv5-015` [table/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `cv5-016` [procedure/full] completeness=0.25 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `cv5-017` [table/full] completeness=0.25 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `cv5-019` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `cv5-021` [table/full] completeness=0.25 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `cv5-023` [procedure/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `cv5-026` [procedure/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `cv5-028` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
