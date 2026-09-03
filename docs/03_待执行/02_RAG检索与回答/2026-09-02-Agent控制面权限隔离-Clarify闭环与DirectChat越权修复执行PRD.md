# Agent 统一 RAG 控制面、Clarify 闭环与 DirectChat 特权通道移除执行 PRD

| 项目 | 内容 |
| --- | --- |
| 文档版本 | **V2.1** |
| 基线日期 | 2026-09-02 |
| 状态 | **待执行** |
| 优先级 | **P0 / 阻塞子 PRD 05 最终验收** |
| 所属域 | `02_RAG检索与回答` |
| 任务性质 | **Agent 控制面收敛 / DirectChat 特权通道移除 / 统一 Evidence Grounding / Clarify 契约修复 / 可观测性语义纠偏** |
| 事故基线 | `data/qa_traces/20260902/a9165ccbf1c84215827e59f4ed4b2e24.json` |
| 事故问题 | `如何使用pipelienbuilder` |
| 事故约束 | `mode=agent`、`allow_general_knowledge=false`、Stage1=`retrieve`、`possible_meta_chat=false` |
| 影响范围 | Dialogue Understanding、ConversationContext、Main Controller、Tool Registry、Clarify Handler、Evidence/Snapshot、ComposeAnswerHandler、Answer Prompt、Answer Generator、Publication Gate、Grounding Reviewer、ModelStreamRunner、SSE/Trace、AgentBlockProjector |
| 非目标 | 不重写 Hybrid/BM25/Vector/RRF；不删除 Working/Citable 分层；不修改 Graph Evidence 的基本准入原则；不添加 PipelineBuilder/拼写关键词特判；不让 Python 接管 Agent 的下一步策略选择 |
| 总目标 | **系统只保留一种 Agent-RAG 运行模型。Stage1 只做理解辅助，不决定“知识模式/聊天模式”；Main 自主决定需要调用什么工具；准备正式作答时默认主动调用收尾型 `compose_answer`，将冻结 Evidence 交给专门 Answer Generator；所有 Candidate 均经 Publication Gate，含事实 Claim 时必须由 Grounding Reviewer 审核；不再存在 `direct_chat`、`answer_type`、`is_direct_chat` 这种可以同时关闭 Evidence、Reviewer、Prompt 约束的特权开关。** |
| 最新权威关系 | 本 V2.1 **覆盖本文件 V2.0 中“Main 直接 finalize / 所有元对话均交给 Answer Generator”方案**，也覆盖 V1.0 中“Frozen Publication Policy + direct_chat 授权 Veto”方案。后续实现不得继续保留“真正 Meta Chat 仍走 direct_chat”的折中设计。 |

---

# 1. 背景：本次事故暴露的不是一个 DirectChat ACL 缺口

真实请求：

```text
用户：如何使用pipelienbuilder
```

初始状态：

```text
allow_general_knowledge = false
Stage1 = retrieve
possible_meta_chat = false
identity_status = ambiguous_entity
```

实际链路：

```text
Main → clarify
↓
PipelineBuilder 单候选
↓
Clarify Handler 因 meaningful_candidates < 2 返回 DENIED
↓
Main → retrieve_kb(PipelineBuilder)
↓
Working Evidence > 0
Citable Evidence = 0
↓
Main 再次 clarify
↓
DENIED
↓
Main 再次 retrieve
↓
仍 0 Citable
↓
Main 第三次 clarify
↓
DENIED
↓
Main → finalize(answer_type=direct_chat)（旧链）
↓
FinalizationHandler: evidence_required=false（旧链）
↓
AnswerFinalizer: reviewer_count=0（旧链）
↓
Conversation Explain Prompt
↓
模型参数知识：Jenkins / Laravel / AWS CodePipeline / CI/CD
↓
Publication
```

Grounding Trace：

```text
policy = direct_chat
verdict = not_required
review_count = 0
review_attempts = 0
```

V1.0 将根因定义为：

> Main 可以自行请求 direct_chat，而 Runtime 没有先冻结 direct_chat_authorized。

这个诊断只覆盖了事故表面。

重新按第一性原则审查后，真正的问题是：

> **系统不应该存在一个名为 `direct_chat` 的运行模式，让同一个字符串同时切换 Evidence 要求、Grounding 要求、Prompt 类型与 Publication 权限。**

如果只是新增：

```text
Stage1 → direct_chat_authorized
Main → request direct_chat
Harness → allow / veto
```

那么只是把：

```text
Main 自授权
```

改成：

```text
Stage1 / Harness 预先替 Main 决定执行模式
```

这会重新退回“Helper/Runtime 先划工作流边界，Main 在边界里活动”的半 Workflow 架构。

因此 V2.0 的裁决不是“限制 DirectChat”，而是：

> **删除 DirectChat 作为控制模式。**

---

# 2. 第一性原则裁决

## 2.1 系统只有一种运行模式：Agent-RAG

不再存在运行时分叉：

```text
knowledge mode
vs
direct_chat mode
vs
meta_chat mode
```

所有用户输入统一进入：

```text
User Turn
↓
Conversation Understanding
↓
Main Controller
↓
普通 Tool / Existing Context / Clarify / `compose_answer`
↓
Observation / Candidate
↓
Publication Gate
↓
Publication
```

用户问题可以是：

```text
PipelineBuilder 怎么使用？
那它怎么部署？
你刚才为什么反问我？
我什么时候说过 PipelineWebGL？
继续说。
你好。
```

这些只代表**任务不同**，不代表进入不同安全模式。

---

## 2.2 Stage1 只做理解辅助，不拥有执行路由权

Stage1 可以输出：

```text
resolved_question
is_context_dependent
referenced_turns
referents
candidate_entities
identity hypotheses
semantic task / requested facets
rationale
```

Stage1 不再输出具有控制权的：

```text
mode = retrieve
mode = direct_chat
possible_meta_chat → publication permission
```

如果出于迁移兼容暂时保留旧字段：

```text
UnderstandingResult.mode
```

它只能作为 deprecated diagnostic signal，**不得再驱动**：

```text
是否检索
是否需要 Evidence
使用哪个 Answer Prompt
是否运行 Reviewer
是否允许 Publication
```

最终应删除该控制语义。

原则：

> **Stage1 负责帮助 Main 看懂问题，不负责替 Main 决定怎么完成问题。**

---

## 2.3 Main Controller 拥有完整策略决策权

Main 可以根据当前：

```text
用户问题
对话历史
已解析身份
Evidence State
Tool Observations
预算
Graph Working Set
```

自主决定：

```text
retrieve_kb
reuse_evidence
expand_graph_scope
clarify
environment.read_status
web_search（若授权）
compose_answer
```

追问场景不需要另开 DirectChat。

示例：

```text
用户：那它怎么部署？
↓
Main 判断前序证据可能仍适用
↓
reuse_evidence
↓
若缺部署事实，再 retrieve_kb
↓
compose_answer
```

强即时上下文例外：

```text
用户：你刚才为什么反问我？
↓
回答对象主要就是当前这一刻的会话或 Agent 自身状态，且交给 Answer Generator 会损失这种即时上下文
↓
Main 直接回答
↓
Candidate 仍进入 Publication Gate；其中的事实 Claim 接受 Grounding Reviewer
```

原则：

> **Main 决定行为；Harness 只维护工具权限、协议、预算、Evidence 真伪与 Publication 边界。**

---

## 2.4 RAG 的 Evidence 不能等同于“知识库 Chunk”

旧架构隐含：

```text
Evidence ≈ KB Chunk
```

于是遇到：

```text
“你上一轮为什么这么做？”
```

系统只能选择：

```text
不适用 KB
→ direct_chat
→ 不需要 Evidence
```

这是错误抽象。

V2.0 将可 Grounding 的证据来源统一为：

```text
Evidence
├── KB_TEXT
├── GRAPH_RELATION
├── CONVERSATION
├── RUNTIME_EVENT
├── TOOL_OBSERVATION
├── ENVIRONMENT
└── WEB（只有请求显式授权时）
```

其中：

### KB_TEXT

知识库 Chunk / Document Evidence。

### GRAPH_RELATION

已准入 Graph Relation Evidence。

### CONVERSATION

用户与 Assistant 的历史消息、用户选择的 Clarification Callback 等。

### RUNTIME_EVENT

上一轮/当前轮已真实发生的：

```text
Controller decision
clarify DENIED
clarification_card_published
review result
publication result
```

### TOOL_OBSERVATION

Agent 已执行工具的结构化真实结果。

### ENVIRONMENT

通过授权环境工具读取的真实运行状态。

### WEB

只有请求明确允许外部检索时，通过 `web_search` 获取并标注为外部来源的 Evidence。

原则：

> **不同问题改变的是 Evidence Source，不是 Grounding 规则。**

---

## 2.5 模型参数知识不是 Evidence

在：

```text
allow_general_knowledge = false
```

时，模型参数记忆永远不能成为可发布事实来源。

因此：

```text
Jenkins
Laravel
AWS CodePipeline
CI/CD
```

若没有进入当前 Evidence Snapshot，就不得出现在事实性回答中。

V2.0 进一步建议将旧：

```text
allow_general_knowledge
```

逐步收敛为更准确的：

```text
allow_external_evidence
```

即使未来允许“通用知识”，也优先通过：

```text
web_search → External Evidence → Reviewer
```

而不是：

```text
模型记忆 → 无证据 Publication
```

本 PRD 不要求一次性重命名所有 API 字段，但运行语义必须先满足：

> **参数知识不能绕过 Evidence Grounding。**

---

## 2.6 `compose_answer` 是 Main 主动调用的收尾型 Tool

V2.0 旧协议：

```json
{
  "action": "finalize",
  "answer_type": "knowledge|direct_chat",
  "answer_mode": "full|partial"
}
```

目标协议：

```json
{
  "tool": "compose_answer",
  "answer_mode": "full|partial",
  "focus_evidence_ids": []
}
```

删除：

```text
answer_type
```

`compose_answer` 的唯一作用是：

> **当前工具规划已经结束，把当前冻结 Evidence 交给专门 Answer Generator 组织正式答案。**

Main 调用它即完成当前轮的工具规划；`ComposeAnswerHandler` 冻结 Snapshot 并调用 Answer Generator，产出 Candidate。它不是让 Main 自己生成正式答案的别名，也不能携带回答类型或绕过 Publication Gate 的开关。

默认规则必须写入 Controller Prompt：

> **当你已经拥有足够信息准备回答用户时，默认调用 `compose_answer`。只有回答主要依赖当前即时会话状态、刚刚发生的 Agent 行为或用户正在直接控制当前执行过程，而且交给 Answer Generator 会损失这种即时上下文时，才由你直接回答。若无法确定是否属于该例外，调用 `compose_answer`。**

例如以下属于强即时上下文例外：

```text
你刚才为什么反问我？
我刚才选的是哪个？
先不要继续检索。
不是，我说的是 PipelineBuilder。
你为什么刚才又调用了一次检索？
```

以下即使强依赖前文指代，解析出完整问题后仍是正式回答，必须走默认路径：

```text
那 PipelineBuilder 怎么部署？
它还有哪些参数？
继续介绍它的使用流程。
→ retrieve/reuse → compose_answer → Answer Generator
```

Publication Gate 不应该问：

```text
“这是聊天还是知识回答？”
```

它应该只问：

```text
当前 Candidate 中有哪些 Claim？
Claim 使用了哪些 Evidence？
这些 Evidence 是否属于当前冻结 Snapshot？
这些 Claim 是否得到支持？
```

---

## 2.7 Reviewer 统一承担 Claim Detection 与 Grounding

所有由 Answer Generator 或 Main 直答产生、准备对用户发布的 Candidate 都进入同一 Publication Gate。Main 直答只是 Candidate 的窄来源例外，不是 Publication 例外。

事实性元对话示例：

```text
“上一轮系统尝试了三次 clarify，但三次均被 Handler 拒绝，因此你实际没有收到澄清卡片。”
```

必须由：

```text
RUNTIME_EVENT Evidence
```

支持。

Reviewer 检查：

```text
clarify attempts = 3
clarify results = DENIED
clarification_card_published = 0
```

然后才能 PASS。

对于：

```text
“好的。”
“可以继续。”
```

Reviewer 自己完成 Claim Detection；Publication Gate、Python Harness 或任何正则/heuristic 都不得在 Reviewer 之前另行判断“是否存在事实 Claim”。若 Reviewer 判断没有事实性 Claim：

```text
claim_reviews = []
verdict = PASS
```

即可正常发布。

禁止恢复：

```text
if is_direct_chat:
    review_count = 0
    publication
```

这种模式级 Reviewer bypass。

---

## 2.8 No Safe Answer 是 Claim Grounding 的合法终态，不是 0 Evidence 分支

当 Main 调用：

```text
compose_answer
```

Answer Generator 仍可生成 Candidate，随后由 Reviewer 识别 Candidate 中的 Claim 并进行 Grounding。`0 Evidence` 本身不等于不能生成 Response，也不应由 Harness 在生成前直接终止。

例如以下无外部事实的 Candidate 即使没有 Evidence 也可以 PASS 并发布：

```text
你好，有什么需要我帮你的？
帮你把这句话改得更礼貌一些：……
这里有三个标题：……
```

但若 Candidate 出现需要外部事实支撑的 Claim，且当前 Snapshot 无法支持：

合法结果：

```text
No Safe Answer
```

例如严格 KB 请求：

```text
当前知识库中未查询到相关内容。
```

这是 Reviewer 对具体 Claim 给出 `BLOCK` / `NO_SAFE_ANSWER` 后，由 Publication Gate 生成的安全终态；不是 ComposeAnswerHandler 根据 Evidence Count 提前判定，也不是 Python 替 Main 选择下一步工具。

区分：

```text
Main：决定“我不再继续检索，调用 compose_answer”
ComposeAnswerHandler：冻结 Evidence 并交 Answer Generator 生成 Candidate
Reviewer / Publication Gate：仅在 Candidate 的事实 Claim 无法 Grounding 时 BLOCK / NO_SAFE_ANSWER
```

这仍然满足：

> Main = Planner；Harness = Safety / Publication Gate。

---

## 2.9 Clarify 是否需要，由 Main 决定；候选数只决定 UI 形态

不能再把：

```text
candidate_count
```

当成是否允许 Clarify 的语义路由器。

正确关系：

```text
Main 判断当前存在无法可靠继续的 Identity / Intent / Missing Information Gap
↓
Main 调 clarify
↓
Clarify Handler 根据 Snapshot 当前候选数决定交互形态
```

交互形态：

```text
N >= 2
→ 多候选选择卡

N = 1
→ 单候选确认卡
   候选 / 以上都不是

N = 0
→ 自由文本澄清
```

因此：

```text
meaningful_candidates < 2
→ DENIED
```

应从正常业务规则中删除。

---

## 2.10 `allowed_tools` 表示“允许尝试”，不表示“保证成功”

V1.0 中：

> allowed_tools 必须等于当前状态下真实可执行且调用必然完成业务效果的能力。

这一表述过度。

正确语义：

```text
allowed_tools = Main 当前有权尝试调用的 Capability
```

Tool 仍然可以因为：

```text
非法参数
过期 snapshot
权限失效
并发状态变化
协议字段缺失
资源不可用
```

返回：

```text
DENIED / ERROR / NO_PROGRESS
```

真正禁止的是：

```text
Prompt/ToolSpec 明确承诺 1 candidate 可以 clarify
但 Handler 对任何 1 candidate 状态都确定性 DENIED
```

这种**静态契约矛盾**。

---

## 2.11 Tool attempt 与 Tool effect 必须分开

```text
tool_start(clarify)
```

只代表：

> Agent 正在尝试执行 clarify。

只有：

```text
clarification_card_published
+ pause=true
```

才代表：

> 用户实际收到了一次澄清。

UI 不得把前者显示成“已经反问用户”。

---

## 2.12 Model Route Role 与 Semantic Role 必须物理分离

模型路由：

```text
llm
helper_llm
compression_llm
```

用户可见语义角色：

```text
main
helper
```

Controller：

```text
semantic_role = main
model_route_role = llm
stage = agent_controller
```

Answer：

```text
semantic_role = main
model_route_role = llm
stage = answer_generation
```

Rewrite：

```text
semantic_role = main
model_route_role = llm
stage = grounded_retry
```

禁止再使用一个 `role` 同时表达两种含义。

---

# 3. Target Architecture

```text
                           User Turn
                              │
                              ▼
                  Conversation Understanding
             （理解辅助，不产生执行/发布模式）
                              │
                              ▼
                       Main Controller
                              │
       ┌──────────────────────┼──────────────────────┐
       │                      │                      │
       ▼                      ▼                      ▼
普通工具调用           `compose_answer`        强即时上下文直答
检索 / 补检 / 图谱 / 澄清       （收尾型 Tool）          （窄例外）
       │                      │                      │
       ▼                      ▼                      ▼
 Observation            Frozen Evidence Snapshot    Candidate
       │                      │                      │
       └──────→ Main          ▼                      │
                       Answer Generator              │
                              │                      │
                              ▼                      │
                          Candidate ─────────────────┘
                              │
                              ▼
                      Publication Gate
                              │
                              ▼
                     Grounding Reviewer
              （Claim Detection + Grounding）
              PASS        REVISE        GAP       BLOCK
               │             │            │          │
               ▼             ▼            ▼          ▼
          Publication    回原 Candidate   回 Main    安全终止
                         生成者重写       自主规划
```

整个 Target Architecture 中没有：

```text
direct_chat
direct_chat_authorized
answer_type
is_direct_chat
Conversation Explain bypass
Reviewer bypass
```

---

# 4. 核心数据模型目标

## 4.1 UnderstandingResult

目标：只表达语义理解，不表达运行模式。

推荐目标字段：

```python
UnderstandingResult(
    user_utterance: str,
    resolved_question: str,
    retrieval_queries: list[...],
    filters: dict,
    dialogue_focus: str,
    is_context_dependent: bool,
    referenced_turn_ids: tuple[str, ...],
    confidence: float,
    rationale: str,
)
```

旧：

```python
mode: Literal["clarify", "retrieve", "direct_chat"]
```

必须退出控制面。

是否最终彻底删除字段，可按兼容迁移分两步，但运行时不得继续依赖。

---

## 4.2 AgentDecision / Tool Contract

`compose_answer` 是注册给 Main 的收尾型 Tool；Main 必须显式调用它，不能以裸 `finalize` action 结束后隐式生成答案。它不在普通 Observation 后重新进入 Main，而是进入 Answer Generator。

旧 `finalize` 不再包含：

```text
answer_type
```

只保留：

```text
tool=compose_answer
answer_mode=full|partial
focus_evidence_ids=[]
```

`compose_answer` 完成后，Answer Generator 产出的 Candidate 只能进入 Publication Gate。

`Reviewer` **绝不能注册成 Main Tool**。它是 Publication Gate 的内部强制步骤；Main 无权调用、替换、跳过或根据问题类型关闭 Reviewer。

Tool call 协议保持 Agent-first。

---

## 4.3 Unified Evidence Item

推荐在现有 EvidencePool / Snapshot 上扩展 source type，而不是新建第二套平行 Grounding 系统。

最小统一语义：

```python
EvidenceItem(
    evidence_id: str,
    source_type: Literal[
        "kb_text",
        "graph_relation",
        "conversation",
        "runtime_event",
        "tool_observation",
        "environment",
        "web",
    ],
    content: str,
    metadata: dict,
    citable: bool,
    provenance: dict,
)
```

不要求所有类型都直接映射成当前前端 SourcePanel 的“文件来源”。

至少区分：

```text
Citation Evidence
vs
Grounding-only Evidence
```

例如：

```text
Conversation / Runtime Event
```

可以用于 Reviewer Grounding，但不必伪装成知识库文件引用。

---

## 4.4 Evidence Snapshot

`compose_answer` 调用时冻结：

```text
当前 KB/Graph Citable Evidence
+ 与当前问题有关的 Conversation Evidence
+ Runtime / Tool Observations
+ Environment/Web Evidence（若实际执行并授权）
```

冻结后：

```text
Answer Generator
Reviewer
Rewrite
```

全部消费同一 Snapshot。

禁止 Answer 使用 Snapshot 外的事实上下文。

---

# 5. Phase 0：事故 Fixture 与旧行为冻结

## 5.1 固定原事故

Gold 输入：

```text
question = 如何使用pipelienbuilder
allow_general_knowledge = false
identity_status = ambiguous
candidate = PipelineBuilder
Working Evidence > 0
Citable Evidence = 0
```

测试必须能够复现旧事故链：

```text
single candidate clarify DENIED
→ Main 最后 finalize(answer_type=direct_chat)（旧链）
→ evidence_required=false
→ reviewer_count=0
→ external knowledge publication
```

修复完成后该旧行为必须永久失败。

---

## 5.2 新增 Meta Conversation Gold

至少加入：

```text
你刚才为什么反问我？
我什么时候说过 PipelineWebGL？
刚才到底有没有真的弹出澄清卡？
```

目的不是测试 direct_chat，而是验证：

```text
Main 可以不检索 KB
但仍能依据 Conversation / Runtime Evidence
生成经过 Reviewer 的回答
```

---

# 6. Phase 1：Model Stream Role 修复

## 6.1 StreamRunOptions 拆分角色

目标：

```python
StreamRunOptions(
    semantic_role="main",
    model_route_role="llm",
    stage="agent_controller",
)
```

如果短期保留旧 `role`：

```text
role 只能表示 semantic_role
```

模型路由身份必须独立记录。

---

## 6.2 Main 三阶段统一

以下 reasoning SSE：

```text
agent_controller
answer_generation
grounded_retry
```

必须全部：

```text
role = main
```

Reviewer reasoning 仍不投影到主聊天。

---

## 6.3 禁止前端补丁

禁止：

```typescript
role === 'main' || role === 'llm'
```

必须修生产事件语义。

---

# 7. Phase 2：以 `compose_answer` 收敛正式回答路径

这是 V2.1 的核心 Phase。移除 DirectChat 控制链的同时，必须把原先的 `finalize` 终止语义替换为 Main 主动调用 `compose_answer` 的收尾语义。

## 7.1 Dialogue Understanding

移除/废弃：

```text
mode = direct_chat
```

纠错、质疑、历史回顾不再返回特殊运行模式。

它们只通过：

```text
resolved_question
is_context_dependent
referenced_turns
rationale
```

告诉 Main：这是一个依赖会话上下文的问题。

---

## 7.2 Controller Prompt

删除所有：

```text
如果是纯会话回答 → answer_type=direct_chat
```

删除 JSON Schema 中：

```text
answer_type
```

删除 `finalize` action 及其 Schema；以 `compose_answer` ToolSpec 替代。该 ToolSpec 只接受 `answer_mode` 与 `focus_evidence_ids`，不携带模式声明、Reviewer 开关或直接发布权限。

Controller Prompt 必须包含以下默认规则，不能将其弱化为示例或建议：

> **当你已经拥有足够信息准备回答用户时，默认调用 `compose_answer`。只有回答主要依赖当前即时会话状态、刚刚发生的 Agent 行为或用户正在直接控制当前执行过程，而且交给 Answer Generator 会损失这种即时上下文时，才由你直接回答。若无法确定是否属于该例外，调用 `compose_answer`。**

“普通追问”不是例外。`那 PipelineBuilder 怎么部署？`、`它还有哪些参数？`、`继续介绍它的使用流程。` 等问题在解析出完整问题后，仍必须 `retrieve/reuse → compose_answer → Answer Generator`。

---

## 7.3 ComposeAnswerHandler

删除：

```python
if answer_type == "direct_chat":
    evidence_required = False
    accepted
```

删除 `finalize` 的隐式 `evaluate()` 入口。`ComposeAnswerHandler` 仅在 Main 调用 `compose_answer` 后执行：

```text
freeze_snapshot → Answer Generator → Candidate → Publication Gate
```

它只冻结 Snapshot 并调用 Answer Generator；不得以 Evidence Count 决定是否允许生成 Candidate 或提前返回 No Safe Answer。No Safe Answer 只能由后续 Reviewer 对 Candidate 的事实 Claim 进行 Grounding 后产生。

---

## 7.4 Answer Prompt

删除单独的：

```text
_AGENT_CONVERSATION_EXPLAIN_PROMPT
```

作为 Publication 特权入口。

元对话规则并入统一 Answer Prompt：

```text
如果问题要求解释对话历史或系统上一轮行为：
只能使用 Snapshot 中的 Conversation / Runtime Evidence；
不得把模型记忆或外部通用知识当成解释依据。
```

---

## 7.5 Answer Generator / Publication Gate

删除参数：

```text
is_direct_chat
```

删除：

```python
if is_direct_chat:
    review_count = 0
    return publication
```

Answer Generator 的 Candidate 必须进入 Publication Gate。Publication Gate 无条件调用 Grounding Reviewer；Reviewer 同时完成 Claim Detection 与 Grounding：`claim_reviews=[]` 时 PASS 并发布，存在支持充分的事实 Claim 时 PASS，存在不支持的 Claim 时返回 REVISE / GAP / BLOCK。Answer Generator Candidate 的 `REVISE` 保持既有 `Finding → Main Rewrite → Reviewer #2` 闭环。Publication Gate、Python Harness 或 heuristic 不得在 Reviewer 前根据 Candidate 类型、Evidence Count 或文本规则决定跳过 Reviewer。`Reviewer` 不得出现在 Main 的 Tool Registry、`allowed_tools`、Controller Prompt 的可调用工具列表中。

强即时上下文直答也只能直接产出 Candidate，仍必须进入相同 Publication Gate；它绝不等价于直接 Publication。若 Reviewer 返回 REVISE，Finding 返回 Main Controller，由 Main 在当前强即时上下文中直接重写一次后重新进入 Reviewer；不得自动切换到 Answer Generator。若返回 GAP，Observation 返回 Main，由 Main 自主决定工具调用；若返回 BLOCK / NO_SAFE_ANSWER，安全终止。

---

## 7.6 历史兼容

如果旧 QA Trace / 本地持久化中仍存在：

```text
answer_type=direct_chat
final_mode=direct_chat
```

可以保留**只读历史解析兼容**。

但生产新请求不得再生成该控制状态。

兼容代码必须：

```text
read-only
not authoritative
not used for routing
```

并列入后续清理项。

---

# 8. Phase 3：Unified Evidence Grounding

## 8.1 Conversation Evidence

将当前问题所需的历史对话按稳定 ID 注入 Grounding Snapshot。

至少包含：

```text
turn_id
role
content
created_at（如已有）
```

不得把整个无限历史无边界塞进 Snapshot。

由当前 Context / referenced_turns / history budget 选择相关窗口。

Main 可以基于这些 Evidence 回答：

```text
“你上一轮说了什么？”
“我有没有选 PipelineBuilder？”
```

---

## 8.2 Runtime Event Evidence

对系统行为质询，必须能提供真实运行事实：

```text
Tool Started
Tool Result
Clarification Card Published
Pause
Reviewer Verdict
Publication
```

Runtime Event Evidence 必须来自真实 Trace/Event Store，而不是 Main 自己复述自己的记忆。

---

## 8.3 Tool Observation Evidence

当前 turn 已执行的结构化 Tool Observation 可进入 Snapshot。

例如：

```text
environment.read_status
```

返回：

```text
service X unavailable
```

Main 可以回答，但 Reviewer 必须依据该 Observation Grounding。

---

## 8.4 KB / Graph 现有安全边界不放松

本事故中：

```text
PipelineBuilder Working Evidence > 0
Citable = 0
```

原因是身份未确认。

该设计继续保留。

禁止为了统一 Evidence 而把：

```text
Working
```

直接等价为：

```text
Citable
```

---

## 8.5 Snapshot 单点真源

Answer Generator 与 Reviewer 必须看到同一 Frozen Snapshot。

禁止：

```text
Answer 看 Conversation History 全量
Reviewer 只看 KB Evidence
```

否则元对话仍然会出现：

```text
Answer 可以说
Reviewer 无法审
```

必须对齐为同一 Grounding Context。

---

# 9. Phase 4：Clarify 0/1/N 闭环

## 9.1 Main 决定是否澄清

Runtime 不根据 candidate count 自动决定：

```text
必须澄清 / 不得澄清
```

Main 根据当前不确定性决定是否调用 `clarify`。

---

## 9.2 Clarify Handler 只负责交互形态

### 2+ meaningful candidates

```text
候选 A
候选 B
...
以上都不是
```

### 1 meaningful candidate

```text
你指的是 PipelineBuilder 吗？

○ PipelineBuilder
○ 以上都不是
```

### 0 meaningful candidate

```text
请补充具体产品、模块、功能描述或相关上下文。
```

复用现有 free-text callback 契约，不另造第二套恢复协议。

---

## 9.3 删除正常业务 `meaningful_candidates_insufficient`

候选数 0/1/N 都是正常 Clarify 状态。

Handler 只可因以下异常拒绝：

```text
snapshot 不存在/损坏
callback snapshot 过期
非法 option_id
协议参数不合法
权限/并发状态真实变化
```

---

## 9.4 Clarify Effect

只有：

```text
clarification_card_published
pause=true
terminal_action=clarify_pause
```

才算真正向用户提出澄清。

一旦 effect 成功，同一 turn 立即停止 Agent Loop。

---

## 9.5 DENIED 后的 Agent 行为

DENIED/ERROR 作为 Observation 返回 Main。

Main 自主判断：

```text
修正参数
改用其他工具
compose_answer
```

Python 不替 Main 自动选择恢复动作。

重复调用仍由：

```text
cycle / budget / gap registry
```

限制。

---

# 10. Phase 5：`compose_answer` / Publication Gate / No Safe Answer

## 10.1 `compose_answer` 默认完成正式作答

统一默认路径：

```text
Main 调用 compose_answer
↓
Freeze Snapshot
↓
Answer Generator
↓
Candidate
↓
Publication Gate
```

`compose_answer` 是 Main 的收尾型 Tool，不是 Answer Generator 的别名，也不是 Main 直答的后处理。只要不是强即时上下文例外，Main 已有足够信息准备回答时就必须调用它；不确定时也必须调用它。

即使 Frozen Snapshot 为 0 Evidence，Answer Generator 仍可生成 Candidate。是否可以发布由 Reviewer 对 Candidate 的事实 Claim 决定，而不是由 Snapshot 数量提前决定。

---

## 10.2 Publication Gate 无条件调用 Reviewer

Publication Gate 是 Candidate 的唯一发布入口，不是 Main Tool。其决策顺序固定为：

```text
Candidate
↓
Grounding Reviewer（Claim Detection + Grounding）
├── claim_reviews=[] → PASS → Publication
├── factual claims supported → PASS → Publication
├── Answer Generator Candidate 的 REVISE → Finding 返回 Main Rewrite → Reviewer #2
├── Controller Native Candidate 的 REVISE → Finding 返回 Main 直接重写 → Reviewer #2
├── GAP / evidence gap → Observation 返回 Main → Main 自主决定工具
└── BLOCK / NO_SAFE_ANSWER → 安全终止
```

禁止在 Reviewer 前增加 `has_factual_claim`、`evidence_count == 0` 或同类 Python/regex/heuristic 分流。若未来存在性能需求，只能为协议级、结构化产生的极简单 ACK 另立经过验证的优化；本 PRD 实施阶段不引入该优化。

Reviewer 协议本体继续使用现有 5 字段：

```text
verdict
coverage
summary
claim_reviews
rewrite_actions
```

本 PRD 不重新设计 Reviewer JSON 协议。

需要扩展的是 Reviewer 可消费的 Evidence 类型。

---

## 10.3 强即时上下文直答是唯一窄例外

以下回答对象主要是当前即时会话、刚发生的 Agent 行为或用户正在进行的执行控制，并且经过 Answer Generator 会损失即时性时，Main 可以直接产出 Candidate：

```text
你刚才为什么反问我？
我刚才选的是哪个？
先不要继续检索。
不是，我说的是 PipelineBuilder。
你为什么刚才又调用了一次检索？
```

该例外不允许 Main 直接 Publication。Candidate 一律进入 Publication Gate；若含有“刚才调用了一次检索”这类事实 Claim，仍必须由 Runtime / Conversation Evidence 支持并经 Grounding Reviewer。

强即时上下文直答被 Reviewer `REVISE` 时，Finding 必须返回 Main Controller；Main 基于当前即时上下文直接重写一次，再进入 Reviewer #2。被 Reviewer 标为 `GAP` 时，Observation 返回 Main，由 Main 自主决定是否调用检索、补检、图谱或澄清。`BLOCK` / `NO_SAFE_ANSWER` 进入安全终止。上述闭环不得自动切换到 Answer Generator，否则会破坏 Main 直答的例外前提。

## 10.4 元对话事实 Claim 也进入 Reviewer

例如：

```text
“上一轮我没有真正向你发出三次澄清；系统只是三次尝试调用 clarify，均被 Handler 拒绝。”
```

Reviewer 必须逐 Claim 对：

```text
Runtime Event Evidence
```

核对。

---

## 10.5 纯会话/无事实 Claim

若 Candidate 仅为：

```text
好的。
可以继续。
```

Reviewer 可返回：

```text
PASS
claim_reviews=[]
```

不得通过回答“类型”提前跳过 Reviewer。

---

## 10.6 Strict Evidence Fail-closed

当：

```text
allow_general_knowledge=false
```

时，Answer Candidate 不得出现 Snapshot 外的客观事实。

Reviewer 异常继续：

```text
fail-closed
```

不得因“像聊天”降低标准。

---

# 11. Phase 6：Tool Attempt / Tool Effect UI 语义

## 11.1 ToolBlock 展示真实状态

Clarify Tool：

```text
RUNNING → 正在尝试发起澄清
SUCCESS + card published → 已发起澄清
DENIED → 未发起澄清
ERROR → 澄清执行失败
```

禁止 Tool Start 就固定展示：

```text
反问澄清
```

让用户误以为已经被询问。

---

## 11.2 Clarification Card 只由 effect 事件生成

只有：

```text
clarification_card_published
```

创建真正等待用户操作的卡片。

`tool_start(name=clarify)` 不得创建交互卡。

---

## 11.3 SSE / Trace / UI 三方对账

同一次 clarify 必须可确定：

```text
attempt_count
effect_count
result
pause
```

例如原事故：

```text
attempt_count = 3
effect_count = 0
```

UI 必须准确表达“尝试三次、实际零次”。

---

# 12. Phase 7：测试矩阵

## 12.1 Controller Reasoning Role

断言：

```text
model_route_role = llm
semantic_role = main
stage = agent_controller
```

真实 SSE：

```text
llm_reasoning_start.role = main
llm_reasoning_delta.role = main
llm_reasoning_end.role = main
```

Answer/Rewrite 不回归。

---

## 12.2 Stage1 不再拥有 DirectChat 路由权

至少覆盖：

```text
你刚才为什么反问我？
我什么时候说过 PipelineWebGL？
```

断言：

```text
不会因为 UnderstandingResult 某个 mode 直接跳过 Agent Loop
不会直接切换 Answer Prompt
不会直接关闭 Reviewer
```

---

## 12.3 Controller `compose_answer` Tool Contract

断言生产协议不再接受：

```text
answer_type=direct_chat
```

新的收尾调用：

```text
tool=compose_answer
answer_mode=full|partial
focus_evidence_ids
```

断言：

```text
Main 已准备正式作答时调用 compose_answer
compose_answer 冻结 Snapshot 后调用 Answer Generator
Reviewer 不在 Main Tool Registry / allowed_tools 中
```

---

## 12.4 Clarify 三态

```text
0 candidate → free text card + pause
1 candidate → candidate + 以上都不是 + pause
2+ candidate → multi-choice + 以上都不是 + pause
```

正常业务不得返回：

```text
meaningful_candidates_insufficient
```

---

## 12.5 强即时上下文直答

Case：

```text
上一轮 clarify 调用 DENIED，card 未发布
用户：你刚才是不是问了我三次？
```

断言：

```text
Main 可不调用 retrieve_kb，也可不调用 compose_answer
Main 直接产生 Candidate，且只在回答主要依赖当前即时会话/刚发生行为时成立
Candidate 使用 Runtime Event Evidence
Candidate 无条件进入 Publication Gate 并调用 Reviewer；由 Reviewer 检测并核对事实 Claim
最终准确解释 attempt ≠ actual clarification
```

---

## 12.6 Conversation Evidence

Case：

```text
用户上一轮明确选择 PipelineBuilder
用户：我刚才选的是哪个？
```

断言：

```text
不需要 KB Chunk
Conversation Evidence 支持 Main 直接 Candidate
Candidate 无条件进入 Publication Gate；Reviewer 检测 Conversation Claim 后 PASS
```

## 12.7 普通追问必须走 Answer Generator

至少覆盖：

```text
那 PipelineBuilder 怎么部署？
它还有哪些参数？
继续介绍它的使用流程。
```

断言：

```text
即使 Understanding 标记 is_context_dependent，也不属于强即时上下文直答
Main 先 retrieve/reuse 所需 Evidence
Main 调用 compose_answer
Answer Generator 产出 Candidate
Candidate 进入 Publication Gate
```


## 12.8 Strict KB 原事故

输入：

```text
如何使用pipelienbuilder
```

条件：

```text
allow_general_knowledge=false
single PipelineBuilder candidate
Working > 0
Citable = 0
```

第一阶段必须：

```text
Main → clarify
↓
PipelineBuilder + 以上都不是
↓
clarification_card_published
↓
pause
```

绝不得出现：

```text
Jenkins
Laravel
AWS CodePipeline
CI/CD
answer_type=direct_chat
review_count=0 direct_chat bypass
```

---

## 12.9 用户确认后

```text
用户确认 PipelineBuilder
↓
confirmed identity
↓
Main 自主 retrieve/reuse/graph
↓
Citable Evidence
↓
compose_answer
↓
Answer Generator → Candidate → Publication Gate → Reviewer
↓
Publication
```

如果确认后仍无 Citable Evidence，Answer Generator 仍可生成 Candidate，但 Reviewer 必须 BLOCK 其中任何 Snapshot 外的 PipelineBuilder 事实 Claim，并给出 No Safe Answer；不得使用参数知识补齐。

---

## 12.10 0 Evidence 不预判 No Safe Answer

断言：

```text
Main 调用 compose_answer 后仍调用 Answer Generator
Candidate 始终进入 Reviewer；不存在 Python/regex `has_factual_claim` 或 `evidence_count == 0` shortcut
“你好”/改写/标题等无外部事实 Candidate：claim_reviews=[] → PASS → Publication
包含无 Evidence 外部事实的 Candidate：unsupported claim → BLOCK / No Safe Answer
不调用特殊 Conversation Explain Prompt
不出现参数知识
```

---

## 12.11 Reviewer 不可被问题类型绕过

至少覆盖：

```text
知识问题
元对话事实问题
历史回顾问题
环境状态问题
```

凡 Candidate 包含事实 Claim：

```text
review_count >= 1
```

纯无事实 Claim 响应也必须经过 Publication Gate，不得走 direct_chat shortcut。

并断言所有 Candidate（包括纯无事实 Claim）均实际调用 Reviewer，由 Reviewer 返回 `claim_reviews=[]` 或对应 Claim verdict；Publication Gate 不得先行做 Claim 类型判断。

## 12.12 强即时上下文直答的 Reviewer 闭环

Case：

```text
强即时上下文 → Main 直答 Candidate → Reviewer = REVISE
```

断言：

```text
Finding 返回 Main Controller
Main 基于当前即时上下文直接重写一次
Reviewer #2 审核重写 Candidate
不得自动切 Answer Generator

Reviewer = GAP → Observation 返回 Main，由 Main 自主决定工具
Reviewer = BLOCK / NO_SAFE_ANSWER → 安全终止
```

## 12.13 不确定时走生成器

覆盖边界不明确的当前上下文问题。断言：

```text
Controller 无法可靠证明“交给 Answer Generator 会损失即时上下文”时
→ 调用 compose_answer
→ Answer Generator
→ Candidate
→ Publication Gate
```

---

## 12.14 Observability

原事故旧 Trace：

```text
3 x tool_start(clarify)
3 x DENIED
0 x clarification_card_published
```

前端断言：

```text
不会显示“三次已经反问”
```

---

# 13. Phase 8：真实 HTTP/SSE + 浏览器验收

## 13.1 原事故真实链

真实执行：

```text
如何使用pipelienbuilder
```

必须在浏览器看到：

```text
Controller reasoning（中文、可见）
↓
Clarify Tool attempt
↓
PipelineBuilder 单候选确认卡
↓
等待用户选择
```

不能提前出现答案正文。

---

## 13.2 Callback 继续执行

用户选择 PipelineBuilder 后：

```text
resume
↓
Main reasoning
↓
工具调用
↓
Evidence
↓
Answer reasoning
↓
Reviewer activity
↓
Publication
```

---

## 13.3 Meta Conversation 真实链

真实执行：

```text
你刚才为什么反问我？
```

要求：

```text
不需要特殊 direct_chat 模式
仅在属于强即时上下文时，Main 可直接产生 Candidate
Candidate 仍进入 Publication Gate；事实 Claim 由 Reviewer 正常审核
若不是强即时上下文或无法确定，Main 调用 compose_answer
```

---

## 13.4 滚动与透明度回归

继续验收 Reasoning 子 PRD 既有要求：

```text
生成时用户可自由滚动
不强制锁到底部
Controller/Answer/Rewrite Main reasoning 可见
Reviewer reasoning 不显示
Reviewer activity 可见
```

---

# 14. 与 Reasoning 子 PRD 03/04/05 的关系

## 14.1 子 PRD 03

Controller reasoning 消失问题由本 PRD Phase 1 修复。

必须重新执行：

```text
Controller
Answer
Rewrite
```

三阶段真实 reasoning 可见性验收。

---

## 14.2 子 PRD 04

Reviewer Finding / Activity 协议本体不改变。

新增约束：

> **Reviewer 不再因为 direct_chat / meta chat 等回答类型被跳过。**

---

## 14.3 子 PRD 05

浏览器最终 E2E 在本 PRD 完成前继续阻塞。

05 恢复前必须新增验证：

```text
不存在 direct_chat 特权 publication
Meta Conversation 同样 Evidence-grounded
Clarify attempt/effect UI 一致
Controller reasoning role 正确
```

---

# 15. 禁止实现

## 15.1 禁止 PipelineBuilder / 拼写特判

禁止：

```python
if "pipelienbuilder" in question:
```

Gold Case 是事故样本，不是业务规则来源。

---

## 15.2 禁止“保留 DirectChat，只再加一个授权层”

禁止重新实现：

```text
Stage1 → direct_chat_authorized
Main → request direct_chat
Harness → approve
```

本 V2.0 已明确否决该方案。

---

## 15.3 禁止前端接受 `role=llm` 打补丁

禁止：

```typescript
role === 'main' || role === 'llm'
```

必须修后端事件语义。

---

## 15.4 禁止单候选自动 confirmed

```text
1 candidate → confirmed identity
```

禁止。

单候选仅改变 Clarify UI 形态。

---

## 15.5 禁止把所有 Conversation History 无边界塞入 Evidence

统一 Evidence 不等于无限上下文。

必须受：

```text
history budget
referenced turns
context relevance
```

约束。

---

## 15.6 禁止为了 Meta Conversation 再造一套 Reviewer

不要创建：

```text
ConversationReviewer
KnowledgeReviewer
```

目标是统一 Grounding Reviewer 消费多类型 Evidence。

---

## 15.7 禁止 Python 替 Main 自动规划工具

当 Tool 返回 DENIED / NO_PROGRESS：

```text
Observation → Main
```

Python 不得自动：

```text
clarify → retrieve
retrieve → graph
```

Main 保持策略决策权。

---

## 15.8 禁止以 answer_type 控制 Prompt / Reviewer / Evidence

最终生产链不得再出现：

```text
answer_type == direct_chat
→ Conversation Prompt
→ evidence_required=false
→ reviewer skip
```

---

# 16. DoD

只有全部满足才能结项。

## 16.1 Agent Control Plane

- [ ] 系统生产运行时不再存在 `direct_chat` 作为执行/发布模式。
- [ ] Stage1 不再决定 retrieve/direct_chat 路由。
- [ ] Main Controller 自主决定普通工具调用与 `compose_answer` 收尾调用。
- [ ] 生产协议不存在 `finalize`；`compose_answer` 不包含 `answer_type`、Reviewer 开关或直接发布权限。
- [ ] Main 准备正式作答时默认调用 `compose_answer`；无法确定是否是窄例外时同样调用它。
- [ ] 仅回答主要依赖当前即时会话/刚发生 Agent 行为/用户正在控制执行过程且交给生成器会损失即时性时，Main 才可直接产生 Candidate。
- [ ] Python Harness 不替 Main 选择下一步工具。

## 16.2 Unified Evidence

- [ ] KB / Graph Evidence 继续保留现有 Working/Citable 安全边界。
- [ ] Conversation Evidence 可进入统一 Grounding Snapshot。
- [ ] Runtime / Tool Observation Evidence 可进入统一 Grounding Snapshot。
- [ ] Answer Generator 与 Reviewer 消费同一 Frozen Snapshot。
- [ ] Snapshot 外的参数知识在 strict 模式不可发布。
- [ ] `0 Evidence` 不会在生成前被 Harness 直接映射为 No Safe Answer。

## 16.3 Reviewer / Publication

- [ ] `ComposeAnswerHandler` / Answer Generator 不再接收/依赖 `is_direct_chat`。
- [ ] 不再存在 `review_count=0` 的 direct_chat shortcut。
- [ ] `Reviewer` 不注册为 Main Tool，不出现在 Main 的 `allowed_tools`，且 Main 无权跳过它。
- [ ] 所有 Candidate 均无条件进入 Reviewer；Reviewer 同时承担 Claim Detection 与 Grounding，Publication Gate 不使用 Python/regex/heuristic 提前判断事实 Claim。
- [ ] 元对话与强即时上下文直答中的事实 Claim 也接受 Grounding Reviewer。
- [ ] 纯无事实 Claim 由 Reviewer 返回 `claim_reviews=[]` / PASS 后发布。
- [ ] 0 Evidence 时仍可生成无外部事实 Candidate；仅其事实 Claim 无法 Grounding 时才 BLOCK / No Safe Answer。
- [ ] 强即时上下文直答被 REVISE 时 Finding 返回 Main 直接重写并复审；GAP 返回 Main 自主规划；不得自动切 Answer Generator。
- [ ] Reviewer error 继续 fail-closed。

## 16.4 Clarify

- [ ] 是否 Clarify 由 Main 决定。
- [ ] 0 candidate → free text。
- [ ] 1 candidate → candidate + 以上都不是。
- [ ] 2+ candidate → multi-choice。
- [ ] 正常候选数不再触发 `meaningful_candidates_insufficient`。
- [ ] 成功发布 Card 后立即 `clarify_pause`。
- [ ] DENIED 作为 Observation 返回 Main，不由 Runtime 自动换工具。

## 16.5 Role / Reasoning

- [ ] Controller `semantic_role=main` 与 `model_route_role=llm` 分离。
- [ ] Controller reasoning SSE `role=main`。
- [ ] Answer / Rewrite reasoning 无回归。
- [ ] Reviewer reasoning 仍不显示。
- [ ] Model audit 仍保留真实 route role/provider/model。

## 16.6 Observability

- [ ] Tool attempt 与 Tool effect UI 语义分离。
- [ ] `clarification_card_published` 才表示用户真的被反问。
- [ ] DENIED clarify 不再显示成已成功反问。
- [ ] SSE / Trace / UI 对 attempt/effect/pause 一致。

## 16.7 Accident Regression

- [ ] `如何使用pipelienbuilder` 第一阶段稳定出现 PipelineBuilder 单候选确认卡。
- [ ] 未确认前 Working Evidence 不擅自升级 Citable。
- [ ] 不再出现 Jenkins/Laravel/AWS CodePipeline/CI-CD 等 Snapshot 外知识。
- [ ] 用户确认后重新 Admission 并正常进入 `compose_answer` → Answer Generator → Publication Gate。
- [ ] 确认后无 Citable Evidence 时，任何外部事实 Claim 都被 Reviewer BLOCK / No Safe Answer；不因 Evidence Count 跳过 Answer Generator 或 Reviewer。

## 16.8 Meta Conversation Regression

- [ ] `你刚才为什么反问我？` 不需要 direct_chat 模式即可完成，并符合强即时上下文直答的窄例外定义。
- [ ] `那 PipelineBuilder 怎么部署？` 等正式追问不会误走直答，默认进入 `compose_answer` → Answer Generator。
- [ ] Main 可自主判断是否需要调用 KB/Runtime/其他工具；不能自主调用或跳过 Reviewer。
- [ ] 直答 Candidate 仍进入 Publication Gate，事实 Claim 依据 Conversation/Runtime Evidence 并经 Reviewer 审核。

## 16.9 Engineering Validation

- [ ] 后端相关专项全部通过。
- [ ] 前端 Projector / SSE parser / typecheck / build 通过。
- [ ] `git diff --check` 通过。
- [ ] 非 integration 全量回归通过或仅存在与本 PRD 无关、已有记录的失败。
- [ ] 原事故真实 HTTP/SSE 通过。
- [ ] Meta Conversation 真实 HTTP/SSE 通过。
- [ ] QA Trace 与浏览器实际显示完成对账。
- [ ] 完成后恢复子 PRD 05 最终验收。

---

# 17. 推荐实施顺序

```text
Phase 0 事故 Fixture + Meta Conversation Gold
↓
Phase 1 semantic_role / model_route_role
↓
Phase 2 移除 Stage1/Controller/ComposeAnswerHandler 的 DirectChat 控制链，并接入 compose_answer
↓
Phase 3 Conversation / Runtime / Tool Observation 统一 Evidence
↓
Phase 4 Clarify 0/1/N UI 闭环
↓
Phase 5 compose_answer + Publication Gate + Reviewer + No Safe Answer
↓
Phase 6 Tool attempt/effect UI
↓
Phase 7 专项 + 全量回归
↓
Phase 8 原事故 + Meta Conversation 真实 HTTP/SSE + 浏览器验收
↓
恢复子 PRD 05
```

注意：

> Phase 2 与 Phase 3 不应被拆成“先删除 direct_chat，再暂时允许元对话无 Grounding”的中间长期状态。

实施时可以分 commit/步骤，但最终提交验收前必须同时闭合。

---

# 18. 最终验收示意

## 18.1 原事故正确链

```text
用户：如何使用pipelienbuilder
        ↓
Understanding
只解析：疑似 PipelineBuilder / identity ambiguous
不输出 direct_chat/retrieve 权限模式
        ↓
Main Controller reasoning（可见）
        ↓
Main 判断需要确认身份
        ↓
clarify
        ↓
○ PipelineBuilder
○ 以上都不是
        ↓
clarification_card_published
        ↓
PAUSE
```

用户确认：

```text
PipelineBuilder confirmed
        ↓
Main Controller
        ↓
retrieve / reuse / graph（Main 自主）
        ↓
Evidence Admission
        ↓
Main 调用 compose_answer
        ↓
Frozen Snapshot
        ↓
Answer Generator
        ↓
Candidate
        ↓
Publication Gate
        ↓
Grounding Reviewer（Claim Detection + Grounding）
        ↓
PASS → Publication
REVISE → Main Rewrite → Reviewer #2
GAP → Main 自主工具规划
BLOCK / NO_SAFE_ANSWER → 安全终止
```

---

## 18.2 强即时上下文直答正确链（窄例外）

```text
用户：你刚才为什么反问我？
        ↓
Understanding
识别为回答主要指向刚发生的会话/Agent 行为
但不切 direct_chat
        ↓
Main Controller
        ↓
强即时上下文，交给 Answer Generator 会损失即时性
        ↓
Main 直接回答
        ↓
Candidate
“上一轮系统尝试调用 clarify，但 Tool 被拒绝，实际没有发布澄清卡片。”
        ↓
Publication Gate
        ↓
Reviewer 对 Runtime Evidence 执行 Claim Detection + Grounding
        ↓
PASS → Publication
REVISE → 返回 Main 直接重写 → Reviewer #2
GAP → 返回 Main 自主决定工具
BLOCK / NO_SAFE_ANSWER → 安全终止
```

若只是解析依赖上文的正式问题，例如“那 PipelineBuilder 怎么部署？”，则不属于本例外，必须：

```text
retrieve / reuse → compose_answer → Answer Generator → Candidate → Publication Gate
```

不需要：

```text
direct_chat
Conversation Explain bypass
Reviewer skip
```

---

## 18.3 0 Evidence 的正确处理

```text
Main 已决定停止继续调用工具，并调用 compose_answer
当前 Frozen Snapshot = 0 Evidence
        ↓
compose_answer
        ↓
Answer Generator → Candidate
        ↓
Grounding Reviewer（Claim Detection + Grounding）
        ↓
无外部事实 Claim → claim_reviews=[] → PASS → Publication
外部事实 Claim 无法支持 → BLOCK / No Safe Answer
```

永远不能：

```text
→ 切换聊天模式
→ 使用模型参数知识
→ 按 Evidence Count 在生成前直接 No Safe Answer
→ Reviewer 0 次
```

---

# 19. 完成定义

本 PRD 完成不是：

```text
“pipelienbuilder 不再触发 Jenkins”
```

也不是：

```text
“direct_chat 增加了一个 ACL”
```

而是以下五个系统不变量成立：

> **1. 系统只有一个 Agent-RAG 控制面；不存在可以退出 Evidence/Publication Gate/Reviewer 约束的聊天特权模式。**
>
> **2. Stage1 帮助 Main 理解问题，但不替 Main 决定执行模式；Main 保持工具策略决策权，并在准备正式作答时默认主动调用收尾型 `compose_answer`。**
>
> **3. `compose_answer` 将冻结 Evidence 交给专门 Answer Generator 形成 Candidate；只有强即时上下文才允许 Main 直接产生 Candidate，且不确定时必须走生成器。**
>
> **4. Candidate 的唯一出口是 Publication Gate：所有 Candidate 必经同时承担 Claim Detection 与 Grounding 的 Reviewer；无事实 Claim 由 `claim_reviews=[]` / PASS 发布，事实 Claim 必须获得支持；Reviewer 不属于 Main Tool，Main 无权跳过。**
>
> **5. Grounding 的核心是 Evidence，而 Evidence 可以来自 KB、Graph、Conversation、Runtime、Tool、Environment、授权 Web；Clarify 是否调用由 Main 决定，0/1/N 候选只决定交互形态。**
>
> **6. 观察层只展示真实发生的事实：Tool attempt 不等于 Tool effect，模型路由角色不等于用户可见语义角色。**

只有以上不变量同时通过自动化测试、真实 HTTP/SSE、QA Trace 与浏览器对账后，才允许解除 P0 阻塞并恢复子 PRD 05 的最终验收。
