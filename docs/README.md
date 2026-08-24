# RAG 本地知识库问答系统 — 项目仪表盘 (docs)

> [!NOTE]
> **最近同步时间**：2026-08-21
> **一句话当前阶段**：**Agent V1.6 实施中 + Agent 两阶段回答/模型路由改善待执行 + 图谱/检索治理收口 + 切块 0D/0E 待执行**。文本 RAG 检索基线可复现；GraphRAG 本地可开全图，生产默认不开图。文档状态统一以 [文档治理总账](00_文档治理/文档状态总账.md) 为准。

---

## 1. 项目当前阶段与核心状态

- **文本 RAG 检索基线（冻结）**：FR-10 v4，2026-07-20 快照（约 2537 live chunks），39/45 = **86.67%**。08-14 语料已有手册重扫，**尚未重跑 v4**；该分数绑定旧快照，不是当天库规模。
- **正式图谱规模**：
  | 口径 | 实体 | 关系 | 说明 |
  | :--- | ---: | ---: | :--- |
  | 现场只读（2026-08-14） | **305** | **297** | `data/rag_relational.db`；links 341；另有 staging 候选约 3.6 万 |
  | R7 纪要（2026-07-27） | 1375 | 1214 | 历史建设快照，**不是** 08-14 现场 |
- **环境配置对照**（以 ini 为准；代码默认图开关为关、融合席位为 1）：

  | 配置项 | 本地 `config.ini` | 生产模板 `config-prod.ini` | 说明 |
  | :--- | :---: | :---: | :--- |
  | `graph_retrieval.enabled` | `true` | `false` | 图扩召回 + 与文本路融合 |
  | `query_rewrite_enabled` | `true` | `false` | 图辅助 canonical 改写；**须** `enabled=true` 才生效 |
  | `anchor_chunk_filter_enabled` | `true` | `false` | 锚点硬约束过滤 |
  | `anchor_graph_chunk_enabled` | `true` | `false` | allowlist 小开图 chunk |
  | `reranker.enabled` | `true` | `false` | Cross-Encoder 精排 |
  | `max_graph_only_slots` | **2** | 未写（代码默认 **1**） | 纯图路最多占几席；第 4 轮复测按 1 席 |

  `config-local.ini` 的图检索开关与本地 `config.ini` 一致（含 `max_graph_only_slots=2`）。Intent 评分（alias / `different_from`）**不依赖** `graph_retrieval.enabled`。

---

## 2. 已实现并上线的核心能力

### A. 文档解析与切块基石

- 多格式解析（PDF / Office / 文本 / Markdown / Excel）；章节硬边界 + 语义切块；表格/代码结构保护。
- `section_path` Canonical 投影；Chroma ↔ 文件索引一致性校验与受控重建。
- 0A–0C / 0F / 0G 已结清。**0D 表格治理、0E OCR/媒体仍待执行**。

### B. 知识图谱建设与抽取

- 产品主干 `product_relation_backbone.json` 边界；规则 + LLM 双通道；经验产品化早拒。
- 第 1–4 轮、实体消解、主干锚定、锚点过滤均已收口。
- **2026-08-11**：抽取路径本地化（示例学习 / 本地 Ollama）已结项。
- **2026-08-13**：二次开发图谱锚定与代码示例整改（J3 / Cookbook）Phase 0–3 PASS。

### C. 多策略检索与融合

- 同步 / 异步 / 流式统一走 `RetrievalStrategy`（Hybrid ± 可选 Rerank）。
- 图融合控噪：`max_graph_only_slots` + 保护文本 top1（代码默认 1 席；本地配置现为 2）。
- EvidencePack：`POST /admin/qa-debug`（cited / retrieved_uncited / gaps / conflicts）。
- 对话上下文 Phase 0–2 已落地（追问改写与表面 query）；**不是**跨会话用户偏好记忆。

### D. 对话 Agent 编排

- **V1.6 是当前唯一实施基线**：`SemanticTaskContext`、`IdentityScope`、`ExplorationGrant` 已进入代码，专项测试当前 **16/16 PASS**。
- V1.6 仍是**实施中**，不是已完成：旧 `EvidenceScope` / `admissible_entities` 兼容语义仍存在，Phase 6 清理与全量场景验收未结项。
- V1.3 / V1.4 / V1.5 已降级为历史演进文档，不得再作为现行版本。
- Agent 两阶段回答、`finalize_answer` 终止控制动作与 Main/Helper 模型路由改善已形成待执行 PRD；该 PRD 描述目标架构，不代表当前代码已经切换。

---

## 3. 现行待办与后续计划

### P0（生产开图阻断项）

1. **`quality --graph` 全绿**：补黄金边、清非法边后再议生产默认开图。
2. **生产默认开图决策**：须 quality 全绿 + 运维观察窗口；勿擅自改 `config-prod.ini`。

### P1（建设与治理）

3. **对话 Agent V1.6 收口**：完成旧 `EvidenceScope` / `admissible_entities` 兼容层退出、Phase 6 与全量场景验收。[V1.6 PRD](02_实施中/04_对话Agent与上下文/对话Agent检索编排_PRD_V1.6.md)。
3a. **Agent 两阶段回答与模型路由改善**：Agent Controller/Answer 统一使用 Main，Linear 轻量理解/改写使用 Helper，接入 `finalize_answer` 终止控制与 Evidence Gate。[改善 PRD](03_待执行/02_RAG检索与回答/2026-08-21-Agent两阶段回答与模型路由改善PRD.md)。
4. **反问指称唯一性与二次开发直达**（Phase 0 已落地，FR-7 未做）：点名合法锚零反问，废除「种子 ≥ 2 ⇒ 反问」。[08-14 PRD](02_实施中/04_对话Agent与上下文/2026-08-14-反问指称唯一性与二次开发直达整改PRD.md)。
5. **路径段类型独占与功能区短名展示**（待实施）：[08-14 PRD](03_待执行/03_知识图谱与GraphRAG/路径段类型独占/2026-08-14-路径段类型独占与功能区短名展示PRD.md)。
6. **四层架构叶子挂载**：功能区候选+人工确认、`mount_status`；禁止以 `endswith("表")` 作挂载主路径。[PRD](02_实施中/03_知识图谱与GraphRAG/2026-07-31-知识图谱四层架构与领域语义模型升级PRD.md)（双图切换等已部分落地）。
7. **legacy Profile 自动瘦身**。
8. **切块 0D / 0E**；全量 `--mode qa` 生成式批测（FR-10 是检索路径冻结，**08-07 10 题模型选型评测不能替代**）。

### P2（交付与运维）

9. 部署风险、Excel 复杂 fixture、前端布局剩余项（Dagre 已接，未用 G6）等见 [待办清单](01_当前有效/00_项目总览与架构/待办清单.md)。

未完成 PRD 集中在 [`3_待办清单/待执行/`](03_待执行)；切块 0D/0E、图谱 08-14 路径段、08-14 反问指称 FR-7（Phase 0 已落地）仍在各自专题的 `待执行-*` 目录。

---

## 快速导航

| 文档 | 用途 |
|------|------|
| [文档治理入口](00_文档治理/README.md) | 文档状态分类、目标目录与治理规则 |
| [文档状态总账](00_文档治理/文档状态总账.md) | 原始 293 个文件的全量归类与迁移映射 |
| [当前系统架构基线](01_当前有效/00_项目总览与架构/2026-08-20-当前系统架构基线.md) | 当前代码与 Trace 对齐的架构真源 |
| [待办清单.md](01_当前有效/00_项目总览与架构/待办清单.md) | 现行任务板 |
| [Agent V1.6 PRD](02_实施中/04_对话Agent与上下文/对话Agent检索编排_PRD_V1.6.md) | 当前 Agent 实施真源 |
| [Agent 两阶段回答与模型路由改善 PRD](03_待执行/02_RAG检索与回答/2026-08-21-Agent两阶段回答与模型路由改善PRD.md) | Agent/Linear 模型路由与回答阶段目标方案 |
| [Evidence Sufficiency Gate PRD](03_待执行/02_RAG检索与回答/2026-08-20-Evidence-Sufficiency-Gate与定向补检闭环PRD.md) | 生成前证据充分性与定向补检 |
| [知识图谱PRD剩余轮次总览](01_当前有效/03_知识图谱与GraphRAG/2026-07-13-知识图谱PRD剩余轮次总览.md) | 图谱轮次进度 |
| [切块基石治理 README](01_当前有效/01_文档解析与切块/README.md) | 切块结账（FR-10 冻结口径） |
| [PRD 现状版](05_历史过时/01_旧架构快照/AI智能体知识库系统-产品需求说明书(PRD)现状版.md) | **历史快照**：仅描述截至 2026-08-10 的产品形态 |
| [技术路线说明](05_历史过时/01_旧架构快照/AI智能体知识库系统-技术路线说明.md) | **历史快照**：08-20 后不得作为当前唯一架构真源 |
| [2_历史记录与总结/](04_已完成归档/00_项目总览与架构/历史研发记录) | 已完成快照（含 08-07 模型选型评测、08-11 对话上下文） |
| [5_操作指南与规范/](01_当前有效) | 操作规范（非待办） |
