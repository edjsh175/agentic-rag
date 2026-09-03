# 会话级 Agent Runtime 与持续证据记忆总体重构 PRD

## 0. 文档信息

- **文档类型**：总体架构 PRD / 母 PRD
- **状态**：待执行
- **基线日期**：2026-09-02
- **源码基线**：2026-09-02 当前工作区 `rag_knowledge/services/agent_orchestration/`、`rag_knowledge/services/rag.py`、`rag_knowledge/services/conversation_context.py`、`web/src/views/ChatView.vue`
- **目标版本**：Session Agent Runtime V1
- **替代关系**：不废止现有 Identity / Candidate / Evidence / Grounding、Main Controller、Working/Citable、Graph Evidence 等已落地规则；自本 PRD 起，关于“跨用户轮次的 Agent 生命周期、Evidence 连续性、Clarify/Reviewer Resume、运行中用户介入”的后续架构以本文为最高权威。
- **被谁替代**：无
- **实施子 PRD**：
  1. `2026-09-02-阶段P0-EvidenceLedger与跨轮证据连续性执行PRD.md`
  2. `2026-09-02-阶段P1-SessionAgentState与会话级Runtime执行PRD.md`
  3. `2026-09-02-阶段P2-AgentEvent与PauseResume统一执行PRD.md`
  4. `2026-09-02-阶段P3-运行中用户Steering与单动作执行执行PRD.md`
  5. `2026-09-02-阶段P4-StructuredState与旧请求级链路收口执行PRD.md`

---

# 1. 背景与当前结论

当前系统已经不是“用户无论输入什么都执行同一条固定工具流水线”。Main Controller 在单次请求内部拥有真实的语义决策权，可以在预算和 Harness 协议内选择 `retrieve_kb`、`reuse_evidence`、`expand_graph_scope`、`clarify`、`environment.read_status`、可选 `web_search` 或 `finalize`。

但当前 Agent 生命周期仍绑定单次 HTTP 请求：

```text
User Message
  ↓
ConversationContext
  ↓
new EvidencePool
  ↓
new AgentLoop
  ↓
Main 动态决策 / Tool / Evidence
  ↓
finalize
  ↓
Answer Generator
  ↓
Reviewer
  ↓
Request 结束
```

下一条用户消息到来时，再重新构造新的 `ConversationContext / EvidencePool / AgentLoop`。因此当前系统更准确的定义是：

> **Request-centric Agentic Workflow：单次请求内部已 Agent 化，跨请求仍主要依赖 history 恢复状态。**

本 PRD 的目标不是继续往当前请求级 AgentLoop 增加特殊 flag，而是把生命周期提升为：

> **Session-centric Agent Runtime：会话持有持续状态，HTTP 请求只是向 Runtime 注入事件。**

---

# 2. 第一性原则

本轮所有设计必须同时满足以下原则。

## 2.1 LLM 负责语义决策，Runtime 负责确定性状态与协议

```text
Main Controller
= 下一步做什么、为什么做、缺什么、是否继续调查

Runtime / Harness
= 当前状态是什么、动作是否合法、预算是否允许、如何持久化、如何暂停恢复
```

禁止 Harness 根据语义自行替 Main 选择检索、澄清、图谱扩展或终止动作。

## 2.2 Conversation Memory 与 Evidence Memory 必须分离

```text
Conversation Memory
= 用于理解“它、刚才第二点、继续、换一个方向”等语义

Evidence Memory
= 用于决定哪些事实具有 Working / Citable 资格
```

历史 Assistant 回答永远不能因为被记住而自动成为知识事实。

## 2.3 历史 Evidence 可复用，但不得自动继承引用权

```text
历史证据存在
≠
当前问题可直接引用
```

正确关系：

```text
Historical Evidence Record
  ↓
current SemanticTask / Identity / Evidence Epoch 重新 Qualification
  ↓
Working / Citable / Context-only / Rejected
```

## 2.4 Session 是 Agent 生命周期，Request 是 Event 载体

从：

```text
Request = Agent 生命周期
```

改为：

```text
SessionAgentRuntime = Agent 生命周期
Request = User/Event 输入或状态查询
```

## 2.5 Pause 不是 Terminate

`clarify`、用户要求“先别回答”、Reviewer 请求补检等场景应改变 Agent 状态，而不是销毁当前任务后靠下一请求猜测恢复。

## 2.6 用户 Steering 是新输入，不是绕过 Agent/Harness 的后门

用户可以改变调查方向、约束下一动作、要求只执行一次工具，但任何工具仍必须经过 Registry、Harness、身份/证据规则和预算检查。

## 2.7 Answer Generator 与 Controller 继续分权

```text
Controller = 决定行动
Answer Generator = 在 Frozen Evidence Snapshot 上表达
```

Answer Generator 不获得工具，不修改 Evidence，不从历史 Assistant 文本创造事实。

---

# 3. 当前必须解决的根问题

## 3.1 P0：Evidence State 只在同一 Request 内真正连续

当前 Reviewer Resume 可以把 V1 Evidence 带入 V2 并与新 Evidence 合并，这是正确能力；但正常用户下一轮会重新创建 EvidencePool。

因此：

```text
同请求补检：A + B → A + B + C   ✅
跨用户轮次：Turn1(A+B) → Turn2(C) 不能保证得到 A+B+C   ❌
```

## 3.2 P0：正常跨轮 `reuse_evidence` 数据形态不闭环

前端 history 只发送轻量 `SourceSummary`，主要包含：

```text
file_name / source / section_title / page_label / chunk_id / preview
```

而 `EvidencePool.seed_previous_cited()` 按完整 Evidence Doc 读取 `content + metadata + support_scope + evidence_class`。当前未发现统一的 `evidence_id/chunk_id → server-side full evidence record` 恢复层。

这意味着客户端来源摘要正在承担它不应该承担的 Evidence Memory 职责。

## 3.3 P0：只保存 last_sources，不是 Session Evidence Memory

当前 `SessionState.last_sources` 只代表最近一个包含来源的 Assistant 消息，前端还默认最多保留 4 个 Source Summary。它不能表示整个会话的 Evidence 历史。

## 3.4 P1：Agent State 每轮重建

ConversationContext、EvidencePool、GraphWorkingSet、Gap Registry、Budget 等没有统一的会话级持久状态对象。

## 3.5 P1：Clarify 是“请求终止”，不是“状态等待”

Main 调用 `clarify` 后当前 AgentLoop 结束；用户回调后创建新请求。虽然结构上可再次 clarify，但不是同一 Runtime 的自然恢复。

## 3.6 P1：Reviewer Resume 是专用循环

Reviewer 补检使用专门的 resume loop，而不是统一事件模型。用户补充、Clarify 回调、Reviewer gap 本质上都属于“新事件进入同一任务状态”，却由不同代码路径处理。

## 3.7 P1：运行过程中用户无法介入

前端 `loading` 时 ChatInput 被禁用。当前 SSE 仅 Server → Client，没有向活动 AgentRun 注入新消息的协议。

## 3.8 P1：无法正式表达“只做一个动作，然后停”

用户可以自然语言要求“再查一下图谱”，但没有统一 `ExecutionConstraint` 表达：

```text
只允许下一步 graph
执行后暂停
先不要 finalize
```

## 3.9 P2：Controller 长上下文依赖文本裁剪

Controller 使用结构化 ConversationContext，但最终 prompt 有 `[:1200]` 截断，近期历史也只保留少量 turn/字符。复杂长期任务仍过度依赖 Stage-1 将当前话一次性改写成 standalone query。

## 3.10 P2：Agent Prompt 与 Controller Policy 没有正式分层

当前 `agent_prompt` 主要影响 Answer Generator，不能稳定表达 Agent 的工具策略。未来需要把 Controller Instruction 与 Answer Instruction 分开，同时保持 System Policy 最高优先级。

---

# 4. 目标总体架构

```text
                         Session Agent Runtime
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
          ▼                       ▼                       ▼
 ConversationState           TaskState              IdentityState
          │                       │                       │
          └───────────────┬───────┴───────────┬───────────┘
                          │                   │
                          ▼                   ▼
                   EvidenceLedger      GraphWorkingSet
                          │
                 ┌────────┴────────┐
                 ▼                 ▼
             Working           Citable
                          │
                          ▼
                    Main Controller
                          │
      ┌───────────┬───────┼─────────┬────────────┐
      ▼           ▼       ▼         ▼            ▼
   retrieve      graph   reuse    clarify    environment
      │           │       │         │            │
      └───────────┴───────┼─────────┴────────────┘
                          ▼
                     Agent State
                    /     |      \
               RUNNING  WAITING  FINALIZE
                          │          │
                     User Event      ▼
                          │    Frozen Snapshot
                          └──────┐    │
                                 ▼    ▼
                           Main Controller
                                      │
                               Answer Generator
                                      │
                                   Reviewer
                                  /        \
                               PASS        GAP
                                │           │
                             Publish   Review Event
                                            │
                                            └→ Main
```

---

# 5. 核心状态模型

## 5.1 SessionAgentState

建议引入单一会话状态聚合根：

```python
SessionAgentState:
    session_id
    state_version
    status

    conversation_state
    task_state
    identity_state

    evidence_ledger_ref
    graph_working_set
    gap_registry

    execution_state
    pending_wait
    current_run_id

    created_at
    updated_at
```

`state_version` 用于并发/过期事件校验，避免多个请求同时覆盖状态。

## 5.2 Runtime Status

首版只保留必要状态：

```text
IDLE
RUNNING
WAITING_USER
PAUSED
COMPLETED
FAILED
CANCELLED
```

禁止为了每个工具再创建状态枚举。

## 5.3 TaskState

```text
active_task_id
original_user_goal
resolved_goal
requested_facets
user_constraints
open_questions
resolved_questions
active_gap_ids
```

TaskState 是“当前调查任务”的连续语义状态，不等于聊天全文。

## 5.4 IdentityState

复用现有 IdentityScope / IdentityResolution 语义，不另造竞争模型：

```text
identity_status
confirmed_entity/entities
raw_mentions
clarification_history
identity_epoch
```

实体切换继续触发 Evidence Epoch 更新。

---

# 6. Evidence Ledger

## 6.1 Evidence Record

服务端必须拥有完整 Evidence 真相。最低字段：

```text
evidence_id
source_type
chunk_id / relation_id
content
content_hash
metadata

origin_session_id
origin_turn_id
origin_snapshot_id

identity_scope_id
evidence_epoch
support_scope
evidence_class

working_status
citation_status
created_at
last_qualified_at
```

## 6.2 Evidence Snapshot

每次准备回答时创建不可变 Snapshot：

```text
snapshot_id
session_id
task_id
turn_id
evidence_version
ordered_evidence_ids
created_at
```

Citation 编号只属于 Snapshot，不属于 Evidence Record 永久属性。

## 6.3 客户端边界

浏览器只需要持有：

```text
snapshot_id
evidence_id
citation display data
```

禁止由客户端回传完整 Evidence metadata 决定引用资格。

## 6.4 跨轮复用

Main 调用 `reuse_evidence` 时参数应从不稳定的客户端 SourceSummary 转向服务端身份：

```text
snapshot_id + evidence_ids
```

Runtime 从 EvidenceLedger 解析完整 record，再进行当前任务 Qualification。

## 6.5 Retention

首版只要求会话生命周期内持久化，并允许会话删除时级联删除 Runtime/Evidence 数据。不要在本轮实现跨用户长期知识记忆。

---

# 7. Agent Event 模型

统一事件入口：

```text
USER_MESSAGE
CLARIFICATION_RESPONSE
USER_INSTRUCTION
CONTINUE
PAUSE
CANCEL
REVIEW_FEEDBACK
```

所有事件至少包含：

```text
event_id
session_id
expected_state_version
created_at
payload
```

服务端保证 `event_id` 幂等。

## 7.1 用户普通消息

更新 ConversationState / TaskState 后交回 Main。

## 7.2 Clarification Response

不是创建“特殊问答流程”，而是更新 Identity/TaskState，然后从 `WAITING_USER` 恢复。

## 7.3 Reviewer Feedback

Reviewer 只产出结构化 Evidence Gap Event：

```text
missing_claim/facet
reason
suggested_search_focus
```

Runtime 写入 Gap Registry，再交回 Main。Reviewer 不自己决定调用哪个工具。

---

# 8. Pause / Resume

## 8.1 Clarify

Main 调用 clarify 后：

```text
status = WAITING_USER
pending_wait.kind = clarification
```

不得标记 run 为 completed。

用户选择后：

```text
CLARIFICATION_RESPONSE
→ validate snapshot/option
→ update state
→ status = RUNNING
→ Main next decision
```

允许再次 clarify，不设“澄清回调后必须 retrieve”的语义硬规则。

## 8.2 User Pause

“先别回答”转换成 Runtime 执行约束和 `PAUSED` 状态，不需要特殊业务分支。

## 8.3 Tool 正在执行时的 Intervention

首版不尝试强行中断一个已进入执行函数的普通工具；除 `CANCEL` 外，新用户事件进入队列，在下一个 Controller Decision Boundary 消费。

这是并发简单性和可预测性的必要边界。

---

# 9. User Steering 与 ExecutionConstraint

用户 Steering 不等于开放原始工具 RPC。

建议结构：

```text
ExecutionConstraint:
    allowed_tools?      # 可选
    pause_after_action? # bool
    forbid_finalize?    # bool
    expires_after_step  # 默认 1
```

例如用户：

```text
从 PipelineBuilder 向外扩一跳，先别回答。
```

Stage-1/Main 理解后可形成：

```text
allowed_tools = [expand_graph_scope]
pause_after_action = true
forbid_finalize = true
expires_after_step = 1
```

工具参数仍由 Main 生成，Harness 仍负责合法性。

禁止默认向普通用户暴露可绕过 Main 的 `/execute-tool` 通用接口。

---

# 10. Controller / Harness / Answer / Reviewer 权限边界

| 组件 | 有权决定 | 无权决定 |
|---|---|---|
| Main Controller | 工具选择、参数语义、是否澄清、是否继续、是否 finalize | 绕过 Evidence/Citation/ACL/Harness |
| Runtime | 状态转移、持久化、事件消费、并发、预算 | 替 Main 做语义检索计划 |
| Harness | Tool/参数/预算/身份 ID/协议合法性 | 替 Main 选择备用工具 |
| Answer Generator | Frozen Snapshot 上的回答表达 | 调工具、修改 Evidence、沿用历史引用编号 |
| Reviewer | Claim/Evidence 支持审查、产生 gap | 决定 Main 下一工具 |

---

# 11. Graph Bootstrap 边界

现有“确认实体后自动 1-hop Bootstrap”不要求本轮直接删除，但必须被正式定义为环境上下文初始化：

```text
固定 1-hop
固定小预算
不改变 answer_subject
不自动授予文本 Evidence 引用权
不算 Main tool decision
```

所有真正的扩大探索范围继续由 `expand_graph_scope` 交给 Main。

若实际 Bootstrap 已出现明显资源消耗或大量 Evidence 物化，则应在 P4 复核后转为 Main action；不得无数据先重构。

---

# 12. 持久化与并发方案

## 12.1 首版持久化

考虑当前为本地单机服务、已经使用 SQLite 和文件持久化，首版推荐新增独立 `AgentRuntimeStore`，采用 SQLite 存储 Runtime/Evidence 索引与事务状态，避免把完整 Evidence 塞入 `data/chats/*.json`。

不得修改图谱 `relational_db` 的业务表来承载 Agent Runtime。

## 12.2 单会话单 Controller

同一个 `session_id` 同时只允许一个 Controller 执行临界区。

用户事件可以并发到达，但按 event queue 顺序消费。

## 12.3 乐观版本

写事件时携带 `expected_state_version`；过期事件返回明确 stale 状态，不能静默覆盖新 State。

---

# 13. API 目标形态

不要求一次删除当前 `/query`，迁移期兼容。

首版建议最小接口：

```text
POST /agent/sessions/{session_id}/events
GET  /agent/sessions/{session_id}/state
GET  /agent/sessions/{session_id}/stream
POST /agent/sessions/{session_id}/cancel
```

SSE 继续承担 Server → Client 实时事件；用户消息/干预通过 POST 注入，不必为了“双向”强制改 WebSocket。

现有 `/api/query/stream` 在迁移期可作为 adapter：

```text
legacy query request
→ translate USER_MESSAGE event
→ wait/publish compatible SSE
```

最终是否删除旧接口由 P4 决定，不在 P0-P3 提前做大爆炸迁移。

---

# 14. 前端目标体验

## 14.1 输入框

Agent 运行时不再完全禁用输入。

用户可：

```text
发送给正在运行的 Agent
暂停
继续
停止
```

## 14.2 事件归属

UI 必须区分：

```text
本轮普通消息
对活动 Agent 的 intervention
clarification response
```

但不要求用户理解内部 `event_type`。

## 14.3 滚动行为

沿用现有要求：生成过程中允许自由滚动，不强制锁定最底部。

---

# 15. 不在本轮范围

以下内容不得顺手加入：

1. 长期跨会话用户画像/偏好记忆；已有独立 PRD 继续独立治理。
2. 多 Agent 协作、Agent-to-Agent delegation。
3. 分布式消息队列、Redis/Kafka。
4. 云端多实例 Runtime。
5. 任意用户直接调用内部工具 RPC。
6. 自动把所有历史 Evidence 永久激活。
7. 用历史 Assistant 回答代替 Evidence。
8. 重新设计现有检索算法、图谱 Schema 或 Reviewer Claim 协议，除非阶段实施发现其直接阻塞本架构。

---

# 16. 分阶段实施

## P0：Evidence Ledger 与跨轮证据连续性

目标：先修当前最具体的数据断层，不改变 Agent 生命周期。

完成后必须实现：

```text
Turn1: A+B
Turn2: reuse(A/B) + retrieve(C)
→ 当前 Snapshot 可包含 A+B+C
```

且旧 Evidence 必须重新 Qualification。

## P1：SessionAgentState 与会话级 Runtime

目标：把 Conversation/Task/Identity/Evidence/Execution 聚合为持久会话状态，Request 不再拥有唯一状态真源。

## P2：AgentEvent + Pause/Resume

目标：统一 Clarify callback、Reviewer resume、continue/pause，使其成为同一 Runtime 的事件与状态迁移。

## P3：运行中用户 Steering

目标：允许运行时输入新指令，并支持“一次动作后暂停”等约束。

## P4：Structured State 与旧链路收口

目标：减少 Controller 对字符串 history 截断的依赖，接入 Controller Policy；删除被新 Runtime 替代的 request-centric/专用 resume 兼容代码。

---

# 17. 总体验收场景

以下全部通过才允许将母 PRD 判定为完成。

## G1 跨轮证据累积

```text
U1: PipelineWebGL 的部署步骤是什么？
→ Evidence A/B
U2: 再查 Linux 服务配置，并结合刚才证据重新总结。
→ 新 Evidence C/D
```

最终 Snapshot 能合法包含经当前任务重新 Qualification 的 A/B/C/D，不依赖重新命中 A/B。

## G2 实体切换不污染

```text
U1: PipelineWebGL ... → A/B
U2: PipelineWebRTC 呢？
```

A/B 不得自动获得当前 Citable 资格；Evidence Epoch 正确变化。

## G3 多次 Clarify

```text
Main clarify #1
User response
Main 仍判断缺关键条件
Main clarify #2
User response
Main retrieve
```

同一 task/session state 连续，不依赖新建互不关联的 Agent 世界。

## G4 用户运行中纠偏

Agent 已开始调查 WebGL 时，用户发送：

```text
方向不对，改查 PipelineBuilder，之前 WebGL 的证据先保留为历史，不要用于 Builder 的直接事实。
```

新指令在下一 Decision Boundary 生效，Identity/Evidence 状态正确更新。

## G5 单动作执行

用户：

```text
从 PipelineBuilder 向外扩一跳，先不要回答。
```

只执行一次符合 Harness 的 graph action，然后 Runtime 进入 PAUSED；不会自动生成最终答案。

## G6 Reviewer Resume 统一

Reviewer 产生 Evidence Gap 后，不调用专用语义决策逻辑；Gap 以 event/state 形式进入 Main，旧 Evidence 保持可追踪。

## G7 服务重启恢复

在允许的持久化范围内，WAITING_USER/PAUSED 会话重启服务后能恢复状态；RUNNING 中断请求必须恢复为明确可继续或失败状态，不能假装仍在执行。

## G8 客户端不可伪造 Evidence Authority

修改前端回传的 preview/source metadata 不能让不存在或失效 Evidence 获得 Citable 权限。

---

# 18. 测试矩阵

至少覆盖：

```text
Unit
- EvidenceRecord/Snapshot persistence
- requalification
- evidence epoch
- state transition
- event idempotency
- stale version
- ExecutionConstraint expiry

Integration
- Turn1 → Turn2 evidence reuse
- clarify x2
- reviewer gap → main resume
- intervention during running
- tool-only + pause
- cancel
- service reload

Regression
- Identity / Candidate / Evidence / Grounding
- Graph Evidence
- Main single controller
- Answer Finalizer / Reviewer
- SSE execution events
- frontend chat history / clarification
```

真实模型至少跑 3 条稳定 micro-chain：

1. 跨轮 evidence reuse + new retrieve。
2. clarify → clarify → retrieve。
3. reviewer gap → resume → PASS。

---

# 19. DoD

母 PRD 只有在以下全部成立时才能归档：

- [ ] P0-P4 全部子 PRD 完成并各自通过 DoD。
- [ ] 存在唯一 `SessionAgentState` 真源，不再由多个 request-local 对象隐式拼接会话状态。
- [ ] 存在服务端 EvidenceLedger，前端 SourceSummary 不再承担 Evidence Authority。
- [ ] 正常跨轮 Evidence 可按稳定 ID 恢复、重验、合并。
- [ ] Clarify 是 WAITING_USER，支持重复澄清。
- [ ] Reviewer feedback、用户补充、Clarify callback 统一为 Event/Resume 模型。
- [ ] Agent 运行时用户可发送 intervention。
- [ ] 支持一次动作后暂停，且不绕过 Main/Harness。
- [ ] Controller 主要读取 Structured State，不再依赖 `ConversationContext.to_prompt()[:1200]` 作为长期任务记忆核心。
- [ ] Answer Generator 仍只使用 Frozen Evidence Snapshot 作为事实来源。
- [ ] Working/Citable、Identity/Exploration、Graph/Text Admission 边界未回退。
- [ ] 旧 request-centric 专用兼容逻辑已明确删除或有有期限的迁移说明。
- [ ] `git diff --check`、专项测试、全量非 integration、前端测试/build 通过。
- [ ] 真实模型验收通过，且 Trace 能证明状态/证据连续性而不是仅靠重新检索碰巧命中。

---

# 20. 禁止的伪修复

以下做法即使能过局部测试，也视为不符合本 PRD：

```text
remember_last_evidence=true
reuse_last_two_turns=true
continue_previous=true
force_tool=true
skip_answer=true
manual_tool_args=true
```

若这些 flag 只是为绕过没有 Session Runtime 的根问题，则禁止引入。

同样禁止：

```text
把完整 Evidence 塞进 browser history 再回传
把历史 Assistant answer 当事实
跨实体自动激活旧 Evidence
Reviewer 直接选择检索工具
Harness 在 Main 失败时做语义 fallback
```

---

# 21. 最终定义

本轮完成后，系统应从：

```text
Request
  └─ AgentLoop
```

升级为：

```text
SessionAgentRuntime
  ├─ ConversationState
  ├─ TaskState
  ├─ IdentityState
  ├─ EvidenceLedger
  ├─ GraphWorkingSet
  ├─ GapRegistry
  ├─ ExecutionState
  └─ Main Controller
```

用户的“继续、再查一下、结合刚才证据、刚才方向不对、只执行图谱、先别回答、再反问一次”不再分别对应一堆特殊流程，而都只是：

> **向一个持续存在、可暂停、可恢复、证据可追踪的 Session Agent Runtime 注入新事件。**
