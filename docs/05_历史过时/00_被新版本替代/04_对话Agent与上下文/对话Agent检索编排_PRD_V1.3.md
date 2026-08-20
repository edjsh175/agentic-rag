# 对话 Agent 检索编排 · 产品需求说明书（PRD）

| 项目 | 内容 |
|------|------|
| 文档版本 | **V1.3** |
| 基线日期 | 2026-08-17 |
| 修订 | V1.3（2026-08-17）：重划 LLM 决策自由 / Harness 边界。**同日补丁**：取消整请求绝对硬超时（含 8 秒截断歉答）；循环仍受 step / retrieve 次数预算约束。V1.3 其余：撤回 `#2` 事实优先级、`head_entity` 强制切题、固定 Staircase；`answer` 退出 Tool Registry；Evidence Gate 为治理结果。**同日（并行约束）**：标明与 08-14 指称唯一性 PRD 的并行/串行边界，禁止 Agent 重写「何时反问」。 |
| 状态 | **历史过时：已被 V1.6 替代** |
| 范围 | 将固定 RAG 流水线重构为 **LLM 主导的带工具对话 Agent + Harness Runtime**：理解上下文 → 按需选择实体/图谱/澄清/检索/环境工具 → 观察工具结果 → 动态调整问题与下一步 → Evidence Gate → 有限补检 → 作答 |
| 实施原则 | **LLM 负责控制流决策，Harness 负责运行时治理，Tools 提供确定能力。** 产品体验像 Chat + Tools；事实只能来自 Evidence Pool；澄清是会话级暂停；检索补检有预算；工具有白名单、Schema、权限与副作用边界。 |
| 关联文档 | [对话上下文机制改进 PRD（Phase 0–2 已结项）](../../../04_已完成归档/04_对话Agent与上下文/2026-08-11-对话上下文机制改进PRD.md)、[反问指称唯一性 PRD](../../../02_实施中/04_对话Agent与上下文/2026-08-14-反问指称唯一性与二次开发直达整改PRD.md)、[用户偏好记忆与反问继承系统-PRD](../../../03_待执行/04_对话Agent与上下文/用户偏好记忆与反问继承系统-PRD.md)、[歧义消解规则管理系统-PRD](../../../03_待执行/04_对话Agent与上下文/歧义消解规则管理系统-PRD.md)、[2026-07-20-RAG架构升级PRD](../../../03_待执行/05_知识资产与文档管理/2026-07-20-RAG架构升级PRD.md) |

> **版本治理（2026-08-20）**：本文只保留为历史演进记录。当前实施真源为 [V1.6](../../../02_实施中/04_对话Agent与上下文/对话Agent检索编排_PRD_V1.6.md)；现行行为仍以代码与测试为准，不得再把 V1.3 作为当前版本。
>
> **产品形态（已拍板）**：ChatView 仍走一条 `/query/stream` SSE，用户无感；Agent 编排发生在后端内部。不接 MCP、不把本仓库做成对外工具 sidecar。
>
> **与 08-11 PRD 的关系**：08-11 已落地 Session / Understanding / GenerationPack / 短记忆 / `topic_shift`。本文保留 **Dialogue ≠ Evidence**，并在其上将固定 DAG 的控制流升级为 **LLM 主导的 Tool Calling + Harness Runtime**。
>
> **与 08-14 PRD 的关系（并行约束，避免踩坑）**：08-14 管「本 Job 合法锚是否唯一」；本文管控制流。二者**不是替代关系**。允许并行：**08-14 收口**（聊天页复验、可选 FR-7）与本文 **Phase 1**（Runtime / Registry / EvidencePool / `retrieve_kb`，`agent_orchestration.enabled` 默认 false）。**禁止并行**：两边同时改 `analyze()` / 卡片选项，或在 Agent 内再写一套「种子 ≥ 2 ⇒ 反问」。**必须串行**：Phase 2 的 `clarify` / `link_entities` 只能调用已收口的 `resolve_anchor_binding()`；Harness 须覆盖「模型想跳过强制 J3 卡」和「点名合法锚后仍全列家族」。Phase 1 不得把澄清收进 Loop。总开关关闭时澄清仍走现有 `/query/clarify`。
>
> **V1.3 边界**：Harness 约束越权、超预算、无证据作答、旧证据混入可引用区；不规定 Agent 必须如何改写 query、不得把检索轮次当事实优先级。

---

## 一、项目概述

### 1.1 背景与问题

当前主路径（除闲聊 / 敏感 / 少量拒答外）是编译好的 DAG：

```text
Understanding → Plan → Hybrid 检索 → Pack → 生成
```

`agents.json` 主要提供角色/提示词，不是能够自主选择工具并根据工具结果继续行动的 Chat Agent。检索、澄清、图谱、联网等能力虽然已经在代码中分模块，但控制流仍主要由代码预先决定：

- 用户提问后几乎必然进入检索；
- 检索完成后几乎必然直接进入生成；
- 缺少“观察检索结果 → 判断缺口 → 再选择工具”的反馈回路；
- 上下文与证据虽然已有边界约束，但尚未在数据结构层面形成独立的 `ConversationContext` 与 `EvidencePool`；
- 图谱实体链接、澄清卡片尚未成为 Agent 可按需调用的标准工具；
- 环境类能力尚未纳入统一工具契约与权限模型；
- 后续希望由 LLM 动态选择检索策略，但当前检索器内部实现仍不适合直接暴露给模型。

因此本项目不是“给现有 RAG 增加一个小循环”，而是：

> **将固定 DAG 的控制流改为 LLM 主导的对话式 Tool Calling Agent；保留现有 RAG、图谱、澄清等能力作为工具，由 Harness 负责工具契约、权限、预算、状态、SSE、trace 与最终治理。**

### 1.2 第一性原理

| # | 原则 | 含义 |
|---|------|------|
| A1 | **LLM 主导控制流，Harness 主导运行时治理** | LLM 负责理解、选工具、观察工具结果、改写、判断证据、再决策、组织回答。Harness 约束「不能做什么」（越权工具、超预算、无证据作答、把旧证据当当前证据），**不**规定「应该怎么思考」（固定 query 阶梯、强制采信某一轮检索） |
| A2 | **Dialogue ≠ Evidence** | 对话上下文只用于理解语境、指代、省略、切题和用户意图；不得作为知识事实依据。事实回答只能来自 Evidence Pool |
| A3 | **ConversationContext 与 EvidencePool 是两个独立对象** | 注入层必须明确区分“用于理解的上下文”和“允许引用的证据”；证据按当前问题与检索轮次分组，并具有生命周期 |
| A4 | **实体消歧是按需工具调用，不是固定 DAG 节点** | LLM 根据上下文判断是否存在实体不确定性；需要时调用 `link_entities`，出现多候选/歧义才调用 `clarify` |
| A5 | **图谱先解决“指谁”，再参与“找什么”** | Entity Linking / 相似实体 / 别名 / 冲突用于消歧；实体确定后，图扩召回才可作为 `retrieve_kb` 内部能力参与检索 |
| A6 | **工具结果是 Agent 下一步决策的观察输入** | Agent 不是调用完工具就结束，而是读取结果后重新判断：继续、换工具、补检、复用证据或回答 |
| A7 | **证据不足可以行动，但行动必须有预算** | Evidence 不足时允许再次调用检索工具；默认首次 + 1 次补检，配置最多首次 + 2 次补检；不得开放无限 ReAct |
| A8 | **事实门禁不可被模型覆盖** | LLM 对证据的 `support / insufficient / uncertain` 只是**决策建议**。最终是否允许知识作答由 Harness 的 Evidence Gate / Answer Gate 决定。无 sources、无可引用 chunk、实体冲突、规则侧否决或 Gate 为 insufficient/uncertain 时，模型不得用 history 或常识补齐知识答案 |
| A9 | **工具能力与工具底层实现解耦** | LLM 选择“精确检索 / 扩展检索 / 相关检索”等能力时，不直接修改 BM25、Vector、RRF、MMR 等底层参数；具体检索器仍由工具内部治理 |
| A10 | **副作用工具必须有权限边界** | 环境读取、执行、写入、删除等能力可以纳入统一 Agent，但必须声明 side effect、permission、confirmation_required；高风险操作不得靠模型“随机应变”直接执行 |
| A11 | **澄清是会话级暂停** | `clarify` 返回卡片后结束本轮 HTTP；用户选择后下一轮带着已选实体重新进入 Agent，不把用户等待伪装成同一请求内循环 |
| A12 | **灵活性属于决策层，不属于规则层** | 模型可以动态选择下一步，但不能修改系统契约、预算、权限、引用规则或工具实现。Harness 不做 Agent Planning（不写死第一次失败必须去修饰词、第二次必须宽泛化等） |
| A13 | **展示优先级 ≠ 事实可信度** | Prompt 中补检组可以前置以突出针对当前 Gap 的相关性；**不得**把 `retrieve round` 本身作为冲突时的事实采信规则 |

---

### 1.3 目标

1. **Chat Agent 化**：用户面对的是一个自然对话 LLM，而不是一个显式 RAG Pipeline；Agent 根据上下文动态选择是否使用工具以及下一步做什么。
2. **工具化能力**：将实体链接、澄清、证据复用、知识库检索、联网、环境操作等能力统一为内部 Tool Registry。
3. **上下文/证据彻底分离**：生成 Prompt 明确分为 `ConversationContext` 与 `EvidencePool`，前者不可作为事实依据，后者是唯一知识事实来源。
4. **动态实体理解**：问题含糊时，Agent 可根据上下文调用图谱实体链接，获得候选实体/相似实体/冲突关系，再决定是否需要澄清。
5. **动态问题改写**：Agent 根据用户原话、上下文、已选实体和工具结果形成当前 `resolved_question` / retrieval intent，而不是机械复用原问题。
6. **动态检索编排**：Agent 可以调用不同“检索能力工具”；本期底层仍由现有 Hybrid ± Rerank + QueryPlanner 实现，后期再开放检索策略档位给 LLM。
7. **Evidence-aware Loop**：首次检索后，Agent 必须观察 chunk；若不足，针对 Evidence Gap 类型选择恢复策略再检索（非固定 query 阶梯）。
9. **有限循环**：Agent 可以多次“理解 → 工具 → 观察 → 决策”，但单请求受 **步数与检索次数** 预算约束（无整请求墙钟硬超时）；知识检索默认最多首次 + 1 次补检，配置最多首次 + 2 次补检。
10. **环境工具可扩展**：未来可以让 Agent 处理环境读取/操作需求，但统一纳入工具权限与副作用模型，不与知识问答工具混为无约束能力。
11. **兼容现有产品协议**：ChatView 仍走 `/query/stream` SSE；用户不感知后端从 DAG 到 Tool Calling Agent 的变化。
12. **完整可观测**：`qa_trace` 能回放 Agent 每次工具选择、工具结果、实体消歧、证据组、补检原因、最终门禁和回退路径。

### 1.4 非目标

| 非目标 | 说明 |
|--------|------|
| MCP Server / 对外 Skill | 本期不需要；另立项 |
| 无边界 Open ReAct | 禁止；必须有 Harness Runtime、工具白名单与预算 |
| 取消现有 RAG 治理 | 不取消；现有检索、Pack、引用和质量基线继续复用 |
| 每问固定先图谱 | 不做；图谱实体链接由 Agent 根据问题决定是否调用 |
| 每问先图扩召回 chunk 再反问 | 禁止；实体消歧与图扩召回分层 |
| LLM 直接修改 BM25 / Vector / RRF / MMR 参数 | 本期不做 |
| LLM 自由创造工具 | 禁止；工具必须存在于 Tool Registry |
| LLM 绕过 Evidence Gate 直接知识作答 | 禁止 |
| 无确认的高副作用环境操作 | 禁止 |
| 跨会话用户偏好 | 仍见偏好记忆 PRD |
| 把图谱关系文本直接当事实答案 | 图谱主要用于实体理解与检索组织，知识事实仍以可引用来源为准 |
| 替换 ChatView 协议或拆微服务 | 不在范围 |

### 1.5 成功标准（总览）

| 编号 | 标准 | 度量 |
|------|------|------|
| S1 | 明确知识问题由 Agent 自主判断并调用检索工具，不再依赖固定 DAG 强制进入某个代码节点 | `qa_trace.tools[]` 可见工具决策 |
| S2 | 宽口径/歧义实体经图谱链接后才出反问；唯一明确实体不无故出卡 | 与 08-14 对齐：调用 `resolve_anchor_binding()`，禁止 Agent 另写反问规则 |
| S3 | 追问且未切题、上一轮 cited chunks 仍覆盖当前问题时可复用 | `route=reuse_evidence`，sources 非空 |
| S4 | 旧证据未经 `reuse_evidence` 不得进入当前可引用区；`topic_shift` 或澄清 callback 后同此 | trace + 单测 |
| S5 | 证据不足时 Agent 能识别缺口并再次调用检索工具，且不超过预算 | trace 有补检原因与 `retrieve#2` |
| S6 | ConversationContext 与 EvidencePool 在 prompt / trace 中完全可区分 | 单测断言分区与不可依据声明 |
| S7 | Agent 可以根据工具结果改变下一步，而不是执行固定节点顺序 | 工具轨迹测试 |
| S8 | 高副作用工具没有未经授权的执行 | permission / confirmation 测试 |
| S9 | 无引用编造、history 当事实、旧证据污染等核心回归为零 | pytest + 人工黄金集 |
| S10 | Agent 总体延迟、工具次数、补检率可观测 | `qa_trace` + 指标 |

---

## 二、现状基线（2026-08-17 对照代码）

| 能力 | 现状 | 目标变化 |
|------|------|------|
| 主路径 | `stream_query` / `aquery`：Understanding 后几乎必 Hybrid | 由 Agent 决定是否调用何种工具 |
| Understanding | `DialogueUnderstanding`；已有 `topic_shift` | 升级为 Agent 的上下文理解输入 |
| 澄清 | 独立 `POST /query/clarify` + 前端卡片 | 暴露为 `clarify` 工具，但卡片仍结束当前请求 |
| 图谱 | 模式 1 评分不依赖开图；模式 2 扩召回受 `enabled` | 拆分 Entity Linking 与检索内部图扩 |
| 注入 | `_build_messages`：history 规定“不能当事实”，context 当事实 | 升级为独立 `ConversationContext` + `EvidencePool` |
| Agent | `agents.json` 系统提示词 | 增加 Tool Registry、Tool Schema、Agent Loop |
| 检索 | Hybrid ± Rerank + QueryPlanner | 保留为 `retrieve_*` 工具内部实现 |
| 观测 | `qa_trace` 有 understanding / pack | 增加 tools / decisions / evidence / gate / budget |
| 环境操作 | 尚未纳入统一 Agent 工具 | 后期通过统一权限模型接入 |

---

## 三、目标架构

### 3.1 总览

```text
                         用户
                          │
                          ↓
                ┌──────────────────┐
                │    Chat LLM      │
                │                  │
                │ Understand       │
                │ Select Tool      │
                │ Interpret Result │
                │ Reformulate      │
                │ Assess Evidence  │
                │ Decide Next Step │
                │ Answer           │
                └────────┬─────────┘
                         │
                    Tool Calling
                         │
          ┌──────────────┼────────────────┐
          ↓              ↓                ↓
   Entity / Context   Evidence         Environment
      Tools            Tools              Tools
          │              │                │
          └──────────────┼────────────────┘
                         ↓
                 Tool Result / Observe
                         │
                         ↓
                    Chat LLM 再决策
                         │
             ┌───────────┼───────────┐
             ↓           ↓           ↓
          Clarify     Retrieve     Answer
             │           │           │
             │           ↓           │
             │      EvidencePool     │
             │           │           │
             │       EvidenceGate    │
             │           │           │
             │      不足 → 再检       │
             │           │           │
             └────暂停────┴───────────┘
```

外层统一由 Harness Runtime 包住：

```text
┌──────────────────────────────────────────────────────┐
│                    HARNESS RUNTIME                    │
│                                                      │
│ Tool Registry / Schema / Permission                 │
│ Session State / ConversationContext / EvidencePool  │
│ Step / Time / Token / Tool Budgets                   │
│ Clarify Pause / SSE                                  │
│ Evidence Gate / Answer Gate / Citation Governance   │
│ Error / Fallback / Heartbeat                         │
│ Trace / Metrics                                      │
└──────────────────────────────────────────────────────┘
```

### 3.2 控制流原则

目标不是重新编译一个更复杂的 DAG，而是：

```text
User
 ↓
Chat LLM
 ↓
“我现在应该做什么？”
 ↓
调用工具
 ↓
观察工具结果
 ↓
“结果改变了我对问题的理解吗？”
 ↓
继续调用工具 / 改写 / 澄清 / 回答
```

因此：

- **LLM 决定下一步业务动作**；
- **Harness 决定该动作是否允许执行**；
- **Tool 决定动作如何具体实现**；
- **Tool Result 再返回给 LLM**；
- LLM 可以根据结果改变原先计划；
- 不要求所有请求经过同一条固定节点顺序。

这才是本项目所定义的“Chat Agent 化”。

### 3.3 Harness Runtime

Harness 不负责替 LLM 规划业务流程，而负责运行时治理。

#### 必须负责

- Tool Registry；
- Tool Schema 校验；
- Tool 白名单；
- Tool permission；
- side-effect / confirmation 管理；
- 最大 Agent step；
- 最大 retrieve attempt；
- **SSE 进度心跳（Progress Heartbeat）**：当 Agent 执行超过 `heartbeat_initial_delay`（默认 1.5s）仍未产出最终 `answer` 或 `clarify` 时，Harness 必须下发 `{"type":"heartbeat","phase":"thinking"}`（或复用现有 `phase`）。此后按 `heartbeat_interval` 重复。事件不携带业务内容，只防止前端因无 SSE 而判死。**心跳不是超时，不得据此截断生成。**
- **整请求不做绝对硬超时**：Harness **不得**从接单起算墙钟时间，到期无论 LLM 在干嘛都截断进歉答（包括但不限于 8 秒）。请求以 step / retrieve 次数预算、工具失败与用户取消结束。Nginx / 网关若另有连接超时，属于部署层事实，**不是**本 Agent 的正确性条款，也不得写成「超时必须歉答」。
- SSE 生命周期；
- `clarify` 会话暂停；
- `ConversationContext` / `EvidencePool` 生命周期；
- Evidence Gate；
- Answer Gate；
- Citation Governance；
- fallback；
- trace。

#### 不负责

- 替 LLM 固定规定“下一步一定 graph”；
- 替 LLM 固定规定“下一步一定 retrieve”；
- 根据代码写死完整业务 DAG；
- 自己承担自然语言意图理解。

---

### 3.4 ConversationContext 与 EvidencePool

两者必须在数据模型与 Prompt 中独立存在。

#### ConversationContext

用于：

- 历史轮次；
- 当前对话焦点；
- `DialogueUnderstanding`；
- `topic_shift`；
- 用户已选择的实体；
- 澄清历史；
- 对话摘要；
- 指代、省略、上下文理解。

**ConversationContext 永远不能作为知识事实依据。**

#### EvidencePool

用于：

- 当前问题检索到的 chunk；
- 允许复用的上一轮 cited chunk；
- 外部来源；
- 每次检索的 query / tool / retrieval index；
- chunk_id；
- citation metadata。

推荐结构：

```text
EvidencePool
├── Q_current
│   ├── retrieve#1
│   │   ├── chunk A
│   │   ├── chunk B
│   │   └── chunk C
│   └── retrieve#2
│       ├── chunk D
│       └── chunk E
│
└── reusable
    └── previous_turn_cited
        ├── chunk X
        └── chunk Y
```

#### Evidence 生命周期

- 当前问题的证据组默认 `ACTIVE`；
- 上一轮证据默认不可引用；
- 只有 Agent 明确选择 `reuse_evidence`，且没有 `topic_shift` / 实体冲突，旧证据才进入当前可引用区；
- 切题或换实体后旧证据组 `FROZEN`；
- 冻结组可以保留在 trace 中用于回放，但不得注入当前可引用区；
- 每次补检必须建立新的 `retrieve#n` 组，不覆盖旧组。

#### 证据区排序与事实优先级（展示 ≠ 可信度）

Prompt 注入时，**可以把**针对当前 Evidence Gap 的补检组放在首次检索组前面，以提高模型对缺口相关块的注意。这是展示技巧，可选。

**禁止**下列硬规则：

- `retrieve#2` 必须排在 `retrieve#1` 前（不得作为验收断言）；
- 冲突时优先采信补充检索。

事实可信度由 Citation Governance 与下列因素综合决定，**检索轮次只是其中之一、且不是最高优先级**：

```text
source authority
+ entity match
+ temporal validity
+ relevance
+ retrieval confidence
+ retrieval round（最弱）
```

System Prompt 应写：

> 多组证据并存时，按来源权威、实体是否匹配、时效、与当前问题的相关性判断；不得仅因某组是补检结果或排在前面而采信。冲突须显式指出，或只回答有依据且可引用的部分。

#### Entity Change 与 Evidence 生命周期（`head_entity` 不是 topic_shift 的唯一定义）

Harness 每轮维护当前问题的 `head_entity`（优先 canonical / entity id，避免别名误判）。它是 **Evidence 生命周期的硬锚点之一**，不是 `topic_shift` 的唯一判定。

拆开三个概念：

| 变化 | 含义 | Harness 默认 |
|------|------|----------------|
| Entity Change | 主实体变了 | 旧 Evidence 组 **不得自动**进入当前可引用区 |
| Intent 延续 | 同一类问题换实体（「那它呢」「是不是也这样」）或比较追问 | 不自动等同切题；Agent 可对新实体 `retrieve_kb`，必要时显式 `reuse_evidence` |
| Topic Change | 意图与主题都换了 | 冻结旧组；通常必须新检索 |

规则：

- `head_entity` 明确不同 → 记 `entity_transition`；**默认冻结**上一轮 active 证据（不进可引用区）。**不**因此自动 `topic_shift=true`。
- 是否切题、是否需要旧证据：由 DialogueUnderstanding + 是否比较/延续同一 intent 共同决定；LLM 可建议，Harness 仍禁止未 `reuse_evidence` 的旧组混入可引用区。
- 任一侧 `head_entity` 无法可靠提取 → 不得用实体规则强行切题。
- `reuse_evidence` 仍须 Agent 显式调用，且不得与当前确认实体冲突。

---

### 3.5 Tool Registry

所有 Agent 能力统一通过 Tool Registry 暴露。

| 工具 | 输入 | 输出 | Agent 用途 |
|------|------|------|------|
| `understand` | question, context | UnderstandingResult | 理解当前语境 |
| `link_entities` | question / entity hints | candidates, aliases, conflicts, confidence | 判断“用户说的是谁” |
| `clarify` | candidates, question | clarification card | 用户需要选择时暂停会话 |
| `rewrite` | context, entity, intent, evidence gap | resolved_question / queries | 将自然语言问题转为当前任务 |
| `reuse_evidence` | cited chunk ids | evidence group | 追问时复用已有证据 |
| `retrieve_kb` | resolved question / query intent / filters | evidence group | 知识库检索 |
| `retrieve_related` | entity / relation gap | evidence group | 后期可独立暴露的相关检索能力 |
| `web_search` | question | external evidence group | 用户显式要求或策略允许时调用 |
| `environment.*` | tool-specific schema | tool result | 环境读取/操作；按权限模型治理 |

**`answer` 不是 Tool Registry 中的普通工具。** 它是 Agent Loop 的 **终止动作**：

```text
Tool calls → Observe → Decide
    ├── continue（再调工具）
    ├── clarify（会话暂停）
    └── finish
            ↓
        Answer Gate
            ↓
         Generate（SSE）
```

`answer` / Generate 必须经过 Answer Gate / Citation Governance，禁止做成可被模型直接 `answer()` 绕过门禁的 registry tool。

`graph_expand_chunks` 本期仍可作为 `retrieve_kb` 的内部实现，不要求 LLM 直接操作图扩底层参数。

---

### 3.6 Entity Resolution：问题 → 图谱 → 歧义 → 反问

目标流程不是固定执行，而是由 LLM 根据上下文判断是否需要实体工具。

```text
用户问题
   ↓
Chat LLM 理解上下文
   ↓
是否存在实体不确定？
   │
   ├── No → 继续当前任务
   │
   └── Yes
         ↓
   link_entities
         ↓
   候选实体 / 别名 / 相似实体 / 冲突
         ↓
   是否唯一高置信？
      ├── Yes → resolved_entity
      └── No  → clarify
                    ↓
               结束本轮 HTTP
                    ↓
             用户选择后下一轮继续
```

反问触发条件：

- 多个候选实体均合理；
- 宽口径术语存在多个产品/工具/服务解释；
- 实体置信度不足；
- 当前语境无法可靠消歧。

不应反问：

- 用户已经点名唯一合法实体；
- 图谱只返回一个高置信实体；
- 只是检索结果不足，而不是实体不明确。

**裁决入口（硬约束）**：上列条件不得在 Agent Prompt / Helper LLM / 图邻居计数里各写一份。`clarify` 工具必须调用 08-14 `resolve_anchor_binding()`（及现有 J3 卡构建）。非法锚（如 J3×Pipeline*）不得因「点了名」而正锚检索。模型建议 `clarify=false` 时，若绑定结果要求强制 J3 卡，Harness 仍须出卡。

图谱工具的主要职责是**Entity Resolution**，不是提前替代 RAG。

---

### 3.7 动态问题改写

LLM 可以根据：

- 原始问题；
- ConversationContext；
- 已解析实体；
- 用户刚刚选择的澄清项；
- 已有 Evidence；
- Evidence Gap；

动态形成：

```text
resolved_question
retrieval_intent
queries[]
```

例如：

```text
用户：
“那 WebGL 呢？”

上一轮：
PipelineBuilder 发布服务

LLM 根据上下文：
→ resolved_entity = PipelineWebGL
→ intent = 发布服务
→ resolved_question = “PipelineWebGL 如何发布服务？”
```

改写是 Agent 的核心能力之一。

但改写后的 query 仍必须进入受治理的检索工具，不能绕过 Evidence Gate。

---

### 3.8 Evidence Gate 与有限补检

Agent 获得检索结果后，必须先观察 Evidence，而不是自动回答。

#### 规则侧

以下情况直接判定不能知识作答：

- EvidencePool 为空；
- 无可引用 `chunk_id`；
- 当前问题显式实体与证据主体冲突；
- 旧证据未经过 `reuse_evidence`；
- 当前证据明显属于已冻结问题/实体。

#### 模型侧（recommendation only）

LLM 可以判断：

- 当前证据是否覆盖 `resolved_question`；
- 哪些要点已支持、哪些缺失；
- 是否存在冲突；
- 建议下一步：finish / 补检 / 换检索能力 / 澄清。

输出建议：

```text
support
insufficient
uncertain
```

**模型判断不能单方面放行。** `support` 仍须通过规则侧与 Answer Gate。`insufficient / uncertain` 以及规则否决，不得进入知识答案。

```text
Chat LLM：「我认为够了」
        ↓
Evidence Gate / Answer Gate
    sources > 0 ?
    可引用 chunk_id ?
    entity match ?
    未混入冻结组 ?
    required facts / conflict ?
        ↓
    允许 Generate 或强制补检/歉答
```

#### 补检

```text
retrieve#1
   ↓
Observe
   ↓
Evidence insufficient
   ↓
LLM 找出 evidence gap
   ↓
重新改写 / 选择检索工具
   ↓
retrieve#2
   ↓
Observe
   ↓
Answer 或最多继续一次
```

预算：

- 默认：首次 + 1 次补检；
- 最大配置：首次 + 2 次补检；
- 全局 Agent step 仍受 Harness 限制；
- 补检超时、空结果、工具异常不得无限重试；
- 达到上限后按现有“部分依据/无依据”治理回答。

关键要求：

> **补检必须针对 Evidence Gap，而不是简单重复搜索，也不是固定「去修饰 → 宽泛化」阶梯。**

#### 按 Gap 类型选择恢复策略（非固定 Staircase）

Harness **不**把补检变成状态机（#1 必须剥修饰、#2 必须宽泛化）。

流程：

```text
Evidence Gap
      ↓
Gap Type
      ↓
Recovery Strategy（注册表，可演进）
      ↓
Query Rewrite（LLM 在该策略约束内生成文本）
      ↓
retrieve
```

Gap 类型至少包括：`missing_fact` / `missing_relation` / `missing_scope` / `entity_conflict` / `temporal_conflict` / `low_relevance` / `empty_retrieval`。

策略是受治理的选项，不是强制顺序。示例：

| Gap 类型 | 更合适的策略 | 不合适 |
|----------|----------------|--------|
| `empty_retrieval` | `strip_modifiers` 或 `broaden_semantics`（须保留已确认实体） | — |
| `missing_fact`（如缺版本） | `add_missing_attribute` | 再宽泛化丢掉属性 |
| 实体对、缺步骤 | `add_missing_attribute` / 更具体 procedure query | 扩大到无关上位词 |
| `entity_conflict` / 事实冲突 | `increase_entity_constraint`、提高权威来源约束 | 宽泛化混入论坛帖 |
| 仍不够且无歧义 | 允许一次 `broaden_semantics` 作为 **fallback** | 为走完阶梯而再检 |

第一版可以把 `strip_modifiers` → `broaden_semantics` 当作 **默认 fallback 链**（仅当 Gap 为 empty / low_relevance 且未指定更具体策略）。后续用 trace 扩展 `RecoveryStrategyRegistry`。

约束：

1. trace 记录 `gap_type` 与 `recovery_strategy`（不再要求固定 `staircase_stage` 顺序）；
2. LLM 生成该策略允许的 Query 文本，不选择 BM25 / Vector / RRF / MMR；
3. 宽泛化必须保留已确认实体；不得用宽泛检索绕过实体确认；
4. 实体歧义时先 Entity Resolution / Clarify；
5. Gate 已 support（且规则侧放行）则立即 finish，不得为走完整策略链而继续检索。

---

### 3.9 动态检索能力（后期）

本期保留现有 `retrieve_kb` 内部 Hybrid ± Rerank + QueryPlanner。

后期允许 LLM 在工具层面选择检索能力，例如：

```text
retrieve_precise
retrieve_broad
retrieve_related
retrieve_keyword
```

但仍不直接暴露：

```text
bm25_weight
vector_weight
rrf_k
mmr_lambda
raw_top_k
```

也就是说：

> **LLM 可以选择“怎么解决问题”，但不能直接修改检索器的底层治理参数。**

后期只有在离线评测能够证明策略选择稳定提升 Recall / Answer Support 后，才允许逐步开放。

---

### 3.10 环境工具

Agent 可以逐步接入环境操作能力，但所有环境工具必须声明：

```text
tool_name
input_schema
output_schema
permission
side_effect
confirmation_required
timeout
```

分级：

| 类型 | 示例 | 默认确认 |
|------|------|------|
| Read | 查看文件、读取状态、查询配置 | 否 |
| Execute | 执行诊断、运行非破坏性命令 | 视工具 |
| Write | 修改配置、写入文件 | 是 |
| Destructive | 删除、重建、清空 | 必须 |

本期不把运维工具作为 Phase 1–3 的交付目标，但 Tool Registry 必须从设计上支持该能力，避免未来再次改造 Agent 核心。

---

### 3.11 单请求工作流（验收用主路径）

典型知识问答：

```text
1. Harness 初始化 Session / Budget / Context / EvidencePool
2. Chat LLM 理解用户问题与上下文
3. LLM 判断是否需要 Entity Resolution
4. 如需要 → link_entities
5. 如存在歧义 → clarify → 结束本轮
6. **若下一轮是澄清回调（callback）**：Harness 在 Agent Loop 启动前执行 Evidence Hard Reset：
   - 将上一轮「澄清发起轮」写入的 Evidence 组全部标为 `FROZEN`（移出 **当前可引用池**）；**不要求物理删除**，trace / `EvidencePool.history` 须保留以回放误搜块；
   - `EvidencePool.active` 为空；
   - 保留 ConversationContext 中的对话摘要、澄清历史与用户 `selected_entity`；
   - 将 `selected_entity` 硬绑定到本轮 resolved context；
   - 本轮禁止 `reuse_evidence`（含冻结组），必须重新 `retrieve_kb`；
7. 无歧义 → LLM 形成 resolved_question / retrieval intent
7. LLM 选择 reuse_evidence 或 retrieve 工具
8. Tool 返回结果 → 写入 EvidencePool
9. LLM 观察 Evidence
10. Evidence 不足 → 找出 Evidence Gap
11. LLM 再调用检索工具（最多 1～2 次补检）
13. Gate 允许 → Answer Gate
14. Citation Governance
15. **terminal：Generate / SSE**（非 `answer` 工具）
16. trace 完整记录整个 Agent trajectory
```

**注意：上述不是要求所有请求固定执行 1～15。**

例如：

- 闲聊可能直接回答；
- 明确实体的问题可以不调用 `link_entities`；
- 追问可能直接 `reuse_evidence`；
- 环境读取可能完全不进入 RAG；
- 歧义问题可能在 `clarify` 后结束本轮。

这正是从 DAG 到 Chat Agent 的核心变化。

---

## 四、分阶段交付

### Phase 1 — Agent Runtime + Tool Registry + Context/Evidence 分区

目标：先完成“LLM 主导、工具调用、Harness 治理”的基础骨架，不立即追求复杂自主检索。

- 建立 Tool Registry / Tool Schema；
- 建立 Agent Loop；
- Harness 管理 step / retrieve 次数预算 / tool whitelist（**无整请求墙钟硬超时**）；
- Prompt 注入拆为 `ConversationContext` / `EvidencePool`；
- EvidencePool 按 `question × retrieve_index` 分组；
- `qa_trace` 增加工具轨迹；
- 保留现有 `retrieve_kb` 作为知识工具；
- 总开关关闭可完整回退旧 DAG；
- **可与 08-14 收口并行**；本阶段不注册、不调用 `clarify`，不改 `query_clarification.py` / `resolve_anchor_binding()`。

验收：

- Agent 能选择是否调用工具；
- 工具结果能返回给 Agent 并影响下一步；
- history 不可作为事实；
- EvidencePool 可以单独引用；
- SSE 协议不变。

### Phase 2 — Entity Resolution + Clarify Tool

目标：让 Agent 能像聊天一样处理歧义实体。

- 暴露 `link_entities`；
- **统一 08-14 指称唯一性裁决**：`clarify` 只包 `resolve_anchor_binding()` + 现有 J3 选项构建，禁止重写「何时反问」；
- Agent 根据候选结果**建议**是否 `clarify`；Harness 以 08-14 绑定结果为准（可拦模型漏问 / 多余全列家族）；
- `clarify` 结束当前 HTTP；
- 用户选择后带 resolved entity 重入 Agent；
- 图关闭时降级到现有 catalog / clarification 规则。

**前置**：08-14 Phase 0 已在运行中的 `/query/clarify` 上复验通过（点名合法锚零反问、无产品写代码仍强制 J3 卡）。不得对着未重启的旧 10605 调 Phase 2。

验收：

- `pipeline` 等宽口径问题能正常消歧；
- 点名唯一实体不无故反问（08-14 C-named-webrtc / C-named-webgl / C-j2-named / C-j1-named）；
- 无产品写代码仍出 J3 卡且无 Pipeline*（C-unclear-line / C-via-code）；
- 模型想跳过强制 J3 卡、或点名后仍全列家族 → Harness 拦住（trace 记 `fallback`）；
- 图谱不确定不会直接污染 EvidencePool。

### Phase 3 — EvidenceGate + Bounded Recovery Loop

目标：实现真正的“看证据 → 找缺口 → 再行动”。

- EvidenceGate；
- Evidence Gap 结构；
- 首次检索 + 最多 1～2 次补检；
- 补检 query 针对 Gap 类型改写（默认 fallback 可含剥修饰/宽泛化，非强制状态机）；
- 新结果独立分组；
- Agent 能根据第一次结果改变第二次工具调用；
- Citation Governance；
- 延迟 / 补检率 / 命中率观测。

验收：

- 明显证据不足时触发补检；
- 补检针对缺口，而非简单重复；
- 不超过预算；
- 无证据不得知识作答；
- 引用必须来自当前 EvidencePool；
- **Evidence Gate 是治理结果**：LLM 报 `support` 仍须规则侧 + Answer Gate 放行；单测覆盖「模型说够了但池为空 / 实体冲突 → 不得知识作答」；
- **澄清重置验收**：用户点击澄清卡片后重入，**当前可引用池**不残留上一轮 chunk（组状态为 FROZEN，trace 可回放）；禁止 reuse；必须重新 retrieve。

撤回的验收（不得再写进测试）：补检组必须排在首次之前；冲突时必须优先采信 `#2`；`head_entity` 变化必须 `topic_shift=true`；补检必须走固定剥修饰/宽泛化顺序。

### Phase 4 — Tool Expansion / Environment Tools

目标：扩展 Agent 的“Chat + Tools”能力。

- `web_search`；
- 环境 Read 工具；
- 非破坏性 Execute 工具；
- Write / Destructive 工具的 confirmation；
- 工具权限审计。

本阶段仍不开放任意环境执行。

### 后期期望 — LLM 管理检索策略

明确不作为 Phase 1–4 阻塞项：

- LLM 在受控档位中选择 `precise / broad / related / high_recall`；
- 离线评测验证；
- 允许工具根据问题复杂度选择不同内部检索实现；
- 不允许模型直接修改底层检索公式和任意参数；
- 失败自动回退当前 Planner 默认。

---

## 五、观测与配置

### 5.1 qa_trace

在现有 `understanding` / `pack` 基础上增加：

| 字段 | 含义 |
|------|------|
| `agent_steps[]` | 每一步 Agent 决策 |
| `tools[]` | 有序工具名、输入摘要、耗时、状态 |
| `route` | 本轮最终路径 |
| `entity_link` | 候选数、是否反问、实体、降级原因 |
| `conversation_context` | context 版本/摘要标识，不记录为事实依据 |
| `evidence_groups[]` | `{question_id, kind, retrieve_index, chunk_ids, status}` |
| `evidence_gap[]` | 缺失的事实/关系/范围 |
| `gate` | LLM 建议：`support` / `insufficient` / `uncertain` |
| `answer_gate` | Harness 最终是否允许知识作答及原因（与 `gate` 分离） |
| `retrieve_attempts` | 检索总次数，含首次 |
| `reuse` | 是否复用上一轮证据 |
| `budget` | step / retrieve / time 使用情况 |
| `fallback` | 如 `reuse_to_retrieve`、`link_to_rules`、`tool_timeout` |
| `retrieve_improvement` | **粗指标** `0/1/null`：补检是否新增「首检缺失且与当前问相关」的可引用来源。**不能**单独决定是否保留补检 |
| `counterfactual_support` | 离线对照：仅 `retrieve#1` vs `#1+#2` 的 answer support / citation_gain；另计 `unnecessary_retrieval_rate` |

**观察者模式（Phase 3 上线前建议）**：先开启补检但不让补检结果改变最终生产答案。主决策看反事实 support 与不必要补检率；`retrieve_improvement` 只作辅指标。连续观察至少 2 周后由运营决定收紧阈值或关补检，**30% 不是永久正确率保证，也不是唯一闸门**。  

### 5.2 配置

保持少量全局治理项：

- `agent_orchestration.enabled`（总开关，默认 false，本地可开）
- `agent_orchestration.max_steps`
- `agent_orchestration.max_retrieve_attempts`（默认 2 = 首次 + 1 次补检）
- `agent_orchestration.tool_timeout`（**仅**单次工具调用挂起防护，可选；禁止用作整请求 8 秒歉答）
- `agent_orchestration.heartbeat_initial_delay`（默认 1.5s）
- `agent_orchestration.heartbeat_interval`
- 现有 `[graph_retrieval] enabled` 不因本文改生产默认
- 各高副作用环境工具独立 permission / confirmation 配置

总开关关闭时：

```text
/ query / query/stream
→ 回到当前 DAG
```

并保留 08-11 短记忆与 `topic_shift`。

---

## 六、后期期望：LLM 管理检索模式

**标注：后期期望，明确不做本期必做项。**

未来目标不是让 LLM 直接调参数，而是让 LLM 选择受治理的检索能力：

```text
retrieve_precise
retrieve_broad
retrieve_related
retrieve_high_recall
```

约束：

1. 工具由 Registry 定义；
2. 工具内部拥有自己的检索参数；
3. LLM 只能选择能力，不修改底层公式；
4. 上线前必须使用冻结评测集做 A/B；
5. 不得低于现有 Hybrid 基线；
6. 策略选择失败 → 静默回退 Planner 默认。

最终目标是：

> **LLM 负责“选择解决问题的方法”，检索系统负责“保证这个方法可靠地执行”。**

---

## 七、风险与反对方案

| 风险 | 处理 |
|------|------|
| Agent 变成无边界 ReAct | Harness step / time / tool budget |
| LLM 工具调用抖动 | Tool Schema + 明确工具描述 + trace |
| 补检使延迟翻倍 | 默认仅 1 次补检；Evidence 足够立即停止 |
| LLM 为了多搜而多搜 | 必须输出 Evidence Gap；补检与 gap 绑定 |
| 证据池累积导致切题粘滞 | 未经 reuse 的旧组不得进可引用区；澄清 callback 冻结歧义轮证据 |
| Agent 被墙钟掐死 | **不做**整请求绝对超时歉答；用 heartbeat 保活 SSE；用 step / 补检次数限循环 |
| 图谱每问都反问 | Agent 仅在实体不确定时调用；唯一高置信直接继续 |
| 与 08-14 双写「要不要问」 | Phase 1 不碰 clarify；Phase 2 只调 `resolve_anchor_binding()`；禁止「种子 ≥ 2 ⇒ 反问」回流；08-14 与 Phase 2 禁止同时改 `analyze()` |
| 模型用对话区作答 | Prompt 分区 + Evidence Gate + Answer Gate |
| LLM 乱改检索参数 | 底层参数隐藏在 Tool 内；后期只开放策略档位 |
| 环境工具误操作 | permission / side_effect / confirmation |
| 与旧系统回滚困难 | `agent_orchestration.enabled=false` 完整回退 |
| “Chat Agent”变成重新写一套 RAG | 复用现有 Retrieval / Pack / Citation 能力，仅重构控制流 |

### 已否定

- 无 Harness 的开放 Loop；
- 每问固定先图扩 chunk；
- 把 history 当作知识证据；
- 无证据池的「LLM 自己判断够不够就能答」；
- LLM 直接控制 BM25 / Vector / RRF / MMR；
- 问答 Agent 内无权限执行重建 / 删除 / 改配置；
- 为了 Agent 化而推翻已有 RAG、图谱和引用治理；
- 无 Harness 的整请求绝对硬超时（含「超时 8 秒无论模型在干嘛都歉答」）；
- **把检索轮次当事实优先级**（冲突采信 `#2`）；
- **`head_entity` 变化 ≡ `topic_shift`**；
- **固定 Staircase 作为补检状态机**；
- 把 `answer` 做成普通 Tool Registry 工具以绕过 Answer Gate；
- 在 Agent 内重写 08-14「何时反问」，或恢复「家族扩展种子 ≥ 2 ⇒ 反问」。

---

## 八、Harness 硬边界（治理，不是规划）

以下才是实现验收硬约束。Harness **约束不能做什么**，不规定 Agent 必须按哪条阶梯思考。

| 编号 | 硬约束 | 执行主体 |
|------|--------|----------|
| F1 | 超过 `heartbeat_initial_delay` 无最终事件则发 heartbeat，并按 `heartbeat_interval` 续发 | Harness |
| F2 | **禁止**整请求绝对硬超时截断歉答；循环结束只看 step / retrieve 预算、工具失败或用户取消 | Harness |
| F3 | 未经 `reuse_evidence` 的旧 Evidence 组不得进入当前可引用区 | Harness |
| F4 | `head_entity` 明确变化时旧组默认冻结（不自动进可引用区）；**不**因此强制 `topic_shift` | Harness |
| F5 | 澄清 callback：歧义轮 Evidence 移出 active、标 FROZEN，trace 保留 | Harness |
| F6 | 澄清 callback 禁止 reuse，必须重新 retrieve | Harness |
| F7 | 补检必须绑定已记录的 Evidence Gap（及 gap 类型）；禁止无 gap 重复检索 | Harness + LLM |
| F8 | 达到 retrieve / step 预算后不得继续工具循环 | Harness |
| F9 | 补检收益可观测：反事实 support 为主，`retrieve_improvement` 仅粗指标 | Trace / 离线评测 |
| F10 | 最终事实引用必须来自当前 **active** EvidencePool | Answer Gate |
| F11 | LLM 的 `support` 不能单方面放行知识作答 | Evidence Gate / Answer Gate |
| F12 | `answer` 不是白名单工具；Generate 只能作为 Loop 终止态经过 Answer Gate | Harness |
| F13 | `clarify` 以 08-14 `resolve_anchor_binding()` 为准；模型建议不得否决强制 J3 卡，也不得对已绑定合法锚再出家族全列卡 | Harness |

已从硬约束移除：整请求墙钟超时（含 8 秒）、1.5s 当作业务规则、`#2` 必须排前且冲突优先、`head_entity` 强制切题、固定 Staircase stage。

这些边界的目的不是把 Agent 再编译回 DAG。

---

## 九、回滚

`agent_orchestration.enabled=false` 后：

- `/query` / `/query/stream` 回到当前 DAG；
- 澄清仍走 `/query/clarify`；
- 08-11 Session / 短记忆 / `topic_shift` 保留；
- 旧检索器与 Pack 保持可用。

不得把 Agent Prompt / 分区注入改造成无法关闭的破坏性变更。

---

## 十、建议实施顺序（代码未授权前仅作计划）

1. **先做 Runtime 骨架**：Tool Registry、Tool Schema、Harness Budget、Agent Step、trace。**可与 08-14 收口并行**；本步及 2～4 **不**接入 `clarify`。
2. **建立 ConversationContext / EvidencePool 双对象**：完成 Prompt 分区与 Evidence 生命周期。
3. **接入现有 `retrieve_kb`**：验证“LLM 选择工具 → 工具结果 → LLM 再决策”闭环。
4. **接入 `reuse_evidence`**：验证追问与 `topic_shift`。
5. **接入 `link_entities` + `clarify`**：统一 08-14 裁决器（`resolve_anchor_binding()`）。**仅当 08-14 Phase 0 已在运行进程复验通过**；不得另写反问规则；Harness 覆盖漏问 J3 / 点名后家族全列。
6. **实现 EvidenceGate + Gap 类型恢复 + 补检**：首次 + 1 次默认，最多 +2。
7. **接入 Citation / Answer Gate**：确保 Agent 自由决策不突破事实边界。
8. **再扩展 web / environment tools**：按权限模型逐步加入。
9. **最后再研究 LLM 管理检索策略**：以离线指标决定是否上线。

**开始改代码前须单独授权；本文只定义需求与架构。**
