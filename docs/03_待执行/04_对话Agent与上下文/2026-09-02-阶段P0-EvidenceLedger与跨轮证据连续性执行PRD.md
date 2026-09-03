# 阶段 P0：Evidence Ledger 与跨轮证据连续性执行 PRD

## 0. 文档信息

- **状态**：待执行
- **基线日期**：2026-09-02
- **上位 PRD**：`2026-09-02-会话级AgentRuntime与持续证据记忆总体重构PRD.md`
- **前置依赖**：现有 `EvidencePool`、Working/Citable、Evidence Epoch、Snapshot/Reviewer 规则
- **本阶段目标**：先修复跨正常用户轮次 Evidence 无法稳定恢复/复用的问题，不提前引入 Session Runtime/Pause/Event 大改造。

---

# 1. 当前问题

当前同 Request 内 Reviewer Resume 可以将旧 Evidence 与新 Evidence 合并，但正常跨轮只通过 `history[].sources` 传递轻量来源摘要。

现状：

```text
Turn 1 Evidence Doc
  ↓
前端 SourceSummary
  ↓
Turn 2 history
  ↓
seed_previous_cited()
```

数据形态不一致：`seed_previous_cited()` 期待完整 `content + metadata`，而前端只有 `chunk_id/preview/file_name...`。

同时前端默认每个 Assistant 只向 history 携带最多 4 个来源，且 `SessionState` 只有 `last_sources`，不是完整会话 Evidence Memory。

---

# 2. 第一性原则

1. **客户端不是 Evidence Authority。**
2. **SourcePanel 展示数据与 Agent Evidence Memory 分离。**
3. **Evidence Record 服务端持有完整内容与协议字段。**
4. **历史 Evidence 存在不等于当前可引用。**
5. **跨轮复用必须经过当前 SemanticTask / Identity / Evidence Epoch 重新 Qualification。**
6. **本阶段不改变 Main 的语义决策权。是否 reuse 仍由 Main 决定。**

---

# 3. 目标数据模型

## 3.1 EvidenceRecord

至少包含：

```text
evidence_id
source_type
chunk_id / relation_id
content
content_hash
metadata
support_scope
evidence_class
identity_scope_id
evidence_epoch
origin_session_id
origin_turn_id
origin_snapshot_id
created_at
last_qualified_at
```

`evidence_id` 为服务端稳定标识，不等于 citation number。

## 3.2 EvidenceSnapshot

```text
snapshot_id
session_id
turn_id
task_key(optional in P0)
evidence_version
ordered_evidence_ids
created_at
```

Snapshot 不可变；Citation 编号按 Snapshot 顺序动态生成。

## 3.3 EvidenceLedgerStore

新增独立持久层，首版允许 SQLite。职责：

```text
save_record
get_record(s)
save_snapshot
get_snapshot
list_session_snapshots
resolve_snapshot_evidence
mark/remove session data
```

禁止把该职责塞进 Graph relational DB 业务表。

---

# 4. 实施范围

## 4.1 写入链路

当当前回答产生 Frozen Evidence Snapshot 时：

```text
EvidencePool Citable Docs
  ↓
normalize to EvidenceRecord
  ↓
EvidenceLedgerStore
  ↓
EvidenceSnapshot
```

Snapshot ID / Evidence IDs 随回答来源 metadata 返回前端，但前端无需保存完整 Evidence。

## 4.2 下一轮恢复

下一轮 `ConversationContext` 可从最近 Assistant message 得到：

```text
snapshot_id
```

后端：

```text
snapshot_id
→ EvidenceLedgerStore
→ 完整 EvidenceRecord
→ previous_turn_cited FROZEN
```

不得再把 SourceSummary 当完整 Evidence Doc。

## 4.3 `reuse_evidence`

工具参数逐步收敛为：

```text
snapshot_id?
evidence_ids?
```

Main 可选择全部或部分历史 Evidence。

Runtime 根据稳定 ID 解析完整 Record，再做当前 Qualification。

## 4.4 兼容策略

迁移期允许旧 history 没有 `snapshot_id`：

```text
没有 snapshot_id
→ 不伪造 Evidence
→ 可继续使用 source_anchor 重新检索
```

禁止 fallback 为“把 preview 当 Evidence”。

---

# 5. Evidence Qualification

跨轮复用必须执行：

```text
Historical Record
  ↓
current evidence_epoch check
  ↓
current identity/task admission
  ↓
Working / Citable / Context-only / Reject
```

若实体切换导致 epoch 增加，旧记录默认 `STALE_FOR_CITATION`，除非经过当前轮重新 Admission 后生成新的资格状态。

历史 Record 本体保持不可变；当前轮资格作为新的 qualification/result 记录，不覆盖历史事实。

---

# 6. 需要修改的主要代码面

预期涉及：

```text
rag_knowledge/services/agent_orchestration/models.py
rag_knowledge/services/rag.py
rag_knowledge/models/api.py
rag_knowledge/services/chat_storage.py（仅保存 snapshot 引用，非完整 Evidence）
web/src/utils/chatHistory.ts
web/src/types/index.ts
```

建议新增：

```text
rag_knowledge/services/agent_runtime/evidence_ledger.py
rag_knowledge/services/agent_runtime/store.py
```

实际文件名以当前模块边界最简实现为准，不要求为了目录形式强拆。

---

# 7. 明确禁止

- 前端把完整 chunk/content/metadata 回传作为事实真源。
- 仅靠 `preview` 恢复 Evidence。
- 自动把所有历史 Snapshot Evidence ACTIVE。
- 跨实体复用跳过 Admission。
- 给永久 Evidence Record 写死 citation_id。
- 为 P1/P2 提前实现完整 Event Runtime。

---

# 8. 测试

## 8.1 单元测试

1. EvidenceRecord 稳定写入/读取。
2. 相同 chunk 去重但不同 qualification 不覆盖。
3. Snapshot immutable。
4. Citation 顺序属于 Snapshot。
5. 缺失 snapshot 返回明确 miss。
6. 客户端伪造 metadata 不影响服务端 Record。
7. entity epoch 变化后旧 Record 不直接 Citable。

## 8.2 集成测试

### Case A：正常跨轮复用

```text
Turn1 → A/B → Snapshot S1
Turn2 → Main reuse S1:A/B + retrieve C
Final Snapshot → A/B/C
```

### Case B：实体切换

```text
Turn1 PipelineWebGL → A/B
Turn2 PipelineWebRTC
```

A/B 不自动进入 Citable。

### Case C：旧客户端兼容

无 snapshot_id 的 history 仍能问答，但只允许 source_anchor/retrieve，不得制造旧 Evidence。

### Case D：超过 4 个来源

Turn1 有 8 个 Evidence；Turn2 通过 Snapshot 可恢复 8 个服务端 Record，不受前端展示 limit=4 影响。

---

# 9. DoD

- [ ] 新增服务端 EvidenceLedger/EvidenceSnapshot 持久层。
- [ ] 正常回答写入 Snapshot。
- [ ] 前端/Chat history 能携带 Snapshot 引用。
- [ ] 下一轮按 Snapshot 解析完整 Evidence，不再把 SourceSummary 当 Evidence Doc。
- [ ] `reuse_evidence` 使用稳定 Evidence ID。
- [ ] 历史 Evidence 必须重新 Qualification。
- [ ] 跨实体 Evidence 不自动 Citable。
- [ ] 前端来源展示数量不影响服务端 Evidence Memory 完整性。
- [ ] 旧无 Snapshot 历史 fail-safe，不 fail-open。
- [ ] 专项测试通过。
- [ ] Identity/Evidence/Graph 相关既有回归通过。

---

# 10. 本阶段完成后的架构状态

完成 P0 后仍是 Request-centric Agent，但证据连续性由：

```text
客户端 SourceSummary 猜恢复
```

升级为：

```text
服务端 EvidenceSnapshot / EvidenceLedger 精确恢复
```

这为后续 SessionAgentState 提供可靠 Evidence 基础。
