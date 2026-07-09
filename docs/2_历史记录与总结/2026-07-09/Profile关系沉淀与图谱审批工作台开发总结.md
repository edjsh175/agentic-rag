# Profile 关系沉淀与图谱审批工作台开发总结 (2026-07-09)

## 1. 任务背景

在 RAG 系统的检索治理中，`retrieval_intent_profiles.json` 扮演了“检索意图治理中间件”的角色，通过临时规则补丁解决了很多具体的检索偏差问题。然而，随着项目深入，Profile 中承担了越来越多的“领域事实知识”（如实体同义别名、数据表字段属性、表族兄弟关系等）。

为了将这些稳定的领域知识沉淀到关系型知识图谱中，并在 Profile 里只保留纯检索策略参数，本阶段开发了 **Profile 关系沉淀到知识图谱机制** 及 **前端图谱候选审批工作台**，打通了“检索治理中间件 -> 稳定关系沉淀 -> 知识图谱资产化”的闭环链路。

---

## 2. 后端设计与实现

### 2.1 Profile 关系同步服务 ([profile_graph_sync.py](file:///e:/%E7%94%B3%E6%B5%A9%E9%9C%96%E5%AE%9E%E4%B9%A0%E6%96%87%E4%BB%B6%E5%A4%B9/rag_cy/rag/rag_knowledge/services/profile_graph_sync.py))
新增 `ProfileGraphSyncService` 类，实现以下核心逻辑：
- **同义别名抽取**：解析 Profile 中的 `entity_aliases`，将首个作为 canonical 实体，其余作为 `Alias` 候选，关联 `alias_of` 关系。
- **章节定位与从属**：解析 `section_families`，提取出 Module、Section、DataTable，并生成 `has_table`、`belongs_to`、`defined_in` 候选关系。
- **字段关联提取**：解析 `recall_terms` 召回项，自动过滤长度小于3或纯数字等噪声项，提取并关联 `has_field` 关系。
- **兄弟表族消歧**：解析 `sibling_penalty_groups`，在各个实体之间建立两两互斥的 `different_from` 关系。
- **策略依赖转换**：将 `preferred_sources` 和 `fallback_sources` 策略转换为带有较弱置信度 (0.6) 的 `belongs_to` 临时候选关系，并记录诊断日志。
- **候选批次落库**：将预览候选生成批次 (`BuildBatchResult`) 写入 SQLite 的 `extraction_batches` 及其关联候选表中，等待管理员审批。

### 2.2 同步命令行脚本 ([sync_profiles_to_graph.py](file:///e:/%E7%94%B3%E6%B5%A9%E9%9C%96%E5%AE%9E%E4%B9%A0%E6%96%87%E4%BB%B6%E5%A4%B9/rag_cy/rag/sync_profiles_to_graph.py))
提供便捷的 CLI 入口：
```bash
# 预览抽取出来的图谱关系与实体
python sync_profiles_to_graph.py --dry-run

# 执行抽取并生成 pending 状态的候选批次
python sync_profiles_to_graph.py --apply --review-status pending
```

---

## 3. 前端设计与实现

为了让管理员能直观地对提取的实体、别名、关系候选进行审核，前端新增了 **图谱候选审批工作台**：

### 3.1 路由与导航配置
- 路由中新增 `/admin/graph-candidates` 映射到 [GraphCandidatesView.vue](file:///e:/%E7%94%B3%E6%B5%A9%E9%9C%96%E5%AE%9E%E4%B9%A0%E6%96%87%E4%BB%B6%E5%A4%B9/rag_cy/rag/web/src/views/GraphCandidatesView.vue)。
- 导航栏中新增“图谱候选审批”菜单项。

### 3.2 审核工作台主要功能
- **批次列表**：展示所有来源为 `profile_sync` 或 `document_extraction` 的提取批次、生成时间、审核状态与统计数据（实体数、别名数、关系数）。
- **审批详情面板**：
  - 支持按分类（实体 / 别名 / 关系）页签展示具体候选项目。
  - 展示置信度、来源证据片段、字段映射。
  - 支持单项批准 (Approve) 或拒绝 (Reject)。
  - 支持批量批准 (Approve All) / 批量拒绝，一键沉淀到 SQLite 的正式图谱实体表 `entities` 与关系边表 `relations`。

---

## 4. 自动化测试与验证

后端和前端均补齐了单元测试：
- **后端测试** ([test_profile_graph_sync.py](file:///e:/%E7%94%B3%E6%B5%A9%E9%9C%96%E5%AE%9E%E4%B9%A0%E6%96%87%E4%BB%B6%E5%A4%B9/rag_cy/rag/tests/test_profile_graph_sync.py))：覆盖了空意图列表、同义词冲突、非法关系诊断、多层级章节属于关系，以及命令行参数解析 and preview/apply 执行路径。
- **前端测试** ([GraphCandidatesView.spec.ts](file:///e:/%E7%94%B3%E6%B5%A9%E9%9C%96%E5%AE%9E%E4%B9%A0%E6%96%87%E4%BB%B6%E5%A4%B9/rag_cy/rag/web/src/views/GraphCandidatesView.spec.ts))：对列表加载、单项审核操作、批量选择审批进行桩测试与验证。

目前，运行单元测试表现良好，所有新增功能都已收口。
