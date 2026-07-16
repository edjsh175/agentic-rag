# GraphRAG §13 成功标准勾选表

- **记录日期**：待填
- **适用范围**：知识图谱语义抽取母 PRD §13 收口
- **前置条件**：第 3 轮安全全量重建完成并通过核心门禁

| # | 成功标准 | 验收材料 | 结果 | 备注 |
|---:|------|----------|:---:|------|
| 1 | 跨文档抽取业务实体 | 第 3 轮 §10 指标表、audit after | 待填 | 业务实体数量与类型分布改善 |
| 2 | LLM 构建可控、可审计、可回滚 | `rebuild_safe_execute_round3.md`、timestamped 备份、执行日志 | 待填 | 包含备份、审批、apply、audit 链路 |
| 3 | 人工/seed/profile 事实保留 | export-manual diff、保留集统计、Task 8.1 gate | 待填 | `manual/admin/seed*/rule:special*/profile_sync` |
| 4 | stale links 可清理 | `quality --graph --profile full`、post audit | 待填 | stale_link_count 应为 0 |
| 5 | 图谱不再 Section-heavy | before/after audit diff、§10 表 | 待填 | Section 占比下降，业务类型增加 |
| 6 | 图谱增强 RAG | GraphRAG A/B 验收报告 | 待填 | 至少 2 类意图 Recall@3 或 MRR 提升 |

## 例外记录

| 标准 | 例外原因 | 风险 | 后续动作 | 负责人/日期 |
|------|----------|------|----------|-------------|
| 待填 | 待填 | 待填 | 待填 | 待填 |

## 收口结论

待填：6/6 通过，或列出已审批例外后收口。
