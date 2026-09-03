# 阶段 P1：SessionAgentState 与会话级 Runtime 执行 PRD

## 0. 文档信息

- **状态**：待执行
- **基线日期**：2026-09-02
- **上位 PRD**：`2026-09-02-会话级AgentRuntime与持续证据记忆总体重构PRD.md`
- **前置依赖**：P0 Evidence Ledger 完成
- **目标**：建立唯一会话级 Agent 状态聚合根，使 Request 不再是 Agent 状态唯一真源。

---

# 1. 当前问题

当前每次用户消息都会重新创建：

```text
ConversationContext
EvidencePool
AgentBudget
Gap Registry
Graph Working Set
AgentLoop
```

系统虽然能利用 history 恢复部分语义，但不能表示一个持续任务的完整状态。

这导致：

- Clarify 后靠新 Request 恢复；
- Reviewer Resume 有独立循环；
- 用户“继续”会重新构造世界；
- Tool History / Gap / Budget 跨轮边界不统一；
- 未来 Steering 很容易演化成大量 flag。

---

# 2. 第一性原则

1. Session 是 Agent 生命周期。
2. Request 只负责提交事件或订阅输出。
3. Runtime 状态必须有单一聚合根，不能靠多个隐式对象拼出来。
4. Main Controller 继续无状态调用；“持久”属于 Runtime，不属于 LLM 会话缓存。
5. 本阶段只建立 State/Store 与兼容 adapter，不提前实现完整用户 Steering。

---

# 3. SessionAgentState

建议最小模型：

```text
SessionAgentState
- session_id
- state_version
- status
- current_run_id

- conversation_state
- task_state
- identity_state
- evidence_ledger_session_ref
- graph_state
- gap_state
- execution_state

- pending_wait
- created_at
- updated_at
```

## 3.1 status

首版仅：

```text
IDLE
RUNNING
WAITING_USER
PAUSED
COMPLETED
FAILED
CANCELLED
```

## 3.2 ConversationState

只保存 Agent 需要的连续语义状态，不复制整个聊天存储：

```text
recent_turn_refs
rolling_summary
current_focus
resolved_references
```

## 3.3 TaskState

```text
active_task_id
original_goal
resolved_goal
requested_facets
user_constraints
open_questions
resolved_questions
```

## 3.4 IdentityState

复用现有 IdentityScope/Resolution 输出：

```text
identity_status
confirmed_entities
raw_mentions
identity_scope_id
identity_epoch
clarification_history
```

## 3.5 GraphState / GapState

迁移当前 request-local 的必要字段；不得把图谱数据库内容复制到 Session State，只保存工作集引用/已探索范围/预算状态。

---

# 4. Store

新增 `AgentRuntimeStore`，至少提供：

```text
get_or_create_session_state
load_state
compare_and_set_state
append_execution_record
mark_status
delete_session_runtime
```

P0 EvidenceLedger 可与 RuntimeStore 使用同一 SQLite 文件，但必须逻辑分表/分 repository，不能混成一个 JSON blob 无法审计。

---

# 5. 生命周期

## 5.1 首次消息

```text
USER request
→ create/load SessionAgentState
→ status RUNNING
→ materialize current-turn runtime objects
→ AgentLoop
→ write state
```

## 5.2 下一轮消息

不再通过 history 从零推断所有状态：

```text
load SessionAgentState
+ current user message
+ chat history as conversation evidence only
→ derive turn delta
→ Main
```

## 5.3 运行结束

回答发布不等于删除 Session State：

```text
status = IDLE or COMPLETED(task-level)
```

会话仍保留 Task/Evidence/Identity 历史。

---

# 6. 与当前 `/query` 的兼容

P1 不要求立即改前端 API。

当前 `/query` / `/query/stream` 作为 adapter：

```text
legacy request
→ resolve session_id
→ load SessionAgentState
→ run one event/turn
→ old response/SSE shape
```

必须确保兼容层只是协议转换，不再拥有独立状态语义。

---

# 7. 状态物化边界

现有 `ConversationContext`、`EvidencePool` 可暂时保留为单次 Controller 执行的 in-memory view，但来源改成 Session State：

```text
SessionAgentState
→ materialize ConversationContext
→ materialize EvidencePool current view
→ AgentLoop
→ commit state delta
```

不要在 P1 同时重写所有模型类。

---

# 8. 并发与版本

## 8.1 单 Session 单 Controller

同一 `session_id` 同时只能有一个 Main Controller 临界执行。

## 8.2 state_version

每次成功状态提交：

```text
state_version += 1
```

后续 P2 Event 必须可以携带 expected version。

## 8.3 服务重启

- IDLE/WAITING_USER/PAUSED 可直接恢复；
- RUNNING 若服务中断，不得恢复成“仍运行”；启动恢复时标记为明确可恢复/FAILED_INTERRUPTED 状态映射，首版可以统一转 PAUSED + interruption reason。

---

# 9. Budget

本阶段区分：

```text
TurnBudget
TaskBudget
```

但不要提前设计复杂计费系统。

最低要求：用户下一轮“继续”不会因为上一轮 `AgentLoop.max_steps` 用完而永久失去继续能力；同时同一 task 的 retrieval/graph 总预算仍可追踪。

---

# 10. 主要修改代码面

预期：

```text
rag_knowledge/services/agent_orchestration/models.py
rag_knowledge/services/agent_orchestration/runtime.py
rag_knowledge/services/rag.py
rag_knowledge/services/chat_storage.py
rag_knowledge/api/routes.py
```

建议新增：

```text
rag_knowledge/services/agent_runtime/state.py
rag_knowledge/services/agent_runtime/store.py
rag_knowledge/services/agent_runtime/service.py
```

仍以最少边界为准。

---

# 11. 测试

## Unit

- create/load state
- state_version CAS
- task state carry
- identity carry
- evidence ledger reference carry
- interrupted RUNNING recovery
- session delete cascade

## Integration

### Case A：连续任务

```text
Turn1 调查 A
Turn2 “继续查部署”
```

第二轮从同一 SessionAgentState 继续，Trace 可证明 task_id 连续。

### Case B：新主题

新显式实体触发 Identity/Task transition，但不是创建新 session。

### Case C：并发

两个相同 state_version 写请求不能静默互相覆盖。

### Case D：重启

WAITING/PAUSED 状态在 store reload 后保持。

---

# 12. DoD

- [ ] 存在唯一 SessionAgentState 模型与 Store。
- [ ] `/query` Agent 模式通过 Session State adapter 执行。
- [ ] Conversation/Task/Identity/Evidence/Gap/Graph 关键状态不再只能存在 request-local。
- [ ] `state_version` 并发保护有效。
- [ ] Session 删除能清理 Runtime/Evidence 数据。
- [ ] AgentLoop 仍由 Main 决策，未引入 Runtime 语义 fallback。
- [ ] 现有 Answer Generator/Reviewer 行为不回退。
- [ ] 专项与既有 Agent 回归通过。

---

# 13. 阶段结束状态

P1 完成后系统仍可以“一条消息跑到答案”，但其底层已从：

```text
每轮临时 Agent 世界
```

转成：

```text
持久 SessionAgentState + 每轮 Controller 执行视图
```

P2 才开始把 Clarify/Reviewer/用户补充真正统一为 Event/Pause/Resume。
