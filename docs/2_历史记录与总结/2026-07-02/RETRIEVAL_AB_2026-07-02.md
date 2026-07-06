# 检索 A/B 结果（2026-07-02）

## 已完成结果

| 数据集 | 策略 | Recall@3 | Recall@5 | MRR | 平均延迟 |
| --- | --- | ---: | ---: | ---: | ---: |
| 标准集（36） | Hybrid | 83.33% | 86.11% | 0.6736 | 331.6ms |
| 标准集（36） | Hybrid+Rerank | 88.89% | 94.44% | 0.7731 | 704.3ms |
| 标准集（36） | Hybrid+Rerank+Quality | 88.89% | 91.67% | 0.7662 | 807.5ms |
| 难例集（144） | Hybrid | 84.72% | 88.19% | 0.6858 | 227.2ms |
| 难例集（144） | Hybrid+Rerank | 90.97% | 95.83% | 0.7888 | 595.4ms |
| 难例集（144） | Hybrid+Rerank+Quality | 90.28% | 93.06% | 0.7812 | 580.3ms |

延迟会受机器负载影响。Quality 对纯 Hybrid 没有收益，叠加 Reranker 后反而降低 Recall 和 MRR，因此推荐 `Hybrid+Rerank`。

## Reranker 结论

- RTX 3060 Ti 使用 `torch 2.12.1+cu126` 后可正常进行 FP16 推理。
- 20 个短候选预热后推理约 40ms，模型常驻显存约 1.1GB。
- 完整检索链路平均约 0.6–0.7 秒，质量提升明确。
- 本地配置启用 Reranker；生产配置保持关闭，部署机确认具备 CUDA 后再开启。

详细机器可读结果见 `data/retrieval_ab_results.json`。
