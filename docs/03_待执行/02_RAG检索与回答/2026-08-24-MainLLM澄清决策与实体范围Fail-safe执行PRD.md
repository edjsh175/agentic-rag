# Main LLM 澄清决策与实体范围 Fail-safe 执行 PRD

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 日期 | 2026-08-24 |
| 状态 | 待实施 |
| 所属域 | `02_RAG检索与回答` |
| 解决问题 | 未解析/疑似拼写实体被错误放行到 Agent，随后在授权失败后扩大为无 target 的宽检索，最终出现“随机召回” |
| 核心裁决 | **是否需要澄清由 Main LLM / Agent Controller 决定；代码负责候选发现、实体解析、Scope/Grant 和状态机约束；未确认实体不得被当作合法 `target_entity`；身份不确定时扩大澄清候选，不扩大证据检索范围。** |
| 关联文档 | `2026-08-21-Agent两阶段回答与模型路由改善PRD.md`、`2026-08-21-问答质量止损与恢复PRD.md`、`2026-08-24-HelperLLM回答Grounding审查执行PRD.md` |

---

## 1. 背景

第二类真实事故表现为：用户输入一个疑似专有实体、拼写不完整或主体不明确的词，例如：

```text
pipelien
```

系统没有在正确时机停下来向用户确认，而是把该字符串继续当作实体目标传入 Agent 工具：

```text
link_entities(target_entity="pipelien")
retrieve_kb(target_entity="pipelien")
```

随后 Scope / ExplorationGrant 正确拒绝该目标：

```text
exploration_not_authorized
```

但 Runtime 没有把“目标身份未解析”视为必须停止实体探索的状态，反而继续尝试同一 target，并最终通过 recovery/harness 去掉 `target_entity`，执行：

```text
retrieve_kb(query="pipelien", target_entity=null)
```

这把一个“身份不明确”的问题错误转化为“扩大证据搜索范围”的问题，导致全库宽召回。由于 Query 自身几乎没有有效语义，Retriever 只能从一组低质量候选中强行返回 TopK，于是用户看到的效果近似“随机抽 Chunk”。

2026-08-21 事故 Trace `d1a717c0d5794ef7abfd69ca74fee6a4.json` 已确认：

```text
clarify = null / {}
→ target_entity = pipelien
→ exploration_not_authorized
→ 多次重复 target 探索
→ harness_autonomous_retry
→ target_entity = null
→ broad retrieval
```

因此，本问题不是单纯的 Clarification UI Bug，也不是单纯的 Retriever 相关性问题，而是一整条：

```text
澄清决策
→ 候选发现
→ 用户确认
→ Canonical Resolution
→ IdentityScope
→ ExplorationGrant
→ Agent Recovery
→ Retrieval Fail-safe
```

职责边界没有收口。

---

## 2. 第一性原则裁决

### 2.1 “是否继续行动”属于 Main Controller，不属于 seed 数量规则

是否需要澄清，本质是：

> 当前问题是否已经足够明确，使 Agent 能安全地选择后续工具并确定检索范围？

这是 Agent Controller 的决策职责。

因此：

```text
Main LLM / Agent Controller
= 判断 CLEAR / NEED_CLARIFICATION
= 决定调用 clarify / retrieve_kb / link_entities / finalize
```

代码不再使用：

```text
len(seeds) < min_options
→ needs_clarification = false
```

来代表“主体已经明确”。

Seed 只能帮助生成候选，不能决定是否需要澄清。

### 2.2 Clarify Tool 是候选发现与卡片执行工具，不是最终裁判

目标职责：

```text
Main Controller
= 决定是否需要澄清

Clarify Tool
= 收集系统候选
+ 接收 Main 建议候选
+ 合并/标注候选来源
+ 生成结构化澄清卡片
+ 暂停等待用户选择
```

不得再让 QueryClarificationService 同时承担：

```text
是否澄清的最终裁决
+ 候选发现
+ 卡片构造
```

### 2.3 Main 可以提出候选，但不能授予候选实体身份

Main 允许根据：

```text
Question
Conversation Context
系统 Seed Candidates
已知产品命名模式
当前 Agent 推理
```

向澄清卡片补充合理选项。

但必须区分：

```text
model_suggested_candidate
≠
canonical_entity
```

Main 的权限是：

```text
提出“你可能指的是 X”
```

不是：

```text
宣告“X 是知识库合法实体，并授予 X ExplorationGrant”
```

### 2.4 用户确认意图后，仍要经过 Canonical Resolution

用户点击一个澄清项，只表示：

> 这是用户想表达的方向。

是否能够成为 `target_entity`，仍由实体解析层决定。

因此选项确认后必须分成：

```text
CONFIRMED_ENTITY
CONFIRMED_TOPIC
OTHER / FREE_TEXT
```

其中：

- `CONFIRMED_ENTITY`：能够映射到真实 canonical entity，可进入 IdentityScope / ExplorationGrant。
- `CONFIRMED_TOPIC`：用户确认了一个文本方向，但无法映射到 canonical entity；可以作为 Query Context 使用，但不能获得实体型 Grant，也不能调用需要实体身份的图谱探索。
- `OTHER / FREE_TEXT`：用户明确否定已有选项或补充新文本，重新进入 Main Controller 判断。

### 2.5 未确认实体不得获得 `target_entity`

必须建立明确的身份状态：

```text
identity_status =
  CONFIRMED_ENTITY
  CONFIRMED_TOPIC
  UNRESOLVED
```

只有：

```text
CONFIRMED_ENTITY
```

可以写入：

```text
target_entity
head_entity
IdentityScope.confirmed_entity
ExplorationGrant.target
```

禁止：

```text
raw user token
LLM 猜测
soft match candidate
model_suggested option
```

在用户确认 + canonical resolve 之前直接进入 `target_entity`。

### 2.6 身份不确定时扩大澄清候选，不扩大证据检索范围

系统核心不变量：

```text
identity uncertain
→ candidate space can expand
→ evidence search scope cannot expand
```

禁止：

```text
exploration_not_authorized
→ drop target_entity
→ broad retrieve
```

身份未解析时，恢复方向必须是：

```text
clarify
candidate discovery
request more context
```

而不是扩大 Retrieval Scope。

### 2.7 Retriever 必须允许“0 个合法结果”

本 PRD 不负责 Chunk 内容质量治理，但必须修复一个检索状态语义：

```text
TopK ranking
≠
valid evidence found
```

当 Query / Scope 不具备足够合法匹配时，检索链路必须允许：

```text
retrieval_status = NO_VALID_EVIDENCE
source_documents = []
```

而不是为了满足 `top_k` 强行把低相关文档交给后续链路。

Chunk 本体低信息、空 Excel 表格等治理放到第三点 PRD，本 PRD 只定义：

> 没有合法相关结果时可以返回 0，且不得通过扩大 Scope 来“凑结果”。

---

## 3. 产品目标

本次改造完成后，专有实体、疑似拼写、模糊主体、单词式查询必须具备稳定的前置决策和失败保护。

目标：

1. Main Controller 成为“是否需要澄清”的唯一智能决策者。
2. Clarify Tool 同时支持代码 Seed Candidate 与 Main Suggested Candidate。
3. 用户选择后，只有 canonical resolve 成功的选项才能成为实体 Scope。
4. 未解析字符串不能继续进入 `target_entity`。
5. Agent 工具被 Scope / Grant 拒绝后，不得通过删除 target 扩大检索范围。
6. 多次失败不再触发同一 unresolved target 的重复探索。
7. Retriever 可返回 0 个合法结果。
8. Trace 可明确解释：为什么澄清、候选来自哪里、用户选了什么、最终绑定成 Entity 还是 Topic、为什么停止检索。

---

## 4. 非目标

本 PRD 不处理：

- Chunk 解析质量、Excel 空表格、低信息内容清洗。
- Answer Grounding Checker；该问题由 `2026-08-24-HelperLLM回答Grounding审查执行PRD.md` 负责。
- Main / Helper 模型更换。
- 图谱主干架构重建。
- 自动修复用户拼写并静默绑定实体。
- 通过硬编码 typo 词典为特定词打补丁。
- 给 Main 权限创建知识库正式实体。

---

## 5. 目标模型职责

### 5.1 Main LLM / Agent Controller

负责：

```text
理解当前问题
判断是否足够明确
决定 clarify / retrieve_kb / link_entities / finalize
基于系统 seed 补充澄清候选
决定澄清问题如何表达
```

Main 不负责：

```text
直接授予实体 canonical 身份
绕过 Scope / Grant
把自己建议的候选自动写成 target_entity
```

### 5.2 Helper LLM

本 PRD 不新增 Helper 职责。

现有 Helper 可继续承担 Common Stage-1 的上下文化、指代消解、Rewrite 等准备工作，但：

> **是否需要澄清的最终动作决策由 Main Controller 完成。**

### 5.3 Code

代码负责：

```text
Candidate Discovery
Canonical Resolution
Identity Binding State
Scope / Grant
Tool Contract
Card Serialization
User Selection Callback
Recovery State Machine
Retrieval Result Validity State
Trace
```

代码不得用 candidate 数量替代 Main 的澄清决策。

---

## 6. 目标设计：Main First-Action Decision

### 6.1 Main 第一轮必须能够选择 clarify

Agent Registry 中 `clarify` 必须是 Main 第一轮可调用工具，而不是后续补救工具。

Main 决策空间：

```text
clarify
retrieve_kb
link_entities
reuse_evidence
finalize
```

Main Prompt 明确：

```text
当用户主体、产品、模块、专有名词或意图不足以安全确定检索范围时，
优先调用 clarify，而不是猜测实体或做无范围搜索。
```

典型需要 clarify：

```text
疑似拼写错误
单一陌生专有词
多个产品/模块均可能匹配
用户使用模糊族名
用户问题无法确定要查哪个实体
```

典型无需 clarify：

```text
用户明确写出 canonical entity
用户已完成澄清 callback
显式比较 A 和 B 且两个实体都明确
普通不依赖实体绑定的通用知识库问题
```

这里由 Main 语义判断，不新增正则决定表。

---

## 7. Clarify Tool V2 契约

### 7.1 输入

建议：

```json
{
  "question": "你指的是下面哪个产品或模块？",
  "query": "pipelien",
  "model_suggested_options": [
    {
      "label": "PipelineWebGL",
      "rationale": "可能是相近的 Pipeline 系列产品名"
    }
  ]
}
```

Main 可以不给建议候选，此时工具仅使用系统 Candidate Discovery。

### 7.2 系统 Candidate Discovery

代码候选来源至少支持：

```text
backbone
canonical alias
domain catalog
graph entity names
name similarity
spelling similarity
family / prefix similarity
different_from / sibling hints
```

本 PRD 不要求一次性把所有算法重写，但输出必须统一为 Candidate DTO。

### 7.3 Candidate DTO

统一结构：

```json
{
  "candidate_id": "cand_01",
  "label": "PipelineWebGL",
  "canonical_name": "PipelineWebGL",
  "entity_type": "Tool",
  "source": "backbone",
  "binding_status": "canonical",
  "score": 0.91
}
```

Main 补充项：

```json
{
  "candidate_id": "model_01",
  "label": "Pipeline 发布服务",
  "canonical_name": null,
  "entity_type": null,
  "source": "model_suggested",
  "binding_status": "unresolved",
  "score": null
}
```

### 7.4 合并规则

Clarify Tool 合并：

```text
System Candidates
+
Main Suggested Candidates
+
固定 Other 选项
```

去重优先级：

```text
canonical exact > alias > graph/catalog > fuzzy > model_suggested
```

Main 不允许通过相同 label 覆盖一个已有 canonical candidate 的真实 metadata。

### 7.5 Main 候选不得直接生成 filter.entity_name

当前实现中自定义 option 会直接转为：

```json
{
  "filter": {"entity_name": "<label>"},
  "source": "llm_agent"
}
```

该逻辑必须删除。

目标：

```text
model_suggested option
→ binding_status=unresolved
→ user selected
→ canonical resolver
→ resolve success 才写 entity_name
```

---

## 8. 用户选择后的绑定协议

### 8.1 选择 canonical candidate

```text
User selects cand_01
↓
canonical_name 已由系统确认
↓
identity_status = CONFIRMED_ENTITY
confirmed_entity = PipelineWebGL
↓
IdentityScope
↓
ExplorationGrant
↓
Agent 定向检索
```

### 8.2 选择 Main suggested candidate，解析成功

```text
User selects model_01
↓
canonical resolver(label)
↓
映射到真实 entity
↓
identity_status = CONFIRMED_ENTITY
↓
进入实体型 Scope
```

### 8.3 选择 Main suggested candidate，无法解析

不允许：

```text
label → target_entity
```

应转为：

```text
identity_status = CONFIRMED_TOPIC
confirmed_topic = <label>
target_entity = null
```

允许：

```text
基于用户已明确确认的 topic 做普通文本 Query
```

禁止：

```text
link_entities(target_entity=<label>)
entity-scoped graph exploration
ExplorationGrant for <label>
```

该场景与“系统误把 unresolved token 做 broad retrieval”不同：

> 这里的 Topic 已由用户明确确认，仅作为文本检索意图，不伪装成知识库实体。

### 8.4 用户选择 Other

```text
identity_status = UNRESOLVED
```

系统请求用户补充文本，重新交回 Main Controller。

---

## 9. Identity State 契约

建议在 `ConversationContext` / `SemanticTaskContext` 中加入明确状态：

```text
identity_status:
  confirmed_entity
  confirmed_topic
  unresolved
```

并区分字段：

```text
confirmed_entity
confirmed_topic
suggested_entities
raw_entity_mention
```

禁止继续复用一个 `head_entity` 字段同时表达：

```text
用户提到的字符串
模型推测的实体
soft match 候选
用户已确认实体
```

这是当前很多 Scope 漂移的根因之一。

---

## 10. Tool Authorization Fail-safe

### 10.1 `target_entity` 前置资格

所有实体型工具在执行前统一验证：

```text
arguments.target_entity
必须来自 confirmed_entity / 合法 grant
```

否则返回：

```text
identity_not_confirmed
```

而不是把 raw target 继续交给 ExplorationGrant 再反复失败。

### 10.2 `exploration_not_authorized` 后禁止同目标重复调用

同一 turn 内：

```text
(target_entity, tool, authorization_reason)
```

第一次拒绝后进入 blocked target set。

Main 后续如果再次提交相同 target：

```text
不执行工具
→ 返回 deterministic ToolObservation:
  target_already_rejected
```

并提示 Controller：

```text
不要再次尝试该 target；若身份未确认，改用 clarify；若身份已确认但探索范围不允许，保留当前合法 Scope。
```

### 10.3 禁止 Drop-Target Recovery

删除所有以下语义：

```text
target 被拒
→ target_entity = null
→ 同 query broad retrieve
```

除非满足一个完全不同的显式条件：

```text
当前任务本来就是 non-entity broad QA
且 Main 明确选择无实体范围检索
```

不能由授权失败自动推导出来。

---

## 11. Agent Recovery 新状态机

### 11.1 unresolved identity

```text
identity_status = unresolved
↓
Main 请求实体型工具
↓
Harness 阻止
↓
建议 clarify
↓
不得 retrieve/link target
```

### 11.2 confirmed entity + exploration denied

```text
identity_status = confirmed_entity
↓
某 related target 未获 grant
↓
阻止该 related target
↓
保留 confirmed entity scope
↓
Main 可基于现有合法 scope 继续或 finalize
```

不得把主实体 Scope 一起删除。

### 11.3 no retrieval progress

```text
合法检索执行
↓
无新增 Evidence
↓
Main 判断：
  finalize partial/no-knowledge
  或一次真正不同的定向检索
```

不得：

```text
无新增
→ 同 query 重试
→ drop target
→ broad retry
```

---

## 12. Retrieval Fail-safe

本 PRD 仅处理“是否有合法结果”的状态，不处理 Chunk 内容清洗。

Retriever 输出增加：

```text
retrieval_status =
  MATCHED
  NO_VALID_EVIDENCE
```

当结果没有达到有效匹配条件时：

```text
docs = []
retrieval_status = NO_VALID_EVIDENCE
```

后续 Runtime：

```text
NO_VALID_EVIDENCE
≠
需要扩大范围
```

而是交给 Main：

```text
是否澄清
是否返回 no-knowledge / partial
是否基于已确认实体做另一种定向 query
```

具体 relevance/admissibility 算法可在第三点 Evidence Quality PRD 中进一步完善。

---

## 13. 目标流程

### 13.1 未明确实体

```text
User Question
      ↓
Main Controller
      ↓
Need clarification?
      ↓ YES
clarify tool
      ↓
System Candidate Discovery
+
Main Suggested Candidates
      ↓
Clarification Card
      ↓
User Selection
      ↓
Canonical Resolution
      ├─ canonical entity
      │      ↓
      │  CONFIRMED_ENTITY
      │      ↓
      │  IdentityScope / Grant
      │      ↓
      │  Retrieval / Graph
      │
      ├─ confirmed text topic
      │      ↓
      │  CONFIRMED_TOPIC
      │      ↓
      │  textual retrieval only
      │
      └─ other/unresolved
             ↓
          Ask More Context
```

### 13.2 明确实体

```text
Question explicitly names canonical entity
↓
Main Controller: no clarification
↓
canonical resolution
↓
CONFIRMED_ENTITY
↓
IdentityScope
↓
Agent / Retrieval
```

### 13.3 `pipelien` 目标行为

```text
User: pipelien
↓
Main: 主体不够明确，调用 clarify
↓
Clarify Tool:
  system seeds: PipelineBuilder / PipelineWebGL / ...
  main suggestions: 可补充合理项
↓
Card
↓
User selects PipelineWebGL
↓
confirmed_entity = PipelineWebGL
↓
retrieve_kb(target_entity=PipelineWebGL)
```

禁止再出现：

```text
link_entities(target_entity=pipelien)
retrieve_kb(target_entity=pipelien)
exploration_not_authorized × N
target=null broad retry
```

---

## 14. Trace 与可观测性

新增事件：

```text
controller_clarification_decided
clarification_candidate_discovery_started
clarification_candidates_discovered
clarification_candidates_merged
clarification_card_published
clarification_selection_received
clarification_selection_resolved
identity_binding_updated
tool_target_rejected
retrieval_no_valid_evidence
```

Trace 示例：

```json
{
  "clarification": {
    "decision_source": "main_controller",
    "needed": true,
    "reason": "subject_not_clear",
    "candidate_counts": {
      "system": 3,
      "model_suggested": 1,
      "final": 4
    }
  },
  "identity": {
    "raw_mention": "pipelien",
    "status": "confirmed_entity",
    "confirmed_entity": "PipelineWebGL",
    "binding_source": "user_clarification_selection"
  }
}
```

每个 Candidate 至少记录：

```text
candidate_id
label
source
canonical_name
binding_status
```

不能只存最终 label，否则无法复盘是代码 seed 还是 Main 添加。

---

## 15. 前端卡片要求

卡片展示必须允许混合来源候选，但不向用户暴露内部复杂 metadata。

前端数据：

```json
{
  "ask_question": "你指的是下面哪个产品或模块？",
  "options": [
    {"id": "cand_01", "label": "PipelineBuilder"},
    {"id": "cand_02", "label": "PipelineWebGL"},
    {"id": "model_01", "label": "Pipeline 发布服务"},
    {"id": "other", "label": "以上都不是"}
  ]
}
```

前端回传：

```text
option_id
```

优先于回传纯 label。

原因：

> 同名 label 不能承载 canonical / model_suggested / other 的身份差异。

---

## 16. 现状改进范围（As-Is → To-Be）

### 16.1 `query_clarification.py`

当前问题：

```text
先收 seed
len(seeds) < min_options
→ needs_clarification=false
→ Main 没机会判断
```

改造：

- QueryClarificationService 不再拥有“是否澄清”的最终裁决权。
- 保留并增强 Candidate Discovery。
- 输出 Candidate DTO。
- 删除 seed 数量不足即代表“无需澄清”的产品语义。
- `min_options` 可以作为 UI/候选质量参数，但不能作为是否需要澄清的决策条件。

### 16.2 `dialogue_understanding.py`

当前前置 `run_clarify=True` 能拦截部分问题，但职责与 Main Controller 重叠。

目标：

- 保留 Stage-1 上下文化、指代消解、Semantic Task 准备。
- 不再由 Helper/ClarificationService 最终决定澄清动作。
- 将 Main Controller 需要的 ambiguity/identity hints 放入 Context，但不替 Main 做最终 Action。

### 16.3 `agent_orchestration/runtime.py`

改造：

- `clarify` 成为第一轮正常 Action。
- 增加 identity state validation。
- unresolved target 在工具执行前阻止。
- 删除授权失败后的 drop-target broad recovery。
- 同一 rejected target 不重复执行。
- Clarification callback 后根据 option binding status 路由 Entity / Topic。

### 16.4 `rag.py::handle_clarify`

当前问题：

```text
Main custom option
→ filter.entity_name = label
→ source = llm_agent
```

这错误地把 Main 建议文本伪装成合法实体。

目标：

- Clarify Handler 合并 system seeds + Main suggested options。
- Main option 默认 `binding_status=unresolved`。
- 不直接生成 `filter.entity_name`。
- 用户点击后再 canonical resolve。

### 16.5 `identity_scope.py` / `evidence_scope.py` / `exploration_grant.py`

目标：

- 只接受 `CONFIRMED_ENTITY` 作为实体绑定来源。
- 明确支持 `CONFIRMED_TOPIC` 作为非实体文本上下文，但不给实体 Grant。
- Trace 记录 binding source。

### 16.6 Retrieval 层

目标：

- 支持 `NO_VALID_EVIDENCE`。
- 不要求每个 query 都返回 top_k 个可发布候选。
- 不因 target 被拒绝自动转为无 target broad search。

---

## 17. 实施阶段

### Phase 0：冻结事故基线

**实施**

- 固化 `pipelien` Trace。
- 保存当前 Clarification、Agent steps、Authorization、Recovery、Retrieval 结果。
- 补充 10–20 条实体澄清真实回归样本。

**验收**

能复现：

```text
clarify miss
→ unresolved target
→ authorization reject
→ repeated calls
→ target drop
→ broad retrieval
```

---

### Phase 1：Clarify Tool V2 + Candidate DTO

**实施**

- 将 QueryClarificationService 收敛为 Candidate Provider。
- 建立 Candidate DTO。
- 合并 system seed + model_suggested。
- Main option 不再直接写 `filter.entity_name`。
- 卡片使用 option_id。

**验收**

- Main 可添加系统 seed 外的候选。
- Main 候选 source 明确为 `model_suggested`。
- Main 候选未 canonical resolve 前无实体 Scope。

---

### Phase 2：Main Controller 接管 Clarification Decision

**实施**

- Main 第一轮可直接调用 clarify。
- Prompt 明确“不明确则 clarify，不猜实体、不 broad retrieve”。
- Stage-1 不再通过 seed 数量直接终止澄清判断。

**验收**

- `pipelien` 第一轮 Main Action = clarify。
- `PipelineWebGL 是什么` 不无意义反问。
- `PipelineWebGL 和 PipelineBuilder 有什么区别` 不误反问。

---

### Phase 3：Identity State 与 Callback Binding

**实施**

- 增加 `CONFIRMED_ENTITY / CONFIRMED_TOPIC / UNRESOLVED`。
- User Selection 根据 candidate metadata 进入 canonical resolve。
- model_suggested 未解析项只能变成 Confirmed Topic。

**验收**

- raw mention 不会自动成为 head_entity。
- model-suggested label 不会自动获得 ExplorationGrant。
- canonical selection 能正常进入现有实体定向检索。

---

### Phase 4：Agent Recovery Fail-safe

**实施**

- unresolved target 工具调用前拦截。
- rejected target 去重。
- 删除 `exploration_not_authorized → drop target → broad retrieve`。
- 无进展时优先 clarify/finalize，而不是扩大 Scope。

**验收**

`pipelien` 不得出现：

```text
exploration_not_authorized × N
harness_autonomous_retry(target=null)
```

同一 target 授权拒绝最多实际执行一次。

---

### Phase 5：Retrieval Zero-Result 语义

**实施**

- Retriever / ToolObservation 支持 `NO_VALID_EVIDENCE`。
- Runtime 不把 0 Evidence 自动解释为扩大检索范围。

**验收**

低置信 Query 可以返回：

```text
0 source docs
```

而不是强行补齐 TopK。

---

### Phase 6：E2E 回归与上线

覆盖：

```text
明确单实体
明确双实体比较
宽泛族名
疑似拼写
完全未知词
Main 补充候选
用户选 model suggested canonical success
用户选 model suggested unresolved topic
用户选其他
授权拒绝
无检索结果
```

---

## 18. 重点回归案例

### Case A：`pipelien`

期望：

```text
Main → clarify
Clarify Tool → system + model candidates
User → select PipelineWebGL
Canonical → confirmed entity
Retrieval → PipelineWebGL scoped
```

不得出现 broad retrieval。

### Case B：明确实体

```text
PipelineWebGL 是什么？
```

期望：

```text
Main → retrieve/link
无 Clarification Card
```

### Case C：明确比较

```text
PipelineWebGL 和 PipelineBuilder 有什么区别？
```

期望：

```text
Main → no clarify
两个显式实体均保留
```

### Case D：Main 补充系统未发现候选

System seeds：

```text
PipelineBuilder
```

Main suggested：

```text
PipelineWebGL
```

期望卡片同时展示。

但 Main suggested 未经 resolver 前：

```text
binding_status=unresolved
```

### Case E：Main 补充不存在的候选

Main suggested：

```text
PipelineMagicServer
```

用户点击后 resolver 无匹配。

期望：

```text
CONFIRMED_TOPIC
```

不得：

```text
target_entity=PipelineMagicServer
ExplorationGrant=allowed
```

### Case F：Scope 拒绝

某 related target 被拒绝。

期望：

```text
记录 rejected target
不重复调用
不 drop 主 scope
不 broad retrieve
```

---

## 19. 测试要求

### Unit：Candidate Discovery

```text
system candidate source
model candidate source
canonical 去重
model candidate 不覆盖 canonical metadata
option_id 稳定
```

### Unit：Identity Binding

```text
canonical option → CONFIRMED_ENTITY
model option resolve success → CONFIRMED_ENTITY
model option resolve fail → CONFIRMED_TOPIC
other → UNRESOLVED
```

### Unit：Runtime

```text
unresolved target blocked
rejected target not repeated
exploration_not_authorized does not drop target
confirmed topic cannot link_entities
confirmed entity can retrieve/link
```

### E2E

必须使用真实 Main LLM 运行 Clarification Decision Gold Set，不能只靠 mock 证明 Main 会调用 clarify。

---

## 20. 指标

| 指标 | 目标 |
| --- | ---: |
| 明确实体误澄清率 | ≤ 3% |
| 应澄清样本漏澄清率 | ≤ 5% |
| unresolved 字符串进入 `target_entity` | 0 |
| Main suggested 未解析候选直接获得实体 Grant | 0 |
| 同一 target 授权拒绝后重复实际调用 | 0 |
| 授权失败后自动 drop target broad retrieval | 0 |
| Clarification callback 可追溯率 | 100% |
| 低置信检索允许 0 结果 | 100% |

重点优先级：

```text
漏澄清导致错误范围
>
多一次合理澄清
```

但不能通过“所有单词查询都强制澄清”的粗暴策略达标。

---

## 21. Definition of Done

- [ ] 是否需要澄清由 Main Controller 做最终 Action 决策。
- [ ] `len(seeds) < min_options` 不再代表“无需澄清”。
- [ ] Clarify Tool 支持 system candidates + Main suggested candidates。
- [ ] Main suggested candidate 不直接写入 `filter.entity_name`。
- [ ] 用户选择后统一经过 canonical resolution。
- [ ] Identity State 明确区分 CONFIRMED_ENTITY / CONFIRMED_TOPIC / UNRESOLVED。
- [ ] 只有 CONFIRMED_ENTITY 可成为 `target_entity` 并获得实体型 Grant。
- [ ] CONFIRMED_TOPIC 可做文本检索，但不能做实体图谱探索。
- [ ] unresolved target 在工具执行前被阻止。
- [ ] 同一 rejected target 不重复实际调用。
- [ ] 授权失败不得自动删除 target 并扩大为 broad retrieval。
- [ ] Retriever 支持 NO_VALID_EVIDENCE / 0 docs。
- [ ] `pipelien` 真实事故样本不再进入无 target 宽检索。
- [ ] Trace 能还原 Clarification Decision → Candidates → Selection → Binding → Retrieval 全链路。

---

## 22. 最终架构

```text
                         User Question
                              │
                              ▼
                       Main Controller
                              │
                 ┌────────────┴────────────┐
                 │                         │
              CLEAR                 NEED_CLARIFICATION
                 │                         │
                 │                         ▼
                 │                    clarify tool
                 │                         │
                 │              ┌──────────┴──────────┐
                 │              │                     │
                 │        System Candidates     Main Suggestions
                 │              │                     │
                 │              └──────────┬──────────┘
                 │                         ▼
                 │                Clarification Card
                 │                         │
                 │                         ▼
                 │                    User Selection
                 │                         │
                 │                         ▼
                 │                Canonical Resolution
                 │                         │
                 │            ┌────────────┼────────────┐
                 │            │            │            │
                 │     CONFIRMED_ENTITY  CONFIRMED_TOPIC UNRESOLVED
                 │            │            │            │
                 │            ▼            ▼            ▼
                 │      IdentityScope   Text Query   Ask More Context
                 │            │
                 └────────────┤
                              ▼
                       Agent / Retrieval
                              │
                 ┌────────────┴────────────┐
                 │                         │
              MATCHED              NO_VALID_EVIDENCE
                 │                         │
                 ▼                         ▼
              Evidence              Main 决定 Partial /
                                    No Knowledge / Clarify
```

这条链路的核心只有三条：

```text
Main 决定是否澄清；
用户确认之前，不把猜测当实体；
身份不确定时扩大候选，不扩大证据范围。
```
