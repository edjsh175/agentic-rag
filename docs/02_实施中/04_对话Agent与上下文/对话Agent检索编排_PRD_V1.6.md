# 对话 Agent 检索编排 · 产品需求说明书（PRD）

| 项目 | 内容 |
|:---|:---|
| **文档版本** | **V1.6 · 动态证据探索终态版** |
| **基线日期** | 2026-08-20 |
| **继承基线** | `对话Agent检索编排_PRD_V1.5.md` |
| **核心架构定位** | **Stage 1 语义任务理解 + IdentityScope 身份锁 + Stage 2 Agent 自主执行 + Tool-level ExplorationGrant 动态证据准入** |
| **核心修正** | **锁住“用户在说谁”，但不锁死“为了回答问题可以合法查谁”** |
| **状态** | **实施中（核心机制已落地，兼容层清理与全量验收未结项）** |

---

> **实施复核（2026-08-20）**：代码已落地 `SemanticTaskContext`、`IdentityScope`、`ExplorationGrant` 及 V1.6 专项测试；`tests/test_agent_orchestration_v16.py` 当前 16/16 通过。旧 `EvidenceScope` / `admissible_entities` 兼容语义仍在多处保留，因此 Phase 6 尚未完成，本 PRD 不得再标记为“待执行”，也尚不能标记为“已完成”。

## 一、为什么需要 V1.6

V1.5 已经完成了两阶段 Agent 架构的职责拆分：

```text
Stage 1：解决“我们在谈什么”
Stage 2：解决“确定任务后怎么取证并回答”
```

但当前实现仍存在一个结构性矛盾：

```text
主体身份锁定
    被实现成
全链路单实体证据白名单
```

结果是：

- 单实体问答的串货风险显著下降；
- 但复合意图、多实体关系、跨模块依赖、协同部署等场景中，Agent 即使正确规划了下一步，也可能被底层静态 Scope 拦住；
- 当前 `is_comparison` 等正则实际上承担了“什么时候允许第二实体进入证据范围”的业务语义判断，这与 V1.5 的 Stage 1 LLM 语义理解原则冲突。

典型错误：

```text
用户：PipelineWebGL 和 PipelineBuilder 如何协同部署？

当前实现：
1. ScopeResolver 先锁定 PipelineWebGL；
2. “协同”不命中 comparison regex；
3. PipelineBuilder 不进入 admissible_entities；
4. Agent 后续 retrieve_kb("PipelineBuilder ...") 被 Pre-TopK 过滤；
5. link_entities 在 locked Scope 下仍只映射 PipelineWebGL root；
6. Agent 无法获得 PipelineBuilder 侧合法证据。
```

因此，本轮不是撤销 EvidenceScope，也不是放开底层过滤，而是**重新划定 Scope 的职责边界**。

---

# 二、V1.6 第一性原则

## P1. 身份锁定 ≠ 证据探索锁死

用户明确说的是 `PipelineWebGL`，意味着：

```text
禁止：
PipelineWebGL 被 alias / fuzzy match / reranker / graph linker
重新解释成 PipelineBuilder。
```

但不意味着：

```text
禁止：
为了回答“PipelineWebGL 与 PipelineBuilder 的协同关系”
去检索 PipelineBuilder 的合法证据。
```

---

## P2. Agent 可以自主规划，但不能自主授予证据权限

Agent 可以决定：

- 下一步查哪个实体；
- 用什么 query；
- 调什么工具；
- 是否需要继续探索。

但 Agent **不能仅凭自身猜测**把任意实体加入合法证据范围。

任何新增探索实体必须有可审计授权来源。

---

## P3. Pre-TopK 必须保留

非法证据必须在进入 rerank / EvidencePool 之前被排除。

严禁退回：

```text
先全库召回
→ 再在 rerank 后过滤错误实体
```

正确方式：

```text
每个工具调用先形成 ExplorationGrant
→ Vector/BM25 根据 Grant 做 Pre-TopK
→ 只有合法候选进入 rerank
```

---

## P4. Stage 1 负责语义任务理解，不再由正则猜复合意图

`is_comparison`、`和/与`、`协同`、`依赖`、`关系` 等词不应该在 Scope 层决定业务语义。

Stage 1 应输出结构化任务上下文，Scope 层只消费结果。

---

## P5. Evidence Guard 校验“证据支持谁、支持什么”，而不是只问“是不是主实体”

最终回答中的事实必须绑定到对应证据组：

```text
A 的证据只能支持 A 的属性；
B 的证据只能支持 B 的属性；
A-B 的关系事实需要关系证据。
```

---

# 三、V1.6 目标架构

```text
                         用户 Query + History
                                  │
                                  ▼
                  ┌────────────────────────────┐
                  │ Stage 1 Semantic Gate      │
                  │ LLM + Graph Candidate      │
                  └──────────────┬─────────────┘
                                 │
                ┌────────────────┴────────────────┐
                │                                 │
                ▼                                 ▼
          需要澄清                         任务语义明确
                │                                 │
                ▼                                 ▼
        Clarify Card                  SemanticTaskContext
                                      ├─ primary_entity
                                      ├─ mentioned_entities
                                      ├─ task_type
                                      ├─ resolved_question
                                      └─ confidence
                                                │
                                                ▼
                                      IdentityScope
                                      只负责防身份重绑定
                                                │
                                                ▼
                                  Stage 2 Agent ReAct Loop
                                                │
                         ┌──────────────────────┼──────────────────────┐
                         ▼                      ▼                      ▼
                    retrieve_kb            link_entities         reuse_evidence
                         │                      │
                         └──────────┬───────────┘
                                    ▼
                           ExplorationGrant
                         当前 Step 的探索授权
                                    │
                                    ▼
                         Vector / BM25 Pre-TopK
                          继续执行结构硬过滤
                                    │
                                    ▼
                              Rerank / Merge
                                    │
                                    ▼
                              EvidencePool
                          按目标实体 / 关系分组
                                    │
                                    ▼
                         Evidence / Claim Guard
                                    │
                                    ▼
                                  Answer
```

---

# 四、核心数据模型

## 4.1 SemanticTaskContext

Stage 1 的统一语义出口。

```python
@dataclass(frozen=True)
class SemanticTaskContext:
    resolved_question: str
    primary_entity: str | None
    mentioned_entities: tuple[str, ...]
    task_type: str
    confidence: float
```

建议 `task_type` 首期只保留粗粒度类别：

```text
single_entity
multi_entity_relation
composite_task
unbound
```

注意：

- `task_type` 只用于表达任务结构；
- 不允许在下游写大量 `if task_type == ...` 的业务规则；
- 多实体是否合法，仍由实体来源和 ExplorationGrant 决定。

---

## 4.2 IdentityScope

替代当前 EvidenceScope 中“主体身份”相关职责。

```python
@dataclass(frozen=True)
class IdentityScope:
    scope_id: str
    primary_entity: str | None
    binding_strength: BindingStrength
    forbidden_rebindings: frozenset[str]
    scope_reason: str
```

唯一职责：

> 防止一个已经明确的主体被重新解释成另一个歧义实体。

### IdentityScope 明确不负责

- 不维护全局 `admissible_entities` 白名单；
- 不决定本轮所有工具只能查谁；
- 不承担业务关系类型识别；
- 不把“当前主实体”解释成“唯一允许出现的实体”。

---

## 4.3 ExplorationGrant

每一次需要访问知识库或图谱时，由系统生成一次临时授权。

```python
@dataclass(frozen=True)
class ExplorationGrant:
    grant_id: str
    identity_scope_id: str
    target_entities: tuple[str, ...]
    source_type: str
    source_ref: str
    allowed_relations: frozenset[str]
    max_hops: int
    materialized_chunk_ids: frozenset[str]
```

### 合法 Grant 来源

首期只允许以下来源：

1. `user_explicit_mention`
   - 用户当前问题显式提到实体；
2. `stage1_resolved_entity`
   - Stage 1 已明确确认的主体；
3. `clarification_confirmed`
   - 用户在 Clarify Card 中确认；
4. `graph_relation`
   - 已审核图谱关系从当前合法实体扩展得到；
5. `trusted_entity_chunk_link`
   - 已审核 `entity_chunk_links` 直接物化证据；
6. `previous_confirmed_context`
   - 连续追问时，由上一轮 CONFIRMED / EXPLICIT 主体继承。

### 明确禁止的 Grant 来源

```text
llm_guess
query_similarity_only
filename_match_only
section_title_match_only
reranker_high_score
```

LLM 可以提出探索目标，但系统必须验证目标是否具有合法授权来源。

---

# 五、Stage 1 改造要求

## 5.1 Stage 1 必须先于 IdentityScope 物化

当前错误顺序：

```text
ConversationContext.from_request
→ ScopeResolver.resolve
→ DialogueUnderstanding
→ AgentLoop
```

目标顺序：

```text
DialogueUnderstanding
→ SemanticTaskContext
→ IdentityScopeResolver
→ ConversationContext
→ AgentLoop
```

不允许再出现：

> Scope 在语义理解之前先猜主体，随后语义理解只能被迫适配 Scope。

---

## 5.2 去除 Scope 层业务语义正则

目标删除：

- `ScopeResolver._extract_comparison_from_query()`；
- Scope 内部 `is_comparison` 正则；
- “只有 comparison 才允许第二实体”的特殊逻辑。

原则：

```text
Scope 不判断“比较 / 协同 / 依赖 / 联合 / 关系”。
Scope 只接受 Stage 1 已解析出的实体任务结构。
```

---

# 六、Stage 2 工具调用模型

## 6.1 retrieve_kb

目标 Tool Schema：

```json
{
  "query": "PipelineBuilder 部署方式",
  "target_entity": "PipelineBuilder",
  "mode": "hybrid",
  "intent": "deployment"
}
```

执行流程：

```text
Agent 提出 target_entity
        │
        ▼
ExplorationGrantResolver.authorize(...)
        │
   ┌────┴────┐
   │         │
合法        非法
   │         │
   ▼         ▼
Grant      Observation:
   │       exploration_not_authorized
   ▼
Pre-TopK filter
   │
   ▼
Vector/BM25
```

### 单实体问题

用户：

```text
PipelineWebGL 怎么配置？
```

Stage 1：

```text
primary = PipelineWebGL
mentioned = [PipelineWebGL]
```

Agent 如果直接请求：

```text
retrieve_kb(target_entity="PipelineBuilder")
```

若无合法图谱关系授权：

```text
拒绝授权
```

---

### 多实体问题

用户：

```text
PipelineWebGL 和 PipelineBuilder 如何协同部署？
```

Stage 1：

```text
primary = PipelineWebGL
mentioned = [PipelineWebGL, PipelineBuilder]
task_type = multi_entity_relation
```

因此系统可为两个实体分别签发：

```text
Grant A → PipelineWebGL
Grant B → PipelineBuilder
```

Agent 可独立检索两侧证据。

---

## 6.2 link_entities

当前错误行为：

```text
if Identity locked:
    永远只 link root entity
```

目标行为：

```text
IdentityScope 防止 primary_entity 被 rebind；
但 link_entities 可以针对已授权 exploration target 解析其它实体。
```

例如：

```text
primary = PipelineWebGL

link_entities(
    query="PipelineBuilder",
    target_entity="PipelineBuilder"
)
```

当 PipelineBuilder 来自 `user_explicit_mention` 时：

```text
允许精确 canonical link PipelineBuilder
但不改变 primary_entity = PipelineWebGL
```

---

## 6.3 图谱动态扩展

如果 Agent 想探索一个用户没有明确提到的新实体：

```text
ServiceA
```

必须先有合法图谱路径：

```text
PipelineWebGL --depends_on--> ServiceA
```

然后生成：

```text
ExplorationGrant(
    target_entities=[ServiceA],
    source_type="graph_relation",
    source_ref="relation:<id>"
)
```

再允许：

```text
retrieve_kb(target_entity="ServiceA")
```

图谱扩展必须继续受：

- RelationPolicy；
- max_hops；
- max entities；
- approved relation；
- provenance；

约束。

---

# 七、Pre-TopK 的最终职责

Pre-TopK 不删除，只改变过滤输入。

## 当前

```text
全局 EvidenceScope.admissible_entities
→ 整轮所有 retrieve 共用
```

## 目标

```text
IdentityScope
+
当前 Tool Step 的 ExplorationGrant
→ 本次 retrieve 的结构 filter
```

示例：

```text
Step 1:
Grant = PipelineWebGL
→ document_entity IN [PipelineWebGL]

Step 2:
Grant = PipelineBuilder
→ document_entity IN [PipelineBuilder]

Step 3:
Grant = ServiceA
→ document_entity IN [ServiceA]
```

非法兄弟实体仍然永远不应该混入该 Step 的 candidate pool。

---

# 八、EvidencePool 改造

EvidencePool 不再只是一堆无差别 chunk。

目标结构：

```python
@dataclass
class EvidenceGroup:
    group_id: str
    target_entity: str | None
    relation_key: str | None
    grant_id: str
    chunk_ids: list[str]
    provenance: list[dict]
```

示例：

```text
Group A
  target_entity = PipelineWebGL
  chunks = [...]

Group B
  target_entity = PipelineBuilder
  chunks = [...]

Group R
  relation_key = PipelineWebGL -> PipelineBuilder
  chunks / graph relation = [...]
```

要求：

每个进入 EvidencePool 的 chunk 必须可以回答：

1. 它为什么被检索；
2. 它针对哪个实体或关系；
3. 哪个 ExplorationGrant 允许它进入；
4. 它的 provenance 是什么。

---

# 九、Evidence / Claim Guard

Guard 分两层。

## 9.1 Structural Admission Guard

负责：

```text
chunk 是否属于当前 ExplorationGrant？
provenance 是否可信？
grant_id 是否匹配？
```

这是物理准入层。

---

## 9.2 Claim Alignment Guard

负责最终事实对齐：

```text
说 A 的属性 → 必须存在 target_entity=A 的证据；
说 B 的属性 → 必须存在 target_entity=B 的证据；
说 A 与 B 的关系 → 必须有关系证据或明确描述该关系的 chunk。
```

首期不要求实现完整自然语言事实蕴含模型，但必须先建立**实体级证据分组约束**，禁止跨组偷证据。

---

# 十、跨轮对话

跨轮只继承身份，不继承无限探索权限。

例如：

```text
Round 1:
用户确认 PipelineWebGL
→ IdentityScope = CONFIRMED PipelineWebGL

Round 2:
用户：它依赖哪些服务？
→ 继续继承 PipelineWebGL 身份
→ Graph 可基于 PipelineWebGL 合法扩展关系实体

Round 3:
用户：那 PipelineBuilder 呢？
→ Stage 1 识别显式新实体
→ 新 Grant 可探索 PipelineBuilder
→ 是否切换 primary_entity 由 Stage 1 任务语义决定
```

禁止：

```text
上一轮曾经探索过 B
→ 下一轮自动永久把 B 留在全局 admissible_entities
```

---

# 十一、Cache 与 Trace

## 11.1 Cache fingerprint

缓存边界至少包含：

```text
IdentityScope fingerprint
ExplorationGrant target_entities
Grant source_type/source_ref
materialized_chunk_ids
kb_name
doc_category
method
query
```

不同 Grant 的相同 query 不能共用错误缓存。

---

## 11.2 Trace

目标 Trace：

```text
semantic_task_context
  ↓
identity_scope
  ↓
agent_step
  ↓
exploration_request
  ↓
grant_authorization
  ↓
retriever_requests
  ↓
pre_topk_scope
  ↓
scoped_recall
  ↓
rerank
  ↓
evidence_group
  ↓
claim_guard
  ↓
cited_evidence
```

每次 Grant 至少记录：

```text
grant_id
target_entities
source_type
source_ref
authorized / rejected
rejection_reason
```

---

# 十二、禁止事项

本轮明确禁止以下“看起来能修”的方案：

## 12.1 禁止继续扩 comparison regex

禁止通过增加：

```text
协同|依赖|关系|联合|配合|联动|调用|交互|一起|...
```

来扩大第二实体准入。

这是补丁式演化。

---

## 12.2 禁止直接撤掉 Pre-TopK

不能为了 Agent 灵活性恢复全库自由召回。

---

## 12.3 禁止 Agent 自己修改全局 Scope

Tool 参数只能提出探索目标，不能直接写：

```text
scope.add_entity(...)
```

授权必须经过 `ExplorationGrantResolver`。

---

## 12.4 禁止关系探索等同主体切换

```text
查到了 PipelineBuilder
≠
当前主体从 PipelineWebGL 变成 PipelineBuilder
```

Identity 与 Exploration 必须严格分离。

---

# 十三、建议代码职责边界

目标新增 / 重构职责：

```text
services/
├─ dialogue_understanding.py
│   └─ 输出 SemanticTaskContext
│
├─ identity_scope.py
│   └─ IdentityScope + IdentityScopeResolver
│
├─ exploration_grant.py
│   └─ ExplorationGrant + ExplorationGrantResolver
│
├─ relation_policy.py
│   └─ 继续作为图谱关系准入唯一事实源
│
├─ retrieval_strategy.py
│   └─ 接受当前 ExplorationGrant 做 Pre-TopK
│
├─ graph_retrieval.py
│   └─ Identity 不重绑定 + Grant 驱动合法探索
│
└─ agent_orchestration/
    ├─ models.py
    │   └─ ConversationContext / EvidenceGroup
    ├─ runtime.py
    │   └─ 每个工具调用请求 Grant
    └─ evidence_gate.py
        └─ Structural Admission + Claim Alignment
```

迁移完成后：

- `evidence_scope.py` 中旧的单体职责应拆除；
- 如果文件只剩兼容适配层，应在迁移完成后删除，而不是长期维护双 Scope 模型。

---

# 十四、实施阶段

## Phase 1：先改数据模型，不改检索行为

完成：

- `SemanticTaskContext`；
- `IdentityScope`；
- `ExplorationGrant`；
- Trace 新字段；
- 对现有 EvidenceScope 提供临时适配。

验收：旧测试全部通过。

---

## Phase 2：Stage 1 前置并去业务正则

完成：

- DialogueUnderstanding 先执行；
- SemanticTaskContext 再生成 IdentityScope；
- 删除 Scope 的 comparison 正则和第二实体特殊准入。

验收：

```text
A 和 B 的区别
A 和 B 如何协同
A 依赖 B 怎么部署
先介绍 A，再比较 B 和 C
```

都不再依赖关键词规则决定实体集合。

---

## Phase 3：Tool-level Grant 接管 Pre-TopK

完成：

- `retrieve_kb(target_entity=...)`；
- Grant 授权；
- Vector/BM25 过滤输入改为 Grant；
- query cache 绑定 grant fingerprint。

验收：单实体防串货保持不退化，多实体可分步检索。

---

## Phase 4：Graph 探索解锁但不重绑定

完成：

- locked IdentityScope 下允许对合法 Grant target 做 canonical link；
- 图谱关系可签发新 Grant；
- 非授权实体仍拒绝。

---

## Phase 5：EvidencePool 分组 + Claim Guard

完成：

- 每组绑定 target_entity / relation；
- Guard 禁止 A 证据支持 B；
- relation claim 需要关系证据。

---

## Phase 6：删除兼容残留

删除：

- `is_comparison` Scope 规则；
- 旧 `admissible_entities` 全会话白名单语义；
- locked scope 下只允许 `link_scope_roots()` 的旧逻辑；
- 已无调用的 RetrievalScope / EvidenceScope 兼容层；
- 重复的业务实体判断。

目标是单一来源，而不是新旧两套长期并存。

---

# 十五、必须覆盖的验收场景

| # | 场景 | 期望 |
|:---|:---|:---|
| 1 | `PipelineWebGL 怎么配置？` | 只授权 PipelineWebGL；PipelineBuilder 不得进入候选 |
| 2 | `PipelineWebGL 和 PipelineBuilder 有什么区别？` | 两实体分别可检索，不依赖 comparison regex |
| 3 | `PipelineWebGL 和 PipelineBuilder 如何协同部署？` | 两实体分别可检索；关系证据独立归组 |
| 4 | `PipelineWebGL 与后台服务的通信机制` | “与”不触发任何硬编码模式；由 Stage 1 决定实体结构 |
| 5 | `PipelineWebGL 依赖的服务如何配置？` | Graph 关系合法扩展 Service，签发 Grant 后检索 |
| 6 | Agent 无依据突然查询 sibling | Grant 拒绝，返回 `exploration_not_authorized` |
| 7 | Agent 查询合法关系实体 | Grant 通过，Pre-TopK 仅检索该 target |
| 8 | A 证据 + B claim | Claim Guard 拒绝 |
| 9 | A/B 均有证据但缺关系证据，回答声称 A 依赖 B | Guard 不得把两侧独立证据拼成关系事实 |
| 10 | 上一轮明确 A，本轮“它呢？” | IdentityScope 继承 A |
| 11 | 上一轮 A，本轮显式“那 B 呢？” | Stage 1 可切换/扩展，不被旧 Identity 卡死 |
| 12 | 相同 query，不同 Grant | Cache 不得串用 |
| 13 | Vector / BM25 | 都必须在 Top-K 前执行 Grant filter |
| 14 | Graph link | Identity locked 时仍可探索授权 target，但 primary 不被改写 |
| 15 | Trace | 能完整解释每个 chunk 为什么具有进入 EvidencePool 的资格 |

---

# 十六、核心质量指标

除继承 V1.5 指标外，新增：

| 指标 | 目标 |
|:---|:---|
| 单实体 sibling 串货率 | **0%** |
| 用户显式多实体探索成功率 | **100%** |
| 合法图谱关系扩展成功率 | **≥95%** |
| 无授权实体进入 Pre-TopK 候选率 | **0%** |
| EvidencePool 无 provenance chunk 比例 | **0%** |
| 跨实体错证率（A 证据支持 B） | **0%** |
| Scope 业务语义正则数量 | **0** |
| Grant 可解释率 | **100%** |

---

# 十七、最终架构判定标准

V1.6 完成后，以下四句话必须同时成立：

```text
1. 用户明确的主体永远不会被模糊重绑定。

2. Agent 可以为了完成复合任务，自主规划并探索多个合法实体。

3. Agent 无权凭空扩大证据范围；每次探索都必须有可审计授权来源。

4. 非法证据在 rerank 前即被排除，合法多实体证据则按实体/关系分别进入 EvidencePool。
```

如果只能做到其中一半，都不算 V1.6 完成。

---

# 十八、一句话终态定义

> **锁住“你在说谁”，不锁死“你为了回答问题可以合法查谁”；身份由 IdentityScope 保证，探索由每个 Agent Step 的 ExplorationGrant 授权，Pre-TopK 继续负责物理证据隔离。**
