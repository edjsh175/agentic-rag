# RAG 问题现状与修复进度复核（2026-07-09）

> **历史快照（2026-07-28）**：反映 2026-07-08 复核；现行待办见 [待办清单](../../../../01_当前有效/00_项目总览与架构/待办清单.md)。


本文记录 2026-07-09 对当前 RAG 项目问题链路的复核结论，补充到 2026-07-08 的知识库一致性、UEModelBuilder 缺失排查、Graph-RAG 消歧和测试隔离记录之后。

## 1. 当前真正的问题主线

本轮问题不是单一的“检索参数不好”，而是四条线叠在一起：

1. 知识库半重建导致 `file_index.json` 与 Chroma collection 不一致。
2. `UEModelBuilder > 工程设置` 曾经无法召回，因为当时活动 Chroma 中缺少对应 chunk。
3. Graph-RAG 接入后需要防止 `UEModelBuilder / ModelBuilder`、`PipelineBuilder / 管线发布服务` 等相似实体互相污染。
4. pytest 测试过去存在误触正式 Chroma、正式 SQLite、正式运行目录的风险；当前已加熔断器，但测试迁移未完全收口。

一句话判断：**数据层事故已修复，但测试隔离和部分检索质量回归仍未完全收尾。**

## 2. 知识库一致性现状

2026-07-09 复核时，通过 `KnowledgeBaseConsistencyService().audit()` 对现场数据做只读审计，结果为：

```json
{
  "consistent": true,
  "index_file_total": 10,
  "index_chunk_total": 767,
  "chroma_chunk_total": 767,
  "missing_indexed_chunk_total": 0,
  "unexpected_chroma_chunk_total": 0
}
```

结论：

- 当前 `file_index.json` 与 Chroma 已一致。
- 半重建事故当前已恢复。
- 受控重建、锁文件、stale PID 检测和一致性断言方向正确，应保留。
- 文档中若仍出现 `index_files = 13` 一类旧口径，应以最新审计输出为准。

## 3. UEModelBuilder 现状

当前 Chroma 中已经存在 UEModelBuilder 相关内容，不再是“完全查不到”。现场可见关键 chunk 包括：

```text
StampTools用户手册.docx
2）不支持自定义曲线 > UEModelBuilder > 新建工程
内容：模型类型：根据数据特点选择数据类型，默认为建筑模型。

StampTools用户手册.docx
2）不支持自定义曲线 > UEModelBuilder > 数据管理
内容：添加目录：建筑模型、地面模型、部件模型、对象部件、水面模型和灯光支持。
添加文件：MatchModel和粒子支持。
```

当前判断：

- `UEModelBuilder` 核心内容已重新入库。
- 但 `section_path` 仍不干净，出现了 `2）不支持自定义曲线 > UEModelBuilder > ...` 这样的标题污染。
- 这会影响章节加权、Graph 抽取、来源展示和 LLM 对“工程设置/数据设置”层级的理解。
- `ueModelBuilder呢？` 这种裸追问仍依赖上下文化质量；如果没有被改写成 `UEModelBuilder 工程设置 数据设置 数据管理 模型类型` 这类查询，召回仍可能偏弱。

验收标准应改为：

```text
历史问题：ModelBuilder如何使用？
追问：ueModelBuilder呢？

期望最终 context 至少命中：
- UEModelBuilder 新建工程 / 工程设置
- UEModelBuilder 数据管理 / 数据设置
- 建筑模型、地面模型、水面模型、部件模型、对象部件、MatchModel、灯光、粒子
```

## 4. AI 回答变短的原因判断

回答变短不是单点故障，主要由以下因素共同造成：

1. Prompt 更严格：现在要求知识库事实必须来自 `<context>`，每项事实都要引用，不能补全隐含逻辑，宁可少答不得编造。
2. reranker 默认最终只保留 `top_n = 4`，普通问题默认 `top_k = 4`，给 LLM 的上下文天然比以前窄。
3. Graph Guard / Entity Guard 会排除或降权相似实体，减少了以前混进来的 `ModelBuilder`、`StampTools` 总览、`StampServer` 等弱相关内容。
4. 重建后 chunk 边界和 section_path 发生变化，如果只召回到短 chunk，回答自然会短。
5. 裸追问如果没有稳定上下文化，BM25/Hybrid 的关键词信号不足，可能召回不到完整流程链路。

当前不建议直接通过放宽 Prompt 或关闭 Guard 来“让回答变长”。正确做法是先记录每次问答的：

```text
original question
standalone_query
planner intent
plan.top_k / candidate_k
linked entities
final source chunk ids
final section_path
reranker 前后排序
```

确认 final context 到底少在哪里，再决定是调 QueryPlanner、上下文化、section_path，还是 top_k/reranker。

## 5. Graph-RAG 与实体消歧现状

已完成方向：

- `QueryEntityGuard` 防止显式实体在改写中丢失或被历史实体替换。
- `GraphEntityGuard` 记录 linked / excluded entities、aliases、doc_category、excluded chunks。
- `different_from` strict exclusion 防止高置信单实体问题被相似实体污染。
- Product metadata-only 约束减少 `StampTools` 产品级章节污染具体工具问题。
- Prompt Entity Hint 帮助 LLM 区分相似实体，但不作为事实来源。

当前判断：

- 这些改动是为了解决实体串台，不应因为回答变短而直接回滚。
- 剩余优化应放在 Final Context Guard 或查询链路观测上，而不是盲目扩大图谱扩展范围。

## 6. 测试隔离真实现状

文档中此前写过“测试隔离体系已完整建立”“全量测试通过”等口径，2026-07-09 复核后需要修正。

当前实际情况：

```text
.\venv\Scripts\python.exe -m pytest -q
结果：51 failed, 303 passed, 6 deselected
```

失败主要不是业务断言失败，而是被 live-path 熔断器拦截：

```text
pytest refused to use a live storage path without ALLOW_LIVE_STORAGE_IN_TESTS=1
```

这说明：

- 好消息：正式库防污染机制已生效，测试不会再静默写入正式 `chroma_db`、`data/rag_relational.db`、`data`、`logs`、`watch_directory` 等路径。
- 坏消息：仍有大量测试没有迁移到 `isolated_storage`，默认 pytest 当前不是绿的。

已知待迁移/待修复测试包括但不限于：

```text
tests/test_neighbor_expansion.py
tests/test_query_planner.py
tests/test_query_contextualizer.py
tests/test_reranker.py
tests/test_retrieval_quality.py
tests/test_routing_and_structured_boost.py
tests/test_chunk_admin.py
tests/test_chunk_stats.py
tests/test_scanner_cleanup.py
```

当前正确口径应为：

```text
正式库防污染熔断器已生效；
integration 默认排除已配置；
但单元测试隔离迁移未完成，默认 pytest 仍需收口。
```

不要为了让测试通过而设置全局：

```text
ALLOW_LIVE_STORAGE_IN_TESTS=1
```

除非是明确标记的 integration 测试，并且手动执行：

```bash
pytest -m integration
```

## 7. 下一步优先级

### P0：测试隔离收口

目标：默认测试命令恢复全绿，且不接触正式运行数据。

```bash
.\venv\Scripts\python.exe -m pytest -q
```

要求：

- 所有会初始化 `Config()`、`VectorStore()`、`RelationalDB()`、`BM25Store()`、`RagChain()`、`QueryPlanner()`、`QueryContextualizer()` 的单元测试都应使用 `isolated_storage` 或纯配置对象。
- 确实依赖正式库的测试必须标记 `@pytest.mark.integration`，默认排除。

### P1：UEModelBuilder 追问回归

新增回归测试覆盖：

```text
上一轮：ModelBuilder如何使用？
追问：ueModelBuilder呢？
```

至少断言最终 sources/context 包含：

```text
UEModelBuilder 新建工程 / 数据管理
建筑模型、地面模型、水面模型、部件模型、对象部件、MatchModel、灯光、粒子
```

### P1：section_path 污染修复

重点排查 DOCX 标题边界，避免继续出现：

```text
2）不支持自定义曲线 > UEModelBuilder > 新建工程
```

期望修正方向：

```text
UEModelBuilder > 工程设置 > 新建工程
UEModelBuilder > 数据设置 > 数据管理
```

### P2：回答变短专项观测

先加日志或测试观测 final context，再决定是否调整：

- QueryPlanner 意图判断
- 追问上下文化
- plan top_k / candidate_k
- reranker top_n
- Graph Guard 融合后排序
- context budget 裁剪

## 8. 当前总判断

```text
已修复：知识库半重建事故、受控重建、一致性审计、UEModelBuilder 核心内容重新入库、Graph-RAG 实体消歧、防正式库污染熔断。

未完成：默认 pytest 全绿、测试全面 isolated_storage 迁移、UEModelBuilder 追问回归、section_path 污染修复、回答变短的 final context 观测。
```

不要把当前状态描述为“全部完成”。更准确的状态是：

```text
数据层已恢复一致；防污染机制已生效；但测试隔离迁移和检索效果回归仍在收口阶段。
```
