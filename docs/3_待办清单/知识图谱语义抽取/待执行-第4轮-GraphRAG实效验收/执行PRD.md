# 知识图谱执行 PRD — 第 4 轮：GraphRAG 实效验收与交付收口

- **记录日期**：2026-07-13
- **轮次编号**：Round-4 / MVP-4
- **母文档**：`../2026-07-09-知识图谱语义抽取升级整体计划与PRD.md`
- **前置条件**：第 3 轮全量重建完成；§10 指标大部分达标
- **周期建议**：4–6 个工作日
- **是否启用 LLM**：图谱构建已完成；本轮侧重**问答侧验证**

---

## 1. 本轮要解决的问题

GraphRAG 代码已接入（`GraphRetriever`、`GraphFusionScorer`、`graph_intent_scoring`），但母 PRD §13 第 6 条仍未验证：

```text
查询依赖、配置、流程、步骤、错误解决时，图谱能实际增强 RAG
```

本轮目标：**用可重复的评测证明 Graph 增强有效**，并完成 PRD 交付收口（含历史债与文档同步）。

---

## 2. 产品目标

### 2.1 必须达成

```text
1. GraphRAG 专项问答集（≥ 20 题）及 A/B 报告（graph on vs off）
2. 至少在 2 类意图上 Recall@3 或 MRR 显著优于无图基线（≥ 5pp 或 p<0.05 人工判定）
3. 回答链路可观测：日志含 linked entities / graph chunks / fallback_reason
4. 全图 quality gate 通过，Task 8.1 Gate PASS
5. 母 PRD §13 六条成功标准逐条勾选
6. CLAUDE.md / 待办清单 同步最终状态
```

### 2.2 可选（P2）

```text
1. API 响应增加 graph_trace 字段（实体链/关系 id 列表），供前端展示
2. legacy retrieval_intent_profiles_v1.json 自动瘦身脚本
3. 管线面表 Phase B 剩余 diagnostic 清零
```

---

## 3. 评测设计

### 3.1 专项问题集

新建：`data/eval_graph_rag_dataset.json`

| 意图 | 题数 | 示例 |
|------|:---:|------|
| definition | 5 | 管线点表有哪些字段？ |
| config | 5 | StampTools 使用哪些配置项？ |
| procedure | 4 | PipelineBuilder 发布流程步骤？ |
| deployment | 3 | 服务依赖哪些组件？ |
| troubleshooting | 3 | 某错误如何解决？ |

每题标注：

```json
{
  "question": "...",
  "intent": "config",
  "relevant_chunk_ids": ["..."],
  "linked_entity_names": ["PipelineBuilder"],
  "kb_name": "文章附件",
  "doc_category": "StampTools"
}
```

### 3.2 A/B 配置

| 组 | config |
|----|--------|
| A 对照 | `[graph_retrieval] enabled = false` |
| B 实验 | `[graph_retrieval] enabled = true`，其余与生产一致 |

固定：`retrieval_strategy.method = hybrid`，`reranker.enabled` 与生产一致。

### 3.3 指标

```text
Recall@3, MRR, Hit Rate（与 evaluation/metrics.py 一致）
额外：graph_fallback_rate（fallback_reason != none 的比例）
额外：linked_entity_hit_rate（问题中实体是否成功链接）
```

### 3.4 实现入口

扩展 `rag_knowledge/evaluation/runner.py` 或新增 `scripts/run_graph_rag_eval.py`（推荐脚本，避免过度抽象）。

---

## 4. 任务清单

| 编号 | 任务 | 优先级 | 验收 |
|:---:|------|:---:|------|
| R4-0 | 确认第 3 轮 §10 指标表 | P0 | 附件齐全 |
| R4-1 | 构建 eval_graph_rag_dataset.json | P0 | ≥ 20 题 |
| R4-2 | GraphRAG A/B 脚本 | P0 | 输出 JSON 报告 |
| R4-3 | 分析 2 类意图提升 | P0 | 结论写入报告 |
| R4-4 | 日志/可观测性补齐 | P1 | rag.log 可 grep graph_retrieval |
| R4-5 | quality --graph + task81 gate | P0 | 均 PASS |
| R4-6 | missing_evidence 存量收尾 | P1 | 方案执行或登记遗留 |
| R4-7 | §13 成功标准勾选表 | P0 | 6/6 或标注例外 |
| R4-8 | 文档收口 | P0 | CLAUDE.md、待办清单更新 |
| R4-9 | check_repo_hygiene + 全量 pytest | P0 | exit 0 |

---

## 5. 实施步骤

```powershell
# 1. 构建评测集（可半自动从现有 chunk 采样）
.\venv\Scripts\python.exe scripts/build_graph_rag_eval_set.py  # 实施时创建

# 2. A/B 运行
$env:GRAPH_RETRIEVAL_ENABLED="false"
.\venv\Scripts\python.exe scripts/run_graph_rag_eval.py --output data/eval_graph_rag_baseline.json

$env:GRAPH_RETRIEVAL_ENABLED="true"
.\venv\Scripts\python.exe scripts/run_graph_rag_eval.py --output data/eval_graph_rag_with_graph.json

# 3. 对比报告
.\venv\Scripts\python.exe scripts/compare_graph_rag_eval.py `
  --baseline data/eval_graph_rag_baseline.json `
  --treatment data/eval_graph_rag_with_graph.json `
  --output docs/3_待办清单/graph-round4-ab-report.md

# 4. 门禁
.\venv\Scripts\python.exe run_graph_build.py quality --graph
.\venv\Scripts\python.exe scripts/validate_task81_graph_gate.py --json
.\venv\Scripts\python.exe scripts/check_repo_hygiene.py
.\venv\Scripts\python.exe -m pytest -q
```

---

## 6. 验收标准

### 6.1 GraphRAG 实效

```text
✅ ≥ 20 题专项集
✅ graph on 组在 definition + config（或 procedure）至少 2 类意图上优于 off 组
✅ graph_fallback_rate < 40%（过高说明链接/扩展失败多）
✅ 无「图谱扩展引入错误 chunk」的人工抽检案例（抽检 10 题）
```

### 6.2 母 PRD §13 勾选

| # | 标准 | 验收方式 |
|---|------|----------|
| 1 | 跨文档抽业务实体 | 第 3 轮 §10 表 |
| 2 | LLM 可控可审计可回滚 | rebuild 报告 + 备份 |
| 3 | 人工事实保留 | export-manual diff |
| 4 | stale links 可清理 | quality gate |
| 5 | 非 Section-heavy | audit 占比 |
| 6 | 图谱增强 RAG | 本轮 A/B 报告 |

### 6.3 交付门禁

```text
✅ check_repo_hygiene.py exit 0
✅ pytest 全绿
✅ Docker 生产 config：graph_extraction.llm.enabled 策略已文档化
```

---

## 7. 生产部署建议（本轮输出文档即可）

```ini
# config-prod.ini 建议
[graph_retrieval]
enabled = true

[graph_extraction.llm]
enabled = false   # 构建在维护窗口用 CLI --include-llm 执行，非常驻开启
```

---

## 8. 本轮不做

```text
1. 完整图谱可视化前端
2. Graph-aware Rerank 新模型
3. Neo4j
4. 全库定时自动 LLM 抽取（仅文档化运维 SOP）
```

---

## 9. 交付物

```text
1. data/eval_graph_rag_dataset.json
2. data/eval_graph_rag_baseline.json / with_graph.json
3. docs/3_待办清单/graph-round4-ab-report.md
4. docs/3_待办清单/graph-round4-section13_checklist.md
5. docs/3_待办清单/2026-07-13-知识图谱PRD剩余轮次总览.md 状态更新为「四轮已完成」
```

---

## 10. 给 Codex 的执行提示

```text
在第 3 轮全量重建验收通过后，按本目录 `执行PRD.md` 执行：
1. 新建 graph rag 评测集与 A/B 脚本
2. 跑对比并写 markdown 报告
3. 产出 §13 勾选表
4. 更新 CLAUDE.md 中图谱章节为「PRD 四轮收口完成」
```

---

## 11. PRD 整体完成定义

当且仅当以下条件全部满足，母 PRD 视为**实施完成**：

```text
1. 四轮执行 PRD 均标记已完成
2. §10 指标 ≥ 6/8 达标，其余有书面例外
3. §13 六条全部勾选或例外已审批
4. GraphRAG A/B 证明至少 2 类意图有提升
5. 生产部署文档更新
```
