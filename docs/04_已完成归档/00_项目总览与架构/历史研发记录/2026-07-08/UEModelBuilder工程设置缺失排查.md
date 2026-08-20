# UEModelBuilder 工程设置缺失排查记录（2026-07-08）

本文记录 `ueModelBuilder呢？` 查询结果不符合预期的专项排查结论。

## 1. 用户期望

用户期望回答来自原始手册中的以下章节：

```text
5.4 UEModelBuilder
5.4.1 工程设置
（1）新建工程
模型类型：建筑模型、地面模型、水面模型、部件模型、对象部件、MatchModel、灯光、粒子等
```

也就是说，在追问 `ueModelBuilder呢？` 时，系统应继承上一轮 `ModelBuilder如何使用？` 的“使用流程”意图，并优先回答 UEModelBuilder 的工程设置、数据设置等主流程内容。

## 2. 当前表现

当前系统已有明显好转：

- `UEModelBuilder` 主体已经识别正确。
- 回答不再被 `ModelBuilder` 抢占。
- 图谱日志显示已链接到 `UEModelBuilder`。

现场日志：

```text
graph_retrieval | linked=['UEModelBuilder'] chunks=5 fallback=none
```

但回答内容只覆盖了当前可召回的 5 个 UEModelBuilder chunk：

```text
UEModelBuilder > UE蓝图支持说明 > 时间轴
UEModelBuilder > UE材质集添加蓝图 > 新建蓝图
UEModelBuilder > UE材质集添加蓝图 > 设置事件
UEModelBuilder > UE材质集添加蓝图 > 保存和编译
UEModelBuilder > UEModelBuilder > 数据设置
```

缺失：

```text
UEModelBuilder > 工程设置
UEModelBuilder > UEModelBuilder > 工程设置
5.4.1 工程设置
```

## 3. 排查结论

根因不是 QueryEntityGuard、QueryPlanner、GraphRetriever、reranker 或 prompt。

真正原因是：

> 当前活动 Chroma 向量库中没有 `UEModelBuilder > 工程设置` 这一 chunk。

因此 LLM 没有看到用户截图中的原文内容，无法回答出“建筑模型、地面模型、水面模型、部件模型、对象部件、MatchModel、灯光、粒子”等工程设置说明。

## 4. 证据

### 4.1 原始 DOCX 中存在该内容

直接解析：

```text
watch_directory/word/StampTools用户手册.docx
```

确认原始文档中存在：

```text
5.4 UEModelBuilder
5.4.1 工程设置
模型类型
对象部件
MatchModel
```

所以原始资料没有丢。

### 4.2 当前 Chroma 缺少该章节

当前 Chroma 状态：

```text
Chroma count = 418
```

当前 Chroma 中 `UEModelBuilder` 相关 chunk 只有 5 个，未发现 `工程设置` 章节。

### 4.3 file_index 与 Chroma 不一致

`data/file_index.json` 中，`StampTools用户手册.docx` 记录了：

```text
78 个 chunk_id
```

但用这 78 个 chunk_id 去当前 Chroma 查询：

```text
file_index_chunks = 78
chroma_found = 0
missing = 78
```

这说明：

> `file_index.json` 记录的 StampTools chunk 与当前 Chroma collection 不是同一批数据。

当前知识库处于半重建/不同步状态。

## 5. 当前判断

当前问题应定义为：

> 知识库数据层不一致，导致 StampTools 用户手册中的部分章节没有进入当前可检索向量库。

不是：

- EntityGuard 失败
- GraphRetriever 失败
- reranker 过滤失败
- prompt 约束不足

当前 RAG 查询链已经能把主体定位到 `UEModelBuilder`，但底层知识库缺少应有的章节 chunk。

## 6. 对后续工作的影响

在修复知识库一致性之前，不建议继续调：

- QueryEntityGuard
- GraphRetriever
- QueryPlanner
- reranker 参数
- prompt 表达

否则会把数据层问题误判成检索策略问题。

## 7. 下一步修复方向

应执行一次受控知识库重建：

```text
确认无残留 rebuild 进程
清理或处理 rebuild.lock / rebuild_state.json
备份当前 file_index、Chroma、graph db
清空当前 Chroma collection
重置 file_index
重新扫描 watch_directory
重建 BM25
重新执行知识库一致性审计
重建或校验知识图谱
```

重建完成后的验收条件：

```text
file_index chunk 总数 == Chroma chunk 总数
missing_indexed_chunk_total = 0
unexpected_chroma_chunk_total = 0
```

UEModelBuilder 专项验收：

```text
Chroma 中可以查到 UEModelBuilder > 工程设置
ueModelBuilder呢？ 的回答包含 5.4.1 工程设置内容
```

回答中至少应能出现：

```text
建筑模型
地面模型
水面模型
部件模型
对象部件
MatchModel
灯光
粒子
```

## 8. 交接结论

当前状态不是“RAG 检索逻辑错误”，而是“知识库索引元数据与实际向量库不同步”。

`ueModelBuilder呢？` 回答不完整的直接原因是：

> 当前 Chroma 中缺少 `UEModelBuilder > 工程设置` chunk。

第一优先级应恢复知识库数据一致性，然后再重新评估 RAG 效果。
