# GraphRAG §13 成功标准勾选表

- **记录日期**：2026-07-27
- **适用范围**：知识图谱语义抽取母 PRD §13 收口
- **前置条件**：第 3 轮规则路径 + 第 3 轮补实 LLM 已完成；第 4 轮检索 A/B 已跑

| # | 成功标准 | 验收材料 | 结果 | 备注 |
|---:|------|----------|:---:|------|
| 1 | 跨文档抽取业务实体 | 第 3 轮补纪要；正式库 entities 1375 / 业务实体约 626 | ✅ | Command/Procedure/Step/ConfigItem 等有可解释增量 |
| 2 | LLM 构建可控、可审计、可回滚 | Round3 execute 报告；R5–R7 备份；分拆审批禁止 approve-all | ✅ | 按类目 CLI `--include-llm`；有 timestamped 备份 |
| 3 | 人工/seed/profile 事实保留 | Task 8.1 Gate PASS；主干 seed 仍在 | ✅ | `validate_task81_graph_gate.py --json` → PASS |
| 4 | stale links 可清理 | `quality --graph` stats `stale_link_count=0` | ✅ | stale=0；另有历史 missing_evidence 等（见例外） |
| 5 | 图谱不再 Section-heavy | section_ratio≈0.51；业务实体 626 | ⚠ 例外 | Section 仍过半；业务叶子已明显增加，未达「不再 heavy」字面，登记例外 |
| 6 | 图谱增强 RAG | [GraphRAG_A_B验收报告.md](GraphRAG_A_B验收报告.md) | ✅ | 3 类意图 ≥+5pp；fallback 15%；**生产默认仍关** |

## 例外记录

| 标准 | 例外原因 | 风险 | 后续动作 | 负责人/日期 |
|------|----------|------|----------|-------------|
| 4（附属） | `quality --graph` 仍报 missing_golden_relation / missing_evidence / 1 illegal | 全库开图可能扩到弱证据实体 | 不默认生产 on；择机补金边/清非法边 | 2026-07-27 |
| 5 | Section 占比约 51% | 结构边偏多 | 继续按类目补叶子；不阻断 §13-6 | 2026-07-27 |

## 收口结论

**6 条中 5 条通过，1 条（§5 Section-heavy）书面例外**；§6 检索 A/B 已证明图谱可增强 RAG。  
**母 PRD 实施完成口径**：建设与实效验证收口；**生产默认 `graph_retrieval.enabled` 仍建议 false**，直到证据债与回退噪声可控。
