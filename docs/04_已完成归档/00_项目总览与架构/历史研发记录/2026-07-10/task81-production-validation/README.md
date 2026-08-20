# Task 8.1 / 8.2 正式库验收留档

- **验收日期**：2026-07-10
- **结论**：正式 Graph Profile 事实已 apply；专项 Gate **PASS**
- **主报告**：[2026-07-10-正式库验收报告.md](2026-07-10-正式库验收报告.md)
- **后续（2026-07-28）**：文中「104 条 missing_evidence」例外已清零；图谱第 1–4 轮已收口。现行进度见 [待办清单](../待办清单.md) / [第4轮阶段总结](../知识图谱语义抽取/已完成-第4轮-GraphRAG实效验收/2026-07-27-阶段总结.md)。

## 文件索引

| 文件 | 说明 |
|---|---|
| [2026-07-10-正式库验收报告.md](2026-07-10-正式库验收报告.md) | 完整验收报告（Gate → dry-run → apply → 复验） |
| [phase6-pre-apply-summary.md](phase6-pre-apply-summary.md) | apply 前检查快照（batch 审批清单） |
| [phase8-post-apply-summary.md](phase8-post-apply-summary.md) | apply 后复验快照 |
| `dry-run-pipeline_point_table.json` | 阶段 2 点表 dry-run 输出 |
| `dry-run-pipeline_line_table.json` | 阶段 2 线表 dry-run 输出 |
| `dry-run-pipeline_face_table.json` | 阶段 2 面表 dry-run 输出 |
| `dry-run-dom_builder_publish.json` | 阶段 2 DOMBuilder dry-run 输出 |

## 关键标识

| 项 | 值 |
|---|---|
| batch_id | `e8267357-e5d2-41e8-9848-b37383be7b1f` |
| batch mode | `profile_sync` |
| 备份 | `backups/rag_relational-pre-task81-apply-20260710-151353.db` |
| 备份 SHA256 | `A8D8C0B8C60D36A5131A8EF8399529B3915A9727D1EC2549E8A2950260893F7A` |
| 候选导出（未纳入 git） | `data/archive/task81/task81_profile_sync_candidates.json` |

## 复验命令

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\venv\Scripts\python.exe scripts\validate_task81_graph_gate.py --json
.\venv\Scripts\python.exe sync_profiles_to_graph.py --dry-run --json
.\venv\Scripts\python.exe -m pytest -q tests/test_graph_intent_scoring.py tests/test_intent_scoring_equivalence.py tests/test_profile_graph_sync_production.py tests/test_task81_graph_gate.py
```

## 关联文档

- 治理规范：`docs/5_操作指南与规范/retrieval_intent_profile治理规范.md` §8
- 待办收口：`docs/3_待办清单/待办清单.md` §二.9 / §二.10
