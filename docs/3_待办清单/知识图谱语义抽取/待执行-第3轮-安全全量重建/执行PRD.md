# 知识图谱执行 PRD — 第 3 轮：安全全量重建

- **记录日期**：2026-07-13
- **状态**：**工程就绪 / chunk 治理阻塞** — dry-run 已通过；chunk 修复后再 execute
- **轮次编号**：Round-3 / MVP-3C
- **母文档**：`../2026-07-09-知识图谱语义抽取升级整体计划与PRD.md`
- **前置条件**（全部满足方可开工）：
  1. 第 2 轮 Go/No-Go 为 **Go**
  2. **第 2.5 轮产品关系主干已 apply**（batch `def72329-d322-467a-b111-bc455a8529a7`，`seed:product_backbone`）
  3. `data/product_relation_backbone.json` 非空且与已 apply 事实一致（抽检 40/40，2026-07-14）
- **周期建议**：5–8 个工作日
- **是否启用 LLM**：**是（全库，与规则合并，且必须服从主干边界）**

> **2026-07-14 口径**：本轮不是「全靠 LLM 冲业务实体」，而是在**官方产品关系主干**已入库的前提下，用规则 + 受限 LLM 做可回滚全量补全。

---

## 1. 本轮要解决的问题

当前能力缺口：

```text
❌ rebuild-safe 仅有 dry-run，不能正式替换自动事实
❌ 正式库未在「主干边界内」做过 rules + LLM 全量重建
❌ §10 指标未验收：Section 占比高、Command/Procedure 等数量不足
❌ missing_evidence 历史债未系统治理
```

本轮目标：**可审计、可回滚的全量图谱重建**，在保留官方主干与人工/seed/profile 事实的前提下，使正式库接近母 PRD §10 MVP 验收指标。

---

## 2. 产品目标

### 2.1 必须达成

```text
1. 实现 rebuild-safe 正式版（非 dry-run）：备份 → 抽取 → 审批 → 替换自动事实 → 恢复/保留受保护事实
2. 全库 extract --force-rebuild --include-llm（在主干边界约束下）
3. before/after audit diff 报告
4. 业务实体数量显著上升，Section 占比下降（相对第 1 轮后基线）
5. stale_link_count = 0
6. 受保护事实保留：manual / admin / seed（含 seed:product_backbone）/ rule:special / profile_sync 策略内事实
7. 无与 product_relation_backbone 矛盾的边进入 apply
```

### 2.2 §10 指标目标（全库重建后）

| 指标 | 目标 |
|------|------|
| stale_link_count | 0 |
| Section 实体占比 | 较第 1 轮基线下降 ≥ 20% |
| EnvironmentComponent | ≥ 10 |
| Command | ≥ 30 |
| Procedure | ≥ 10 |
| Step | ≥ 50 |
| ConfigItem | 明显高于重建前 |
| 业务实体 evidence_text | 100% 有值（新增自动事实部分） |
| missing_evidence（历史 apply 记录） | 不新增；既有条目出治理方案 |
| 主干关系完整率 | 第 2.5 轮已 apply 的 backbone 边 100% 仍在 |

> 绝对数量可按 chunk 总数比例微调，**重点是结构从 Section-heavy 转向 Business-entity-aware，且不破坏官方主干**。

---

## 3. 技术方案

### 3.0 主干边界（强制）

```text
1. rebuild-safe Phase D 保留集合必须包含：
   seed:product_backbone（及既有 manual/admin/seed/rule:special，以及 Task 8.1 profile 保护策略）
2. 规则/LLM 候选若与主干矛盾（改归属、否定 different_from、同名异型未解决）→ 不得 apply
3. LLM 优先在主干实体邻域补 Procedure / Step / Command / ConfigItem / EnvironmentComponent 等叶子
4. 含 LLM 的 batch 禁止 --approve-all
5. 开工前核对：product_relation_backbone.json 与正式库 seed 边一致
```

### 3.1 SafeRebuildService 正式版

**新建/扩展**：`rag_knowledge/services/safe_rebuild.py`

在 `SafeRebuildDryRunService` 基础上增加 `SafeRebuildService.run()`：

```text
Phase A  备份 rag_relational.db → data/backups/rag_relational_<timestamp>.db
Phase B  export-manual → manual_graph_facts_pre_rebuild.json
Phase C  audit_before（含主干边抽检清单）
Phase D  标记待替换的自动事实（created_by 为 rule:* / llm:*；
         不含 manual/admin/seed*/rule:special；seed:product_backbone 不可替换；
         profile_sync 按既有保护策略排除）
Phase E  执行全量 extract --force-rebuild --include-llm
Phase F  分拆 review（可按 kind / confidence 多轮）；拒收与主干冲突候选
Phase G  apply 新 batch（带 confirm 参数）
Phase H  恢复/合并 manual_graph_facts（若 apply 未自动保留）
Phase I  cleanup-stale-links
Phase J  audit_after + diff 报告 + 主干完整率核对
```

**CLI**：`run_graph_build.py rebuild-safe --execute`（与 `--dry-run` 互斥）

### 3.2 审批策略（全库）

```text
禁止 --approve-all（尤其含 LLM 候选时）

建议顺序：
1. --approve-kind entity --approve-confidence-above 0.88
2. --approve-kind alias --approve-confidence-above 0.90
3. 按 relation_type 分批，阈值 ≥ 0.80；与主干冲突的 relation 一律 reject
4. 剩余 pending 人工 export 后逐条处理或 reject
5. field / link 最后审批
```

### 3.3 missing_evidence 历史债

```text
方案 A（推荐）：单独 batch 标记历史 relation/entity 为 superseded，不物理删除；新 apply 覆盖同名自动事实
方案 B：repair 脚本为缺 evidence 的自动事实补 evidence 或 reject
禁止：一刀切 DELETE 全部 relations
禁止：借治理之机删除 seed:product_backbone
```

### 3.4 Profile 与主干事实保护

- Task 8.1 已 apply 的 profile_sync 事实须在 Phase D 排除在「可替换自动事实」之外。
- 第 2.5 轮 `seed:product_backbone` 同样排除；Gate 与主干抽检均须在 apply 后复验。

---

## 4. 任务清单

| 编号 | 任务 | 优先级 | 验收 |
|:---:|------|:---:|------|
| R3-0 | 确认第 2.5 轮已完成 + 主干抽检 | P0 | backbone 边 100% 可查 |
| R3-1 | 全库 rebuild 前 dry-run 报告 | P0 | rebuild_safe_dry_run_report.json |
| R3-2 | SafeRebuildService 正式版（含 seed:product_backbone 保留） | P0 | 单元测试 + 失败回滚测试 |
| R3-3 | `rebuild-safe --execute` CLI | P0 | 文档化命令 |
| R3-4 | 全库 extract + LLM（边界约束） | P0 | rule+llm candidates 均 > 0 |
| R3-5 | 分拆审批全流程 | P0 | 无 approve-all；无主干冲突入 apply |
| R3-6 | apply + cleanup-stale-links | P0 | quality --graph 无 BLOCK |
| R3-7 | before/after audit diff | P0 | MD 报告 |
| R3-8 | §10 指标核对表 | P0 | 填数并标注未达标项 |
| R3-9 | missing_evidence 治理 | P1 | 不新增 + 存量方案 |

---

## 5. 实施步骤

```powershell
# 0. 停止所有后端 / 评估进程
# 0.1 确认第 2.5 轮已完成（否则停止）

# 1. Dry-run（必须）
.\venv\Scripts\python.exe run_graph_build.py rebuild-safe --dry-run `
  --output-json data/rebuild_safe_dry_run_pre_round3.json `
  --output-md data/rebuild_safe_dry_run_pre_round3.md

# 2. 人工确认 preserved 含 seed:product_backbone / superseded 来源统计

# 3. 正式重建（实现 R3-2 后）
.\venv\Scripts\python.exe run_graph_build.py rebuild-safe --execute `
  --include-llm `
  --output-json data/rebuild_safe_execute_round3.json `
  --output-md data/rebuild_safe_execute_round3.md

# 若 --execute 未一键封装，则分步：
.\venv\Scripts\python.exe run_graph_build.py extract --force-rebuild --include-llm
# ... review 分批；拒收与主干冲突 ...
.\venv\Scripts\python.exe run_graph_build.py apply --batch <ID> --confirm-...
.\venv\Scripts\python.exe run_graph_build.py cleanup-stale-links

# 4. 验收
.\venv\Scripts\python.exe run_graph_build.py audit --output-json data/graph_audit_post_round3.json
.\venv\Scripts\python.exe run_graph_build.py quality --graph --profile full
.\venv\Scripts\python.exe scripts/validate_task81_graph_gate.py --json
# 另：按 product_relation_backbone.json 抽检主干边完整率
```

---

## 6. 验收标准

```text
✅ 第 2.5 轮前置已满足
✅ rebuild-safe --execute 可回滚（备份可恢复）
✅ audit diff 显示业务实体上升、Section 占比下降
✅ 主干关系完整率 100%
✅ §10 指标表已填写，至少 6/8 项达标
✅ Task 8.1 Gate 仍为 PASS
✅ stale_link_count = 0
✅ pytest 全绿（含新 safe_rebuild 测试）
✅ SVN/Git 交付清单可引用本轮 audit 报告
```

---

## 7. 风险与回滚

| 风险 | 对策 |
|------|------|
| 全库 LLM 成本/时间过长 | 分批 `--doc-category` extract 再合并 batch（实现可选） |
| apply 中途失败 | 事务性 apply 已有；失败时从备份恢复 SQLite |
| 业务实体误合并 | EntityResolution type_conflict 一律不进 apply |
| Profile / 主干被覆盖 | Phase D 白名单保护 + apply 后 Gate + 主干抽检 |
| LLM 发明错误归属 | 与 backbone 冲突的候选强制 reject |

**回滚 SOP**：

```powershell
# 停止服务 → 复制备份覆盖 data/rag_relational.db → 重启 → audit 对比
```

---

## 8. 本轮不做

```text
1. GraphRAG A/B（第 4 轮）
2. 前端审核台
3. Neo4j
4. legacy Profile JSON 自动瘦身（可记录为第 4 轮 P2）
5. 在缺少第 2.5 轮主干的情况下强行全库 LLM
```

---

## 9. 交付物

```text
1. SafeRebuildService 正式版 + 测试
2. data/rebuild_safe_execute_round3.json / .md
3. data/graph_audit_diff_round3.md（before/after 对比）
4. docs/3_待办清单/graph-round3-full-rebuild/section10_metrics.md
5. 主干完整率抽检记录（可附在 diff 报告）
```

---

## 10. 给 Codex 的执行提示

```text
仅在第 2.5 轮产品关系主干已 apply 后，按本 PRD：
1. 先实现 SafeRebuildService 正式版与 --execute CLI，保留 seed:product_backbone
2. dry-run 通过后再写正式库操作
3. 全库 rebuild 必须 --include-llm + 分拆审批 + 主干冲突拒收
4. 产出 §10 指标核对表与主干完整率核对
```
