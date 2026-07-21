# Round 3 延后执行卡

- **状态**：`deferred`（本轮不执行）
- **日期**：2026-07-21
- **权威 PRD**：[待执行-第3轮-安全全量重建/执行PRD.md](../待执行-第3轮-安全全量重建/执行PRD.md)
- **总览**：[2026-07-13-知识图谱PRD剩余轮次总览.md](../2026-07-13-知识图谱PRD剩余轮次总览.md) §1.1

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

- 再开 [第4轮 GraphRAG 实效验收](../待执行-第4轮-GraphRAG实效验收/执行PRD.md)
- 正式 GraphRAG A/B 通过且关系/实体链接门槛满足后，才考虑生产 `graph_retrieval=true`

## 本轮结论

**不执行 Round 3。** 继续停留在「图谱接入前」；文本 RAG 基线维持可用。
