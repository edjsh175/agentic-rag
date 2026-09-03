# 阶段 P3：运行中用户 Steering 与单动作执行 PRD

## 0. 文档信息

- **状态**：待执行
- **基线日期**：2026-09-02
- **上位 PRD**：`2026-09-02-会话级AgentRuntime与持续证据记忆总体重构PRD.md`
- **前置依赖**：P0、P1、P2 完成
- **目标**：允许用户在 Agent 运行中继续输入自然语言指令，改变后续调查方向；支持“只执行下一动作/执行后暂停”，但不开放绕过 Main/Harness 的原始工具 RPC。

---

# 1. 当前问题

当前前端 `loading=true` 时 ChatInput 被禁用；运行中的 Agent 不能接收：

```text
方向不对，换查 PipelineBuilder
先别回答
只扩一跳图谱
保留刚才证据，再查 Linux 配置
```

用户只能 Stop，然后发起新请求，导致 Task/Evidence/Execution 状态被割裂。

---

# 2. 第一性原则

1. 用户 Steering 是高优先级输入事件，不是工具后门。
2. Main 仍负责把自然语言转换为工具语义和参数。
3. Harness 仍是每个 Tool Call 的强制协议/安全边界。
4. 普通 intervention 在下一个 Decision Boundary 生效；首版不做任意工具线程抢占。
5. “只做一个动作”通过短生命周期 ExecutionConstraint 表达，不引入大量模式 flag。
6. 用户纠偏应更新 Task/Identity State，并自然触发 Evidence Epoch/资格变化。

---

# 3. USER_INSTRUCTION Event

新增：

```text
USER_INSTRUCTION
```

payload 至少包含：

```text
content
```

可由 Stage-1/Main 解析出：

```text
instruction_intent
identity_delta
task_delta
execution_constraint
```

Runtime 不用正则硬编码具体工具语义。

---

# 4. ExecutionConstraint

首版只实现必要字段：

```text
allowed_tools: optional set
forbid_finalize: bool
pause_after_action: bool
expires_after_steps: int = 1
```

约束必须：

- 有明确来源 event_id；
- 默认只影响下一 Controller step；
- 执行/过期后自动清除；
- 不能放宽 Harness 或 Evidence 规则。

## 示例

用户：

```text
从 PipelineBuilder 向外扩一跳，先别回答。
```

允许形成：

```text
allowed_tools = [expand_graph_scope]
forbid_finalize = true
pause_after_action = true
expires_after_steps = 1
```

Main 仍决定：

```text
start_entities
direction
relation_types
additional_hops
```

Harness 仍校验实体/参数/预算。

---

# 5. 纠偏语义

用户：

```text
方向不对，我说的是 PipelineBuilder，不是 PipelineWebGL。
```

正确链路：

```text
USER_INSTRUCTION
→ Stage-1 semantic delta
→ IdentityState correction
→ identity/evidence epoch update
→ 旧 WebGL Evidence FROZEN/STALE for direct citation
→ Main Controller
```

禁止：

```text
if "方向不对" → 特殊 PipelineBuilder 流程
```

---

# 6. Tool 正在执行时的事件

首版规则：

```text
ordinary USER_INSTRUCTION
→ enqueue
→ 当前 Tool 正常结束
→ 写 Tool Observation
→ 消费最新 intervention
→ Main next decision
```

若用户发送 CANCEL：

- 尽最大可能取消当前请求/流；
- 无法安全中断的底层调用允许完成，但结果不得继续触发后续 Main action；
- Runtime 最终状态为 CANCELLED。

---

# 7. 输入优先级

Controller 每次决策应读取：

```text
System Policy
> Harness facts / Runtime state
> latest unconsumed User Instruction
> active Task/Identity state
> Evidence/Gap observations
```

这里的 `>` 表示约束优先级，不意味着 Runtime 替 Main 做语义决定。

如果用户新指令与旧计划冲突，应以最新有效用户意图更新 TaskState，再由 Main 重新规划。

---

# 8. 前端改造

## 8.1 ChatInput

Agent RUNNING 时不再整体 disabled。

UI 根据状态：

```text
IDLE → 正常发送新消息
RUNNING → 发送给正在运行的 Agent
WAITING_USER → 回答澄清/也可输入补充
PAUSED → 发送补充或继续
```

## 8.2 显示

运行中用户发送的 intervention 仍应出现在聊天时间线，但标记其属于当前 active run，而不是误显示为已启动一个并行新回答。

## 8.3 Stop

保留 Stop，映射 `CANCEL` Event，而不是只在浏览器 AbortController 层取消显示。

---

# 9. API / SSE

使用 P2 统一接口：

```text
POST /agent/sessions/{session_id}/events
```

SSE 继续输出：

```text
user_instruction_received
state_transition
controller_decision
tool_start/tool_result
paused/cancelled
```

不强制引入 WebSocket。

---

# 10. 主要测试

## Unit

- ExecutionConstraint 创建/过期/消费
- constraint 不可绕过 Harness
- queued intervention 顺序
- latest instruction / task delta 合并
- cancel state

## Integration

### Case A：运行中换方向

Agent 正在查 WebGL；用户指令改查 Builder。

验证：当前 tool 完成后不继续旧计划；下一 Main decision 能看到 intervention；旧证据不自动支撑 Builder。

### Case B：单动作后暂停

```text
只查一跳图谱，先别回答
```

严格一次合法 graph action 后 PAUSED，无 Answer Generator 调用。

### Case C：保留旧 Evidence + 新检索

```text
刚才 A/B 保留，再查 Linux 配置
```

Main 可 reuse 当前仍相关的 A/B，再 retrieve C；最终 Evidence 状态正确。

### Case D：取消

运行中 CANCEL 后不会继续生成答案/Reviewer 发布。

### Case E：恶意/非法 tool request

用户要求绕过身份或直接引用未准入证据，应被 Harness/Evidence rules 拒绝，不因 `allowed_tools` 放宽。

---

# 11. DoD

- [ ] RUNNING 时前端可发送新用户指令。
- [ ] USER_INSTRUCTION 进入统一 Event queue。
- [ ] 新指令在下一 Decision Boundary 被 Main 消费。
- [ ] ExecutionConstraint 只有最小四字段且短生命周期。
- [ ] 支持“一次动作后暂停”。
- [ ] 用户纠偏能更新 Task/Identity/Evidence 状态。
- [ ] Stop 映射服务端 CANCEL 状态。
- [ ] 不存在通用绕过 Main 的用户 `/execute_tool` 后门。
- [ ] Harness/Working-Citable/Reviewer Publication 边界未弱化。
- [ ] 前端测试/build 与后端专项回归通过。

---

# 12. 阶段结束状态

P3 完成后用户与 Agent 的关系从：

```text
用户发问题 → Agent 跑完 → 用户才能再说
```

升级为：

```text
用户发目标
↕
Agent 持续执行
↕
用户可随时补充/纠偏/暂停/继续
```

但长期上下文仍需 P4 Structured State 收口，避免复杂会话继续过度依赖 history 文本裁剪。
