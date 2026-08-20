# Round 3 延后执行卡

> **口径状态（2026-08-14）**：历史快照，不代表现行进度。现行以 [docs/README.md](../../../README.md) 与 [待办清单.md](../../../01_当前有效/00_项目总览与架构/待办清单.md) 为准。
>
> **进度说明（2026-07-28）**：Round 3 规则路径与第 3 轮补实 LLM 均已完成；本卡为延后期间的历史执行卡，不再表示「仍延后」。见 [第3轮执行验收记录](../../../04_已完成归档/03_知识图谱与GraphRAG/已完成-第3轮-安全全量重建/第3轮执行验收记录.md)。


- **状态**：`deferred`（撰写当日）；**2026-07-28：已过期** — Round 3 规则路径与第 3 轮补均已完成
- **日期**：2026-07-21
- **权威 PRD**：[已完成-第3轮-安全全量重建/执行PRD.md](../../../04_已完成归档/03_知识图谱与GraphRAG/已完成-第3轮-安全全量重建/执行PRD.md)
- **总览**：[2026-07-13-知识图谱PRD剩余轮次总览.md](../../../01_当前有效/03_知识图谱与GraphRAG/2026-07-13-知识图谱PRD剩余轮次总览.md) §1.1

## 为何延后

本轮只做第 5 阶段准入准备，不包含 `rebuild-safe --execute`。Round 3 现场仍缺 execute 报告、post-audit 与专用备份留痕。

## 前置条件（全部满足后再开 Round 3）

1. 治理冲突题台账 `multi_chunk_qa_gold_v4.governance_conflict_ledger.json` 已人工确认并 `applied`
2. v4 检索黄金集人工签核进度可见（`human_signoff_pending` → 进行中或完成）
3. 本地 `[graph_retrieval] enabled=false`，与生产默认对齐
4. 关系证据债清点与 allowlist 草稿已归档（`docs/3_待办清单/知识图谱语义抽取/准入准备/`）
5. Graph off/on FR-10 预检已记录（不替代 Round 4 正式验收）

## 执行顺序（届时）

```powershell
# 0. 停止占用 chroma_db / rag_relational.db 的进程

# 1. 若 live 相对上次 dry-run 已变：重跑 dry-run
.\venv\Scripts\python.exe run_graph_build.py rebuild-safe --dry-run `
  --output-json data/rebuild_safe_dry_run_pre_round3.json `
  --output-md data/rebuild_safe_dry_run_pre_round3.md

# 2. 正式重建（须先备份）
$db = (.\venv\Scripts\python.exe -c "from rag_knowledge.services.graph_governance import resolve_db_path; print(resolve_db_path())").Trim()
.\venv\Scripts\python.exe run_graph_build.py rebuild-safe --execute --include-llm `
  --confirm-db-path $db `
  --backup-dir data/backups `
  --output-json data/rebuild_safe_execute_round3.json `
  --output-md data/rebuild_safe_execute_round3.md

# 3. 分拆审批（禁止 profile_sync / LLM / backbone 的 --approve-all）→ apply
# 4. 验收
.\venv\Scripts\python.exe run_graph_build.py audit --output-json data/graph_audit_post_round3.json
.\venv\Scripts\python.exe run_graph_build.py quality --graph --profile full
.\venv\Scripts\python.exe scripts\validate_task81_graph_gate.py --json
```

## Round 3 完成后

- 第 4 轮已归档：[已完成-第4轮-GraphRAG实效验收](../../../04_已完成归档/03_知识图谱与GraphRAG/已完成-第4轮-GraphRAG实效验收/执行PRD.md)
- 正式 GraphRAG A/B 已 PASS；**生产模板仍建议** `graph_retrieval.enabled=false`，本地可开全图

## 本轮结论（2026-07-21 原文，已失效）

~~**不执行 Round 3。** 继续停留在「图谱接入前」~~ → 已被后续执行推翻；现行口径见总览 / 第 4 轮阶段总结。
