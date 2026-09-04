# Agent Controller 模型可见信息收束与 Runtime 控制面下沉执行 PRD

**状态：实施中**  
**优先级：P0**  
**日期：2026-09-04**  
**适用范围：Agent Controller / Runtime / Tool Registry / Observation / GraphWorkingSet / Prompt Assembly**

## 1. 背景

当前 Main Controller 已经拥有正确的语义决策职责：判断是否澄清、检索、补检、扩图谱、复用证据以及何时调用 `compose_answer`。问题在于 Runtime 同时把大量本应属于控制面的内部状态暴露给 Main，包括预算、调用计数、Guard/Fuse 状态、回调 provenance、内部 ID 集合、协议修复与执行统计。

这使 Main 不仅要判断“下一步最有价值的语义动作是什么”，还要理解“现在是第几次检索、还剩几次、哪个 Guard 生效、为什么工具不可用、第二次检索协议要求哪些字段”等机制细节。模型 reasoning 因而出现大量对用户无价值的运行时状态复述与规则自检。

本 PRD 的目标不是隐藏 reasoning，也不是减少 Main 的语义决策权，而是重新定义 Model-Visible Contract：**Runtime 自己能确定、限制、计数、过滤和校验的事情，由 Runtime 直接执行；只有真正需要语义判断的信息才进入 Main。**

## 2. 第一性原则

系统明确分为三个平面。

### 2.1 Semantic Plane：Main 应看到

Main 只看到会改变语义决策的信息：

- 用户当前意图；
- 当前确认主体与必要实体身份；
- EvidencePool 的语义覆盖；
- 当前缺失事实；
- 上一步工具实际带来的语义增量；
- 当前具有语义意义的图谱根与 frontier；
- 本步骤实际可调用的工具 schema。

### 2.2 Control Plane：Runtime 自己持有

以下内容不得作为普通 ControllerState/Observation 暴露给 Main：

- `max_steps` / `steps_used`；
- `max_retrieve_attempts` / `retrieve_attempts` / `remaining_retrieve_attempts`；
- Graph expansion budget；
- cycle detection / call fingerprint / call history；
- gap attempt registry / fuse / retry / timeout；
- tool ACL 与 tool availability 的计算原因；
- callback provenance、snapshot/trace/option ID；
- protocol validation / repair；
- execution accounting。

这些机制通过改变 Tool Surface、拒绝非法调用、停止循环或恢复语义状态来约束 Agent，而不是要求 Agent理解后主动遵守。

### 2.3 Observability Plane：Trace / 调试

以下信息继续完整保留，但只供 Trace、Evidence Console、测试与调试使用：

- 请求/执行/拒绝次数；
- Working/Citable/Gap Support 增量；
- Budget 使用；
- Reviewer 次数；
- Candidate Version；
- 协议修复次数；
- latency/token usage；
- Cycle/Fuse/Guard 状态。

**调试需要不等于模型需要。**

## 3. 非目标

### 3.1 不改 UI 默认展开

Main Controller reasoning 继续默认展开。工具卡继续默认折叠。Reviewer reasoning 继续不展示。

本次目标是让 reasoning 本身更干净，而不是把冗余 reasoning 折叠起来。

### 3.2 不削弱 Main 的语义自主权

Runtime 不得通过启发式接管以下判断：

- 是否需要澄清；
- 检索什么；
- 还缺什么事实；
- 是否扩图谱；
- 是否复用证据；
- 是否已有足够证据进入 `compose_answer`。

禁止重新退化为固定 Workflow。

## 4. 当前问题与改造

### 4.1 Budget 不再进入 Main Prompt

当前 `_controller_state_for_prompt()` 和 `_observation_history_for_prompt()` 会暴露完整 `AgentBudget.to_dict()`。本次删除以下 Main-visible 字段：

- `max_steps`；
- `steps_used`；
- `max_retrieve_attempts`；
- `hard_retrieve_cap`；
- `retrieve_attempts`；
- `remaining_retrieve_attempts`；
- `retrieval_allowed`；
- `call_history`；
- `retrieval_accounting`。

Budget 对象本身仍保留在 Runtime、Resume Snapshot 和 Trace 中。

### 4.2 Tool Visibility 成为唯一能力表达

如果当前还能调用 `retrieve_kb`，本步骤给模型该工具 schema；如果 Runtime 判断当前不能继续文本检索，则该工具 schema 本步骤不提供。

Main 不再看到：

```json
{"allowed_tools": [...], "retrieval_allowed": false}
```

再被要求自行理解限制。

**工具是否存在，就是当前行动空间。**

### 4.3 Graph budget 下沉

`GraphWorkingSet` 提供给 Main 的状态仅保留语义探索信息，例如：

- roots；
- frontier entities；
- 必要的已探索关系摘要。

删除：

- `remaining_expansion_calls`；
- `max_total_depth`；
- `expansion_allowed`；
- 纯执行统计性质的深度/数量字段（除非被证明确实改变语义判断）。

### 4.4 Guard / Fuse 不直接暴露

删除 Main-visible：

- `exploration_fuse_open`；
- `latest_denial_is_local`；
- 原始 budget denial 解释；
- call fingerprint / registry 状态。

Runtime 原始错误码仍进入 Trace。

Main 只接收必要的语义结果，例如：

- “该检索方向没有新增信息，请改换方向。”
- “针对该事实缺口的相同检索方向已无新增信息。”

如果工具已经从当前 Tool Surface 消失，则不要再重复告诉 Main 预算耗尽原因。

### 4.5 Clarification callback provenance 下沉

Main 不再看到 `clarification_callback=true`、`snapshot_id`、`option_id`、`published_trace_id`、`response_trace_id` 等恢复机制。

Resume 后只投影恢复完成的语义事实：

- 当前确认实体；
- 原始问题/解析后问题；
- 当前有效上下文。

### 4.6 内部实体 ID 收束

P0 先停止暴露整组 `registered_entity_ids`。Main 只保留当前已确认实体及其必要的稳定 ID，供现有 `focus_entity_id` 协议使用。

后续可进一步把 `focus_entity_id` 下沉为 Runtime 自动绑定，避免模型操作内部 ID，但不作为本次 P0 的强制阻塞项。

### 4.7 补检协议去除“第几次调用”认知

Main 不应该因为“这是第二次 retrieve”才知道要如何表达检索目的。

本次将补检语义统一为稳定的事实目标。优先引入/使用 `target_fact` 表达“本次检索试图补什么事实”。Runtime 自己负责：

- 这是第几次检索；
- 是否重复；
- 是否命中 exhausted gap；
- 是否还有预算。

`expected_gain` 不再作为 Main 必须理解“调用次数”后才能填写的字段；在兼容迁移期可继续接受旧字段，但 Prompt 不再要求 Main 根据次数决定是否填写。

## 5. Controller Semantic State

新增明确的模型可见状态投影，禁止继续把 Runtime 内部状态 `to_dict()` 后直接注入 Main。

目标结构示意：

```json
{
  "identity": {
    "status": "confirmed",
    "entity": "PipelineBuilder",
    "entity_id": "ent_xxx"
  },
  "task": {
    "resolved_intent": "了解 PipelineBuilder 的相关信息"
  },
  "evidence": {
    "coverage": "PARTIAL",
    "covered": ["工程设置", "数据设置", "发布格式"],
    "missing": ["整体定位"]
  },
  "graph": {
    "roots": ["PipelineBuilder"],
    "frontier": ["数据规范", "工程设置"]
  }
}
```

P0 不要求一次性生成完美自然语言 `covered/missing`；可以先用现有权威状态做最小语义投影，但必须保证控制面字段不再泄漏。

## 6. Observation Semantic Projection

建立单一 Main-visible Observation 投影，不允许把原始 Runtime Observation 整包塞入 Prompt。

Main-visible Observation 只回答：

1. 刚才执行了什么；
2. 得到了什么；
3. 对当前问题产生了什么语义变化。

原始执行计数、Guard 状态、内部错误码仍进入 Trace。

## 7. Controller Prompt V2

Prompt 从 Runtime 规则手册收缩为语义任务说明。

核心原则：

- 根据用户问题、确认实体、当前证据、语义 Observation 选择下一步最有价值动作；
- 只调用当前实际提供的工具；
- 缺事实则获取事实；
- 表达不足则澄清；
- 图谱关系不足则扩展图谱；
- 信息足够则调用 `compose_answer`；
- 不复述或讨论 Runtime 预算、调用次数、Guard、Fuse、callback provenance、协议修复与执行统计。

删除 Prompt 中所有“如果第 N 次调用”“剩余预算”“某 denial code 属于全局/局部”等机制教学。

## 8. Tool Handler 继续负责确定性校验

Main 决策仍不等于执行权限。Tool Handler / Runtime 继续验证：

- focus entity 合法性；
- graph root 合法性；
- cycle；
- budget；
- gap exhaustion；
- fuse；
- ACL；
- snapshot provenance；
- schema/protocol。

模型不需要知道这些校验是如何实现的。

## 9. 实施阶段

### Phase 1：建立 Model-Visible Contract

- 新增/收敛 `ControllerSemanticState`（名称可按现有代码风格调整）；
- 明确 Runtime Internal / Controller Semantic / Trace 三种状态所有权；
- 禁止 Controller Prompt 直接复用 Runtime `to_dict()`。

### Phase 2：Budget / Guard / Callback / Graph 控制面字段移除

修改至少：

- `_controller_state_for_prompt()`；
- `_observation_history_for_prompt()`；
- `GraphWorkingSet.to_controller_state()` 或替换为 semantic projection；
- Controller Prompt。

### Phase 3：Tool Visibility 单点真源

- 根据 Runtime 当前合法能力构造本步骤实际 Tool Schema；
- 删除 `ControllerState.allowed_tools`；
- 删除 Prompt 中对应二次约束。

### Phase 4：Observation 语义化

- 原始 Observation 继续用于 Trace；
- Main Prompt 使用单独 compact/semantic projection；
- 不再暴露 budget、guard constraints、call history 等内部字段。

### Phase 5：Callback 与补检协议收束

- callback provenance 从 Main Prompt 删除；
- 恢复后只保留语义结果；
- 去除“第二次检索必须因为次数填写 gap/expected_gain”的 Main Prompt 规则；
- Runtime 继续兼容/校验旧字段，逐步迁移至稳定语义目标。

### Phase 6：第二入口与 Answer / Reviewer Snapshot 收束

代码审计发现仅删除 `ControllerState` 字段不足以形成真正的 Model-Visible Contract，必须同时覆盖其他模型输入入口：

- `ConversationContext.to_prompt()` 不得输出 `evidence_epoch`、`topic_shift`、`entity_transition`、`clarification_callback` 等 Runtime provenance；
- Answer Generator Prompt 不得输出检索次数、Snapshot ID、Evidence version 等执行/版本信息；
- Frozen Snapshot 默认只包含知识证据与必要 Conversation 语义，不得无条件把 Guard、Step、Runtime Event、原始 Tool Observation data 转成模型证据；
- 只有 `direct_candidate` 等强即时上下文回答才允许显式投影 Runtime Semantic Evidence，并且只能输出用户可理解的执行结果，不输出 Guard reason、内部 ID 或原始 observation.data；
- Main Controller 协议失败后与 Reviewer 一致，使用完全相同的语义 Prompt 干净重试一次，不把 `validation_error`、`previous_response` 或 repair 指令喂回模型；
- Tool Surface 在提供给 Main 之前同时过滤 permission 与 confirmation-required 等 Runtime 已确定不可直接执行的工具；
- Controller 的 EvidenceDigest 只保留事实、归属、Evidence Class、Support Scope 和缺口，不暴露 chunk_id、Working 计数或 Evidence version。

### Phase 7：真实 reasoning 验收

UI 默认展开保持不变，直接验证真实 Main reasoning 的内容质量。

## 10. 测试与 DoD

### 10.1 Contract 测试

Controller Prompt/State 不得出现：

```text
max_steps
steps_used
max_retrieve_attempts
retrieve_attempts
remaining_retrieve_attempts
hard_retrieve_cap
call_history
retrieval_accounting
remaining_expansion_calls
max_total_depth
clarification_callback
exploration_fuse_open
allowed_tools
```

### 10.2 Tool Surface 测试

当 `budget.can_retrieve() == False` 时，传给 Main 的工具 schema 中不存在 `retrieve_kb`。

Graph expansion 同理。

### 10.3 Trace 保留测试

Main 不再看到 Budget 后，Trace 仍必须完整记录原有执行统计与控制状态。

### 10.4 Callback Resume 测试

Main 可见已确认实体与恢复后的问题语义，不可见 callback provenance。

### 10.5 Reasoning Micro-chain

真实模型连续运行至少三次典型链路，reasoning 不得再出现：

- “还剩 X 次预算”；
- “这是第 X 次检索”；
- `steps_used`；
- `remaining_retrieve_attempts`；
- `allowed_tools`；
- `clarification_callback=true`；
- “根据第二次检索协议必须……”。

合理目标形态：

```text
已确认目标是 PipelineBuilder。当前只有关系信息，缺少具体功能说明，先检索相关资料。
```

而不是：

```text
当前 retrieve_attempts=0，还有两次预算；allowed_tools 包含 retrieve_kb，因此这是第一次检索，gap 可以为 null……
```

### 10.6 UI 验收

- Main reasoning 默认展开：保持；
- Tool 默认折叠：保持；
- Reviewer reasoning 不展示：保持。

### 10.7 代码验收状态（2026-09-04）

已完成第二轮代码审计与修复：

- Controller / Reviewer / Publication 专项：`401 passed，19 deselected`；
- 全量非 integration 分批回归：`1595 passed，31 deselected，0 failed`，另有 `16 subtests passed`；
- `git diff --check` 无内容错误，仅存在既有 LF/CRLF 转换警告；
- Main 与 Reviewer 均不再使用“把 Validator 错误喂回模型修协议”的 LLM repair 模式；
- 普通知识回答默认不再把 Runtime/Guard/Tool raw data 冻结进模型 Snapshot；
- 强即时上下文回答仍保留 Runtime Semantic Evidence 能力。

当前 PRD 仍保持“实施中”，唯一剩余阻塞项是 Phase 7 的真实模型 reasoning 验收。只有真实链路稳定通过后才可移动至 `04_已完成归档`。

## 11. 最终架构原则

> **Main 负责理解世界，Runtime 负责限制世界。**

最终数据流：

```text
用户问题
  ↓
Conversation / Identity / Evidence
  ↓
Runtime Control Plane
  ├─ Budget / Guard / Fuse / Cycle / ACL / Callback / Protocol
  ├─ Trace / Debug
  ↓ 只投影语义事实 + 当前真实 Tool Surface
Main Controller
  ↓
语义决策 / Tool Call
  ↓
Runtime 确定性校验与执行
  ↓
Semantic Observation
  ↓
下一 Main Step
```

本 PRD 的最终 DoD：

- 机制约束 → Runtime 直接执行；
- 语义选择 → Main 自主判断；
- 执行统计 → Trace 保留；
- 用户 reasoning → 默认展开，但只呈现真正有价值的语义判断。
