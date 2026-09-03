# 阶段 P2：AgentEvent 与 Pause / Resume 统一执行 PRD

## 0. 文档信息

- **状态**：待执行
- **基线日期**：2026-09-02
- **上位 PRD**：`2026-09-02-会话级AgentRuntime与持续证据记忆总体重构PRD.md`
- **前置依赖**：P0、P1 完成
- **目标**：把 Clarify callback、Reviewer gap、continue/pause 统一为同一 Session Runtime 的 Event 与状态迁移，不再维护多套专用 resume 语义。

---

# 1. 当前问题

当前存在多套“恢复”机制：

```text
Clarify
→ 当前 AgentLoop break
→ 下一请求带 clarification_* 字段重新开始

Reviewer Gap
→ _iter_reviewer_resume_loop 专用循环

普通用户补充
→ 新 query request
```

它们本质上都是：

> 新信息进入已有任务，然后 Main 重新决策。

却由不同路径实现。

---

# 2. 第一性原则

1. **新信息统一表示为 AgentEvent。**
2. **Pause 是 Runtime 状态，不是一次请求失败或结束。**
3. **Resume 后仍由 Main 决定下一步。**
4. **Reviewer 只提供 Evidence Gap，不选择工具。**
5. **Clarify 可以重复发生，不设一次性生命周期规则。**
6. **事件处理必须幂等、有版本校验。**

---

# 3. AgentEvent

首版事件类型：

```text
USER_MESSAGE
CLARIFICATION_RESPONSE
REVIEW_FEEDBACK
CONTINUE
PAUSE
CANCEL
```

P3 再增加完整 `USER_INSTRUCTION` Steering 语义。

统一 envelope：

```text
event_id
session_id
expected_state_version
event_type
payload
created_at
```

## 3.1 幂等

相同 `event_id` 重放：

```text
不得重复执行工具
不得重复创建 clarification snapshot
不得重复写 Evidence
```

返回已处理结果/当前状态即可。

## 3.2 stale state

`expected_state_version` 与当前不一致时，返回 stale response，客户端刷新状态后再决定是否重发。

---

# 4. Runtime 状态迁移

最小迁移：

```text
IDLE + USER_MESSAGE        → RUNNING
RUNNING + clarify action   → WAITING_USER
WAITING_USER + CLARIFICATION_RESPONSE → RUNNING
RUNNING + PAUSE            → PAUSED（在决策边界生效）
PAUSED + CONTINUE          → RUNNING
RUNNING + reviewer GAP     → RUNNING + GapState update + Main
ANY + CANCEL               → CANCELLED
RUNNING + publish          → IDLE / task completed
```

禁止将每个 tool 映射成 Runtime status。

---

# 5. Clarify 重构

## 5.1 Main 调用

`clarify` 仍然是 Main 工具/动作选择。

成功后：

```text
pending_wait.kind = clarification
pending_wait.snapshot_id = ...
pending_wait.payload = card
status = WAITING_USER
```

当前 run 暂停，不视为 completed。

## 5.2 用户响应

`CLARIFICATION_RESPONSE`：

```text
validate clarification snapshot
validate option/free_text
update IdentityState / TaskState
clear pending_wait
status RUNNING
invoke Main next decision
```

## 5.3 多次 Clarify

删除/修改“澄清回调后必须 retrieve”的强制 Prompt 语义。

正确规则：

```text
澄清后重新判断信息是否足够；
仍缺关键条件 → 可再次 clarify；
足够 → 自主选择 retrieve/graph/reuse/finalize。
```

---

# 6. Reviewer Resume 重构

Reviewer 输出保持 Claim/Evidence 审查，不扩大权限。

当存在 gap：

```text
ReviewerResult
→ REVIEW_FEEDBACK event
→ GapState.append(...)
→ Main Controller
```

Main 能看到：

```text
已有 Evidence
Reviewer gap
已尝试路径
剩余预算
```

再决定 retrieve / graph / reuse / finalize partial / no safe answer。

## 6.1 迁移要求

现有 `_iter_reviewer_resume_loop()` 在迁移期可以作为 adapter，但最终不得继续拥有独立语义规则。

完成 P2 时应做到：

```text
Reviewer resume 的“继续决策”只有 Main 一处。
```

---

# 7. Continue / Pause

## 7.1 Pause

首版 Pause 在 Controller Decision Boundary 生效：

- 若 Main 正在生成下一决策前收到 PAUSE → 立即 PAUSED；
- 若普通 Tool 已经进入执行 → 等 Tool Observation 写入后 PAUSED；
- 不要求强制杀死工具线程。

## 7.2 Continue

`CONTINUE` 不创建新 Task，不重置 Evidence/Gap；从已有 State 回到 Main。

---

# 8. API

在兼容现有 Query API 的同时新增/落地统一事件入口：

```text
POST /agent/sessions/{session_id}/events
GET  /agent/sessions/{session_id}/state
```

事件响应至少包含：

```text
accepted
session_id
state_version
status
run_id
```

SSE 仍可沿现有 stream 输出执行事件。

---

# 9. Trace / 可观测性

Trace 必须可还原：

```text
event_id
from_status
transition
to_status
state_version
run_id
task_id
```

Clarify 应能看到：

```text
Main action clarify
→ WAITING_USER
→ CLARIFICATION_RESPONSE
→ RUNNING
→ Main action ...
```

而不是只看到两个无关联 Request。

---

# 10. 主要代码面

预期：

```text
rag_knowledge/services/agent_runtime/state.py
rag_knowledge/services/agent_runtime/store.py
rag_knowledge/services/agent_runtime/service.py
rag_knowledge/services/agent_orchestration/runtime.py
rag_knowledge/services/rag.py
rag_knowledge/api/routes.py
rag_knowledge/models/api.py
```

前端本阶段只需支持 Clarify/Event adapter；完整运行中输入 P3 做。

---

# 11. 测试

## Unit

- event idempotency
- stale state version
- status transition table
- pending_wait persistence
- clarification response validation
- review feedback → gap state

## Integration

### Case A：Clarify ×2

```text
USER_MESSAGE
→ clarify #1
→ WAITING_USER
→ response #1
→ Main
→ clarify #2
→ WAITING_USER
→ response #2
→ retrieve
```

同一 session/task 连续。

### Case B：Reviewer Gap

```text
retrieve A
→ answer V1
→ reviewer gap B
→ REVIEW_FEEDBACK
→ Main retrieve B
→ answer V2
→ PASS
```

不由 Reviewer 直接调用 retrieval。

### Case C：Pause / Continue

Tool observation 后 PAUSED；CONTINUE 后继续从已有 Evidence/Task 进入 Main。

### Case D：重复事件

相同 clarification response 重试不得重复执行后续工具。

---

# 12. DoD

- [ ] 存在统一 AgentEvent envelope。
- [ ] Event 持久化/幂等/版本校验有效。
- [ ] Clarify 成为 WAITING_USER，不再等价于 Agent terminate。
- [ ] 支持同一 Task 连续多次 clarify。
- [ ] Reviewer gap 经 REVIEW_FEEDBACK Event 回 Main。
- [ ] Reviewer 不直接选工具。
- [ ] Pause/Continue 复用同一 State。
- [ ] Trace 可跨事件还原一个连续 run/task。
- [ ] 旧 clarification/reviewer resume 专用语义已删除或仅作为薄 adapter。
- [ ] 专项与全量相关回归通过。

---

# 13. 阶段结束状态

P2 完成后，系统首次具备真正的：

```text
RUN → WAIT → EVENT → RESUME
```

统一模型。

P3 将在此基础上开放用户运行中的自然语言 Steering，而不是再发明另一套“中途控制”协议。
