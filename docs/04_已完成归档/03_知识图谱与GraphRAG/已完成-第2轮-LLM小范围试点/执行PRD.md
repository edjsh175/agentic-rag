# 知识图谱执行 PRD — 第 2 轮：LLM 小范围试点

- **记录日期**：2026-07-13
- **状态**：**已完成（试点抽取 + 分拆审批 + apply + GO）** — 2026-07-14
- **正式 batch**：`4f735904-07f9-471b-ace7-73cbf95f0171`
- **备份**：`data/backups/rag_relational_pre_round2.db`
- **Go/No-Go**：[试点结果/第2轮-Go判定.md](试点结果/第2轮-Go判定.md) → **GO**
- **轮次编号**：Round-2 / MVP-3B
- **母文档**：`../2026-07-09-知识图谱语义抽取升级整体计划与PRD.md`
- **前置条件**：**第 1 轮已完成并 apply**；规则层级边已在正式库
- **周期建议**：5–7 个工作日
- **是否启用 LLM**：**是（仅试点范围）**

### 实施纪要（2026-07-14）

```text
1. 试点：StampTools --include-llm --limit 80；llm_candidates=331
2. Ollama qwen3:30b 需 think=false，否则 content 为空/超时；timeout=180s
3. quality --llm：invalid_schema+missing_evidence≈11.6%；抽检 precision≈82.9%
4. 分拆审批后 apply：+64 entities / +14 relations；新增 Procedure/Step/ConfigItem/EnvironmentComponent/Error 等
5. Task 8.1 Gate PASS；stale_link=0；manual export summary 未减少
6. 结论 GO，可进入第 3 轮（仍禁止 LLM --approve-all）
```

---

## 1. 本轮要解决的问题

LLMGraphExtractor 代码已有，但正式库从未批量使用（`llm_candidates: 0`）。全库直接开 LLM 风险高。

本轮目标：**在一个可控范围内跑通「LLM 抽取 → 质量评估 → 分拆审批 → apply → audit 对比」完整闭环**，并产出可量化的精度报告，作为第 3 轮全量重建的 Go/No-Go 依据。

---

## 2. 试点范围

### 2.1 默认试点域

```text
doc_category = StampTools
```

可选备选：`StampServer`（若 StampTools chunk 数过少）。

### 2.2 规模上限

```text
首轮试点：--limit 80 chunks（或 StampTools 全部 approved chunk，取较小值）
禁止第一轮试点直接 --force-rebuild 全库
```

### 2.3 Ollama 前置

```text
config.ini [graph_extraction.llm]
  enabled = false          # 全局仍保持 false，只靠 CLI --include-llm
  provider = ollama
  model = qwen3:30b        # 与现网一致；可 A/B 记录模型名
  min_confidence = 0.60
```

---

## 3. 产品目标

### 3.1 必须达成

```text
1. 试点 batch 的 llm_candidates > 0
2. 所有 LLM 候选含 evidence_text / confidence / prompt_version / extractor_version
3. 完成分拆审批（禁止 approve-all）
4. 成功 apply 到正式库
5. 输出 LLM 质量报告（precision 抽样 + 统计指标）
6. 业务实体类型出现 Procedure / Step / Command / ConfigItem / EnvironmentComponent 等（至少 3 类非 Section/DataTable）
```

### 3.2 Go/No-Go 门槛（进入第 3 轮的条件）

| 指标 | 门槛 |
|------|------|
| 人工抽检 precision（实体+关系） | ≥ 70% |
| `invalid_schema` + `missing_evidence` 占比 | < 15% |
| `type_conflict` 未解决数 | 0 条进入 apply |
| apply 后 audit 无新增 stale links | 是 |
| 人工/seed/profile 事实 | 无丢失 |

**未达门槛**：在本轮内调 prompt / `min_confidence` / 试点 chunk 范围，**最多迭代 2 次**，仍不达标则暂停第 3 轮。

---

## 4. 任务清单

| 编号 | 任务 | 优先级 | 验收 |
|:---:|------|:---:|------|
| R2-0 | 试点前 audit + export-manual 备份 | P0 | 备份文件存在 |
| R2-1 | `extract --doc-category StampTools --include-llm --limit N` | P0 | llm_candidates > 0 |
| R2-2 | `quality --batch <id> --llm` 输出统计 | P0 | JSON 含 high/low confidence 分布 |
| R2-3 | 人工抽检 30 条候选（分层抽样） | P0 | 填写抽检表 |
| R2-4 | 分拆 review | P0 | entity/alias/relation 分批评审 |
| R2-5 | apply + post audit | P0 | entities/relations 增量合理 |
| R2-6 | 业务实体计数对比报告 | P1 | Command/Procedure 等 ≥ 母 PRD 试点比例 |
| R2-7 | 记录 prompt/模型/阈值配置快照 | P1 | 写入报告附录 |

---

## 5. 实施步骤

```powershell
# Step 0 备份
.\venv\Scripts\python.exe run_graph_build.py export-manual --output data/manual_graph_facts_pre_round2.json
.\venv\Scripts\python.exe run_graph_build.py audit --output-json data/graph_audit_pre_round2.json

# Step 1 试点抽取（停止后端）
.\venv\Scripts\python.exe run_graph_build.py extract `
  --doc-category StampTools `
  --include-llm `
  --limit 80

# Step 2 质量评估
.\venv\Scripts\python.exe run_graph_build.py quality --batch <BATCH_ID> --llm --profile full

# Step 3 审批摘要
.\venv\Scripts\python.exe run_graph_build.py review --batch <BATCH_ID> --summary

# Step 4 分拆审批（示例）
.\venv\Scripts\python.exe run_graph_build.py review --batch <BATCH_ID> `
  --approve-kind entity --approve-confidence-above 0.85
.\venv\Scripts\python.exe run_graph_build.py review --batch <BATCH_ID> `
  --approve-relation-type defined_in --approve-confidence-above 0.80
# alias / different_from 逐条或更高阈值

# Step 5 apply
.\venv\Scripts\python.exe run_graph_build.py apply --batch <BATCH_ID> `
  --confirm-db-path data/rag_relational.db `
  --confirm-batch <BATCH_ID> `
  --confirm-backup data/backups/rag_relational_pre_round2.db

# Step 6 验收
.\venv\Scripts\python.exe run_graph_build.py audit --output-json data/graph_audit_post_round2.json
.\venv\Scripts\python.exe run_graph_build.py quality --graph
```

---

## 6. 人工抽检表（模板）

在 `试点结果/` 目录留存 `人工抽检样本.csv`：

| candidate_id | kind | name/relation | confidence | evidence 是否真实 | 类型是否正确 | 备注 |
|---|---|---|---|:---:|:---:|---|
| | entity | | | | | |
| | relation | | | | | |

抽样规则：

```text
- 高置信 (≥0.9)：10 条
- 中置信 (0.6–0.9)：15 条
- diagnostic：5 条
- 关系边：10 条
```

---

## 7. 验收标准

```text
✅ llm_candidates > 0 且 created_by = llm:schema_extractor
✅ 分拆审批完成，无 approve-all
✅ apply 成功，batch status = applied
✅ precision ≥ 70%（人工抽检）
✅ Procedure/Step/Command/ConfigItem/EnvironmentComponent 至少 3 类有新增 approved 实体
✅ export-manual 对比：manual/seed/admin 来源计数不减少
✅ pytest 相关用例全绿
```

---

## 8. 本轮不做

```text
1. 不全库 --force-rebuild
2. 不实现 rebuild-safe 正式版
3. 不调 GraphRAG 参数
4. 不开发审核前端
```

---

## 9. 交付物

```text
1. data/archive/graph_rounds/graph_audit_pre_round2.json / post_round2.json（2026-07-20 起归档；命令历史仍写 data/ 根）
2. 试点结果/人工抽检样本.csv
3. 试点结果/第2轮-Go判定.md（Go/No-Go 结论）
4. 试点 batch export：run_graph_build.py export --batch <id> --output ...
```

---

## 10. 给 Codex 的执行提示

```text
第 1 轮已完成的前提下，按本目录 `执行PRD.md` 执行：
1. 仅对 StampTools doc_category 做 --include-llm 试点抽取
2. 输出 --llm quality 报告
3. 编写分拆 review 命令示例（禁止 approve-all）
4. 在 isolated_storage 测试中 mock LLM；正式库操作只写文档步骤，由人工确认后执行
```
