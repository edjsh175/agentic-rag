# Task 8.1 Profile Sync — apply 后复验摘要

> 历史快照。完整报告见 [2026-07-10-正式库验收报告.md](./2026-07-10-正式库验收报告.md)。

生成时间：2026-07-10 15:48 (UTC+8)
## 阶段 7：apply

- batch_id：`e8267357-e5d2-41e8-9848-b37383be7b1f`
- status：`applied`
- 备份：`backups/rag_relational-pre-task81-apply-20260710-151353.db`

| 指标 | before | after | Δ |
|---|---:|---:|---:|
| entities | 702 | 704 | +2 |
| relations | 1615 | 1623 | +8 |
| aliases | 1 | 4 | +3 |
| entity_chunk_links | 2204 | 2204 | 0 |

写入：entity 2 / alias 3 / relation 8（与审批一致）

## 阶段 8：专项复验

### Gate

- `verdict`：**PASS**
- `issues`：[]

### dry-run

- 四 profile 均无 actionable entity / alias / relation
- 仅剩已知 diagnostic：`generic_recall_term`、`missing_section_entity`（管线面表 Section）

### 专项测试

```
17 passed (test_graph_intent_scoring / intent_scoring_equivalence / profile_graph_sync_production / task81_graph_gate)
```

### 全量测试

```
471 passed, 2 failed, 6 deselected
```

失败项与本次 apply **无关**（isolated CLI review 计数断言）：

- `tests/test_graph_extraction.py::test_graph_build_cli_review_reports_invalid_candidate_ids`
- `tests/test_graph_quality_cli.py::test_review_reports_requested_selected_and_safety_rejected`

## 阶段 9：全图质量

- `ok`：false（历史债）
- `missing_evidence_count`：**104**（未新增）
- `invalid_schema_count`：0
- `type_conflict_unresolved_count`：0
- `stale_link_count`：0
- `total_entities`：704
- `total_relations`：1623

## 交付状态表述

- **Task 8.1**：代码完成，正式 Graph 事实已 apply 并通过专项 Gate
- **Task 8.2**：正式 Graph Schema 兼容与 Profile Migration 修复完成
- **全图质量**：仍有 104 条 Phase B 历史 missing_evidence，另行治理

## 已知残留

- 管线面表无 Phase B `defined_in` Section（`PipelineBuilder > 数据规范 > 管线面表`）
- 全量 pytest 有 2 项 CLI review 测试失败（交付前可单独修复，不影响 Task 8.1 专项验收）
