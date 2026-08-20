# GraphRAG A/B 验收报告（第 4 轮）

- **记录日期**：2026-07-27
- **评测数据集**：`data/eval_graph_rag_dataset.json`（20 题）
- **Baseline**：`data/eval_graph_rag_baseline.json`
- **Graph-on**：`data/eval_graph_rag_with_graph.json`
- **对比 JSON**：`data/eval_graph_rag_ab_compare.json`
- **生成时间**：`2026-07-27T03:02:04+00:00`

## 1. 结论摘要

| 项 | 结论 |
|------|------|
| 是否满足第 4 轮 GraphRAG **检索实效**验收（≥2 类意图 +5pp 且 fallback&lt;40%） | **PASS** |
| 至少 2 类意图提升 | **是**（config / definition / procedure） |
| graph_fallback_rate &lt; 40% | **是**（15%） |
| 是否发现图谱扩展引入明显错误 chunk | **有邻域噪声**；抽检见 §5（含 1 例 Hit@3 回退） |
| 是否建议生产默认开启 `graph_retrieval.enabled` | **否**（见 §6；本地可开体验） |
| Task 8.1 Gate | **PASS** |
| `quality --graph` | **未全绿**（历史债，登记例外） |

## 2. 数据集概况

| intent | 题数 | 备注 |
|------|---:|------|
| definition | 5 | PipelineBuilder / 管线点表 / 值域映射 / StampTools |
| config | 5 | PipelinePublishConfig / PIPELINE_Config / 映射类 |
| procedure | 4 | 发布/生成/字段管理链路 |
| dependency | 3 | 工具↔表关系 |
| troubleshooting | 3 | 缺字段 / 映射失败 / UV展开错误 |
| 合计 | 20 | 金标 chunk 来自 `entity_chunk_links` 且仍在 live Chroma |

## 3. A/B 指标

| intent | baseline Recall@3 | graph Recall@3 | Δ | baseline MRR | graph MRR | Δ | 结论 |
|------|---:|---:|---:|---:|---:|---:|------|
| definition | 50.00% | 50.00% | +0.00pp | 0.6333 | 0.7000 | **+6.67pp** | 提升（MRR） |
| config | 40.00% | 60.00% | **+20.00pp** | 0.4500 | 0.4667 | +1.67pp | 提升（R@3） |
| procedure | 42.71% | 45.83% | +3.12pp | 0.5833 | 0.7500 | **+16.67pp** | 提升（MRR） |
| dependency | 54.17% | 54.17% | +0.00pp | 1.0000 | 1.0000 | +0.00pp | 持平 |
| troubleshooting | 45.83% | 45.83% | +0.00pp | 0.6667 | 0.6667 | +0.00pp | 持平 |
| **overall** | **46.04%** | **51.67%** | **+5.63pp** | **0.6375** | **0.6917** | **+5.42pp** | 总体提升 |

## 4. 图谱链路观测

| 指标 | 结果 | 说明 |
|------|------|------|
| linked_entity_hit_rate | 55% | 期望实体集合 ⊆ 实际链接集合 |
| graph_fallback_rate | 15% | 主要为 `graph_evidence_filtered` |
| graph chunk 进入 top5 | 80% | 扩召回经常进入融合结果 |
| A/B 开关 | off：`ENABLED=false` + `ANCHOR_GRAPH_CHUNK=false`；on：`ENABLED=true` | 避免 allowlist 污染对照 |

## 5. 人工/自动抽检（前 10 题）

| # | question id | baseline Hit@3 | graph-on Hit@3 | 金标外图 chunk 入 top5 | 备注 |
|---:|------|:---:|:---:|:---:|------|
| 1 | graphrag-001 | Y | Y | Y | 邻域扩展（同工具其他节） |
| 2 | graphrag-002 | Y | Y | Y | 邻域扩展 |
| 3 | graphrag-003 | Y | Y | Y | 链接未齐套但 Hit 仍过 |
| 4 | graphrag-004 | Y | Y | N | 干净 |
| 5 | graphrag-005 | Y | **N** | Y | **Hit@3 回退**（StampTools 概述被图块挤占） |
| 6 | graphrag-006 | N | N | N | fallback=`graph_evidence_filtered` |
| 7 | graphrag-007 | N | N | N | 同上 |
| 8 | graphrag-008 | N | **Y** | N | 图侧帮助命中 |
| 9 | graphrag-009 | Y | Y | Y | 邻域噪声 |
| 10 | graphrag-010 | Y | Y | N | 干净 |

说明：金标外图 chunk ≠ 必然错误；多数为同产品邻域。真正需警惕的是 **graphrag-005** 这类挤占正例的回退。

## 6. 最终判定

1. **检索实效门禁（PRD §6.1 核心量化）**：**通过**。overall R@3/MRR 各约 +5pp；config / definition / procedure 三类达 +5pp。
2. **全图 quality gate**：**未通过**（历史 `missing_golden_relation` / `missing_evidence` / 1 条非法边），与 R5–R6c 口径一致；**不阻断本轮「图谱能增强检索」的证明**，但作为生产准入例外保留。
3. **生产建议**：**`config-prod.ini` 仍保持 `graph_retrieval.enabled=false`**。本地可开做体验；全库默认开图会引入邻域噪声与个别回退，需 allowlist / 过滤策略后再议生产默认 on。
4. **§13-6「图谱增强 RAG」**：勾选为 **通过（检索 A/B 已证明）**；不等于生产默认开图。
