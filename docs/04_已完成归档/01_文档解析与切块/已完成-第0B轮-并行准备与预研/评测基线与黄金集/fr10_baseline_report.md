# FR-10 Offline Baseline Report

- generated_at: `2026-07-15T01:35:31.971450+00:00`
- mode: `retrieval`
- gold: `E:\申浩霖实习文件夹\rag_cy\rag\docs\3_待办清单\chunk-foundation-parallel-prep\multi_chunk_qa_gold_v2.json`
- total: **120**
- pass_rate: **35.00%**
- mean_completeness: **47.83%**
- mean_evidence_recall: **44.17%**
- forbidden_rate: **0.00%**

## Notes

Baseline on current production chunks. Conflict/completeness may be low until Round 0C–0F and FR-08 prompt wiring. Retrieval mode scores concatenated retrieved content as a proxy answer (not LLM generation).

## By category

| category | total | passed | pass_rate | mean_completeness |
|---|---:|---:|---:|---:|
| conflict | 10 | 0 | 0.00% | 31.67% |
| cross_section | 20 | 9 | 45.00% | 64.58% |
| fact | 30 | 18 | 60.00% | 66.67% |
| none | 10 | 0 | 0.00% | 0.00% |
| ocr | 10 | 0 | 0.00% | 0.00% |
| procedure | 30 | 7 | 23.33% | 44.37% |
| table | 10 | 8 | 80.00% | 80.00% |

## By answerability

| answerability | total | passed | pass_rate | mean_completeness |
|---|---:|---:|---:|---:|
| conflict | 10 | 0 | 0.00% | 31.67% |
| full | 88 | 41 | 46.59% | 60.48% |
| none | 10 | 0 | 0.00% | 0.00% |
| partial | 12 | 1 | 8.33% | 8.33% |

## Fail sample (first 20)

- `mq-018` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-031` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-034` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-035` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-036` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-041` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-042` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-045` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-046` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-047` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-048` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-051` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-002` [procedure/full] completeness=0.14 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-003` [procedure/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-009` [procedure/full] completeness=0.50 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-013` [procedure/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-017` [procedure/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-019` [procedure/full] completeness=0.50 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-025` [procedure/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-030` [procedure/partial] completeness=0.00 checks={'no_forbidden_claims': True, 'partial_or_refusal_ok': False}
