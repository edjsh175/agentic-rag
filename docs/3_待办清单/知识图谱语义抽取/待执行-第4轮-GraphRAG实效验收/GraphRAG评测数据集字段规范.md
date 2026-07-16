# GraphRAG 评测数据集字段规范

- **记录日期**：2026-07-16
- **目标文件**：`data/eval_graph_rag_dataset.json`
- **使用阶段**：第 4 轮 GraphRAG A/B 验收

## 1. 业务目标

这份数据集不是普通问答样例，而是用来证明“图谱是否真的帮到了检索和回答”。每道题都要能追到明确证据，避免模型凭常识答对。

## 2. 字段定义

| 字段 | 必填 | 说明 |
|------|:---:|------|
| `id` | 是 | 稳定题号，如 `graphrag-001` |
| `question` | 是 | 用户会真实提出的问题 |
| `intent` | 是 | `definition` / `config` / `procedure` / `dependency` / `troubleshooting` |
| `relevant_chunk_ids` | 是 | 第 3 轮完成后回填，作为 Recall@3 / MRR 判定依据 |
| `linked_entity_names` | 是 | 问题应命中的图谱实体名称 |
| `expected_evidence_hint` | 是 | 证据应包含的业务要点，不替代标准答案 |
| `answer_key_points` | 是 | 人工判定答案是否正确的 1-3 条要点 |
| `kb_name` | 是 | 知识库名称 |
| `doc_category` | 是 | 文档类别或产品线，如 `StampTools` |
| `notes` | 否 | 题目来源、特殊判断口径或例外 |

## 3. JSON 样例

```json
{
  "id": "graphrag-009",
  "question": "PipelineBuilder 发布流程分为哪些步骤？",
  "intent": "procedure",
  "relevant_chunk_ids": [],
  "linked_entity_names": ["PipelineBuilder"],
  "expected_evidence_hint": "发布步骤、流程顺序",
  "answer_key_points": [
    "能说明发布流程的主要步骤",
    "能体现步骤顺序或前后依赖",
    "答案来源命中 PipelineBuilder 相关文档"
  ],
  "kb_name": "文章附件",
  "doc_category": "StampTools",
  "notes": "第 3 轮完成后回填最新 chunk id"
}
```

## 4. 验收前检查

| 检查项 | 要求 |
|------|------|
| 题数 | ≥ 20 |
| 意图覆盖 | 至少覆盖 5 类意图 |
| chunk id | 全部来自第 3 轮完成后的 live Chroma |
| 实体名称 | 在第 3 轮完成后的正式图谱中可查 |
| 答案要点 | 每题至少 1 条可人工判断的要点 |
