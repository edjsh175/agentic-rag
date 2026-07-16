# GraphRAG 专项问题集草案

- **记录日期**：2026-07-16
- **用途**：第 4 轮 GraphRAG A/B 验收的人工种子题集
- **前置条件**：第 3 轮安全全量重建验收通过后，再落到 `data/eval_graph_rag_dataset.json`

## 1. 题集设计原则

1. 每题应能映射到明确 chunk、实体或关系，避免只靠常识回答。
2. 每类意图至少 4 题，覆盖定义、配置、流程、依赖、排错。
3. A/B 比较时固定 Hybrid 与 reranker 配置，只切换 graph retrieval。
4. 标注字段至少包含 `question`、`intent`、`linked_entity_names`、`expected_evidence_hint`、`doc_category`。

## 2. 草案题目

| # | intent | question | linked_entity_names | expected_evidence_hint | doc_category |
|---:|------|----------|---------------------|------------------------|--------------|
| 1 | definition | 管线点表包含哪些核心字段？ | 管线点表 | 字段定义、字段含义 | StampTools |
| 2 | definition | PipelineBuilder 的主要职责是什么？ | PipelineBuilder | 组件定义、用途说明 | StampTools |
| 3 | definition | DataSpec 在系统里描述什么内容？ | DataSpec | 数据规范、表结构 | StampTools |
| 4 | definition | 值域映射用于解决什么问题？ | 值域映射 | 状态映射、枚举转换 | StampTools |
| 5 | config | StampTools 运行时依赖哪些配置项？ | StampTools | 配置项清单 | StampTools |
| 6 | config | PipelineBuilder 发布前需要配置哪些参数？ | PipelineBuilder | 发布参数、环境参数 | StampTools |
| 7 | config | 数据源连接相关配置在哪里体现？ | 数据源连接 | 连接配置、数据源配置 | StampTools |
| 8 | config | 值域映射的配置规则有哪些约束？ | 值域映射 | 映射规则、约束条件 | StampTools |
| 9 | procedure | PipelineBuilder 发布流程分为哪些步骤？ | PipelineBuilder | 发布步骤、流程顺序 | StampTools |
| 10 | procedure | 从数据规范到管线生成的处理链路是什么？ | DataSpec, PipelineBuilder | 规范解析、生成流程 | StampTools |
| 11 | procedure | 使用状态映射时应按什么步骤配置？ | 使用状态映射 | 操作步骤、配置顺序 | StampTools |
| 12 | procedure | 新增一类管线字段时需要改哪些环节？ | 管线点表, DataSpec | 字段配置、生成、校验 | StampTools |
| 13 | dependency | StampTools 服务依赖哪些外部组件？ | StampTools | 服务依赖、环境组件 | StampTools |
| 14 | dependency | PipelineBuilder 与 DataSpec 之间是什么关系？ | PipelineBuilder, DataSpec | 依赖关系、输入输出 | StampTools |
| 15 | dependency | 管线点表与值域映射之间有什么关联？ | 管线点表, 值域映射 | 字段值、映射关系 | StampTools |
| 16 | dependency | 发布流程中哪些步骤依赖配置项先完成？ | 发布流程, 配置项 | 前置条件、依赖项 | StampTools |
| 17 | troubleshooting | 管线生成结果缺少字段时应先检查哪里？ | 管线点表, PipelineBuilder | 缺字段、排查路径 | StampTools |
| 18 | troubleshooting | 状态值没有正确映射时可能是什么原因？ | 值域映射 | 映射失败、配置错误 | StampTools |
| 19 | troubleshooting | PipelineBuilder 发布失败时应如何定位问题？ | PipelineBuilder | 发布失败、日志、配置 | StampTools |
| 20 | troubleshooting | 数据规范更新后图谱或检索结果不一致时怎么处理？ | DataSpec, 图谱重建 | 重建、审核、一致性检查 | StampTools |

## 3. 转 JSON 时的字段模板

```json
{
  "question": "PipelineBuilder 发布流程分为哪些步骤？",
  "intent": "procedure",
  "relevant_chunk_ids": [],
  "linked_entity_names": ["PipelineBuilder"],
  "expected_evidence_hint": "发布步骤、流程顺序",
  "kb_name": "文章附件",
  "doc_category": "StampTools"
}
```

## 4. 第 4 轮执行前待补

| 项 | 说明 |
|------|------|
| `relevant_chunk_ids` | 第 3 轮完成后基于最新 chunk ID 回填 |
| linked entity 校验 | 确认题目中的实体在重建后图谱中存在 |
| 人工答案要点 | 每题补 1–3 条可判定的答案要点 |
| A/B 判定规则 | 记录 baseline 与 graph-on 的 Recall@3、MRR、人工命中结论 |
