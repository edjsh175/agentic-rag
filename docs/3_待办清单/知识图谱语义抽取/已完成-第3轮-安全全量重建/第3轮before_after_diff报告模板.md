# 第 3 轮 before/after diff 报告模板

- **记录日期**：待填
- **before 来源（历史 dry-run）**：`data/archive/rebuild_reports/rebuild_safe_dry_run_pre_round3.json`；若重跑 dry-run / execute，以当次报告内 `audit_before` 为准
- **after 来源（尚未生成）**：`data/rebuild_safe_execute_round3.json`、`data/graph_audit_post_round3.json`（生成后迁入 `data/archive/`）
- **前置条件**：`rebuild-safe --execute --include-llm` **完成且报告落盘**（2026-07-20 现场尚未满足，见 [第3轮执行验收记录.md](./第3轮执行验收记录.md)）

## 1. 结论摘要

| 项 | 结果 | 说明 |
|------|------|------|
| 是否通过第 3 轮验收 | 待填 |  |
| entities 变化 | 待填 |  |
| relations 变化 | 待填 |  |
| Section 占比变化 | 待填 |  |
| stale_link_count | 待填 | 目标为 0 |
| 主干完整率 | 待填 | 目标 40/40 |
| Task 8.1 gate | 待填 | 目标 PASS |

## 2. 总量对比

| 指标 | before | after | Δ | 结论 |
|------|---:|---:|---:|------|
| entities | 1230 | 待填 | 待填 | 待填 |
| relations | 2737 | 待填 | 待填 | 待填 |
| approved chunks | 3678 | 3678 | 0 | 待确认 |
| stale_link_count | 待填 | 待填 | 待填 | 待填 |

## 3. 实体类型对比

| entity_type | before | after | Δ | 业务解释 |
|-------------|---:|---:|---:|----------|
| Section | 待填 | 待填 | 待填 | 是否降低 Section-heavy |
| EnvironmentComponent | 待填 | 待填 | 待填 | 是否达到 ≥ 10 |
| Command | 待填 | 待填 | 待填 | 是否达到 ≥ 30 |
| Procedure | 待填 | 待填 | 待填 | 是否达到 ≥ 10 |
| Step | 待填 | 待填 | 待填 | 是否达到 ≥ 50 |
| ConfigItem | 待填 | 待填 | 待填 | 是否明显高于重建前 |

## 4. 关系类型对比

| relation_type | before | after | Δ | 业务解释 |
|---------------|---:|---:|---:|----------|
| has_section | 待填 | 待填 | 待填 | 章节层级是否稳定 |
| depends_on | 待填 | 待填 | 待填 | 依赖关系是否增强 |
| has_step | 待填 | 待填 | 待填 | 流程步骤是否增强 |
| configures | 待填 | 待填 | 待填 | 配置关系是否增强 |
| relates_to | 待填 | 待填 | 待填 | 泛化关系是否可控 |

## 5. 受保护事实

| source | before | after | 结果 | 备注 |
|--------|---:|---:|:---:|------|
| `seed:product_backbone` 实体 | 36 | 待填 | 待填 |  |
| `seed:product_backbone` 关系 | 40 | 待填 | 待填 |  |
| `rule:profile_sync` | 待填 | 待填 | 待填 |  |
| `rule:special*` | 待填 | 待填 | 待填 |  |
| manual/admin/seed* | 待填 | 待填 | 待填 |  |

## 6. §10 指标结论

| 指标 | 目标 | after | 结果 |
|------|------|------:|:---:|
| stale_link_count | 0 | 待填 | 待填 |
| Section 实体占比 | 较第 1 轮基线下降 ≥ 20% | 待填 | 待填 |
| EnvironmentComponent | ≥ 10 | 待填 | 待填 |
| Command | ≥ 30 | 待填 | 待填 |
| Procedure | ≥ 10 | 待填 | 待填 |
| Step | ≥ 50 | 待填 | 待填 |
| ConfigItem | 明显高于重建前 | 待填 | 待填 |
| 业务实体 evidence_text | 新增自动事实 100% 有值 | 待填 | 待填 |
| missing_evidence | 不新增；存量有治理方案 | 待填 | 待填 |
| 主干关系完整率 | 40/40 | 待填 | 待填 |

## 7. 结论与后续

待填：是否进入第 4 轮 GraphRAG 实效验收；若不进入，列出阻塞项。
