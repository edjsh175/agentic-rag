# Agent 用户可见 Block 流与执行详情分层架构迁移 PRD

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 日期 | 2026-08-25 |
| 状态 | **历史语义已被 2026-09-02 新总 PRD 部分替代；仅保留 Tool/Trace/Final Answer/Agent-Linear 隔离等不冲突背景，不再作为 reasoning/public explanation/Reviewer 纠错展示的实施真源** |
| 所属域 | `02_RAG检索与回答` |
| 任务性质 | **架构迁移 / 旧展示架构替换，不是 UI 叠加优化** |
| 改造对象 | Agent 用户可见执行流、前端 Assistant Message 数据模型、SSE→UI 投影层、Reasoning/Tool/System Event/Markdown Block、Trace/Debug 详情入口、旧兼容组件清理 |
| 不改对象 | Agent Controller 控制权、Evidence/Grounding 业务规则、Tool Runtime 语义、Finalizer 审核协议、Linear/Pipeline 阶段式 UX |
| 核心目标 | **把 Agent 主聊天界面从“后端执行日志 Timeline”迁移为“真实 LLM 生命周期驱动的用户可见 Block Stream”，同时完整保留 QA Trace 的工程可观测性。** |
| 核心裁决 | Agent 主界面只允许四类用户可见 Block：`Reasoning`、`Tool`、`System/Event`、`Markdown`。后端完整 ExecutionEvent 继续进入 QA Trace，但不得再默认一一映射为主聊天节点。 |
| 兼容裁决 | Linear / Pipeline 模式继续使用既有 `status / pipeline stage → answer → sources`，本 PRD 不将其伪装成 Agent Block Stream。 |
| 正确性裁决 | strict grounding 下 Candidate 正文继续服务端缓冲；只有 Grounding Review 后的 `final_answer` 才能进入 Markdown Block。不得为了“流式感”提前泄露未经审核 Candidate。 |
| 关联文档 | `2026-08-24-Agent全链路透明执行流PRD.md`、`2026-08-24-HelperLLM回答Grounding审查执行PRD.md`、`2026-08-24-Main单控制器与Agent收敛执行PRD.md` |
| 覆盖关系 | **本 PRD 曾覆盖 `2026-08-24-Agent全链路透明执行流PRD.md` 的主聊天 Timeline 裁决；自 2026-09-02 起，关于 reasoning 主备语义、Model Stream、三段 Main reasoning、Reviewer Finding 与 Rewrite 纠错展示，改由新总 PRD 及 01–05 子 PRD 统一裁决。** |
| 新权威入口 | **`2026-09-02-Agent原生Reasoning与证据纠错透明执行流重构总PRD.md`。发生冲突时以 2026-09-02 总 PRD 及 01–05 子 PRD 为准。** |

---

## 1. 背景与问题定义

上一版“全链路透明执行流”解决了两个真实问题：

1. Agent 执行过程不再完全黑盒；
2. Main / Helper LLM 原生 reasoning、Tool 生命周期、Grounding 生命周期已经能够进入 SSE 与 Trace。

但上一版产品层把“系统内部可观测事件”与“用户主界面应看到的执行节点”近似等同：

```text
ExecutionEvent
≈
Agent Timeline Item
≈
主聊天界面一行
```

因此当前 Agent 消息会把：

```text
understanding
decision
guard
tool_start / tool_result
evidence_update
evidence_gap
finalization_check
candidate_status
review_status
rewrite_status
publication
error
llm_reasoning_*
```

大量异质事件放进同一个 `AgentStepStream`。

结果是：

```text
用户提出一个问题
→ 先看到一堆“意图理解 / 控制器决策 / 执行守卫 / 证据增量 / 证据门禁”
→ 真正的模型思考和 Tool Call 只是其中几行
→ 整体像固定工程流水线日志
→ 不是“模型实际思考 → 调工具 → 得结果 → 继续思考 → 最终回答”的动态 Agent 体验
```

这与 DeepSeek-Harness 一类“LLM Runtime Lifecycle / Block Stream”范式存在根本差异。

本 PRD 的目标不是给现有 Timeline 换皮，而是重新划分：

```text
运行时事实
≠
用户界面事实
```

---

# 2. 本次迁移的第一性原则

## 2.1 从零设计时，用户真正需要看到什么

对于 Agent 模式，普通用户真正需要知道的是：

```text
模型现在在想什么
模型实际调用了什么工具
工具正在执行 / 成功 / 失败
是否发生了影响回答结果的重要系统转折
最终正式答案是什么
```

用户不需要主界面持续展示：

```text
Evidence version +1
Guard allow=true
Finalization admissibility=...
Candidate V1 created
coverage=PARTIAL
review_count=1
publication.final_mode=grounded_partial
```

这些信息对调试很重要，但属于 QA Trace / Inspect 层。

因此新的产品层必须建立：

```text
Backend Runtime Event Stream
           │
           ├──────────────→ QA Trace / Debug Detail
           │                 全量、稳定、工程可观测
           │
           └──────────────→ User-visible Projection
                             稀疏、语义化、按需
                                   │
                                   ├─ Reasoning Block
                                   ├─ Tool Block
                                   ├─ System/Event Block
                                   └─ Markdown Block
```

## 2.2 “透明”不等于“把所有日志都显示出来”

本 PRD 对透明度重新定义为：

> **真实行为透明，而不是内部日志倾倒。**

透明的是：

```text
Main LLM 真正产生的 reasoning
Main 真正触发的 ToolCall
Tool Runtime 的真实输入、运行状态、结果和错误
影响回答生命周期的重要系统转折
最终经过证据审核允许发布的答案
```

不透明到主界面、但必须可追踪的是：

```text
Harness allow/deny 细节
EvidenceDelta 计数
Evidence Gate 内部状态
Finalization eligibility
Reviewer 原始 reasoning
Reviewer claim_reviews 完整 JSON
Snapshot/version/provenance 工程字段
内部 fallback / protocol diagnostics
```

这些必须保留在 Trace，而不是删除。

---

# 3. 修改前必须建立的架构控制权矩阵

实施模型在修改任何代码前，必须先输出并核对以下矩阵。若无法正确复述，不得进入编码阶段。

| 职责 | 唯一拥有者 | 其他模块禁止行为 |
| --- | --- | --- |
| Agent 下一步行为决策 | Main LLM Controller | 前端、Projection、Trace 不得重新推断 Agent 决策 |
| Tool 实际执行 | Tool Runtime / Handler | UI 不得根据 `decision` 预造成功 Tool Block |
| Evidence 状态 | EvidencePool / Gate / Runtime | UI 不得把 Evidence 状态伪装成 Agent 动作 |
| Grounding 审查 | Helper Grounding Reviewer + Finalizer 协议 | UI 不得重新判断 PASS/REVISE |
| 用户可见 Reasoning | **Main LLM 原生 reasoning channel** | 不得用 `decision.reason`、模板文案或 Trace 摘要伪造 reasoning |
| 用户可见 Tool | `tool_start` / `tool_result` 真正生命周期 | `decision.tool` 只能作为内部事件，不能单独创建“已调用工具”节点 |
| 用户可见 System/Event | **SSE→UI Projection 白名单规则** | 不得把所有 ExecutionEvent 自动降级成 System/Event |
| 用户可见最终答案 | Grounding 后的 `final_answer` | Candidate / token buffer 不得提前进入正式 Markdown |
| 工程全量可观测 | QA Trace / Debug | 主聊天 UI 不承担调试控制台职责 |
| Linear/Pipeline 过程展示 | 既有 Stage Status | 不得强制转换成 Agent Block Stream |

---

# 4. 硬 Invariants

以下不是“建议”，而是验收不变量。任一违反即视为本 PRD 未完成。

## INV-UI-01：Agent 主界面只有四类 Block

生产 Agent 主消息流中，只允许：

```text
reasoning
tool
system_event
markdown
```

Clarification Card、Sources、Feedback 属于消息级交互/附属信息，不计为执行 Block。

不得继续出现：

```text
understanding block
decision block
guard block
evidence_update block
evidence_gap block
finalization block
candidate block
review_status block
publication block
```

## INV-UI-02：Main Reasoning 必须来自真实 provider reasoning

只有：

```text
llm_reasoning_start
llm_reasoning_delta
llm_reasoning_end
```

且：

```text
data.role == "main"
```

才能创建或更新 Reasoning Block。

不得用以下内容替代：

```text
decision.reason
decision.thought
understanding.summary
review.summary
固定“正在分析...”模板
根据 ToolCall 反推的伪思考
```

## INV-UI-03：Helper reasoning 不进入普通聊天主界面

以下调用即使后端真实产生 reasoning，也只保留在 Trace：

```text
Stage-1 Helper
Grounding Reviewer #1/#2
其他 role=helper 的 reasoning
```

用户当前明确需要的是 Agent Main 模型的思考过程。

## INV-UI-04：Tool Block 只能由真实 Tool 生命周期创建

Tool Block 的生命周期必须是：

```text
tool_start
→ running
→ tool_result
→ completed / failed / denied
```

`decision(action=tool_call)` 本身不得创建一个虚假的已执行 Tool Block。

## INV-UI-05：System/Event 必须是稀疏白名单，不是日志兜底

禁止：

```text
未知 ExecutionEvent
→ 一律变成 System/Event
```

System/Event 只允许表达**会改变用户对当前回答生命周期理解的重要转折**。

## INV-UI-06：Reviewer REVISE 必须用户可见

当 Reviewer #1 返回：

```text
verdict = REVISE
```

主界面必须出现一个 System/Event Block，表达语义：

```text
刚刚的候选回答没有通过证据审查，正在重新组织。
```

文案可以细化，但语义必须保持：

```text
候选被拒绝
+
系统正在重组/修正
```

不得把 REVISE 静默隐藏。

## INV-UI-07：Reviewer PASS 不默认创建“审核通过”日志行

正常 PASS 属于成功路径内部事实，默认不单独刷一行。

用户通过最终答案出现即可知道流程完成。

若产品未来需要显示审核徽章，应作为独立需求，不在本 PRD 追加。

## INV-UI-08：最终 Markdown 只能来自 `final_answer`

strict grounding Agent 下：

```text
Candidate V1
Candidate V2
模型 content stream
```

不得进入正式 Markdown Block。

只有：

```text
final_answer
```

可以创建最终 Markdown。

## INV-UI-09：最终答案到达前不得伪造“模型正在逐字生成正式答案”

允许：

```text
final_answer 到达后一次性渲染
```

也允许未来做纯视觉 reveal，但必须明确它只是 UI 动画；本 PRD 不要求伪造 token stream。

## INV-UI-10：完整 ExecutionEvent 必须继续写入 Trace

本次主界面收敛不得通过“后端不再发 / Trace 不再记”来实现。

至少以下工程事实必须继续可审计：

```text
understanding
decision
guard
tool_start/tool_result
evidence_update/evidence_gap
finalization_*
evidence_snapshot_created
candidate_status
review_status
rewrite_status
publication
error
llm_reasoning_* 元数据
```

## INV-UI-11：Agent 与 Linear 必须继续模式隔离

Agent：

```text
Block Stream
```

Linear/Pipeline：

```text
Stage Status
→ Answer
→ Sources
```

Linear 不得因本次迁移产生 Agent reasoning/tool block 污染。

## INV-UI-12：用户可见状态必须是单一来源

新架构完成后，一个 Agent turn 不得长期同时依赖：

```text
thinking
agentTools
timelineItems
blocks
```

四套并行状态。

目标是：

```text
blocks = 用户可见执行流唯一来源
```

Trace 是调试来源；`content/sources/clarification` 是消息业务数据，不再承担重复执行轨迹。

---

# 5. 用户可见 Block 数据模型

## 5.1 总体模型

建议收敛为 discriminated union：

```ts
type AssistantBlock =
  | ReasoningBlock
  | ToolBlock
  | SystemEventBlock
  | MarkdownBlock
```

所有 Block 至少包含：

```ts
interface BaseBlock {
  id: string
  kind: 'reasoning' | 'tool' | 'system_event' | 'markdown'
  sequence: number
}
```

`sequence` 必须来自前端接收顺序或服务端稳定顺序，不能在渲染时按类型重新排序。

---

## 5.2 Reasoning Block

```ts
interface ReasoningBlock extends BaseBlock {
  kind: 'reasoning'
  callId: string
  stage: 'agent_controller' | 'answer_generation' | 'grounded_retry' | string
  role: 'main'
  model?: string
  provider?: string
  text: string
  status: 'running' | 'completed' | 'unavailable' | 'error'
  elapsedMs?: number
}
```

行为：

```text
llm_reasoning_start
→ 创建 Block，running

llm_reasoning_delta
→ 按 call_id 原位追加 text

llm_reasoning_end
→ completed / unavailable / error
```

同一个 `call_id` 不得产生多个 Reasoning Block。

多个 Main 调用必须分块：

```text
Main · Controller
Main · Answer
Main · Rewrite
```

不得拼成一个无法区分生命周期的大文本。

---

## 5.3 Tool Block

```ts
interface ToolBlock extends BaseBlock {
  kind: 'tool'
  toolCallKey: string
  tool: string
  label: string
  input?: unknown
  output?: unknown
  status: 'running' | 'completed' | 'failed' | 'denied'
  elapsedMs?: number
  error?: string | null
}
```

Tool Block UI 应保留当前 `AgentStepStream.vue` 已有的优秀交互特征：

```text
24px 单行 disclosure
running sweep / active state
完成后原位状态跃迁
IN / OUT 折叠详情
耗时
错误状态
```

但 Tool Block 不再与 Guard/Evidence/Reviewer 等节点混排为同类日志。

---

## 5.4 System/Event Block

System/Event 是本 PRD 新增且明确保留的第四类。

它不是 Runtime 日志容器，而是：

> **用户需要知道、且无法由 Reasoning / Tool / Final Answer 自然表达的重要生命周期转折。**

建议模型：

```ts
interface SystemEventBlock extends BaseBlock {
  kind: 'system_event'
  event: string
  level: 'info' | 'warning' | 'error'
  text: string
  status?: 'active' | 'completed' | 'failed'
  correlationId?: string
}
```

### 首版允许进入主界面的白名单

#### A. Reviewer REVISE

源事件：

```text
review_status.verdict == REVISE
```

用户语义：

```text
候选回答未通过证据审查，正在重新组织。
```

推荐行为：创建：

```text
review-revise:<review_count>
```

System/Event Block。

#### B. Rewrite 失败 / 二审仍无法发布

当 Grounded Rewrite 或最终 Grounding 明确失败并导致候选无法发布时，应显示简洁错误事件，例如：

```text
回答修正失败，本次未发布未经证据支持的结论。
```

不得把 `claim_reviews`、coverage、内部错误堆到主界面。

#### C. Answer / Controller 等导致本轮无法继续的用户可感知错误

例如：

```text
回答模型调用失败
Agent Controller 决策失败并 fail-safe 终止
```

可映射为 error System/Event。

但具体堆栈、code、内部阶段仍进入 Trace。

#### D. 已有 operational notice 中确实影响用户体验的事项

例如模型显存降级等已有用户通知，可以继续映射为 System/Event；必须经过明确白名单，不得自动显示全部 notice。

### 默认不得进入 System/Event 的事件

```text
understanding
decision
guard allow/deny
evidence_update
evidence_gap
finalization_check
candidate_status
review PASS
publication success
heartbeat
snapshot created
```

这些属于 Trace。

---

## 5.5 Markdown Block

```ts
interface MarkdownBlock extends BaseBlock {
  kind: 'markdown'
  text: string
  status: 'final'
}
```

Agent strict grounding 下来源唯一：

```text
final_answer
```

Final Answer 与 Sources 分离：

```text
Markdown Block
↓
Sources 区域
```

Sources 不是第五种执行 Block。

---

# 6. SSE → UI Projection Matrix

这是本次迁移的核心规则表。

| Backend Event | QA Trace | Agent 主界面 | 投影结果 |
| --- | --- | --- | --- |
| `llm_reasoning_start` + `role=main` | 保留 | 显示 | create Reasoning Block |
| `llm_reasoning_delta` + `role=main` | 保留 | 显示 | append Reasoning Block |
| `llm_reasoning_end` + `role=main` | 保留 | 显示 | finalize Reasoning Block |
| `llm_reasoning_*` + `role=helper` | 保留 | 不显示 | Trace only |
| `tool_start` | 保留 | 显示 | create Tool Block running |
| `tool_result` / `tool_end` | 保留 | 显示 | update same Tool Block |
| `review_status=REVISE` | 保留 | 显示 | System/Event：候选被拒绝，正在重组 |
| rewrite/review 导致无法发布的失败 | 保留 | 显示 | System/Event error |
| Controller/Answer 致命错误 | 保留 | 显示 | System/Event error |
| 明确白名单 `notice` | 保留 | 显示 | System/Event |
| `final_answer` | 保留 | 显示 | Markdown Block |
| `sources` | 保留/回答元数据 | 显示 | Sources 区域 |
| `clarify` | 保留 | 显示 | Clarification Card |
| `understanding` | 保留 | 不显示 | Trace only |
| `decision` | 保留 | 不显示 | Trace only |
| `guard` | 保留 | 不显示 | Trace only |
| `evidence_update` | 保留 | 不显示 | Trace only |
| `evidence_gap` | 保留 | 不显示 | Trace only |
| `finalization_*` | 保留 | 不显示 | Trace only |
| `candidate_status` | 保留 | 不显示 | Trace only |
| `review_status=PASS` | 保留 | 默认不显示 | Trace only |
| `publication=success` | 保留 | 默认不显示 | Trace only |
| `heartbeat` | 保留必要状态 | 不显示 | transport only |
| `status/pipeline` in Agent mode | 后端可兼容 | 不显示 | ignored by Agent projection |

原则：

```text
Trace 是全集
User Blocks 是白名单投影
```

不是：

```text
User Blocks 是全集减几个隐藏项
```

---

# 7. 前端架构迁移设计

## 7.1 当前 As-Is

当前 `Message` 同时存在：

```text
content
thinking
agentTools
timelineItems
```

`ChatMessage.vue` 同时引用：

```text
AgentStepStream
AgentThinkingBlock
AgentToolTimeline
```

`ChatView.createStreamHandler()` 又把大量 Backend Event 逐种 `upsertTimeline()`。

这导致：

```text
一个 Agent turn
→ 多个相互重叠的用户可见状态源
→ 历史兼容逻辑长期驻留
→ UI 语义被后端工程事件牵着走
```

## 7.2 To-Be

目标：

```text
Message
├─ blocks: AssistantBlock[]        # Agent 用户可见执行流唯一来源
├─ content                         # 可作为最终答案兼容/存储字段，但不再承担流式执行状态
├─ sources
├─ clarification
├─ feedback
└─ trace_id
```

Agent 主界面：

```text
ChatMessage
└─ AssistantBlockStream
   ├─ ReasoningRow
   ├─ ToolRow
   ├─ SystemEventRow
   └─ MarkdownRenderer
```

可以继续复用/提炼 `AgentStepStream.vue` 中已经成熟的 ReasoningRow / ToolRow 交互样式，但组件职责必须收窄。

## 7.3 推荐组件边界

实现可根据现有 Vue 结构最小化调整，但最终职责必须清晰：

```text
AssistantBlockStream.vue
├─ ReasoningRow.vue / 内部 Reasoning renderer
├─ ToolRow.vue / 内部 Tool renderer
├─ SystemEventRow.vue / 内部 System renderer
└─ Markdown block renderer
```

禁止重新发展成一个包含 15 种 Event `v-else-if` 的巨型 Timeline。

## 7.4 Projection 必须集中

SSE → Block 转换逻辑应有唯一位置，例如：

```text
createStreamHandler 内部专用 projection helper
```

或：

```text
agentBlockProjector.ts
```

二选一即可，禁止为了“架构漂亮”无必要拆层。

唯一要求：

> 不允许 `ChatView`、`ChatMessage`、Block Component 各自再猜一套事件语义。

---

# 8. 旧 → 新迁移表

本任务是**替换，不是叠加**。

实施期间必须维护以下 Migration Table，并在最终验收逐项确认 OLD 已退出生产路径。

| OLD | NEW | 动作 |
| --- | --- | --- |
| `ExecutionEvent ≈ AgentTimelineItem` | ExecutionEvent → whitelist Projection → `AssistantBlock` | **替换** |
| `timelineItems` 承载所有内部事件 | `blocks` 只承载四类用户语义 | **迁移并淘汰** |
| `thinking` 独立状态源 | Main reasoning → ReasoningBlock | **迁移** |
| `agentTools` 独立状态源 | Tool lifecycle → ToolBlock | **迁移** |
| `AgentStepStream` 15+ 类型混合渲染 | 轻量 Block Stream | **重构职责** |
| `AgentThinkingBlock.vue` legacy fallback | ReasoningBlock renderer | **引用归零后删除** |
| `AgentToolTimeline.vue` legacy fallback | ToolBlock renderer | **引用归零后删除** |
| Understanding/Decision/Guard/Evidence/Finalization 主界面行 | QA Trace / Debug | **从主界面移除，不删除 Trace** |
| Reviewer REVISE 普通审查行 | System/Event “候选被拒绝，正在重组” | **语义化投影** |
| Reviewer PASS 行 | 最终 Markdown 自然完成 | **从主界面移除** |
| Publication success 行 | Final Answer 出现 | **从主界面移除** |
| `traceId` 仅透传但 ChatMessage 不使用 | 可进入“执行详情”入口 | **补齐** |

验收重点不是：

```text
NEW 已经存在
```

而是：

```text
OLD 用户可见生产路径已经消失
```

---

# 9. QA Trace / 执行详情分层

## 9.1 后端 Trace 不降级

`qa_trace.py` 当前已有：

```text
execution_events
agent
model_calls
grounding.lifecycle_events
retrieval
answer
```

本次迁移不得减少这些工程信息。

## 9.2 主聊天提供轻量“执行详情”入口

当前 `ChatMessage.vue` 已接收 `traceId`，但普通消息主界面没有充分利用它。

本 PRD 要求：

```text
有 trace_id
→ 用户/开发者可以进入或打开执行详情
```

首版不要求在聊天消息内部复制整个 QaDebugView。

允许最小方案：

```text
“执行详情”按钮
→ 打开现有 QA Debug 对应 Trace
```

或项目现有路由/弹层可支持的等价实现。

具体交互以现有工程最小改造为准。

## 9.3 Debug 页面必须能够看到完整 execution_events

如果现有 `QaDebugView.vue` 尚未展示顶层 `execution_events`，本次必须补齐至少一个按 sequence 排序的原始/结构化事件查看区域，使以下事件仍可查：

```text
Decision
Guard
Evidence
Finalization
Reviewer
Rewrite
Publication
Reasoning metadata
Error
```

主聊天变干净，不能以牺牲可审计性为代价。

---

# 10. Forbidden Implementation

以下实现方式即使测试通过也判定失败。

## 10.1 禁止“隐藏旧 Timeline = 完成”

不得只写：

```vue
v-if="false"
```

或过滤若干类型，但继续以 `timelineItems` 作为事实模型。

必须完成用户可见数据模型的收敛。

## 10.2 禁止新增第五、第六类常驻 Block

不得因为实现方便重新添加：

```text
DecisionBlock
EvidenceBlock
ReviewBlock
FinalizationBlock
StatusBlock
```

用户可见执行 Block 只能四类。

## 10.3 禁止 System/Event 变成新的日志垃圾桶

不得：

```text
所有无法分类的 ExecutionEvent
→ SystemEventBlock
```

必须白名单。

## 10.4 禁止伪 reasoning

不得把：

```text
decision.reason
review.summary
模板文案
阶段 status
```

包装成 ReasoningBlock。

## 10.5 禁止展示 Helper Reviewer reasoning

Reviewer 的原始 reasoning 继续进入 Trace，但不进入普通聊天。

## 10.6 禁止为了流式 Markdown 绕过 Grounding

不得把未审核 Candidate token 恢复为用户正式答案流。

不得先显示 Candidate，再在 Reviewer REVISE 后“撤回”。

## 10.7 禁止永久保留双轨状态

不得最终形成：

```text
blocks 新路径
+
timelineItems 兼容路径
+
agentTools 兼容路径
+
thinking 兼容路径
```

全部长期运行。

历史数据兼容必须通过一次读取适配/迁移实现，而不是永久保留四套渲染组件。

## 10.8 禁止删除 Trace 事件来让 UI 变干净

UI Projection 与 Backend Observability 必须分层解决。

## 10.9 禁止修改无关 Agent 决策/证据逻辑

本 PRD 不是重新实施 Main 单控制器、Evidence Gate 或 Reviewer 协议。

除非为了事件字段一致性存在明确缺陷，否则不得借机改 Agent 控制语义。

## 10.10 禁止为了兼容旧前端测试保留违反新架构的生产路径

旧测试如果断言“Decision/Guard/Reviewer 必须显示在主 Timeline”，应更新测试表达新的产品不变量，而不是保留旧行为。

---

# 11. 历史消息兼容策略

历史本地/服务端消息可能包含：

```text
thinking
agentTools
timelineItems
```

本 PRD 不要求破坏历史会话。

但兼容方案必须满足：

```text
旧数据
→ load-time normalize
→ AssistantBlock[]
→ 新 Block Renderer
```

而不是：

```text
旧数据
→ 继续调用 AgentThinkingBlock / AgentToolTimeline / 老 AgentStepStream
```

历史 Timeline 中的内部事件：

```text
understanding / decision / guard / evidence / review PASS / publication...
```

默认不转换为用户 Block。

历史可转换内容：

```text
旧 thinking → ReasoningBlock（标记 legacy 如有必要）
旧 agentTools / tool_call timeline → ToolBlock
旧明确 review REVISE → SystemEventBlock（如果信息足够）
最终 content → MarkdownBlock
```

如果旧数据无法可靠恢复 call_id/生命周期，不得伪造精确运行时事实；可只保留最终答案和可确定内容。

---

# 12. System/Event 交互规范

System/Event 应视觉弱于 Reasoning 和 Tool，不抢占正文。

推荐：

```text
单行
小图标/状态点
轻量背景或无卡片背景
必要时 warning/error 色义
不默认展开大型详情
```

Reviewer REVISE 示例：

```text
⚠ 候选回答未通过证据审查，正在重新组织…
```

当 rewrite 后继续处理，可原位更新状态，而不是重复刷：

```text
REVISE
Rewrite started
Rewrite completed
Reviewer #2 started
Reviewer #2 PASS
```

五行。

建议生命周期：

```text
review_status=REVISE
→ create system_event(review-revise-1, active)

rewrite_status=completed
→ 可保持原文或更新为“已重新组织，正在完成最终校验”

最终 final_answer
→ system event 结束，不额外创建 PASS 行
```

实现无需过度动画化，重点是避免刷屏。

---

# 13. Reasoning / Tool UI 行为规范

## 13.1 Reasoning

沿用 DeepSeek-Harness 式原则：

```text
单行标题
最新一行实时跟随
running 时有轻量活动状态
可展开查看完整 reasoning
完成后显示耗时
历史块默认折叠
```

不得把每个 reasoning delta 生成新 DOM row。

长 reasoning 必须：

```text
同 call_id 原位 append
限制展开区域高度或允许滚动
避免全量 re-render 导致页面抖动
```

## 13.2 Tool

```text
tool_start
→ running + sweep

tool_result
→ 同一个 Block 原位 completed / failed
```

详情：

```text
IN
OUT
error
elapsed_ms
```

用户未展开时只显示简洁摘要。

## 13.3 顺序

Block 严格遵循事件实际到达顺序：

```text
Reasoning(controller)
Tool(retrieve_kb)
Reasoning(controller #2)
Tool(link_entities)
Reasoning(answer)
System(REVISE)
Reasoning(rewrite)
Markdown(final)
```

不得人为按“思考区域 / 工具区域 / 答案区域”重新分组打乱执行时序。

---

# 14. Linear / Pipeline 模式约束

本 PRD 不改变 Linear 的阶段式用户体验。

Linear 继续允许：

```text
正在理解问题…
正在检索知识库…
正在生成答案…
正在审核答案…
```

并继续使用：

```text
status / pipeline
```

但：

```text
Agent Block Stream 组件不得在 Linear 模式渲染
Agent llm_reasoning_* 不得泄漏到 Linear UI
```

本次不能为了共用组件而破坏两种模式边界。

---

# 15. 一次性实施协议

本任务允许只向实施模型提交**一次任务**，但“一次性实施”定义为：

> **一次任务提交、一次完整架构迁移、一次最终交付；任务内部必须经过多轮只读审计、自审、反证和真实验收。**

禁止执行模式：

```text
读 PRD
→ 大概理解
→ 连续改代码
→ pytest 绿
→ 宣布完成
```

必须执行：

```text
Phase A 只读全仓审计
→ Phase B 架构控制矩阵 + Implementation Map
→ Phase C 完整迁移实施
→ Phase D 删除旧生产路径
→ Phase E Adversarial Self-Review
→ Phase F 单元 + 架构验收测试
→ Phase G HTTP/SSE E2E
→ Phase H 真实模型 + Trace 验收
→ Phase I DoD 逐项签字
```

中途无需用户逐阶段批准；但任一阶段发现未满足条件，实施模型必须自行继续修复，不得提前宣布完成。

---

# 16. Phase A：只读全仓审计

编码前必须只读确认至少以下文件与调用链：

```text
web/src/types/index.ts
web/src/api/index.ts
web/src/views/ChatView.vue
web/src/components/ChatMessage.vue
web/src/components/AgentStepStream.vue
web/src/components/AgentThinkingBlock.vue
web/src/components/AgentToolTimeline.vue
web/src/utils/storage.ts
web/src/views/QaDebugView.vue

rag_knowledge/services/agent_orchestration/models.py
rag_knowledge/services/agent_orchestration/runtime.py
rag_knowledge/services/rag.py
rag_knowledge/services/qa_trace.py
rag_knowledge/services/answer_finalizer.py
rag_knowledge/api/routes.py
```

必须输出：

1. 当前 SSE Event Vocabulary；
2. 每种事件当前流向；
3. 当前用户 UI 状态源；
4. 当前持久化状态源；
5. 旧组件引用位置；
6. Trace 已保存但 Debug UI 尚未呈现的信息；
7. 工作树已有未提交修改，避免覆盖并发工作。

本阶段禁止写代码。

---

# 17. Phase B：Implementation Map

实施前必须形成：

```text
Backend Event
→ Projection Rule
→ Block mutation
→ Renderer
→ Persistence
→ Test
```

并明确：

```text
哪些文件修改
哪些文件删除
哪些现有逻辑直接复用
哪些旧测试需要改写
哪些后端完全不需要修改
```

优先最小改造。

如果后端已经拥有满足前端 Block 的稳定事件，不得为“架构完整”重复设计第二套 SSE 协议。

---

# 18. Phase C：实施要求

实施至少完成：

1. 定义 `AssistantBlock` 四类 union；
2. 建立 Agent SSE → Block 单一 Projection；
3. Main reasoning 按 `call_id` 原位更新；
4. Tool 按生命周期原位更新；
5. Reviewer REVISE → System/Event；
6. 关键不可发布错误 → System/Event；
7. `final_answer` → final Markdown；
8. ChatMessage 使用新 Block Stream；
9. Agent 不再显示旧 Execution Timeline 分段标题；
10. Linear 保持原有 Stage Status；
11. Sources / Clarification / Feedback 保持功能；
12. Trace 入口可访问；
13. Debug 能查看完整 execution_events；
14. 历史消息 load-time normalize。

---

# 19. Phase D：删除旧生产路径

实施模型必须专门停止新增功能，执行旧路径清理。

至少搜索：

```text
AgentThinkingBlock
AgentToolTimeline
timelineItems
agentTools
thinkingDuration
upsertTimeline
AgentStepStream
Execution Timeline
```

逐项判断：

```text
仍是新架构必要代码
OR
旧兼容残余
```

对因本次迁移已经失去职责的代码：

```text
删除引用
删除组件
删除类型字段
删除持久化字段
删除旧测试
```

不得长期留下“可能以后有用”的双轨实现。

如果某字段因服务端历史格式必须暂时保留，必须明确：

```text
仅数据兼容
不进入新生产渲染路径
```

---

# 20. Phase E：Adversarial Self-Review

完成代码后，实施模型必须暂时假设自己的实现是错误的。

目标不是证明正确，而是寻找违反本 PRD 的路径。

必须逐项反证：

```text
是否还有任何 Understanding 行会进入主聊天？
是否还有 Decision 行会进入主聊天？
是否还有 Guard 行会进入主聊天？
是否还有 Evidence/Finalization 行会进入主聊天？
是否还有 Reviewer PASS 行会进入主聊天？
是否还有 Publication success 行会进入主聊天？
是否还有 helper reasoning 会进入主聊天？
是否存在 decision.reason 被包装成 Reasoning？
是否存在 Candidate token 提前进入 Markdown？
是否存在 tool decision 未执行却生成 Tool Block？
是否存在未知事件自动成为 System/Event？
是否仍需同时维护 thinking + agentTools + timelineItems + blocks？
是否删 UI 的同时意外删掉 Trace？
是否历史消息仍走旧组件？
是否 Linear 被 Agent Block 污染？
```

任何一项存在，回到实施阶段修复。

---

# 21. Phase F：测试要求

## 21.1 前端单元测试

至少覆盖：

```text
test_main_reasoning_start_creates_one_block
test_reasoning_delta_appends_same_call_id
test_reasoning_end_completes_same_block
test_helper_reasoning_is_not_projected
test_tool_start_creates_running_block
test_tool_result_updates_same_block
test_decision_does_not_create_tool_block
test_review_revise_creates_system_event
test_review_pass_does_not_create_system_event
test_internal_events_do_not_render_in_chat
test_final_answer_creates_only_final_markdown
test_candidate_token_never_becomes_final_markdown_in_strict_agent
test_linear_mode_does_not_render_agent_blocks
test_legacy_message_normalizes_without_legacy_components
```

## 21.2 System/Event 故障路径

必须主动注入：

```text
Reviewer REVISE
Rewrite failed
Reviewer second-pass blocked
Controller error
Answer generation error
Tool failed
Tool denied
reasoning unavailable
stream interruption
```

验证：

```text
用户看到必要转折
但不会看到内部日志倾倒
```

## 21.3 架构验收测试

新增/收敛一组 Architecture Acceptance Tests，至少断言：

```text
Agent visible block kinds ⊆ {reasoning, tool, system_event, markdown}
helper reasoning visible count = 0
internal ExecutionEvent visible count = 0
review PASS system event count = 0
REVISE user-visible count = 1
ToolBlock without tool_start = 0
final Markdown source != candidate/token
Linear AgentBlock count = 0
legacy renderer production references = 0
```

架构测试的目标不是测 CSS，而是防止未来重新退化成 Timeline Log Dump。

---

# 22. Phase G：HTTP / SSE E2E

必须真实经过 `/api/query/stream`，不能只调用 Vue handler mock。

至少验证事件序列：

### Case A：普通一次检索

```text
Main reasoning
→ tool_start retrieve_kb
→ tool_result
→ Main reasoning
→ final_answer
```

前端：

```text
Reasoning
Tool
Reasoning
Markdown
```

内部 Decision/Guard/Evidence 不显示。

### Case B：二次补检

```text
reasoning
→ retrieve
→ reasoning
→ retrieve
→ answer
```

两个 Tool 必须按真实调用分别出现，不得被阶段 Timeline 合并或预造。

### Case C：Reviewer REVISE → Rewrite → PASS

后端：

```text
Main Answer reasoning
→ Candidate V1
→ Reviewer REVISE
→ Main Rewrite reasoning
→ Candidate V2
→ Reviewer PASS
→ final_answer
```

用户界面：

```text
Reasoning(Main Answer)
→ System/Event：候选回答未通过证据审查，正在重新组织
→ Reasoning(Main Rewrite)
→ Markdown(final)
```

不得显示：

```text
Candidate V1
Reviewer PASS
claim_reviews JSON
publication success
```

### Case D：Reviewer 无法发布

用户界面必须有简洁 System/Event error，不得显示未经审核 Candidate。

### Case E：Linear

继续显示 Pipeline Status；Agent Block Stream 为空。

---

# 23. Phase H：真实模型与 Trace 验收

使用当前真实 Main / Helper 模型链路运行，不以 mock 结束。

至少覆盖：

```text
Main Controller reasoning
真实 retrieve_kb
Main Answer reasoning
Reviewer PASS
```

以及：

```text
Reviewer REVISE
→ Main Grounded Rewrite reasoning
→ Reviewer #2
```

如果真实 REVISE 难稳定触发，可以使用现有真实微链固定用例，但必须至少有一条真实 provider reasoning + rewrite 链。

必须对账：

```text
SSE 原始事件
vs
QA Trace execution_events
vs
前端 User Blocks
```

关系必须满足：

```text
Trace = 原始事实全集
User Blocks = Trace/SSE 的合法白名单投影
```

而不是两套前端自行推断的平行真相。

---

# 24. Trace / UI 指标验收

至少机器统计：

```text
main reasoning projection coverage = 100%
helper reasoning visible = 0
real tool_start visible coverage = 100%
ToolBlock without matching tool_start = 0
REVISE system-event coverage = 100%
PASS system-event default count = 0
internal-event visible leakage = 0
final_answer → Markdown coverage = 100%
strict Candidate leakage = 0
Linear AgentBlock leakage = 0
SSE ↔ Trace execution event consistency = 100%
```

浏览器人工 UX 还必须确认：

```text
长 reasoning 不造成明显布局抖动
最新 reasoning 行能实时跟随
Tool running → result 原位跃迁
System/Event 不重复刷屏
REVISE 文案可见且顺序正确
Final Answer 不重复显示
Sources 仍可点击
历史消息可正常打开
页面刷新后 Block 顺序不乱
```

---

# 25. DoD：最终逐条签字

实施模型最终必须逐条输出：

```text
PASS / FAIL + 证据
```

不得用“测试已通过”代替。

## 架构

- [ ] Agent 主界面只有 Reasoning / Tool / System Event / Markdown 四类 Block。
- [ ] ExecutionEvent 与 User Block 已通过 Projection 层解耦。
- [ ] Backend ExecutionEvent / QA Trace 未因 UI 收敛而降级。
- [ ] `blocks` 成为 Agent 用户可见执行流唯一事实来源。
- [ ] Linear / Pipeline 继续保持独立阶段式展示。

## Reasoning

- [ ] Main Controller reasoning 真实逐增量显示。
- [ ] Main Answer reasoning 真实逐增量显示。
- [ ] Main Grounded Rewrite reasoning 真实逐增量显示。
- [ ] Helper reasoning 在主聊天可见数为 0。
- [ ] 不存在任何伪 reasoning 生成路径。
- [ ] 同一 call_id 只对应一个原位更新 Block。

## Tool

- [ ] Tool Block 只由真实 `tool_start` 创建。
- [ ] `tool_result` 更新同一个 Block。
- [ ] Tool running / completed / failed / denied 状态正确。
- [ ] IN / OUT / elapsed / error 详情仍可查看。
- [ ] `decision.tool` 不会单独伪造 Tool Block。

## System/Event

- [ ] Reviewer REVISE 用户可见率 100%。
- [ ] 用户看到“候选回答未通过证据审查，正在重新组织”等价语义。
- [ ] Reviewer PASS 默认不刷日志行。
- [ ] 内部 Guard/Evidence/Finalization 不会变相进入 System/Event。
- [ ] System/Event 使用白名单而非 catch-all。
- [ ] 无法发布的关键错误有用户可理解提示。

## Final Answer

- [ ] strict grounding Candidate 正文泄漏为 0。
- [ ] 只有 `final_answer` 创建正式 Markdown。
- [ ] Final Answer 不重复显示。
- [ ] Sources 仍正确关联最终答案。

## 旧路径清理

- [ ] `AgentThinkingBlock.vue` 生产引用归零并删除，或有明确无法删除的硬证据。
- [ ] `AgentToolTimeline.vue` 生产引用归零并删除，或有明确无法删除的硬证据。
- [ ] 旧 `AgentStepStream` 不再承担全量 Execution Timeline；若保留则已收窄为 Block Renderer。
- [ ] `thinking + agentTools + timelineItems + blocks` 不存在长期四轨状态。
- [ ] 历史消息兼容通过 normalize，而不是旧组件永久兜底。

## Debug / Trace

- [ ] Chat Message 可通过 trace_id 进入执行详情。
- [ ] QaDebug 可查看完整 execution_events。
- [ ] SSE 与 Trace 事件对账一致。
- [ ] 主界面隐藏的 Decision/Guard/Evidence/Reviewer 仍能在 Debug 找到。

## 测试

- [ ] 前端单元测试通过。
- [ ] Architecture Acceptance Suite 通过。
- [ ] 故障注入测试通过。
- [ ] HTTP/SSE E2E 通过。
- [ ] 真实 Main 模型链通过。
- [ ] 真实 REVISE → Rewrite 链通过。
- [ ] 浏览器人工 UX 验收完成。

只有全部硬 DoD 为 PASS，才允许把本 PRD 状态改为“已完成”。

---

# 26. 完成判定与禁止提前结项

以下均**不等于完成**：

```text
npm build 通过
vitest 全绿
后端 pytest 全绿
页面看起来更简洁
AgentStepStream 少了几行
新 blocks 已经能显示
```

完成必须是：

```text
代码迁移完成
+
旧路径退出生产
+
架构不变量反证通过
+
故障路径通过
+
HTTP/SSE E2E 通过
+
真实模型 Trace 对账通过
+
浏览器人工 UX 通过
+
DoD 全部签字
```

任何一项失败：

```text
不得宣布完成
不得把 PRD 状态改为已完成
不得以“核心功能已实现”替代硬 DoD
```

---

# 27. 给实施模型的一次性执行指令

将本 PRD 交给 Gemini / 其他实施模型时，不得只写：

> 按这份 PRD 实施。

必须使用以下执行口径：

> **按本 PRD 完成一次架构迁移。该任务是“替换旧用户可见 Timeline 架构”，不是新增一套 Block UI。修改前必须先完成只读全仓审计、架构控制权矩阵、Invariant/Forbidden 核对和 OLD→NEW Migration Map；然后一次性完成迁移并删除旧生产路径。实施结束后必须切换为 adversarial architecture review，主动寻找任何内部 ExecutionEvent 泄漏到主界面、任何伪 reasoning、任何虚假 Tool Block、任何 Candidate 提前泄漏以及任何旧兼容路径残留。必须完成单元测试、Architecture Acceptance、故障注入、HTTP/SSE E2E、真实模型 Trace 对账和浏览器 UX 验收。不得以测试绿替代 DoD，未完成全部硬 DoD 不得宣布完成。任务内部无需等待人工逐阶段批准，发现失败应自行继续修复直到全部门禁满足或明确报告不可解决的外部阻塞。**

建议实施时使用高/最高 reasoning 档位。

---

# 28. 最终目标形态

迁移完成后的 Agent 用户体验应近似：

```text
用户：PipelineWebGL 的初始化流程是什么？

Main · Controller
  正在判断需要哪些知识库证据……

知识库检索 · running
  query: PipelineWebGL 初始化流程

知识库检索 · completed · 1.8s
  IN / OUT 可展开

Main · Controller
  当前证据仍缺少初始化阶段的关键关系，继续补检……

知识库检索 · completed · 1.4s

Main · Answer
  正在根据冻结证据组织回答……

⚠ 候选回答未通过证据审查，正在重新组织……

Main · Rewrite
  正在删除未受支持的断言并保留可验证事实……

最终回答 Markdown
  ……

Sources
```

而不是：

```text
意图理解
控制器决策
执行守卫
工具调用
证据增量
证据缺口
控制器决策
执行守卫
工具调用
证据增量
证据门禁
候选生成
证据审查
定向修正
证据审查
答案发布
最终回答
```

后者仍然存在，但应该存在于：

```text
QA Trace / 执行详情
```

而不是普通聊天主界面。

最终原则：

> **Agent 主界面展示“模型真实在做什么”；Trace 展示“系统内部完整发生了什么”。两者共享同一事实源，但不共享同一展示粒度。**
