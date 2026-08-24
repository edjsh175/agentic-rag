# Evidence Sufficiency Gate 与定向补检闭环 PRD

| 项目 | 内容 |
|:---|:---|
| **文档版本** | **V1.0** |
| **基线日期** | **2026-08-20** |
| **状态** | **待执行** |
| **所属域** | `02_RAG检索与回答` |
| **上游依赖** | `对话Agent检索编排_PRD_V1.6.md`、现有 `EvidencePool`、`ExplorationGrant`、`IdentityScope` |
| **下游依赖** | `AnswerFinalizer` / Grounding Firewall / Grounded Retry / Deterministic Fallback |
| **核心目标** | **在 Candidate 生成前判断 Evidence 是否足够；不足时明确缺什么并驱动定向补检，而不是让生成模型自行补全。** |

---

## 0. 执行摘要

当前系统已经具备较强的**生成后发布治理**：

```text
LLM Candidate
→ Deterministic Grounding Gate
→ 可选 Semantic Verifier
→ Grounded Retry / Deterministic Fallback
→ Publish
```

该链路解决的是：

> **“模型已经生成了答案，这个答案能不能发布？”**

但当前 Agent 检索侧仍缺少与之对称的生成前门禁：

> **“当前 EvidencePool 到底够不够回答这个问题？”**

若没有这层能力，系统即使最终能拦截幻觉，仍会频繁出现：

```text
证据没搜够
→ 直接让模型生成
→ Candidate 混入外部知识或过度推断
→ Grounding FAIL
→ Retry
→ Fallback
```

这虽然安全，但会带来：

- 回答质量偏保守；
- Fallback 比例偏高；
- Agent 检索没有真正形成“缺什么补什么”的自主研究闭环；
- 用户的问题明明可以通过下一轮定向检索回答，却提前进入生成阶段；
- Trace 无法明确说明“错误来自检索没搜够，还是生成乱说”。

因此，本 PRD 新增 **Gate A：Evidence Sufficiency Gate**，与已有 **Gate B：Grounding Firewall** 形成双门禁：

```text
用户问题
↓
Required Facts / Answer Contract
↓
Agent 自主检索
↓
EvidencePool
↓
Gate A：Evidence Sufficiency
├─ PARTIAL / INSUFFICIENT
│      ↓
│   Missing Facts
│      ↓
│   Targeted Follow-up
│      ↓
│   再检索
│
├─ CONFLICT
│      ↓
│   冲突治理 / 补证
│
└─ SUFFICIENT / 达预算上限
       ↓
Candidate
↓
Gate B：Grounding Firewall
├─ PASS
├─ Grounded Retry
└─ Deterministic Fallback
↓
Publish
```

本轮只新增**生成前证据充分性治理**，不重构已经稳定的发布后 Grounding Firewall。

---

# 一、问题定义

## 1.1 当前系统已经解决什么

截至 2026-08-20，当前系统已具备：

- Stage 1 语义任务理解；
- `IdentityScope` 主体身份锁；
- `ExplorationGrant` 动态证据探索授权；
- Pre-TopK 证据准入；
- Agent Stage 2 自主调用检索/图谱工具；
- `EvidencePool`；
- Candidate 服务端隔离；
- Deterministic Grounding Gate；
- Grounded Retry；
- Deterministic Fallback；
- 可选 Semantic Entailment Verifier；
- Semantic Verifier activation hard gate；
- QA Trace grounding 审计。

当前系统已经能解决：

```text
“模型回答中出现 OpenGL ES / React / Node.js / Spring Boot 等
知识库没有支持的事实时，禁止它们直接发布。”
```

## 1.2 当前系统仍然缺什么

当前 Stage 2 的核心判定仍然过于粗糙：

```text
有 Evidence
≈
可以开始回答
```

但现实中：

```text
有证据 ≠ 证据足够
```

例如：

```text
用户：StampWebGL 和 StampWebRTC 是不是不同技术路线？

第一轮 Evidence：
[1] 端口不同
[2] API 不同

这些证据只能证明：
- 部署细节存在差异
- 接口体系存在差异

但不能直接证明：
- 二者在当前产品体系中属于不同产品线 / 不同技术路线
```

当前系统可能直接进入 Candidate 生成，导致模型自行补齐“不同技术路线”的结论。

正确行为应该是：

```text
Gate A 发现：
required_fact = 产品线关系
status = MISSING

→ Agent 再查：
“StampWebGL StampWebRTC 产品线”
“StampWebGL StampWebRTC 产品关系”

找到明确关系证据后再回答。
```

## 1.3 根因

根因不是 Top-K 太小，也不是 Agent 不够自由，而是系统没有一个明确的数据结构回答：

```text
回答这个问题需要哪些事实？
当前已经覆盖哪些？
还缺哪些？
这些缺口值不值得继续查？
```

因此 Agent 的“是否继续检索”仍容易退化为：

```text
感觉相关
→ 继续

感觉差不多
→ 回答
```

而不是可审计的证据闭环。

---

# 二、第一性原则

## P1. Gate A 与 Gate B 必须完全分责

### Gate A：Evidence Sufficiency

输入：

```text
Question
SemanticTaskContext
AnswerContract / RequiredFacts
EvidencePool
Agent Budget
```

输出：

```text
SUFFICIENT
PARTIAL
INSUFFICIENT
CONFLICT
```

它只负责：

> **要不要继续查。**

### Gate B：Grounding Firewall

输入：

```text
Candidate
EvidencePool
```

它只负责：

> **这份 Candidate 能不能发。**

严禁把两者合并成一个“大模型 Judge”。

---

## P2. 检索是否充分，必须相对于问题的 Required Facts 判断

禁止：

```text
retrieved_docs >= 3
→ sufficient
```

禁止：

```text
rerank score > threshold
→ sufficient
```

正确方式：

```text
Required Fact A → Evidence [1]
Required Fact B → Evidence [3]
Required Fact C → MISSING
```

Sufficiency 是**问题需求覆盖度**，不是文档数量或相似度。

---

## P3. Missing Fact 必须驱动下一轮检索

失败后禁止简单重复原 Query：

```text
原问题
→ 搜不到
→ 再搜原问题
```

必须：

```text
Evidence Gap
→ missing_fact
→ targeted follow-up intent
→ Agent 生成更窄、更可解释的检索计划
```

---

## P4. Agent 可以决定怎么补检，但不能伪造“已覆盖”

Agent 可以提出：

- 下一条 query；
- 调哪个工具；
- 查哪个实体；
- 是否通过图谱扩展；
- 是否复用上一轮证据。

但 `RequiredFact.status = COVERED` 必须有明确 Evidence 引用。

不允许：

```text
LLM 觉得常识上应该成立
→ COVERED
```

---

## P5. Graph 继续负责导航，Chunk 继续负责最终证据

允许：

```text
Graph relationship
→ 发现该查哪个实体 / 文档 / chunk
```

但 Gate A 的最终 `covered_by` 首期必须绑定可追溯 Evidence：

```text
chunk_id / citation_id / source / section_path
```

不能只因为图谱中存在一个 LLM 抽取关系，就直接判定最终事实已覆盖。

---

## P6. 预算是系统约束，不是回答充分性的伪装

如果达到最大检索轮次：

```text
仍有 missing facts
```

不得将状态伪造为 `SUFFICIENT`。

应保留真实状态：

```text
PARTIAL / INSUFFICIENT
+
budget_exhausted = true
```

然后让下游生成一个明确受限的答案，最终仍由 Gate B 验证。

---

## P7. Gate A 必须 Fail-safe，但不能因为 Judge 故障让系统不可用

Gate A 可以包含模型语义判断，但模型故障时：

- 不得自动判 `SUFFICIENT`；
- 可以退化到保守的结构规则；
- 若无法确认，按 `PARTIAL` / `INSUFFICIENT` 处理；
- 必须记录 fallback 原因。

---

# 三、目标架构

```text
                           User Query + History
                                   │
                                   ▼
                     Stage 1 Semantic Understanding
                                   │
                                   ▼
                         SemanticTaskContext
                                   │
                                   ▼
                    Answer Contract Builder
                 ┌─────────────────────────────┐
                 │ Required Facts              │
                 │ Optional Facts              │
                 │ Answer Shape                │
                 │ Directness / Completeness   │
                 └──────────────┬──────────────┘
                                │
                                ▼
                         IdentityScope
                                │
                                ▼
                       Stage 2 Agent Loop
                                │
                ┌───────────────┼────────────────┐
                ▼               ▼                ▼
           retrieve_kb      link_entities    reuse_evidence
                │               │
                └──── ExplorationGrant ────────┘
                                │
                                ▼
                         EvidencePool
                                │
                                ▼
                  Gate A: Evidence Sufficiency
                  ┌────────────────────────────┐
                  │ Coverage                   │
                  │ Missing Facts              │
                  │ Conflicts                  │
                  │ Answerability              │
                  │ Follow-up Targets          │
                  └────────────┬───────────────┘
                               │
               ┌───────────────┼────────────────┐
               │               │                │
               ▼               ▼                ▼
          SUFFICIENT       PARTIAL/INSUF.    CONFLICT
               │               │                │
               │          Targeted Follow-up    │
               │               │                │
               │         Agent 再检索            │
               │               │                │
               └───────────────┴────────────────┘
                               │
                  达到充分或检索预算终止
                               │
                               ▼
                        Candidate Generation
                               │
                               ▼
                    Gate B: Grounding Firewall
                               │
                   PASS / Retry / Fallback
                               │
                               ▼
                            Publish
```

---

# 四、核心数据模型

## 4.1 AnswerContract

`AnswerContract` 是“回答这个问题需要什么”的结构化契约。

```python
@dataclass(frozen=True)
class AnswerContract:
    contract_id: str
    resolved_question: str
    task_type: str
    required_facts: tuple["RequiredFact", ...]
    optional_facts: tuple["RequiredFact", ...] = ()
    answer_shape: str = "direct"
    completeness_required: bool = False
    source: str = "stage1"
```

### 职责

只描述：

```text
“回答需要什么事实”
```

不描述：

```text
“去哪搜”
“允许查谁”
“某事实已经成立”
```

后两者分别属于 Agent / ExplorationGrant 和 Evidence Coverage。

---

## 4.2 RequiredFact

```python
@dataclass(frozen=True)
class RequiredFact:
    fact_id: str
    description: str
    subject_entities: tuple[str, ...] = ()
    relation_hint: str | None = None
    priority: str = "required"   # required | optional
    evidence_requirement: str = "direct"
```

示例：

```json
{
  "fact_id": "f_product_relation",
  "description": "StampWebGL 与 StampWebRTC 在当前产品体系中的关系",
  "subject_entities": ["StampWebGL", "StampWebRTC"],
  "relation_hint": "product_line_relation",
  "priority": "required",
  "evidence_requirement": "direct"
}
```

### RequiredFact 设计约束

首期应保持**粗粒度**。

错误：

```text
把一句问题拆成 15 个细碎 fact
```

正确：

```text
只拆影响最终结论的 1~5 个核心事实槽位
```

---

## 4.3 FactCoverage

```python
@dataclass(frozen=True)
class FactCoverage:
    fact_id: str
    status: str  # COVERED | PARTIAL | MISSING | CONFLICT
    evidence_ids: tuple[int, ...] = ()
    chunk_ids: tuple[str, ...] = ()
    rationale: str = ""
    confidence: float = 0.0
```

### 硬约束

`COVERED` 必须满足：

```text
evidence_ids != empty
```

或有等价的、系统认可的原始证据引用。

不能出现：

```json
{
  "status": "COVERED",
  "evidence_ids": []
}
```

---

## 4.4 EvidenceSufficiencyVerdict

```python
@dataclass(frozen=True)
class EvidenceSufficiencyVerdict:
    status: str
    coverage: tuple[FactCoverage, ...]
    coverage_ratio: float
    missing_fact_ids: tuple[str, ...]
    conflict_fact_ids: tuple[str, ...]
    follow_up_targets: tuple["FollowUpTarget", ...]
    budget_exhausted: bool = False
    reason: str = ""
```

### status 枚举

首期只允许：

```text
SUFFICIENT
PARTIAL
INSUFFICIENT
CONFLICT
```

#### SUFFICIENT

全部 required facts 均为 `COVERED`。

#### PARTIAL

至少一个 required fact 已覆盖，但仍有 required fact 为 `MISSING/PARTIAL`。

#### INSUFFICIENT

核心 required facts 基本未覆盖，当前 EvidencePool 无法回答核心问题。

#### CONFLICT

至少一个关键 RequiredFact 存在相互矛盾的合法 Evidence。

---

## 4.5 FollowUpTarget

注意：Gate A 首期不直接决定最终搜索字符串，它输出**补检目标**。

```python
@dataclass(frozen=True)
class FollowUpTarget:
    fact_id: str
    missing_description: str
    target_entities: tuple[str, ...]
    relation_hint: str | None
    preferred_sources: tuple[str, ...] = ()
    rationale: str = ""
```

由 Agent 根据 FollowUpTarget 自主决定：

- `retrieve_kb(query=...)`
- `link_entities(...)`
- 图谱关系扩展
- 文档范围
- query rewrite

这样保留 Agent 自主性，同时不让 Gate A 与 Agent Planner 职责混杂。

---

# 五、Answer Contract 生成要求

## 5.1 生成位置

建议：

```text
Stage 1 SemanticTaskContext
↓
AnswerContractBuilder
↓
Stage 2 Agent
```

不得放在每次 retrieve 后重复生成。

首轮形成契约，后续只允许有限更新。

---

## 5.2 哪些问题不需要复杂 Required Facts

### 简单事实查询

```text
“StampServer 默认端口是多少？”
```

RequiredFacts：

```text
f1 = 默认端口值
```

### 简单操作问题

```text
“PipelineBuilder 怎么发布？”
```

可以拆为：

```text
f1 = 发布入口 / 前置条件
f2 = 核心操作流程
```

### 多实体关系

```text
“WebGL 和 WebRTC 是不是不同技术路线？”
```

RequiredFacts：

```text
f1 = 当前产品体系关系
f2 = API / SDK 区别（辅助）
f3 = 部署 / 架构区别（辅助）
```

不得为了追求完整而要求知识库必须存在“技术路线”四个字。

Gate A 判断的是是否存在足以支撑用户问题的事实证据，而不是关键词完全相同。

---

## 5.3 Contract 不得被模型自由扩大

例如用户只问：

```text
“默认端口是多少？”
```

不得自动扩成：

```text
- 默认端口
- TLS 端口
- 管理端口
- 防火墙策略
- 部署架构
```

否则 Gate A 会人为制造 Evidence Gap。

---

# 六、Gate A 判定逻辑

## 6.1 首期推荐两层实现

```text
Layer 1：Deterministic Coverage
Layer 2：Optional Semantic Coverage Judge
```

与 Gate B 一样，优先让可确定问题由规则解决。

---

## 6.2 Layer 1：Deterministic Coverage

可直接确认：

- `fact_id` 已绑定明确 evidence；
- Evidence 属于当前合法 EvidencePool；
- citation / chunk provenance 合法；
- EvidenceScope / ExplorationGrant 合法；
- 明确数值、端口、路径、实体、关系已出现；
- 冲突证据可被结构化检测。

如果某 RequiredFact 有直接 EvidenceClaim/关系证据，可直接标记 `COVERED`。

---

## 6.3 Layer 2：Semantic Coverage Judge

只用于回答：

> “这一组 Evidence 是否已经足以回答这个 RequiredFact？”

输入必须限制为：

```text
RequiredFact
+
与其候选相关的 Evidence
```

不要把整个会话和整个知识库塞给 Judge。

输出：

```json
{
  "fact_id": "f1",
  "status": "COVERED",
  "evidence_ids": [2, 3],
  "reason": "产品关系已由证据直接说明"
}
```

或：

```json
{
  "fact_id": "f1",
  "status": "MISSING",
  "evidence_ids": [],
  "reason": "现有证据只说明接口和端口差异，未覆盖产品体系关系"
}
```

### Fail-safe

Judge：

- timeout；
- provider error；
- malformed JSON；
- invalid citation；
- 返回未知 fact_id；

均不得升级成 `COVERED`。

默认降级：

```text
保持上一版 coverage
或
PARTIAL/MISSING
```

---

# 七、Answerability 判定

Gate A 的最终状态不得仅看 coverage ratio。

## 7.1 SUFFICIENT

条件：

```text
所有 priority=required 的 RequiredFact 均 COVERED
且不存在 required CONFLICT
```

## 7.2 PARTIAL

条件：

```text
至少一个 required fact COVERED
但仍有 MISSING/PARTIAL
```

处理：

- 有预算 → 定向补检；
- 无预算 → 进入受限回答，只回答已覆盖部分。

## 7.3 INSUFFICIENT

条件：

核心事实未覆盖。

有预算：继续补检。

无预算：

```text
不允许生成完整肯定答案
```

下游 prompt 必须收到明确限制：

```text
只能回答已覆盖事实；核心问题证据不足需明确说明。
```

## 7.4 CONFLICT

出现多个合法 Evidence 对同一个 RequiredFact 给出冲突事实。

不得：

```text
随机选择 rerank 分更高的一条
```

必须：

- 尝试补充更权威 / 更直接证据；
- 或明确向用户呈现冲突；
- 下游 Grounding 仍必须保证引用分别对应。

---

# 八、Targeted Follow-up 设计

## 8.1 Gate A 不直接生成最终 Query

Gate A 输出：

```text
missing_fact
目标实体
关系提示
优先来源
```

Agent Planner 再决定怎么查。

原因：

- Gate A 是判断器；
- Agent 是执行器；
- ExplorationGrant 是授权器；
- RetrievalStrategy 是检索器。

必须保持单一职责。

---

## 8.2 示例

用户问题：

```text
WebGL 和 WebRTC 是不是不同技术路线？
```

首轮：

```text
Evidence：
[1] 端口差异
[2] SDK API 差异
```

Gate A：

```json
{
  "status": "PARTIAL",
  "missing_fact_ids": ["f_product_relation"],
  "follow_up_targets": [
    {
      "fact_id": "f_product_relation",
      "missing_description": "缺少 StampWebGL 与 StampWebRTC 的产品体系关系证据",
      "target_entities": ["StampWebGL", "StampWebRTC"],
      "relation_hint": "product_line_relation"
    }
  ]
}
```

Agent 可执行：

```text
retrieve_kb("StampWebGL StampWebRTC 产品线")
link_entities("StampWebGL", "StampWebRTC")
retrieve_kb("StampWebGL StampWebRTC 产品关系")
```

而不是机械再次：

```text
retrieve_kb("WebGL 和 WebRTC 是不是不同技术路线")
```

---

# 九、与 V1.6 Agent 编排的接口边界

## 9.1 IdentityScope 不变

Gate A 不得：

- 修改 primary identity；
- 重新消歧用户已经明确的主体；
- 为了补缺口把主实体替换成兄弟实体。

---

## 9.2 ExplorationGrant 不变

Gate A 可以说：

```text
“缺 PipelineBuilder 与 StampServer 的依赖关系”
```

但不能直接给任意实体授予检索权限。

下一轮 Agent 仍必须经：

```text
合法 Grant 来源
→ ExplorationGrant
→ Pre-TopK
```

---

## 9.3 EvidencePool 增强

EvidencePool 建议新增只读覆盖视图：

```python
EvidencePool.coverage_for(answer_contract)
```

或由单独：

```python
EvidenceSufficiencyEvaluator.evaluate(contract, evidence_pool)
```

实现。

推荐后者，避免 EvidencePool 变成业务判断大类。

---

# 十、Agent Loop 控制流

## 10.1 新的 Stage 2 循环

```text
Agent Step
↓
Tool Call
↓
EvidencePool 更新
↓
Gate A Evaluate
│
├─ SUFFICIENT
│      ↓
│   stop_retrieval = true
│
├─ PARTIAL / INSUFFICIENT
│      ↓
│   有预算？
│   ├─ yes → missing facts 注入下一 Agent step
│   └─ no  → stop_retrieval = true + budget_exhausted
│
└─ CONFLICT
       ↓
    有预算？
    ├─ yes → conflict-targeted follow-up
    └─ no  → 带 conflict 状态进入生成
```

---

## 10.2 不要每个 Tool Call 都强制调用 LLM Gate

考虑成本和延迟，首期建议：

Gate A 触发点：

1. 首轮有效检索结束后；
2. EvidencePool 有实质新增后；
3. Agent 主动准备进入 `answer/finalize` 前；
4. 达到预算边界时。

若某个工具调用：

```text
0 new evidence
```

无需重复完整 Semantic Judge。

---

# 十一、预算与终止策略

## 11.1 建议配置

```ini
[evidence_sufficiency]
enabled = true
max_gate_evaluations = 3
max_targeted_followups = 2
semantic_judge_enabled = false
judge_timeout = 20
min_required_coverage = 1.0
```

说明：

`min_required_coverage` 首期仍建议 required facts 100% 才能判 SUFFICIENT。

不得通过降低为 0.6 来伪造“够用了”。

---

## 11.2 终止条件

Stage 2 停止补检的合法条件：

1. `SUFFICIENT`；
2. 达到 `max_targeted_followups`；
3. 达到 Agent Step Budget；
4. 所有合法探索路径已尝试且无新增证据；
5. 用户问题本身属于 direct chat / 无需 KB；
6. 系统发生不可恢复的检索错误。

终止 ≠ SUFFICIENT。

必须分别记录：

```text
stop_reason
answerability
budget_exhausted
```

---

# 十二、Candidate 生成约束

## 12.1 SUFFICIENT

正常生成：

```text
基于完整 Required Facts 回答
```

## 12.2 PARTIAL

Prompt 必须包含：

```text
已覆盖：...
未覆盖：...

只能回答已覆盖事实；
不得把缺失部分补成肯定事实。
```

## 12.3 INSUFFICIENT

不得生成“完整答案”。

可以：

```text
说明当前知识库未覆盖核心问题
+
列出少量已检索到的相关事实
```

## 12.4 CONFLICT

Candidate 必须：

- 明示冲突；
- 分别引用；
- 不静默选边。

最终仍经过 Gate B。

---

# 十三、与 Grounding Firewall 的最终关系

最终系统必须保留：

```text
Gate A PASS
≠
Candidate 自动可信
```

原因：

即使 Evidence 完全足够，生成模型仍可能额外写一句外部知识。

所以完整闭环必须是：

```text
Gate A
回答之前：证据够不够？
↓
Candidate
↓
Gate B
回答之后：有没有乱说？
```

任何实现不得因为新增 Gate A 而绕过 `AnswerFinalizer`。

---

# 十四、Evidence Claim Layer（Phase 2，可后置）

Gate A 首版稳定后，可以引入：

```python
@dataclass(frozen=True)
class EvidenceClaim:
    claim_id: str
    text: str
    evidence_ids: tuple[int, ...]
    chunk_ids: tuple[str, ...]
    status: str   # SUPPORTED | DISPUTED | UNSUPPORTED
    provenance: tuple[dict, ...]
```

目标：

```text
Raw Chunk
↓
Verified EvidenceClaim
↓
Candidate
```

但 Phase 1 不应强制做完整 Claim Graph，避免本轮范围失控。

---

# 十五、QA Trace 与可观测性

每轮 Trace 新增：

```json
{
  "answer_contract": {
    "required_facts": []
  },
  "evidence_sufficiency": {
    "evaluations": [
      {
        "round": 1,
        "status": "PARTIAL",
        "coverage_ratio": 0.67,
        "covered": ["f_api", "f_port"],
        "missing": ["f_product_relation"],
        "conflicts": [],
        "follow_up_targets": ["f_product_relation"]
      },
      {
        "round": 2,
        "status": "SUFFICIENT",
        "coverage_ratio": 1.0,
        "covered": ["f_api", "f_port", "f_product_relation"],
        "missing": []
      }
    ],
    "stop_reason": "sufficient",
    "budget_exhausted": false
  }
}
```

必须能回答：

```text
为什么第一轮没回答？
缺了什么？
第二轮为什么查这个？
新证据解决了哪个缺口？
最终为什么停止？
```

---

# 十六、评测体系

新增评测必须把“检索不够”和“生成乱说”拆开。

## 16.1 Gate A 指标

### Required Fact Coverage

```text
已覆盖 required facts / required facts 总数
```

### Missing Fact Recall

Gold 中真实缺失的关键事实，Gate A 是否识别出来。

### False Sufficiency Rate

最重要指标之一：

```text
证据其实不够
但 Gate A 判 SUFFICIENT
```

### False Insufficiency Rate

```text
证据已经足够
但 Gate A 仍要求继续补检
```

### Targeted Follow-up Success Rate

```text
补检是否找到了对应 missing fact 的有效 Evidence
```

### Follow-up Efficiency

```text
平均补检轮数
无效补检比例
重复 Query 比例
```

### Conflict Detection Recall

关键冲突事实是否被识别。

---

## 16.2 Gate B 指标继续保留

- Grounding False Accept；
- Grounding False Reject；
- Citation Completeness；
- Unsupported Claim Rate；
- Grounded Retry Success Rate；
- Deterministic Fallback Rate。

---

## 16.3 End-to-End 诊断标签

每个失败样本必须至少归类为：

```text
RETRIEVAL_MISS
SUFFICIENCY_FALSE_PASS
SUFFICIENCY_FALSE_REJECT
GENERATION_UNSUPPORTED
GROUNDING_FALSE_ACCEPT
GROUNDING_FALSE_REJECT
BUDGET_EXHAUSTED
CONFLICT_UNRESOLVED
```

这样才能知道系统到底坏在哪一层。

---

# 十七、黄金集要求

建议新增：

```text
tests/fixtures/evidence_sufficiency_gold_v1.json
```

至少覆盖：

1. 单事实直接充分；
2. 多事实全部充分；
3. 有相关 chunk 但核心事实缺失；
4. API 差异存在但产品关系缺失；
5. 产品关系存在但部署差异缺失；
6. 第一轮 PARTIAL，第二轮补检后 SUFFICIENT；
7. 补检无新证据，预算耗尽；
8. 冲突端口；
9. 冲突产品关系；
10. 多实体关系问题；
11. 单实体操作问题；
12. 用户只要求简短回答，不应过度拆 Required Facts；
13. 兄弟实体干扰证据不得用于填充 coverage；
14. Graph 导航找到了关系，但无 Chunk 证据时不得直接 COVERED；
15. Previous Evidence reuse 合法覆盖；
16. Clarification 确认实体后的覆盖；
17. query rewrite 后覆盖；
18. PARTIAL 状态下 Candidate 不得补齐 missing fact；
19. INSUFFICIENT 状态下禁止完整肯定回答；
20. Gate A Judge 失败时 fail-safe。

---

# 十八、验收标准

## AC-1：Required Facts 可审计

每个进入知识库 Agent 问答的非 direct-chat 请求，都能在 Trace 中看到：

```text
required_facts
```

简单问题允许只有 1 个。

---

## AC-2：有证据不等于充分

构造：

```text
问题需要 A+B+C
Evidence 只覆盖 A+B
```

必须：

```text
status != SUFFICIENT
```

---

## AC-3：Missing Fact 驱动补检

第一轮缺 `f3` 时，下一轮 Agent 的有效检索必须能在 Trace 中解释为：

```text
target_fact_id = f3
```

不得只是重复原始 query。

---

## AC-4：补检仍受 ExplorationGrant 约束

Gate A 发现缺口后，不得绕过：

```text
IdentityScope
ExplorationGrant
Pre-TopK
```

---

## AC-5：预算耗尽不伪造 SUFFICIENT

达到预算仍有缺口：

```text
status = PARTIAL / INSUFFICIENT
budget_exhausted = true
```

---

## AC-6：Conflict 不静默选边

同一事实存在互相冲突的合法证据时：

```text
status = CONFLICT
```

不得仅按 rerank score 静默采用一方。

---

## AC-7：Gate A 不可绕过 Gate B

所有非 direct-chat Candidate 最终仍必须经过当前 `AnswerFinalizer`。

---

## AC-8：失败归因可解释

至少能区分：

```text
没搜到
搜到了但 Gate A 判断错
生成乱说
Gate B 判断错
```

---

## AC-9：真实事故回归

`webgl webrtc是不是不同技术路线` 类样本：

第一轮如果只有 API / 端口证据，应先出现 `PARTIAL`；找到产品线证据后再进入完整回答。

`BS架构下有哪些技术路线` 类样本：

不得因为知识库没有 React/Vue/Node.js 等内容而自动把它们加入 Required Facts 或 Candidate。

---

# 十九、实施阶段

## Phase 0：只读诊断

目标：不改变线上回答行为。

实施：

- 新增 `AnswerContract`；
- 新增 RequiredFact 生成；
- 新增 `EvidenceSufficiencyEvaluator`；
- Gate A 仅写 Trace；
- 不控制 Agent 终止。

验收：

- 在真实 Trace 上验证 Contract 是否合理；
- 测 False Sufficiency / False Insufficiency；
- 防止一开始就让 Gate A 误杀生产检索。

---

## Phase 1：Gate A 控制“是否进入回答”

开启：

```text
SUFFICIENT → answer
PARTIAL/INSUFFICIENT → 若有预算则继续 Agent
```

不引入 Semantic Judge，优先 deterministic coverage + 明确 evidence binding。

---

## Phase 2：Targeted Follow-up

将：

```text
missing_fact
```

注入下一 Agent Step。

验收：

- 下一轮 query 不机械重复；
- 能解释 target fact；
- 无权限实体仍被 ExplorationGrant 拒绝。

---

## Phase 3：Conflict 与预算治理

加入：

- Conflict follow-up；
- no-new-evidence 终止；
- budget_exhausted；
- PARTIAL / INSUFFICIENT 降级提示。

---

## Phase 4：Optional Semantic Coverage Judge

只有 deterministic coverage 无法判断的 residual facts 才调用。

要求与现有 Semantic Grounding Verifier 一致：

```text
必须有独立评测集
必须有 activation gate
不能靠修改 enabled=true 直接上线
```

---

## Phase 5：Evidence Claim Layer

待前四阶段稳定后再实施。

不要提前把本轮改造成完整 Claim Graph 项目。

---

# 二十、建议代码归属

以下仅作为 owner 建议，实施时以当前仓库职责为准。

```text
rag_knowledge/services/
├─ answer_contract.py
│   ├─ AnswerContract
│   ├─ RequiredFact
│   └─ AnswerContractBuilder
│
├─ evidence_sufficiency.py
│   ├─ FactCoverage
│   ├─ FollowUpTarget
│   ├─ EvidenceSufficiencyVerdict
│   └─ EvidenceSufficiencyEvaluator
│
├─ agent_orchestration/
│   └─ runtime.py
│       只消费 Gate A verdict 控制下一步
│
├─ evidence_pack.py
│   保持 Gate B deterministic grounding owner
│
└─ answer_finalizer.py
    保持最终 publish owner
```

### 明确禁止

不要把 Gate A 实现进：

```text
answer_finalizer.py
```

不要把 RequiredFacts 塞进：

```text
IdentityScope
```

不要把补检权限塞进：

```text
EvidenceSufficiencyEvaluator
```

不要让：

```text
reranker score
```

成为 `SUFFICIENT` 的最终 owner。

---

# 二十一、配置建议

首期配置：

```ini
[evidence_sufficiency]
enabled = false
trace_only = true
max_gate_evaluations = 3
max_targeted_followups = 2
semantic_judge_enabled = false
judge_timeout = 20
```

Phase 0：

```text
enabled=false
trace_only=true
```

Phase 1 验收通过后：

```text
enabled=true
trace_only=false
```

---

# 二十二、风险与防呆

## 风险 1：Required Facts 过度拆分

后果：

```text
永远觉得证据不够
```

防护：

- 首期限制 1~5 个 required facts；
- 只拆影响核心答案的事实；
- 简单问题禁止复杂 Contract。

---

## 风险 2：Gate A 变成第二个 Planner

后果：职责混乱。

防护：

Gate A 只输出：

```text
缺什么
```

Agent 决定：

```text
怎么查
```

---

## 风险 3：Semantic Judge 自己产生幻觉

防护：

- Judge 不得扩大 Evidence；
- 无 evidence_id 不得 COVERED；
- Fail-safe；
- 后续 activation gate。

---

## 风险 4：无限补检

防护：

- max followups；
- Agent step budget；
- no-new-evidence stop；
- repeated target detection；
- repeated query detection。

---

## 风险 5：为了“覆盖率”扩大实体范围

防护：

任何补检仍必须：

```text
ExplorationGrant
→ Pre-TopK
```

Gate A 不能成为扩大 EvidenceScope 的后门。

---

## 风险 6：Gate A 与 Gate B 输出互相覆盖

防护：

Trace 分字段：

```text
evidence_sufficiency

grounding
```

禁止共用一个 `verdict` 字段。

---

# 二十三、回滚策略

Gate A 必须支持独立关闭：

```ini
[evidence_sufficiency]
enabled = false
```

关闭后恢复：

```text
现有 V1.6 Agent 检索
→ Candidate
→ 当前 Grounding Firewall
```

不得影响：

- IdentityScope；
- ExplorationGrant；
- Pre-TopK；
- AnswerFinalizer；
- Grounding Trace；
- Direct Chat。

因此本轮属于**可旁路、可灰度、可独立回滚**增强。

---

# 二十四、Definition of Done

本 PRD 完成必须同时满足：

- [ ] `AnswerContract` 有唯一 owner；
- [ ] RequiredFact 首期不超过合理粒度；
- [ ] `EvidenceSufficiencyEvaluator` 与 Agent Planner 解耦；
- [ ] Gate A 可输出 `SUFFICIENT/PARTIAL/INSUFFICIENT/CONFLICT`；
- [ ] Coverage 必须绑定合法 Evidence；
- [ ] Missing Fact 能驱动 Targeted Follow-up；
- [ ] Follow-up 不绕过 ExplorationGrant；
- [ ] Budget Exhausted 不被伪装成 SUFFICIENT；
- [ ] PARTIAL/INSUFFICIENT Candidate 有明确生成约束；
- [ ] Candidate 最终仍经过 AnswerFinalizer；
- [ ] QA Trace 可解释每轮 Gate A；
- [ ] 有独立 Evidence Sufficiency Gold；
- [ ] 有 False Sufficiency / False Insufficiency 指标；
- [ ] 两条历史外部知识泄漏事故保持 0 leak；
- [ ] Gate A 可独立关闭回滚；
- [ ] 不引入新的静态实体白名单；
- [ ] 不把 Graph 二级知识直接当最终事实证据；
- [ ] 不把 Semantic Judge 当作首期唯一判定器。

---

# 二十五、最终设计结论

本轮不是继续扩展 Grounding Firewall，而是补齐它前面的另一半闭环。

最终职责必须稳定为：

```text
Stage 1
理解用户到底要问什么
↓

Answer Contract
回答需要哪些核心事实
↓

Stage 2 Agent
自主探索和收集 Evidence
↓

Gate A: Evidence Sufficiency
证据够不够？缺什么？是否需要继续查？
↓

Candidate
只基于已经允许进入生成阶段的 Evidence
↓

Gate B: Grounding Firewall
Candidate 有没有说 Evidence 没有支持的话？
↓

Retry / Deterministic Fallback
↓

Publish
```

一句话定义：

> **Gate A 防止“没搜够就开始答”；Gate B 防止“搜到了还乱答”。**

只有两者同时存在，Agentic RAG 才形成完整的“检索前半环 + 发布后半环”证据治理闭环。
