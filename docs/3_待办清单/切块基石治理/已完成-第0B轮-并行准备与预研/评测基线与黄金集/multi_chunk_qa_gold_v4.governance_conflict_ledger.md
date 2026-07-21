# 治理冲突题台账草稿（待人工确认）

- **状态**：`draft_pending_human`
- **台账 JSON**：`multi_chunk_qa_gold_v4.governance_conflict_ledger.json`
- **依据报告**：`fr10_live_2537_v4_governance_qa_report.json`（冲突 5/10 通过）

确认前**不得**修改 `multi_chunk_qa_gold_v4_governance_candidate.json` 或重跑门禁刷分。

## 逐题摘要

| ID | 草稿决定 | 失败原因摘要 | 确认后动作 |
|---|---|---|---|
| mq-124 | **修订** | 只答 5349，未并列 5439，无冲突信号 | 按 updates 改题干/锚点/required_facts |
| mq-125 | **驳回** | 无可靠 PDF↔DOCX 冲突锚点，只能拒答 | 移出 governance conflict 子集 |
| mq-126 | **修订** | 证据有两键但按 conflict 拒答 | 改为分别引用两键的事实题 |
| mq-128 | **驳回** | 443 在各来源一致，非冲突 | 移出 governance conflict 子集 |
| mq-129 | **驳回** | 两侧为同一示例凭证，非冲突 | 移出 governance conflict 子集 |

## 人工确认清单

对每题在 JSON 中将 `status` 改为 `confirmed` 或 `overridden`，并填写 `reviewer` / `reviewed_at`。

- [ ] mq-124 — 同意修订 / 改为批准 / 改为驳回
- [ ] mq-125 — 同意驳回 / 保留并补锚点
- [ ] mq-126 — 同意改为事实题 / 保留 conflict
- [ ] mq-128 — 同意驳回 / 保留并补冲突定义
- [ ] mq-129 — 同意驳回 / 改为警示题

全部确认后：应用 updates → 重跑 `scripts/eval_answer_governance.py` → 台账 `status=applied`。
