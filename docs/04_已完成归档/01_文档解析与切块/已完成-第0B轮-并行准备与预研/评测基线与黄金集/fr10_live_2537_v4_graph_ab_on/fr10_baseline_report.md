# FR-10 Offline Baseline Report

- generated_at: `2026-07-21T07:32:40.288982+00:00`
- mode: `retrieval`
- gold: `E:\申浩霖实习文件夹\rag_cy\rag\docs\3_待办清单\切块基石治理\已完成-第0B轮-并行准备与预研\评测基线与黄金集\multi_chunk_qa_gold_v4.json`
- total: **45**
- pass_rate: **86.67%**
- mean_completeness: **91.48%**
- mean_evidence_recall: **91.11%**
- forbidden_rate: **0.00%**

## Notes

Baseline on current production chunks. Conflict/completeness may be low until Round 0C–0F and FR-08 prompt wiring. Retrieval mode scores concatenated retrieved content as a proxy answer (not LLM generation). Retrieval candidates use the production query planner and graph plan.

## By category

| category | total | passed | pass_rate | mean_completeness | mean_evidence_recall |
|---|---:|---:|---:|---:|---:|
| fact | 21 | 17 | 80.95% | 88.10% | 85.71% |
| procedure | 17 | 15 | 88.24% | 92.16% | 94.12% |
| table | 7 | 7 | 100.00% | 100.00% | 100.00% |

## By answerability

| answerability | total | passed | pass_rate | mean_completeness |
|---|---:|---:|---:|---:|
| full | 45 | 39 | 86.67% | 91.48% |

## Fail sample (first 20)

- `mq-035` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `mq-044` [fact/full] completeness=0.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `mq-049` [fact/full] completeness=0.50 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
- `mq-050` [fact/full] completeness=1.00 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': True}
- `mq-019` [procedure/full] completeness=0.33 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': False, 'fact_coverage_ge_0_7': False}
- `mq-067` [procedure/full] completeness=0.33 checks={'no_forbidden_claims': True, 'evidence_anchor_recall': True, 'fact_coverage_ge_0_7': False}
