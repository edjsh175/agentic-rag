# Graph-RAG 实体约束与消歧现状记录（2026-07-08）

> **历史快照（2026-07-28）**：反映 2026-07-08 现状；GraphRAG 实效验收见第 4 轮目录。


本文记录 Phase C Graph-RAG Entity Guard 的当前实现状态、验证结果以及剩余优化方向。

## 1. 当前阶段状态

知识图谱路线当前状态：

```text
Phase A：知识图谱底座建设       已完成
Phase B：规则抽取与图谱构建     已完成
Phase C：Graph-RAG 接入         已完成主体实现，进入优化阶段
Phase D：LLM 抽取与自动构建     尚未开始
```

当前 Graph-RAG 已具备：

- Entity Linking
- Graph Expansion
- Graph Evidence Retrieval
- RRF 融合
- different_from 消歧
- Entity Context Guard
- Prompt Entity Hint 注入

---

## 2. 本轮解决的问题

典型问题：

用户询问：

```text
管线发布工具如何使用
```

系统曾出现概念混淆：

```text
PipelineBuilder
(StampTools / Tool)

错误召回：

管线发布服务
(StampServer / Service)
```

二者关系：

```text
PipelineBuilder
    different_from
管线发布服务
```

已在真实图谱中建立并审核通过。

---

## 3. 已完成优化

### 3.1 Entity Guard

新增 GraphGuardContext，用于保存：

- linked entity
- aliases
- categories
- excluded entities
- excluded chunks
- strict exclusion 条件

strict exclusion 条件：

```text
单一实体高置信匹配
confidence >= 0.9
非 comparison intent
用户未主动提及 excluded entity
```

满足条件时：

- different_from 对应 chunk 强排除
- 避免相似实体污染上下文

---

### 3.2 Product metadata-only 约束

解决问题：

```text
PipelineBuilder
    belongs_to
StampTools(Product)
```

过去会导致：

```text
StampTools 产品级章节
工具概述
运行环境
纹理格式
```

进入检索上下文。

当前策略：

如果 Product 不是用户直接 link 的实体：

- 保留为归属 metadata
- 不贡献 chunk_ids
- 不贡献 retrieval_queries
- 不继续展开 Product 章节

---

### 3.3 RRF Fusion 优化

GraphRetriever.fuse 保持纯函数。

支持：

- strict exclusion
- soft penalty
- graph channel boost
- entity aligned boost

当前规则：

```text
linked entity section/source 命中
    boost

graph channel
    boost

excluded chunk
    strict exclusion 或降权
```

---

### 3.4 Prompt Entity Hint

LLM system prompt 增加：

```text
## 当前图谱识别实体
```

内容包括：

- 当前实体
- 类型
- 别名
- 不应混淆实体

用于降低最终回答阶段的实体混淆。

---

## 4. 验证结果

### 自动化测试

执行：

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_graph_retrieval.py tests/test_rag_stage6.py -q
```

结果：

```text
42 passed, 6 subtests passed
```

覆盖：

- different_from strict exclusion
- comparison exemption
- Product expansion limit
- Product own link exclusion
- prompt entity hint

---

### 格式检查

执行：

```bash
git diff --check
```

结果：

```text
通过
```

---

## 5. 真实库验证

测试问题：

```text
管线发布工具如何使用
```

当前：

正确识别：

```text
PipelineBuilder
Tool
StampTools
```

成功排除：

```text
管线发布服务
StampServer
```

主要召回内容：

```text
PipelineBuilder > 数据设置
PipelineBuilder > 工程设置
PipelineBuilder > 生成数据
PipelineBuilder > 字段映射
PipelineBuilder > 值域映射
```

---

## 6. 当前剩余问题

虽然 Graph channel 已正确约束，但 standard retrieval 仍可能贡献少量弱相关文档。

例如：

```text
StampServer PDF 中的 Pipeline 描述
工具概述中的编译日志
```

这些不是 Graph Guard 失败，而是：

```text
Hybrid Retrieval
    +
Rerank
```

阶段仍允许部分相似文本进入候选集。

---

## 7. 下一步优化方向

如果目标是进一步提升实体级准确率：

### Final Context Guard

在 rerank 后增加最终实体约束：

高置信单实体问题：

优先保留：

- graph 命中文档
- section_path 命中实体
- source/category 与实体一致文档

降低：

- 仅关键词相似
- 无实体关联的 retrieval 文档

注意：

comparison / dependency / integration 类问题不能启用强过滤。

---

## 当前结论

```text
Graph-RAG Entity Guard v1 已完成。

实体识别：通过
图谱消歧：通过
different_from 约束：通过
Product 扩展污染：解决
Prompt 实体提示：完成

剩余优化：Final Context Guard
```
