# FR-10 Offline Baseline Report

- generated_at: `2026-07-16T02:54:08.019533+00:00`
- mode: `retrieval`
- gold: `E:\申浩霖实习文件夹\rag_cy\rag\docs\3_待办清单\切块基石治理\已完成-第0B轮-并行准备与预研\评测基线与黄金集\multi_chunk_qa_gold_v2.json`
- total: **120**
- pass_rate: **0.00%**
- mean_completeness: **0.00%**
- mean_evidence_recall: **0.00%**
- forbidden_rate: **0.00%**

## Notes

Baseline on current production chunks. Conflict/completeness may be low until Round 0C–0F and FR-08 prompt wiring. Retrieval mode scores concatenated retrieved content as a proxy answer (not LLM generation).

## By category

| category | total | passed | pass_rate | mean_completeness |
|---|---:|---:|---:|---:|
| conflict | 10 | 0 | 0.00% | 0.00% |
| cross_section | 20 | 0 | 0.00% | 0.00% |
| fact | 30 | 0 | 0.00% | 0.00% |
| none | 10 | 0 | 0.00% | 0.00% |
| ocr | 10 | 0 | 0.00% | 0.00% |
| procedure | 30 | 0 | 0.00% | 0.00% |
| table | 10 | 0 | 0.00% | 0.00% |

## By answerability

| answerability | total | passed | pass_rate | mean_completeness |
|---|---:|---:|---:|---:|
| conflict | 10 | 0 | 0.00% | 0.00% |
| full | 88 | 0 | 0.00% | 0.00% |
| none | 10 | 0 | 0.00% | 0.00% |
| partial | 12 | 0 | 0.00% | 0.00% |

## Fail sample (first 20)

- `mq-001` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-007` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-014` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-016` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-018` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-020` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-021` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-022` [fact/partial] completeness=0.00 checks={'no_forbidden_claims': True, 'partial_or_refusal_ok': False}
- `mq-024` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-031` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-032` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-033` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-034` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-035` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-036` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-037` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-038` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-039` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-040` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
- `mq-041` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'fact_coverage_ge_0_7': False}
