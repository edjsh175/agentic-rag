# GraphRAG A/B 验收报告模板

- **记录日期**：待填
- **评测数据集**：`data/eval_graph_rag_dataset.json`
- **Baseline**：`data/eval_graph_rag_baseline.json`
- **Graph-on**：`data/eval_graph_rag_with_graph.json`
- **前置条件**：第 3 轮全量重建验收通过，§10 指标大部分达标

## 1. 结论摘要

| 项 | 结论 |
|------|------|
| 是否满足第 4 轮 GraphRAG 实效验收 | 待填 |
| 至少 2 类意图提升 | 待填 |
| graph_fallback_rate < 40% | 待填 |
| 是否发现图谱扩展引入错误 chunk | 待填 |
| 是否建议生产开启 graph retrieval | 待填 |

## 2. 数据集概况

| intent | 题数 | 备注 |
|------|---:|------|
| definition | 待填 |  |
| config | 待填 |  |
| procedure | 待填 |  |
| dependency | 待填 |  |
| troubleshooting | 待填 |  |
| 合计 | 待填 |  |

## 3. A/B 指标

| intent | baseline Recall@3 | graph Recall@3 | Δ | baseline MRR | graph MRR | Δ | 结论 |
|------|---:|---:|---:|---:|---:|---:|------|
| definition | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| config | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| procedure | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| dependency | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |
| troubleshooting | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |

## 4. 图谱链路观测

| 指标 | 结果 | 说明 |
|------|------|------|
| linked_entity_hit_rate | 待填 | 问题实体是否成功链接 |
| graph_fallback_rate | 待填 | `fallback_reason != none` 的比例 |
| graph chunk 命中率 | 待填 | 图谱扩展 chunk 是否进入 topK |
| fallback 主要原因 | 待填 | 实体未链接 / 无邻接关系 / 证据不足等 |

## 5. 人工抽检

| # | question id | baseline 结论 | graph-on 结论 | 是否引入错误 chunk | 备注 |
|---:|------|------|------|:---:|------|
| 1 | 待填 | 待填 | 待填 | 待填 |  |
| 2 | 待填 | 待填 | 待填 | 待填 |  |
| 3 | 待填 | 待填 | 待填 | 待填 |  |
| 4 | 待填 | 待填 | 待填 | 待填 |  |
| 5 | 待填 | 待填 | 待填 | 待填 |  |
| 6 | 待填 | 待填 | 待填 | 待填 |  |
| 7 | 待填 | 待填 | 待填 | 待填 |  |
| 8 | 待填 | 待填 | 待填 | 待填 |  |
| 9 | 待填 | 待填 | 待填 | 待填 |  |
| 10 | 待填 | 待填 | 待填 | 待填 |  |

## 6. 最终判定

待填：说明是否通过第 4 轮 GraphRAG 实效验收；若未通过，列出阻塞项和下一轮修复建议。
