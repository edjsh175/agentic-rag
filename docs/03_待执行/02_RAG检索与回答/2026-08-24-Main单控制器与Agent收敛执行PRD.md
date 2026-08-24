# Main 单控制器与 Agent 收敛执行 PRD

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 日期 | 2026-08-24 |
| 状态 | 核心实施完成（DoD 已收口；待全量回归、仓库卫生与 SVN 交付门禁） |
| 所属域 | `02_RAG检索与回答` |
| 改造对象 | Agent Controller、Runtime Harness、Finalization/Evidence Gate、补检策略、Cycle Detection、Budget、Trace |
| 解决问题 | Agent 反复决策、重复检索、Harness/Gate 越权编排、调用次数过多、无信息增量仍继续探索 |
| 核心裁决 | **Main LLM 是唯一“下一步做什么”的决策源；Harness 只负责拒绝非法行为和强制熔断；Gate 只报告 Evidence 状态；任何补检必须由 Main 基于明确 Evidence Gap 和 Information Gain 决定。** |
| 关联文档 | `2026-08-21-Agent两阶段回答与模型路由改善PRD.md`、`2026-08-24-HelperLLM回答Grounding审查执行PRD.md`、`2026-08-24-MainLLM澄清决策与实体范围Fail-safe执行PRD.md` |

---

## 1. 背景

当前 Agent 编排已出现明显“多控制器叠加”现象。Main LLM 名义上是 Agent Controller，但 Runtime Harness 和 Finalization Gate 也在通过代码主动决定后续动作：

```text
Main Controller
→ 给出 action
→ Harness 再判断并可能改写 action
→ Tool
→ Finalization / Evidence Gate
→ Gate 结果触发 Harness Recovery
→ Harness 自己构造补检 / 图谱恢复
→ 再回到 Main
```

真实 Trace 已出现：

```text
Step 1  Main → link_entities        → exploration_not_authorized
Step 2  Main → retrieve_kb          → exploration_not_authorized
Step 3  Main → link_entities        → exploration_not_authorized
Step 4  Main → finalize             → finalization_rejected
Step 5  Harness → retrieve_kb       → autonomous retry
Step 6  Main/Harness → retrieve_kb  → no new evidence
→ retrieve_no_new_evidence
```

同一请求中 `agent_controller` 多次调用，检索阶段耗时显著拉长，用户可见表现是：

```text
反复“正在检索”
反复“缺少关键信息”
反复工具调用
反复 Finalization
最终仍没有得到更好的证据
```

问题不是“Main 太自主”，而是：

> **Main、Harness、Gate 三个组件都在参与“下一步做什么”的决策。**

---

## 2. 当前设计（As-Is）

### 2.1 Main 是 Controller，但不是唯一 Controller

当前 Main 会输出：

```text
clarify
retrieve_kb
link_entities
reuse_evidence
finalize
```

但 Runtime 中存在：

```text
_apply_clarify_harness()
_apply_recovery_harness()
_targeted_retrieval_after_finalization_rejection()
_graph_recovery_decision()
harness_autonomous_retry
harness_targeted_finalization_recovery
harness_graph_recovery
duplicate_retrieve_finalize
```

这些 Python 逻辑不仅“阻止非法行为”，还会主动：

```text
Main finalize
→ Harness 改成 retrieve_kb

Main 当前证据不足
→ Harness 自己构造 targeted retrieval

文本检索没有新证据
→ Harness 自己决定 graph recovery
```

因此 Harness 实际承担了第二 Controller 的职责。

### 2.2 Finalization Gate 不只是观察，还间接触发 Recovery

当前 Finalization 被拒绝后：

```text
finalization_rejected
→ 记录 missing_fact / reason
→ continue tool loop
→ Harness 根据 rejection 强制构造下一次 retrieval
```

因此 Gate 虽然本身是代码规则，但通过 Runtime 形成了：

```text
Gate verdict
→ Recovery Action
```

即：

> Gate 不只是“报告证据状态”，而是在间接参与编排。

### 2.3 Cycle Detection 只识别连续完全相同调用

当前 `AgentBudget.is_cycle()` 只比较：

```text
上一条 tool + arguments
==
当前 tool + arguments
```

因此以下都可能逃过检测：

```text
retrieve("pipelien")
→ retrieve("pipelien 信息")

link_entities("X")
→ retrieve("X")
→ link_entities("X")

retrieve(mode=hybrid)
→ retrieve(mode=bm25)
```

虽然语义上可能没有任何信息增量，但 fingerprint 不同，因此不被视为 cycle。

### 2.4 No-New-Evidence 检测存在首轮空集漏洞

当前逻辑近似：

```text
before_chunk_ids = current evidence
execute retrieve
if before_chunk_ids is not empty
and after - before is empty:
    retrieve_no_new_evidence
```

因此：

```text
before = {}
after  = {}
```

首轮检索完全没有新增证据时，未必立即形成 `NO_PROGRESS`。

### 2.5 Budget 配置存在默认值漂移

Dataclass：

```text
max_retrieve_attempts = 2
```

但配置加载默认值：

```text
max_retrieve_attempts = 8
```

真实运行 Trace 也出现：

```json
{
  "max_steps": 8,
  "max_retrieve_attempts": 8,
  "retrieve_attempts": 3
}
```

这说明 Budget 已从“最后保险”退化成“允许反复尝试的额度池”。

---

## 3. 第一性原则裁决

### 3.1 只能有一个行为决策源

最终职责必须收敛为：

```text
Main LLM = Decide
Tool     = Act
Gate     = Observe
Harness  = Guard
```

完整循环：

```text
Main Decide
↓
Tool Act
↓
Observation + Evidence Delta
↓
Main Decide Again
```

禁止：

```text
Main Decide
→ Harness Decide
→ Tool
→ Gate Decide
→ Harness Decide
→ Main Decide
```

### 3.2 Harness 可以拒绝，但不能替 Main 规划下一步

Harness 允许：

```text
权限拒绝
Scope 拒绝
参数协议拒绝
Budget 拒绝
明确重复调用拒绝
NO_PROGRESS 强制终止
死循环熔断
```

Harness 不允许：

```text
自动构造新 query
自动决定 targeted retrieval
自动决定 graph recovery
把 finalize 改写成 retrieve
把失败工具调用替换为另一工具
根据 Gate 缺口自行选择下一步工具
```

原则：

> **Harness 有 veto 权，没有 planning 权。**

### 3.3 Gate 只报告 Evidence 状态

Gate 输出：

```text
coverage
admissibility
missing facts
missing relations
evidence count
new evidence delta
```

但不得输出或隐式执行：

```text
next_tool = retrieve_kb
next_tool = link_entities
retry = true
recovery_query = ...
```

Main 根据 Gate Observation 自己选择：

```text
继续 retrieve
link_entities
clarify
finalize partial
finalize full
停止
```

### 3.4 第二次检索必须有“明确缺口”，不能只是 Query 换皮

任何补检必须同时满足：

```text
1. 已有明确 Evidence Gap
2. 新 Query/Tool 与上一轮探索目标不同
3. 能说明 expected information gain
```

禁止：

```text
第一次：StampWebRTC UDP
第二次：StampWebRTC UDP 外网
第三次：StampWebRTC UDP 端口配置
```

如果只是同一语义换措辞，不算新探索。

### 3.5 Budget 是保险，不是策略

Budget 只回答：

> “最多允许多少次？”

不能回答：

> “既然还有次数，那就继续搜。”

是否继续必须由 Main 根据：

```text
Evidence Gap
Observation
Information Gain
```

决定。

---

## 4. 目标设计（To-Be）

### 4.1 单控制器 Agent Loop

```text
┌────────────────────┐
│     Main LLM       │
│ Agent Controller   │
└─────────┬──────────┘
          │ decision
          ▼
┌────────────────────┐
│ Harness Guard      │
│ allow / reject     │
└─────────┬──────────┘
          │ allowed
          ▼
┌────────────────────┐
│ Tool Executor      │
└─────────┬──────────┘
          │ observation
          ▼
┌────────────────────┐
│ Evidence Delta     │
│ + Gate Observation │
└─────────┬──────────┘
          │
          └────────────→ Main LLM
```

Harness 不产生替代 Action。

### 4.2 Action 协议

Main 每次输出：

```json
{
  "action": "tool_call | finalize | clarify",
  "tool": "retrieve_kb | link_entities | reuse_evidence | null",
  "arguments": {},
  "reason": "为什么当前应该执行这一步",
  "gap": "当前缺失的具体信息",
  "expected_gain": "本次调用预计新增什么信息"
}
```

其中：

- 初次检索允许 `gap = null`。
- 第二次及以后检索必须有 `gap`。
- 第二次及以后检索必须有 `expected_gain`。

### 4.3 Observation 协议

Tool Observation 应统一包含：

```json
{
  "ok": true,
  "status": "PROGRESS | NO_PROGRESS | DENIED | ERROR",
  "summary": "...",
  "evidence_delta": {
    "new_chunks": 2,
    "new_entities": 0,
    "new_relations": 1,
    "evidence_version_before": 3,
    "evidence_version_after": 4
  }
}
```

### 4.4 Information Gain

`PROGRESS` 至少满足一个：

```text
new_chunks > 0
new_entities > 0
new_relations > 0
scope / identity 获得合法确认
已有 Evidence 的 coverage 明显提高
```

否则：

```text
status = NO_PROGRESS
```

以下必须算 `NO_PROGRESS`：

```text
0 docs → 0 docs
返回的 chunk 全部已存在
关系结果与已有结果重复
同一实体再次解析但无新增信息
只是 query wording 改变，EvidencePool 没变化
```

### 4.5 NO_PROGRESS 的处理

NO_PROGRESS 后：

```text
Observation 返回 Main
```

Main 可以：

```text
finalize partial
finalize no-knowledge
clarify
选择一个真正不同的工具/缺口
```

但 Harness 不替 Main自动补检。

如果连续两次探索都是 `NO_PROGRESS`：

```text
Harness 强制终止进一步探索
```

此处 Harness 只做：

```text
reject future exploratory calls
```

不得生成替代检索。

---

## 5. Finalization / Gate 新职责

### 5.1 Gate 输出数据，不输出行为

建议统一：

```json
{
  "admissibility": "VALID | INVALID",
  "coverage": "SUFFICIENT | PARTIAL | NONE",
  "missing_facts": [],
  "missing_relations": [],
  "evidence_count": 4,
  "evidence_version": 5
}
```

### 5.2 Finalize 不再被 Harness 改写成补检

当前：

```text
Main finalize
→ Gate reject
→ Harness targeted retrieval
```

改为：

```text
Main finalize
→ Gate Observation
→ Main 看到 PARTIAL / NONE
→ Main 自己决定：
   - 补检
   - partial answer
   - clarify
   - no-knowledge
```

### 5.3 Gate 不处理回答 Grounding

本 PRD 的 Gate 只处理“回答生成前 Evidence 状态”。

Candidate 生成后的 Grounding 审核仍按照独立 PRD：

```text
Main Candidate
→ Helper LLM Grounding Reviewer
```

本 PRD 不重新引入任何回答语义硬编码校验。

---

## 6. Harness 新职责

### 6.1 保留

```text
tool registry validation
permission / scope validation
target authorization
argument schema validation
max step hard limit
max retrieval hard limit
identical call rejection
continuous NO_PROGRESS fuse
clarification callback freeze
```

### 6.2 删除 planning 行为

生产路径不再允许 Harness 主动：

```text
harness_autonomous_retry
harness_targeted_finalization_recovery
harness_graph_recovery
_targeted_retrieval_after_finalization_rejection()
_graph_recovery_decision()
```

如果历史兼容暂时保留函数名，必须：

```text
不可进入生产 action 决策路径
```

并在本 PRD Phase 3 删除。

### 6.3 Harness 拒绝结果必须回到 Main

例如：

```json
{
  "status": "DENIED",
  "reason": "exploration_not_authorized"
}
```

之后：

```text
Main Decide Again
```

而不是：

```text
Harness 构造另一个 action
```

---

## 7. 补检契约

### 7.1 初检

初检可使用：

```text
Question
Confirmed Identity / Topic
Intent
```

不强制 gap。

### 7.2 二次补检

二次补检必须明确：

```text
previous evidence summary
missing gap
chosen tool
new query
expected gain
```

示例：

```json
{
  "action": "tool_call",
  "tool": "retrieve_kb",
  "arguments": {
    "query": "StampWebRTC UDP 外网部署端口列表",
    "target_entity": "StampWebRTC"
  },
  "gap": "完整 UDP 外网部署端口清单",
  "expected_gain": "获取除当前访问示例外的明确端口配置"
}
```

### 7.3 不允许无 Gap 的重复检索

如果：

```text
retrieve_attempts >= 1
```

且 Main 再次调用 retrieve，但没有：

```text
gap
expected_gain
```

Harness 返回：

```text
DENIED: missing_retrieval_gap
```

不替 Main补全。

### 7.4 Gap 已经尝试但无增量

维护：

```text
attempted_gaps
```

如果同一 Gap 已经：

```text
尝试过
且 NO_PROGRESS
```

再次针对该 Gap 调用检索：

```text
DENIED: exhausted_gap
```

---

## 8. Cycle Detection 重构

### 8.1 保留 exact duplicate

当前：

```text
same tool + same args
```

仍然立即拒绝。

### 8.2 增加 Gap Cycle

识别：

```text
same normalized gap
+ same target scope
+ previous NO_PROGRESS
```

即便 Query 字符串不同，也视为重复探索。

### 8.3 增加 Progress Cycle

连续调用如果：

```text
Evidence version 不变
new chunks = 0
new entities = 0
new relations = 0
```

达到阈值后：

```text
exploration_fuse = OPEN
```

后续探索型工具统一拒绝。

允许：

```text
finalize
clarify
```

---

## 9. Budget 设计

### 9.1 默认值统一

修复：

```text
AgentOrchestrationConfig.max_retrieve_attempts = 2
Config._load default = 8
```

统一默认：

```text
max_retrieve_attempts = 2
```

配置文件如显式设置其他值，则遵循配置。

### 9.2 max_steps

保留：

```text
max_steps = 8
```

但作为硬保险，不应正常耗尽。

目标正常请求：

```text
1~3 Controller steps
```

复杂多实体问题：

```text
不超过 4~5 steps
```

### 9.3 Budget 不能触发 Action

禁止：

```text
还有 retrieve budget
→ autonomous retry
```

Budget 只可：

```text
can / deny
```

---

## 10. Main Controller Prompt 调整

Main 必须知道：

```text
你是唯一负责选择下一步行为的 Agent Controller。
系统不会替你自动补检或自动切换工具。

每次工具调用后，你会收到结构化 Observation 与 Evidence Delta。

当已有证据足够时，应 finalize。
当只能回答部分时，可以 finalize partial，不必为了完整而反复检索。
当需要补检时，必须明确指出缺少的具体事实（gap）以及预计新增信息（expected_gain）。
如果一次探索返回 NO_PROGRESS，不要只通过改写同义 query 重复尝试。
当同一 gap 已无新增信息时，应选择 finalize partial / clarify / no-knowledge，而不是继续搜索。
```

Main 不应被 Prompt 鼓励：

```text
“尽最大努力多试几个工具”
“证据不足就继续检索直到预算耗尽”
```

---

## 11. 当前代码改造范围

### 11.1 `rag_knowledge/services/agent_orchestration/runtime.py`

当前问题：

```text
_apply_recovery_harness() 带 planning
_targeted_retrieval_after_finalization_rejection() 直接生成 Action
_graph_recovery_decision() 直接生成 Action
finalization_rejected → continue + recovery
```

目标：

```text
Runtime = loop executor + guard + observation builder
≠ recovery planner
```

改造：

1. Harness 只 allow/deny，不返回替代工具 Action。
2. Finalization rejection 作为 Observation 进入下一次 Main Controller。
3. 删除 autonomous retry。
4. 删除自动 graph recovery。
5. 建立 `EvidenceDelta`。
6. 建立 `AttemptedGapRegistry`。
7. 建立连续 `NO_PROGRESS` fuse。
8. 首轮 `0 → 0` 也必须算 `NO_PROGRESS`。

### 11.2 `rag_knowledge/services/agent_orchestration/models.py`

扩展：

```text
AgentDecision.gap
AgentDecision.expected_gain
ToolObservation.status
EvidenceDelta
AttemptedGap
```

建议：

```python
class ToolProgressStatus:
    PROGRESS
    NO_PROGRESS
    DENIED
    ERROR
```

### 11.3 `rag_knowledge/services/agent_orchestration/evidence_gate.py`

收缩职责：

```text
Evidence state evaluator
```

不得返回 recovery action。

输出：

```text
admissibility
coverage
missing_facts
missing_relations
```

### 11.4 `rag_knowledge/services/rag.py`

改造：

1. Tool handler 返回统一 Evidence Delta。
2. 不再依赖 Harness 自动恢复路径。
3. Controller Prompt 输入完整 Observation。
4. Finalization rejected 结果回传 Main。
5. SSE 只展示真实 Main decision / tool / observation 生命周期。

### 11.5 `rag_knowledge/config.py`

统一：

```text
max_retrieve_attempts default = 2
```

不得再出现 Dataclass=2、load default=8 的漂移。

### 11.6 tests

删除以旧行为为目标的测试：

```text
harness automatically retries
harness automatically graph-recovers
finalization reject automatically triggers retrieve
```

新增单控制器测试。

---

## 12. Trace 与可观测性

每一步记录：

```json
{
  "step": 2,
  "controller": {
    "role": "llm",
    "action": "retrieve_kb",
    "gap": "完整 UDP 外网部署端口列表",
    "expected_gain": "获得明确端口配置"
  },
  "guard": {
    "allowed": true,
    "reason": null
  },
  "tool": {
    "name": "retrieve_kb",
    "ok": true
  },
  "evidence_delta": {
    "new_chunks": 0,
    "new_entities": 0,
    "new_relations": 0,
    "evidence_version_before": 2,
    "evidence_version_after": 2
  },
  "progress": "NO_PROGRESS"
}
```

### 12.1 Trace 必须能回答

```text
谁决定了这次工具调用？
为什么要调用？
缺口是什么？
预期增量是什么？
实际新增了什么？
为什么停止？
Harness 有没有越权生成 Action？
```

### 12.2 禁止模糊记录

不能只写：

```text
harness_autonomous_retry
```

而没有：

```text
谁触发
缺口
增量
```

新链路中该事件应消失。

---

## 13. 前端状态

不展示 Chain-of-Thought。

仅展示结构化执行状态：

```text
正在检索与问题直接相关的资料…
当前资料仍缺少：完整 UDP 端口清单，正在进行一次定向补检…
本次补检没有发现新的证据，将基于已有资料回答。
```

禁止同一请求反复出现无实质区别的：

```text
正在进一步深入查询...
正在进一步深入查询...
正在进一步深入查询...
```

---

## 14. 典型场景

### Case A：一次检索足够

```text
Main → retrieve
→ PROGRESS
→ Gate: SUFFICIENT
→ Main → finalize
```

期望：

```text
1 次 retrieve
2 次 Controller decision
```

### Case B：一次检索部分足够

```text
Main → retrieve
→ PROGRESS
→ Gate: PARTIAL
→ Main 判断继续检索价值低
→ finalize partial
```

不得：

```text
Gate PARTIAL → Harness 自动补检
```

### Case C：明确缺口，补检有价值

```text
Main → retrieve
→ PARTIAL
→ Main：gap=完整端口清单
→ retrieve #2
→ PROGRESS
→ Main → finalize
```

最多两次 retrieve。

### Case D：补检无新增

```text
retrieve #1 → PROGRESS
retrieve #2(gap=X) → NO_PROGRESS
→ Main finalize partial
```

不得第三次把 Query 换个说法继续搜索同一 Gap。

### Case E：首轮无结果

```text
before={}
retrieve
→ after={}
→ NO_PROGRESS
```

Main 根据问题类型：

```text
clarify / no-knowledge / different legal tool
```

不得因为 `before` 为空而跳过 NO_PROGRESS 判断。

### Case F：Graph Tool 无新增

```text
link_entities
→ new_entities=0
→ new_relations=0
→ NO_PROGRESS
```

不能由 Harness 自动切成 `retrieve_kb`。

### Case G：Finalization Gate 返回 PARTIAL

```text
Main finalize
→ Gate Observation: PARTIAL, missing=X
→ Main Decide Again
```

Gate/Harness 均不能直接产生 retrieve Action。

---

## 15. 实施阶段

### Phase 0：冻结现状与事故样本

实施：

- 固化 2026-08-21 / 2026-08-24 典型 Trace。
- 统计每条请求：Controller calls、tool calls、retrieve attempts、finalization attempts、Harness recovery 次数。
- 保存 `pipelien` 事故链。

验收：

- 能清楚展示旧链路中 Main / Harness / Gate 各自做了哪些决策。

### Phase 1：引入 EvidenceDelta / Progress

实施：

- 增加 `EvidenceDelta`。
- ToolObservation 增加 `PROGRESS / NO_PROGRESS / DENIED / ERROR`。
- 首轮 `0 → 0` 返回 NO_PROGRESS。
- 图谱、文本检索统一增量表示。

验收：

- 每次工具调用都能回答“新增了什么”。

### Phase 2：补检 Gap 契约

实施：

- AgentDecision 增加 `gap` / `expected_gain`。
- 第二次及以后 retrieval 强制要求 gap。
- 建立 AttemptedGapRegistry。
- 已 NO_PROGRESS 的同一 Gap 再次调用直接拒绝。

验收：

- Query 换皮不能绕过重复探索保护。

### Phase 3：Harness 去 Controller 化

实施：

删除生产路径中的：

```text
harness_autonomous_retry
harness_targeted_finalization_recovery
harness_graph_recovery
_targeted_retrieval_after_finalization_rejection()
_graph_recovery_decision()
```

Harness 只返回：

```text
ALLOW / DENY / FUSE
```

验收：

- 全部 Action 都可以追溯到 Main Controller。
- Harness 不再创建替代 Tool Action。

### Phase 4：Gate Observation 化

实施：

- Finalization Gate 只返回证据状态。
- `finalization_rejected` 不直接触发 recovery。
- Gate Observation 输入下一轮 Main。

验收：

- Gate 不包含 next_action/recovery strategy。

### Phase 5：Budget 收敛

实施：

- 修复默认 `max_retrieve_attempts=2`。
- 保留 `max_steps=8` 作为硬保险。
- 增加 NO_PROGRESS 连续熔断。

验收：

- 正常请求不依赖耗尽 Budget 才停止。

### Phase 6：Prompt / Trace / SSE

实施：

- Main Prompt 强化“唯一 Controller”。
- Main 必须阅读 Observation + EvidenceDelta。
- Trace 增加 gap/expected_gain/progress。
- 前端展示有意义的补检原因。

验收：

- 用户能看到“为什么补检”，而不是重复泛化状态。

### Phase 7：E2E 回归

覆盖：

```text
单次事实问答
部分证据问答
多实体关系
无结果检索
首轮 0 docs
二次 NO_PROGRESS
Graph 无新增
finalization partial
权限拒绝
pipelien 历史事故
```

上线条件：

- 无 Harness planning action。
- 无 Gate-triggered automatic retrieval。
- 平均 Controller / Tool 调用次数显著下降。
- PARTIAL 可正常结束，不以“补到完整”为强制目标。

---

## 16. 测试要求

### Unit：Evidence Delta

```text
0 → 0 = NO_PROGRESS
0 → N = PROGRESS
N → same N = NO_PROGRESS
N → N+M = PROGRESS
relation 0 → 0 = NO_PROGRESS
```

### Unit：Gap Registry

```text
首次 gap X → allow
X + PROGRESS → 可由 Main重新判断
X + NO_PROGRESS → 再次 X deny
query wording changed but gap same → deny
```

### Unit：Harness

```text
invalid tool → deny
unauthorized target → deny
over budget → deny
same call → deny
continuous no-progress → fuse
```

同时断言：

```text
Harness never returns replacement retrieve/link action
```

### Unit：Finalization

```text
PARTIAL → observation only
NONE → observation only
SUFFICIENT → observation only
```

不得自动执行补检。

### E2E：Single Controller

断言每一个执行 ToolCall 都满足：

```text
source = Main Controller decision
```

历史 Harness Action source 必须为 0。

---

## 17. 指标

| 指标 | 目标 |
| --- | ---: |
| Harness-generated tool action | 0 |
| Gate-triggered automatic retrieval | 0 |
| 正常事实问答 retrieve attempts P50 | 1 |
| 普通问题 Controller calls P50 | ≤ 3 |
| 同一 Gap 在 NO_PROGRESS 后重复执行 | 0 |
| 首轮空检索识别 NO_PROGRESS | 100% |
| `step_budget_exhausted` 正常流量占比 | < 1% |
| `retrieve_budget_exhausted` 正常流量占比 | < 1% |
| 无信息增量工具调用占比 | 持续下降，目标 < 10% |
| second retrieval 带明确 gap | 100% |

---

## 18. 非目标

本 PRD 不负责：

```text
1. Candidate Answer Grounding 语义审核
   → 由 HelperLLM Grounding PRD 负责

2. 是否需要澄清以及澄清候选权限
   → 由 MainLLM 澄清决策与实体范围 PRD 负责

3. Excel 空表 / 低信息 Chunk / Loader 质量
   → 属于后续 Evidence Quality / Ingestion 治理

4. 模型选型本身
   → 默认仍 Main=qwen3.5:9b，Helper=qwen3.5:4b
```

---

## 19. 迁移原则

不保留长期双轨：

```text
Main Controller
vs
Harness Recovery Planner
```

开发期间可以通过测试与 Git 提交做回滚，但最终生产架构只能回答：

> 谁决定下一步做什么？

唯一答案：

```text
Main LLM Agent Controller
```

代码可以阻止错误行为，但不能替 Main 规划新行为。

---

## 20. Definition of Done

- [x] Main LLM 是唯一行为决策源。
- [x] Harness 只负责 allow / deny / fuse，不生成替代 Action。
- [x] Finalization / Evidence Gate 只返回 Observation，不触发自动 Recovery。
- [x] `_targeted_retrieval_after_finalization_rejection()` 不再参与生产编排。
- [x] `_graph_recovery_decision()` 不再参与生产编排。
- [x] `harness_autonomous_retry` 不再存在于新链路。
- [x] 每次 ToolCall 都产生 EvidenceDelta。
- [x] 首轮 `0 → 0` 正确判定 NO_PROGRESS。
- [x] 第二次及以后 Retrieval 必须携带 gap + expected_gain。
- [x] 同一 Gap NO_PROGRESS 后不得 Query 换皮重试。
- [x] 连续 NO_PROGRESS 达阈值后 Harness 只熔断，不自动选新工具。
- [x] `max_retrieve_attempts` 默认值统一为 2。
- [x] Budget 仅作为上限，不触发 retry。
- [x] PARTIAL Evidence 可以由 Main 主动结束并进入部分回答。
- [x] Trace 可以明确区分 Controller Decision / Guard / Tool / Observation / Evidence Delta。
- [x] 正常请求不再依赖 `step_budget_exhausted` 或 `retrieve_budget_exhausted` 才停止。
- [x] `pipelien` 历史事故不再出现重复 link/retrieve/recovery 链。

### 20.1 2026-08-24 实施与验证记录

本轮最终收敛点：Controller Prompt 现在直接接收与 Gate 同源的 `current_evidence_state`（`coverage / admissibility / missing_facts / missing_relations / evidence_count / evidence_version`）。这只增加 Observation，不替 Main 选择动作；当 Retrieval 已不可继续且 Coverage 为 PARTIAL 时，由 Main 自己一次性选择 `finalize(answer_mode=partial)`。

验证结果：

- 核心回归：`tests/test_main_single_controller.py`、`tests/test_agent_orchestration.py`、`tests/test_agent_execution_transparency.py`、`tests/test_agent_two_stage_routing_v11.py`，**115 passed**。
- 澄清/Fail-safe 回归：`tests/test_eval_main_clarification.py`、`tests/test_main_clarify_failsafe.py`，**13 passed**。
- 真实 Ollama + 真实知识库：`StampServer 的主要用途是什么？` 收敛为 **3 次 Controller 调用：retrieve → retrieve → finalize(partial)**；`steps_used=3`、`retrieve_attempts=2`、`terminal_action=controller_finalize`，没有通过 `retrieve_budget_exhausted` 或 `step_budget_exhausted` 才终止。
- 真实 `pipelien` 回归：首轮 Main 直接选择 `clarify`，没有进入 retrieve/link/recovery 循环。
- 在线 SSE/Trace E2E 已跑通 Agent 执行、Trace 持久化与事件对账；当前唯一失败断言来自 **Helper Grounding Reviewer** 的协议校验并导致 `reviewer_error`，属于关联的 Helper Grounding PRD，不作为本 Main 单控制器 PRD 的架构回退理由。

交付门禁仍未完成：当前工作树包含大量既有未提交改动，尚未得到一次新的全仓 pytest 全绿结论，也尚未进行 SVN 提交。因此本文状态为“核心实施完成”，而不是“已上线/已交付”。

---

## 21. 最终架构

```text
                     Question
                        │
                        ▼
                ┌──────────────┐
                │   Main LLM   │
                │  Controller  │
                └──────┬───────┘
                       │ Action
                       ▼
                ┌──────────────┐
                │   Harness    │
                │    Guard     │
                └──────┬───────┘
                       │ allow
                       ▼
                ┌──────────────┐
                │     Tool     │
                │   Executor   │
                └──────┬───────┘
                       │
                       ▼
            ┌──────────────────────┐
            │ Observation          │
            │ + Evidence Delta     │
            │ + Gate Evidence State│
            └──────────┬───────────┘
                       │
                       └──────────────→ Main LLM

Main     = 决定下一步
Harness  = 阻止非法行为
Gate     = 报告 Evidence 状态
Tool     = 执行动作
Evidence = 记录是否产生信息增量
```

最终不再存在：

```text
Harness 自动补检
Harness 自动图谱恢复
Gate 拒绝后自动 retrieve
“还有 Budget 所以再试一次”
Query 换皮绕过重复探索
```

这就是第 4 点的目标架构。
