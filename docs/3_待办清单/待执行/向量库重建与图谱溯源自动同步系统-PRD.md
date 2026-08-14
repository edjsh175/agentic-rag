# 向量库重建与图谱溯源自动同步系统 · 产品需求说明书（PRD）

| 文档版本 | 修改日期 | 修改人 | 修改内容 |
|---------|---------|--------|----------|
| V1.0 | 2026-07-28 | 知识图谱与存储组 | 初稿创建：Rebuild后图谱溯源重新匹配与一致性保证 |


## 一、项目概述

### 1.1 项目背景

后端 `RebuildCoordinator` 实现了向量库的 Safe Rebuild（安全重建），可在更换 Embedding 模型或调整切片策略时，在暂存集合中无缝重建 Chroma 向量库。
然而，重建过程会导致 **Chunk ID 重新生成**。目前 SQLite 关系表（`entity_chunk_links` 及 `relations.source_chunk_id`）中仍保存着旧的 Chunk ID，导致重建完成后：
* **图谱溯源失效**：双击图谱节点查看“关联的知识块”或“证据文本”时出现空命中。
* **1跳/2跳检索扩展失效**：通过实体扩图查到的 Chunk ID 在新向量库中不存在。

### 1.2 项目目标

构建**向量库重建与图谱溯源自动同步系统**：
1. **自动 Hook 联动**：在 `RebuildCoordinator` 的事务 `commit_swap` 完成后，自动触发图谱溯源重新同步 Hook。
2. **轻量语义+哈希匹配 (Graph Resync)**：结合文本相似度与文件相对路径+章节全称，将原有实体自动重绑定到新生成的 Chunk ID 上。
3. **一致性断言与校验**：重建完成后调用 `KnowledgeBaseConsistencyService` 校验 Chunk ID 引用合法性，若校验失败自动触发全量图谱补抽。

### 1.3 系统角色定义

| 角色 | 职责 |
|------|------|
| **RebuildCoordinator** | 协调向量库重建并在 Swap 阶段调用图谱同步钩子 |
| **GraphResyncService** | 执行旧 Chunk ID 到新 Chunk ID 的文本/路径重映射 |
| **一致性检查服务** | 验证图谱与新向量库的 `chunk_id` 匹配率 |


## 二、总体架构与数据流转

### 2.1 架构图

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                            向量库重建协调器 (RebuildCoordinator)                     │
│  Staging 暂存构建 ──> 校验一致性 ──> Swap 切换为 Live ──> [触发图谱同步 Hook]       │
└─────────────────────────────────────────────────────────────────────────────────────┘
                                         │
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                            图谱溯源同步服务 (GraphResyncService)                    │
│  ┌────────────────────────┐    ┌────────────────────────┐    ┌───────────────────┐  │
│  │ 建立旧文本/路径哈希表  │ →  │ 新旧 Chunk ID 相似映射  │ →  │ 更新 SQLite 关联表│  │
│  └────────────────────────┘    └────────────────────────┘    └───────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────────┘
                                         │
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                数据层 (Chroma + SQLite)                             │
│  Chroma (新 Chunk ID) ─── 映射对比 ───> SQLite: entity_chunk_links / relations       │
└─────────────────────────────────────────────────────────────────────────────────────┘
```


## 三、数据层与算法设计

### 3.1 旧 Chunk 到新 Chunk 的重映射算法 (Resync Strategy)

当向量库重建完成，新的 Chunk 列表集合 $C_{new}$ 与备份关系表中的旧 Chunk 集合 $C_{old}$ 按以下优先级匹配：

| 匹配层级 | 匹配条件 | 匹配逻辑 |
|---------|---------|----------|
| **L1 (精确匹配)** | `source_file` + `section_title` + 文本 MD5 相同 | 直接将旧 `chunk_id` 替换为新 `chunk_id` |
| **L2 (部分匹配)** | `source_file` + `section_title` 相同且 Jaccard 相似度 > 0.8 | 将旧 `chunk_id` 替换为新 `chunk_id` |
| **L3 (补救抽取)** | L1/L2 均无法匹配（切片策略大幅变更） | 将关联标记为过期，自动把对应文件加入 `GraphBuilder.build_incremental` 重抽队列 |

### 3.2 表变更范围 (SQLite)

* `entity_chunk_links` 表：更新 `chunk_id` 字段。
* `relations` 表：更新 `source_chunk_id` 字段。


## 四、功能详细设计与后端 API

### 4.1 Rebuild 流程扩展

修改 `RebuildCoordinator.run` 流程：

```python
# 1. 提交暂存向量库
_commit_swap(...)

# 2. 刷新 BM25 & 缓存
self._rebuild_bm25()
self._invalidate_retrieval_caches("rebuild_commit")

# 3. [新增] 触发图谱溯源重新同步
resync_result = GraphResyncService(
    db=self._db,
    old_index_backup=index_backup,
    new_store=self._store
).resync()

# 4. 再次执行一致性检查
KnowledgeBaseConsistencyService().assert_consistent()
```

### 4.2 API 接口定义

#### 接口 1：手动触发图谱与向量库溯源同步
* **HTTP 请求**：`POST /api/graph/resync`
* **Response**：
```json
{
  "status": "ok",
  "total_links": 340,
  "remapped_exact": 310,
  "remapped_similar": 25,
  "orphaned_links": 5,
  "message": "图谱溯源同步完成，5条无法匹配的链接已加入重抽队列"
}
```


## 五、前端交互与页面设计

### 5.1 图谱管理页面 (KnowledgeGraphView) 增补

1. **图谱溯源健康度警告框**：
   * 若存在 `source_chunk_id` 无法在新向量库中找到的节点，画布顶部弹出警示提示条：
     `[警告] 检测到 5 个图谱节点的溯源 Chunk 已失效（可能由于近期向量库重建导致），[一键修复同步]`。


## 六、业务流程说明

```
【向量库重建完成】 ──> 提取旧 index 文本签名 ──> 计算新 Chunk 签名 ──> L1/L2 自动重绑定 ──> 完成溯源修复
```


## 七、非功能性需求与界面规范

* **界面与内容禁用 Emoji 规则**：前端 UI 界面、图谱画布、提示弹窗、控制台日志中，一律禁止使用 Emoji 表情符号，图标统一使用 UI 组件库中的矢量/SVG Icon，警告提示统一使用 [警告] 格式前缀。
* **事务安全性**：SQLite 更新使用单独的事务，若 Resync 失败自动回滚关系数据库，不影响已有图谱节点。
* **时延**：2000 个 Chunk 的重映射计算控制在 30 秒以内。


## 八、实施优先级与验收标准

* **验收标准**：
  1. 触发向量库重建完结后，双击图谱画布上的实体节点，右侧仍能准确展示最新的知识块正文与证据段落。
  2. `KnowledgeBaseConsistencyService` 校验无 `orphaned_chunk_id` 报错。
  3. 图谱管理与画布界面中无任何 Emoji 符号。
