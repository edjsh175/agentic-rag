# 阶段 P4：Structured State 与旧请求级链路收口执行 PRD

## 0. 文档信息

- **状态**：待执行
- **基线日期**：2026-09-02
- **上位 PRD**：`2026-09-02-会话级AgentRuntime与持续证据记忆总体重构PRD.md`
- **前置依赖**：P0-P3 完成
- **目标**：让 Main Controller 主要读取结构化 Conversation/Task/Identity/Evidence State，减少对截断 history 文本和 request-local 专用循环的依赖，并清理被 Session Runtime 替代的旧兼容链路。

---

# 1. 当前问题

即使 P0-P3 完成，如果 Controller 仍主要依赖：

```text
ConversationContext.to_prompt()[:1200]
+ 最近少量 history
+ Stage-1 standalone query
```

长期复杂对话仍会出现：

- “刚才第二点”无法稳定指向；
- 用户约束跨多轮遗失；
- 已解决问题与未解决问题混淆；
- Stage-1 一次改写错误会污染后续决策；
- 旧 Reviewer Resume / clarification adapter 长期残留形成双架构。

---

# 2. 第一性原则

1. **长期连续性依赖结构化状态，不依赖把聊天全文塞给 LLM。**
2. **Stage-1 产出 State Delta，而不是每轮重建整个任务世界。**
3. **Controller 读取 State + 必要 recent turns；history 是补充，不是唯一真源。**
4. **旧兼容代码只能作为迁移层，有明确删除条件。**
5. **不重新引入 regex/字符串规则作为语义权威。**

---

# 3. Structured State

## 3.1 ConversationState

最小建议：

```text
current_topic
current_focus
resolved_references
recent_turn_refs
rolling_summary
```

`resolved_references` 只保存必要可解释映射，例如：

```text
"它" → PipelineWebGL
"刚才第二项" → answer_section_ref:xxx
```

不要求保存模型私有推理。

## 3.2 TaskState

```text
active_task_id
original_goal
resolved_goal
requested_facets
user_constraints
open_questions
resolved_questions
last_user_intent_delta
```

例如用户连续：

```text
先问部署
→ 再问 Linux
→ 再要求只基于知识库
```

最终 TaskState 应显式保留这些约束，而不是依赖第 N 轮 history 仍在 prompt 中。

## 3.3 Answer Reference State

为支持：

```text
刚才第二点
上一个方案
最后一个例子
```

Answer Generator 发布后可生成轻量结构索引：

```text
answer_id
sections / bullets stable refs
```

该索引只用于指代解析，不是 Evidence。

---

# 4. Stage-1 输出改造

Stage-1 从“返回完整 standalone 世界”收敛为：

```text
UnderstandingResult
+ ConversationDelta
+ TaskDelta
+ IdentityDelta
```

例如：

```text
User: 那第二步为什么这么做？
```

可输出：

```text
reference_target = previous_answer.step_2
intent = explain_reason
requested_facets += reason
```

Runtime 合并到 State 后，Main 再决定是否需要 retrieve/reuse/clarify。

Stage-1 失败时允许保守保留原 user message 给 Main，而不是 Harness 用启发式替 Main 完成语义决策。

---

# 5. Controller Prompt 收口

目标输入优先级：

```text
System/Controller Policy
Runtime deterministic facts
TaskState
IdentityState
Evidence summary / gaps
latest User Event / instruction
recent conversation snippets (必要时)
```

逐步去掉 `ConversationContext.to_prompt()[:1200]` 作为长期记忆核心。

不是要求一次删掉 `ConversationContext`；可以保留为序列化 view，但其内容来源必须是 Structured State。

---

# 6. Controller Policy 与 Answer Policy 分离

当前 `agent_prompt` 主要影响 Answer Generator。本阶段正式拆分：

```text
controller_instruction
answer_instruction
```

优先级：

```text
System Safety / Evidence / Harness Policy
> Controller/Agent preset policy
> 当前 User Instruction
```

这里 User Instruction 不能覆盖系统安全边界，但可以改变调查策略。

示例：

```text
Controller preset: 优先用图谱确认实体关系，再做文本检索
Answer preset: 中文简洁回答
```

两者职责不能混。

---

# 7. 旧链路清理

P4 必须做一次源码审计，确认哪些旧语义已被新 Runtime 替代。

重点检查：

```text
request-local previous_cited SourceSummary fallback
_iter_reviewer_resume_loop 的专用决策语义
clarification_callback 的独立生命周期语义
loading 时仅靠 AbortController 的停止逻辑
Controller history [:1200] 强依赖
Agent prompt 只进 Answer 的旧单字段语义
```

原则：

- 能删除则删除；
- 必须兼容旧 API 的，只保留薄 adapter；
- adapter 内不得重新维护一套状态或语义规则；
- 所有临时兼容项必须写 TODO/移除条件和测试。

---

# 8. Graph Bootstrap 复核

基于 P0-P3 Trace 再判断自动 1-hop Bootstrap 是否仍满足：

```text
固定小成本
仅上下文初始化
不改变 answer subject
不批量授权 Evidence
```

若满足，保留为 Runtime bootstrap；若真实数据证明其成为明显的探索/成本动作，则迁移成 Main 可见 action。

禁止凭理论提前改掉。

---

# 9. 长上下文策略

不追求无限 history。

推荐：

```text
Structured State = 长期连续真源
recent turns = 近期语气/上下文
rolling summary = 较早对话压缩
Evidence Ledger = 事实真源
```

四者分工清晰。

Answer Generator 继续禁止把 rolling summary / assistant history 当事实来源。

---

# 10. 测试

## Unit

- TaskDelta merge
- IdentityDelta merge
- reference state resolve
- user constraints persistence
- controller/answer instruction separation
- compatibility adapter 不拥有 state

## Integration

### Case A：长对话引用

超过 10 轮后：

```text
回到刚才 PipelineWebGL 和 PipelineWebRTC 的第二个区别
```

能通过 Structured State/Answer refs 正确形成任务，不依赖完整旧回答塞进 Controller prompt。

### Case B：持续用户约束

早期用户要求“只根据知识库回答”；多轮之后仍保持，除非用户明确修改。

### Case C：已解决/未解决问题

TaskState 能区分已回答 facet 和仍缺 Evidence 的 facet，Main 不重复无意义检索。

### Case D：旧 API

旧 `/query/stream` 通过 adapter 行为与新 Session Runtime 一致，不产生第二套 Evidence/Clarify/Reviewer 状态。

---

# 11. 全量收口验收

P4 完成后执行母 PRD 的 G1-G8 全部场景，并补：

```text
- backend targeted
- backend full non-integration
- frontend tests
- frontend build/check
- py_compile/import smoke
- git diff --check
- Main real micro-chain ×3
```

真实模型 Trace 必须证明：

```text
state_id/task_id/snapshot_id 连续
```

而不是仅通过回答文本“看起来像记得”。

---

# 12. DoD

- [ ] Controller 主要读取 Structured Task/Identity/Evidence/Conversation State。
- [ ] 长期用户约束进入 TaskState。
- [ ] 支持引用上一答案结构而不把答案事实化。
- [ ] Stage-1 以 Delta 方式更新 State，不成为唯一长期记忆。
- [ ] controller_instruction 与 answer_instruction 分离。
- [ ] 旧 Reviewer Resume 专用语义退出。
- [ ] 旧 Clarify request-lifecycle 语义退出。
- [ ] SourceSummary 不再承担 Evidence Memory。
- [ ] 兼容 `/query` 仅为薄 adapter，或按确认方案删除。
- [ ] Graph Bootstrap 边界按真实 Trace 复核并记录决策。
- [ ] 无新增固定语义正则/特判替代 Main。
- [ ] 母 PRD G1-G8 全部通过。
- [ ] 全量回归和真实模型验收通过。

---

# 13. 最终状态

P4 完成后，项目的 Agent 架构应可正式描述为：

```text
Session-centric Agent Runtime

用户消息不是一次新 Workflow，
而是对持续 Agent State 的事件输入；

Main 负责语义决策，
Runtime 负责状态与协议，
Evidence Ledger 负责事实连续性，
Answer Generator 只基于 Frozen Snapshot 表达，
Reviewer 只审查并反馈 gap。
```

到这里，本轮总重构才允许结项。
