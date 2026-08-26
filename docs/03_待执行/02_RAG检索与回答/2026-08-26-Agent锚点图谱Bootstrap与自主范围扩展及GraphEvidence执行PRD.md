# Agent 锚点图谱 Bootstrap、自主范围扩展与 Graph Evidence 一等证据执行 PRD

## 0. 文档信息

- **文档类型**：执行 PRD
- **状态**：待实施
- **日期**：2026-08-26
- **适用范围**：Agent 模式知识库问答主链
- **核心目标**：
  > 将当前“图谱主要作为隐藏 Candidate Expansion”的实现，重构为：
  >
  > **Runtime 默认对已锚定实体执行一次一跳图谱 Bootstrap（多实体支持多根并行）→ Main Agent 自主决定是否继续扩大图谱范围（支持从已有 frontier 增加深度的 Depth Expansion，以及从新的已授权合法实体建立局部根的 Root Expansion）→ 图谱关系经过 Query-specific Admission 后成为一等 Evidence → 文本证据与图谱证据统一进入 EvidencePool / Snapshot / Answer / Grounding Reviewer。**

---

# 1. 与现有 PRD 的关系

本 PRD 是：

```text
2026-08-26-Identity-Candidate-Evidence-Grounding
分层与检索召回解耦重构执行PRD
```

的 **Graph 部分修正与扩展**。

> **实施优先级与效力声明**：
> Graph 相关语义以本 PRD（《Agent锚点图谱Bootstrap与自主范围扩展及GraphEvidence执行PRD》）为最终权威；
> 原 Identity/Candidate/Evidence/Grounding PRD 中“Graph only-candidate / Graph relation 永远不能作为 Evidence”条款已废止，其他分层原则继续有效。

原 PRD 的核心原则继续成立：

```text
Identity
≠
Candidate
≠
Admitted Evidence
≠
Claim Support
```

但是原 PRD 中这条 Graph 定义需要纠正：

```text
Graph 只能作为 Candidate Expansion
Graph relation 永远不能作为 Evidence
```

这一条过度收窄。

新的正确原则是：

```text
GraphCandidatePath
≠
GraphRelationEvidence
```

即：

```text
图谱关系用于扩大搜索范围
→ Candidate Provenance

图谱关系本身是 approved 事实
且能够直接支持当前 Query
→ GraphRelationEvidence
→ EvidencePool
```

所以之后不能再简单写：

```text
Graph = Candidate only
```

而应该写成：

```text
Graph
├─ Candidate Expansion
└─ Relation Evidence
```

两种用途，两套准入语义。

---

# 2. 当前已经确认的架构问题

当前代码其实已经存在：

```text
link_entities
GraphRetriever
GraphExpander
relation_policy
EvidencePool.add_relation()
Answer Generator <graph_relations>
Evidence Gate relation checking
```

说明“图谱关系作为证据”的基础设施原本是存在的。

而且历史真实 Agent Trace 已经跑通过：

```text
link_entities
→ approved graph relation
→ EvidencePool(kind=relation)
→ Snapshot
→ Answer
```

但 Candidate Pipeline V2 当前明确存在：

```python
if getattr(grant, "candidate_pipeline_v2", False):
    # V2 graph edges are candidate provenance only, never EvidencePool entries.
    return
```

`handle_link()` 也存在同样语义：

```text
candidate_pipeline_v2=true
→ 不再 evidence.add_relation()
```

因此当前实际链路是：

```text
link_entities
↓
Graph DB 查到 approved relations
↓
Controller Observation 能看到
↓
relation_summaries 能看到
↓
EvidencePool
×
↓
Snapshot
×
↓
Answer Generator
×
```

这就是目前最终回答里几乎看不到图谱关系的直接原因。

更严重的是，Finalization Gate 又要求：

```text
multi_entity_relation
→ 必须有 relation evidence
```

于是形成：

```text
Gate：
“关系题必须有关系证据。”

V2：
“关系不准进入 EvidencePool。”
```

这是明确的 P0 协议冲突。

---

# 3. 第一性原则

最终架构必须满足：

```text
Query Identity
≠
Graph Exploration Roots
≠
GraphWorkingSet
≠
Candidate Space
≠
EvidencePool
≠
Claim Support
```

分别表示：

```text
Query Identity
= 用户到底在问谁（由 Stage-1 锁定，严禁因图谱探索发生漂移）

Graph Exploration Roots
= 图谱探索的合法起点集合（多实体问答可有多根，后续探索可授权扩根）

GraphWorkingSet
= Agent 当前已经探索到的多根局部图谱世界（实体、关系、路径）

Candidate Space
= 哪些 Chunk / Relation 值得进一步检查

EvidencePool
= 哪些材料对本轮 Query 真正具备证据资格

Claim Support
= 最终回答某句话是否真的被 Snapshot 支撑
```

特别禁止重新出现：

```text
Graph neighbor
=
Query Identity
=
Retrieval Scope
=
Evidence Authorization
```

---

# 4. 目标架构

完整 Agent 链路：

```text
User Query
    ↓
Common Stage-1 Understanding
    ↓
Identity Resolution / Clarification
    ↓
identity_status
    │
    ├─ unresolved
    │    ↓
    │  Clarification
    │
    ├─ confirmed_topic
    │    ↓
    │  无实体 Graph Bootstrap
    │
    └─ confirmed_entity / confirmed_entities
         ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Runtime-owned Graph Bootstrap
对所有 confirmed_entities 默认执行 1-hop 查询
形成多根（Multi-root）初始 GraphWorkingSet V1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
         ↓
GraphWorkingSet V1
├─ exploration_roots (PipelineWebRTC, PipelineWebGL...)
├─ discovered entities
├─ approved relations
├─ frontier entities
├─ paths
└─ visited state
         ↓
Relation Admission
├─ PASS
│   ↓
│ GraphRelationEvidence
│   ↓
│ EvidencePool
│
└─ REJECT
    ↓
  仅保留 WorkingSet
         ↓
Main Agent Controller
         │
         ├─ retrieve_kb
         │      ↓
         │ Multi-path Candidate Pipeline
         │      ├─ direct document entity
         │      ├─ entity→chunk
         │      ├─ GraphWorkingSet discovered entities
         │      ├─ graph path provenance
         │      ├─ lexical
         │      ├─ BM25
         │      └─ vector
         │              ↓
         │          rerank
         │              ↓
         │      Entity + Intent Admission
         │              ↓
         │         ChunkEvidence
         │              ↓
         │         EvidencePool
         │
         ├─ expand_graph_scope
         │   (支持 Depth Expansion 沿 frontier 加深，
         │    及 Root Expansion 换已授权合法新起点)
         │      ↓
         │ GraphWorkingSet V2/V3...
         │      ↓
         │ Relation Admission
         │      ↓
         │ new GraphRelationEvidence
         │      ↓
         │ EvidencePool
         │
         ├─ retrieve_kb
         │   (基于扩大/新发现的图谱实体范围继续补检文本事实)
         │
         └─ finalize
                ↓
          Evidence Gate
                ↓
      Immutable Evidence Snapshot
        ├─ ChunkEvidence
        └─ GraphRelationEvidence
                ↓
         Answer Generator
                ↓
       Grounding Reviewer
                ↓
            Publication
```

---

# 5. Runtime 默认一跳 Graph Bootstrap

这是本 PRD 最重要的产品行为之一。

用户已经确认的产品定义是：

> **Agent 对锚定实体默认执行一次一跳图谱查询。**

因此不能仅靠 Main Prompt 写：

```text
“请优先调用 link_entities”
```

因为模型可能：

```text
忘记调用
直接 retrieve
直接 finalize
不同模型行为不一致
```

正确方式是 Runtime 保证。

触发条件：

```text
mode == agent
AND answer_type == knowledge
AND identity_status in {confirmed_entity, confirmed_topic_with_entities}
AND graph_bootstrap_enabled == true
AND graph provider available
```

则：

```text
Identity confirmed
↓
bootstrap_anchor_graph()
↓
GraphWorkingSet
↓
Bootstrap Observation
↓
Main Controller 第一次自由决策
```

注意：

> Bootstrap 属于 Agent 执行过程，但不是 Main Agent 的一次自由 Tool Call。

因此不应消耗 Main Controller 的第一次决策。

---

# 6. Bootstrap 查询范围

首版固定：

```text
anchor / confirmed entities → 1 hop
```

只查询：

```text
review_status = approved
```

并满足：

```text
entity confidence threshold
relation confidence threshold
合法 relation endpoint
合法 graph revision
```

Bootstrap 的目标不是：

```text
直接帮用户回答所有问题
```

而是：

> 给 Main Agent 一开始提供一个局部知识图谱世界。

例如：

```text
PipelineWebRTC
├─ belongs_to → StampTools
├─ related_to → WebRTC
├─ depends_on → StampServer
└─ different_from → PipelineWebGL
```

Main Agent 此时就知道：

```text
当前主体附近到底有哪些真实实体与关系
```

而不需要靠参数记忆猜。

---

# 7. 多锚点与多确认实体 Bootstrap

如果 Stage-1 已经确认：

```text
confirmed_entities = [A, B]
```

例如：

```text
“PipelineWebRTC 和 PipelineWebGL 有什么区别？”
“StampServer 和 StampTools 是什么关系？”
```

则 Bootstrap **分别对所有 confirmed_entities 执行 1-hop 查询**：

```text
A one-hop (Root A: PipelineWebRTC)
+
B one-hop (Root B: PipelineWebGL)
↓
merge
↓
dedup
↓
同一个 Multi-root GraphWorkingSet
```

排序优先级：

```text
A ↔ B 直接关系
>
同时连接两个 confirmed entities 的关系
>
与 SemanticTask 匹配的关系
>
strong candidate expansion relation
>
其他 approved relation
```

---

# 8. GraphWorkingSet：多根局部图工作集

新增 Agent Query 级一等状态对象：

```text
GraphWorkingSet
```

必须支持**多根（Multi-root）**，不是单根 BFS 树。

建议：

```python
@dataclass
class GraphWorkingSet:
    graph_scope_id: str
    question_id: str
    graph_revision: str

    exploration_roots: tuple[str, ...]  # 探索起点集合（含 anchor 及后续授权扩展的 root）
    anchor_entities: tuple[str, ...]    # 原始锚定实体集合

    entities: dict[str, GraphEntityState]
    relations: dict[str, GraphRelationCandidate]
    paths: list[GraphPathCandidate]

    frontier_entity_ids: tuple[str, ...]
    visited_entity_ids: set[str]
    visited_relation_ids: set[str]

    max_depth_reached: int

    expansion_signatures: set[str]
    expansion_calls: int

    bootstrap_status: str
```

它必须是：

```text
Query-scoped
```

不能跨 Query 直接变成永久授权。

---

# 9. Graph Entity 状态

至少保存：

```text
entity_id
canonical_name
entity_type
depth_from_root
origin_root
is_root
is_frontier
first_seen_via_relation_id
```

例如：

```text
Root A: PipelineWebRTC (depth=0, is_root=true)
  └─ WebRTC (depth=1, origin_root=PipelineWebRTC)

Root B: PipelineWebGL (depth=0, is_root=true)
  └─ StampTools (depth=1, origin_root=PipelineWebGL)
```

---

# 10. Graph Relation Candidate

建议：

```python
@dataclass
class GraphRelationCandidate:
    relation_id: str

    source_entity_id: str
    source_name: str
    source_type: str

    relation_type: str

    target_entity_id: str
    target_name: str
    target_type: str

    review_status: str
    confidence: float

    depth_from_root: int
    origin_root: str

    discovery_source: str
    discovery_path: tuple[str, ...]
```

其中：

```text
discovery_source
=
bootstrap
|
depth_expansion
|
root_expansion
```

---

# 11. GraphWorkingSet ≠ EvidencePool

Bootstrap / Expansion 查出来 20 条 relation：

```text
不等于
20 条都进入 EvidencePool
```

正确链路：

```text
GraphRelationCandidate
↓
Relation Admission
├─ PASS → GraphRelationEvidence
└─ REJECT → 仅留 WorkingSet
```

这样可以保证：

```text
Agent 看得到更多图谱上下文
```

但：

```text
最终回答只能使用与当前 Query 真正相关的关系
```

---

# 12. Relation Admission

新增：

```text
GraphRelationAdmissionResult
```

推荐协议：

```json
{
  "verdict": "PASS|REJECT",
  "entity_relevance": "HIGH|MEDIUM|LOW|CONFLICT",
  "intent_relevance": "HIGH|MEDIUM|LOW|NONE",
  "relation_relevance": "DIRECT|CONTEXTUAL|IRRELEVANT",
  "reason": "...",
  "admission_signals": []
}
```

---

# 13. Relation Admission 硬条件

以下条件全部满足才有资格继续语义判断：

```text
review_status == approved

relation_id 非空

source endpoint 存在

target endpoint 存在

relation type 已注册

graph revision 可追踪

relation 属于当前 GraphWorkingSet
```

以下全部禁止成为 Evidence：

```text
pending
rejected
unreviewed
模型凭空生成 relation
没有 relation_id
未知 relation type
```

---

# 14. Relation Admission 语义判断

例如：

```text
Question:
PipelineWebGL 属于什么产品？

Relation:
PipelineWebGL -[belongs_to]-> StampTools
```

则：

```text
DIRECT
PASS
```

但：

```text
Question:
PipelineWebGL 默认端口是多少？

Relation:
PipelineWebGL -[belongs_to]-> StampTools
```

则：

```text
IRRELEVANT to exact_parameter
REJECT as answer evidence
```

但仍可留在 GraphWorkingSet。

---

# 15. Main 不能自己给自己签发 Evidence

Main Agent 可以决定：

```text
我要不要扩图
我要查哪个起点 (start_entities)
我要看哪些 relation type
```

但不能直接说：

```text
“这条 relation 是 Evidence。”
```

Relation Admission 应是 Runtime / deterministic + Helper 辅助判定。

推荐顺序：

```text
Hard validation
↓
deterministic relation/task match
↓
relation_policy
↓
只有模糊部分才交 Helper
```

不要：

```text
每条 relation 都调用 Helper
```

---

# 16. relation_policy.py 重构

当前：

```python
RelationRule(
    identity_equivalent,
    scope_traversal,
    candidate_expansion,
    graph_intents,
    weak_provenance
)
```

建议升级：

```python
@dataclass(frozen=True)
class RelationRule:
    identity_equivalent: bool = False

    candidate_expansion: str = "none"
    graph_intents: frozenset[str] = frozenset()

    answer_evidence: bool = False
    evidence_intents: frozenset[str] = frozenset()

    weak_provenance: bool = False

    path_composable: bool = False
```

明确区分：

```text
candidate_expansion
=
能不能沿它继续找资料

answer_evidence
=
它本身有没有资格成为回答事实
```

这是必须拆开的。

---

# 17. GraphRelationEvidence

现有：

```text
EvidencePool.add_relation()
```

可以复用升级。

不需要为了架构“看起来漂亮”全部推翻。

Relation Evidence 至少保存：

```text
relation_id

source_entity_id
source_entity_name

relation_type

target_entity_id
target_entity_name

review_status
confidence

graph_revision

origin_root
depth_from_root
discovery_source

graph_scope_id
identity_scope_id

admission_verdict
admission_reason

grant_id（如兼容层仍存在）
```

继续：

```text
source_type = graph_relation
file_name = 知识图谱（已审核关系）
```

---

# 18. 引用编号不需要另造 `[G1]`

内部类型可以是：

```text
GraphRelationEvidence
```

但 Frozen Snapshot 继续统一编号：

```text
[1] 文档
[2] 图谱关系
[3] 文档
```

这样能最大限度复用：

```text
Citation
Answer Generator
Reviewer
前端 Sources
```

避免为了 Graph 大改整个引用协议。

---

# 19. 多跳路径

必须明确：

```text
Graph Path
≠
自动派生事实
```

例如：

```text
A -[belongs_to]-> B
B -[depends_on]-> C
```

不能自动推出：

```text
A -[depends_on]-> C
```

因此首版允许：

```text
GraphPathCandidate
```

进入：

```text
GraphWorkingSet
Trace
Controller Observation
```

用途：

```text
发现路径
决定下一跳
指导 retrieve
```

最终 EvidencePool 默认只 materialize 路径中的原始边：

```text
A belongs_to B
B depends_on C
```

回答可以：

> A 属于 B。[1] B 依赖 C。[2]

不能默认：

> 因此 A 依赖 C。[1][2]

首版：

```text
path_composable = false
```

全部默认。

以后若真的需要关系代数，再单独设计。

---

# 20. Agent 主动图谱扩大工具：`expand_graph_scope`

现有：

```text
link_entities
```

职责过多且语义模糊。

Main Agent 统一暴露：

```text
expand_graph_scope
```

**工具定位与核心语义**：

> **扩大当前 GraphWorkingSet 的覆盖范围。**
> 
> 既支持从已有 frontier 继续向外探索深度的 **Depth Expansion**，也支持以新的已授权合法实体作为局部探索根的 **Root Expansion**。

---

# 21. `expand_graph_scope` Schema

推荐：

```json
{
  "start_entities": [
    "PipelineWebGL"
  ],
  "relation_types": [
    "belongs_to",
    "different_from",
    "depends_on"
  ],
  "direction": "both",
  "additional_hops": 1,
  "goal_entities": [
    "StampServer"
  ]
}
```

语义：

```text
start_entities
= 从哪些已授权实体开始扩（支持已发现 frontier 或已授权新 root）

relation_types
= 当前 Agent 重点关心哪些关系（为空表示不过滤）

direction
= out / in / both

additional_hops
= 本次从 start_entities 继续探索几跳（1 或 2）

goal_entities
= 可选，寻找与某目标实体之间的连接路径
```

### 21.1 Root Expansion 不是“从旧 Root 走过去”

必须明确：

> **Root Expansion 是在当前 Query 的 GraphWorkingSet 中直接新增一个已授权局部探索根，不要求该实体已经与原锚定实体存在已知路径，也不要求先从原 Root 一跳一跳走到它。**

例如：

```text
Query Identity = PipelineWebRTC

用户当前问题/上下文已经合法确认 PipelineWebGL 也需要参与比较
```

Main 可以直接调用：

```json
{
  "start_entities": ["PipelineWebGL"],
  "relation_types": [],
  "direction": "both",
  "additional_hops": 1,
  "goal_entities": []
}
```

Runtime 应解释为：

```text
Root Expansion
→ 新建局部 Root: PipelineWebGL (local depth = 0)
→ 查询 PipelineWebGL 自身 1-hop approved 邻域
→ merge 进入同一个 GraphWorkingSet
```

明确禁止把它错误实现成：

```text
PipelineWebRTC
→ 必须先找到某条路径
→ ...
→ PipelineWebGL
→ 才允许查询 PipelineWebGL
```

这会把多根 Agent 图谱错误退化为单根 BFS。

### 21.2 Expansion 类型由 Runtime 解析，不要求 LLM 自报

工具入参不额外要求模型填写 `expansion_type`，避免 LLM 声明与实际状态冲突。

Runtime 根据 `start_entities` 与当前 WorkingSet/授权来源确定：

```text
start_entity 已存在于 GraphWorkingSet
→ resolved_expansion_type = depth_expansion

start_entity 不在 GraphWorkingSet，但通过 Root Authorization
→ resolved_expansion_type = root_expansion
```

Observation / Trace 必须记录：

```text
resolved_expansion_type
start_entities
new_roots
root_authorization_source
new_entities
new_relations
local_depth_before
local_depth_after
```

---

# 22. Start Entity 四类合法来源授权门禁

Main Agent 不能凭参数记忆臆造不存在或未授权的实体：

```text
start_entities = ["某个模型随意记忆的外部系统"]
```

Runtime 在执行扩图前必须进行严格的 **Root / Frontier Authorization 校验**。

每个 `start_entity` 必须满足以下 **4 类合法来源之一**：

1. **Stage-1 确认实体**：属于本轮已识别的 `confirmed_entity` 或 `confirmed_entities`；
2. **Graph 已发现实体**：属于当前 `GraphWorkingSet.entities`（已在 Bootstrap 或历史扩图中观察到）；
3. **文本检索已证实实体**：出现在当前 Query 中已通过 Admission 校验的 Chunk 证据（`admitted evidence`）中，且通过实体消解与合法性校验（**支持“文本检索发现新实体 → 反向查图”双向互促**）；
4. **用户显式提及实体**：在当前对话上下文或追问中用户明确指出，且经实体解析通过。

若任一 `start_entity` 不满足上述任一来源：

```text
DENIED
graph_root_not_authorized
```

### 关键边界铁律：Identity 隔离保护

> **允许直接查 WebGL 的图谱，绝不等于把 Query Identity 偷偷改成 WebGL。**
> 
> `Query Identity` 由 Stage-1 锁定，代表“用户到底在问谁”；
> `Graph Exploration Roots` 代表“Agent 当前探索图谱的世界起点”。
> 
> 探索多根局部图绝不能引起 Query Identity 漂移。

---

# 23. Hop 数由 Agent 决定，但 Runtime 封顶

设计原则：

> Agent 自主判断需要几跳与从何处起步。

建议：

```text
bootstrap_hops = 1

max_hops_per_expansion = 2

max_total_depth = 3

max_expansion_calls = 2
```

其中 `max_total_depth` 必须定义为 **per-root local depth 上限**，不是“相对最初 Query Identity 的全局图距离”。

例如新增：

```text
Root A = PipelineWebRTC
Root B = PipelineWebGL
```

则：

```text
PipelineWebRTC.local_depth = 0
PipelineWebGL.local_depth = 0
```

二者都可以各自执行 1-hop / 2-hop 探索；全局只共享：

```text
max_expansion_calls
max_entities
max_relations
总执行预算
```

不得因为 `PipelineWebGL` 与 `PipelineWebRTC` 当前没有已知路径，就把 WebGL 视为“无限深”或拒绝 Root Expansion。

例如：

```text
Bootstrap:
Root A (depth 0 → 1)
Root B (depth 0 → 1)

Agent expansion #1:
Depth expansion (WebRTC 1 → 2)
或
Root expansion (StampServer 0 → 1)
```

但不能：

```text
Agent 要求 10 hop
Runtime 真给 10 hop
```

---

# 24. Graph Budget 独立于 Retrieval Budget

新增：

```text
GraphBudget
```

至少包含：

```text
bootstrap_calls
expansion_calls

entities_seen
relations_seen

max_expansion_calls
max_entities_total
max_relations_total
max_total_depth

remaining_expansion_calls
```

初始建议：

```text
max_expansion_calls = 2
max_entities_total = 24
max_relations_total = 64
max_total_depth = 3
```

这是初始工程预算，不是永恒配置。

---

# 25. Graph 重复调用熔断

每次 expansion 生成稳定 signature：

```text
start_entities
+
relation_types
+
direction
+
additional_hops
+
goal_entities
+
graph_revision
```

相同 signature：

```text
DENIED
duplicate_graph_expansion
```

如果：

```text
new_entities = 0
new_relations = 0
new_graph_evidence = 0
```

返回：

```text
NO_PROGRESS
```

Main Agent 不准换一种说法再重复。

---

# 26. Bootstrap 失败不应把整个 RAG 打死

Graph Bootstrap 可能返回：

```text
GRAPH_DISABLED
GRAPH_UNAVAILABLE
GRAPH_ENTITY_NOT_FOUND
GRAPH_EMPTY
GRAPH_BOOTSTRAP_OK
```

例如 Graph DB 挂了：

```text
confirmed_entity
↓
GRAPH_UNAVAILABLE
↓
ControllerState:
graph_expansion_allowed=false
↓
Main
↓
retrieve_kb
```

普通文本 RAG 继续跑。

只有纯关系问题且文本也无证据，最后才：

```text
NO_SAFE_ANSWER
```

---

# 27. Candidate Pipeline V2 的关键改造

这是另一个 P0。

当前：

```text
AgentCandidatePipeline
```

自己调用：

```text
_graph_neighbors(target)
→ graph_db.list_relations()
```

同时 Agent Runtime 又有 Graph Tool。

这等于有两个图谱世界：

```text
Hidden Graph State
= Candidate Pipeline 内部

Visible Graph State
= Agent Controller / link_entities
```

这是错误架构。

---

# 28. GraphWorkingSet 必须成为唯一图谱状态源

新链：

```text
Graph DB
↓
Graph Explorer (Bootstrap / expand_graph_scope)
↓
GraphWorkingSet
```

然后 Candidate Pipeline：

```python
pipeline.generate(
    question,
    target_entity=target,
    graph_working_set=graph_working_set,
    ...
)
```

Candidate Pipeline 不再自己：

```text
list_relations()
```

而是：

```text
读取 WorkingSet 里已经探索到的实体与路径
↓
去这些实体对应的 Chunk / Document 中找 Candidate
```

这样才真正可解释：

```text
为什么 PipelineWebRTC 问题
会召回 WebRTC 文档？
```

Trace 可以回答：

```text
anchor=PipelineWebRTC
↓
bootstrap relation r12
↓
discovered entity=WebRTC
↓
candidate source=graph_working_set
↓
chunk c98
↓
admission PASS
```

---

# 29. Graph Candidate 仍不能变成统一硬 Filter

禁止：

```text
GraphWorkingSet:
[PipelineWebRTC, WebRTC, StampServer]

↓

WHERE document_entity IN (...)
```

这是重新创造：

```text
GraphScope = RetrievalScope
```

仍然错误。

必须继续多路 Candidate Generator：

```text
Direct document entity
Entity→Chunk
GraphWorkingSet entity chunks
GraphWorkingSet document entity
Exact lexical
BM25
Vector
```

最后：

```text
merge
dedup
rerank
Admission
```

---

# 30. 与 PipelineWebRTC 事故的适配

旧事故：

```text
Identity = PipelineWebRTC
```

但是：

```text
document_entity=PipelineWebRTC
直属 chunks = 0
```

错误旧链：

```text
Identity
↓
document_entity hard filter
↓
0 result
```

新链：

```text
Identity = PipelineWebRTC
↓
Bootstrap
↓
GraphWorkingSet
↓
发现 WebRTC / StampServer / 其他合法邻居
↓
这些邻居贡献 Candidate
↓
Entity + Intent Admission
↓
合法文本 Evidence
```

所以：

```text
Identity 仍然锁死
```

但：

```text
Candidate 可以跨文档载体、跨实体载体搜索
```

这正好解决旧问题。

---

# 31. 与 PipelineWebGL → PipelineBuilder 污染适配

如果图谱存在：

```text
PipelineWebGL
-[different_from]->
PipelineBuilder
```

该关系可以：

```text
进入 GraphWorkingSet
```

作为：

```text
Structural Guard Signal
```

防止 sibling 污染。

但绝不能：

```text
因为图上有 PipelineBuilder
→ PipelineBuilder 所有文档获得 Evidence 权限
```

所以：

```text
Graph discovery
≠
Evidence authorization
```

继续成立。

---

# 32. ControllerState 增加 Graph State

每轮 Main Controller 至少看到：

```json
{
  "graph_state": {
    "bootstrap_status": "COMPLETE",
    "roots": [
      "PipelineWebRTC",
      "PipelineWebGL"
    ],
    "max_depth_reached": 1,
    "frontier_entities": [
      "WebRTC",
      "StampTools"
    ],
    "entity_count": 6,
    "relation_count": 8,
    "admitted_relation_evidence_count": 1,
    "remaining_expansion_calls": 2,
    "max_total_depth": 3,
    "expansion_allowed": true,
    "last_graph_status": "PROGRESS"
  }
}
```

不需要把整个 Graph DB 塞给 Main。

---

# 33. Main Controller 新规则

Prompt 明确写：

```text
1.
confirmed_entity / confirmed_entities 已经由 Runtime 自动完成一跳 Graph Bootstrap（多实体自动构建多根图），
不要再次为了“认识初始实体”重复调用图谱工具。

2.
如果当前 Evidence Gap 属于：
关系、依赖、组成、所属、上下游、区别、对比、路径，
可调用 expand_graph_scope。

3.
expand_graph_scope 支持两类扩图：
- Depth Expansion：从当前 GraphWorkingSet 已知 frontier 继续加深（如 start_entities=["WebRTC"]）；
- Root Expansion：从用户明确提到的另一合法实体或检索已证实的关联实体开辟新局部根（如对比题中 start_entities=["PipelineWebGL"]）。
严禁凭空编造未经观察与授权的实体作为起点。

4.
expand_graph_scope 是图谱探索工具，绝不得用它篡改 Query Identity。

5.
NO_PROGRESS / GRAPH_BUDGET_EXHAUSTED 后不得同义重试。

6.
端口、参数、命令、配置等精确问题，
文本证据已经充分时不得为了“使用图谱”强行扩图。

7.
GraphRelationEvidence 进入 EvidencePool
不代表自动 FULL，
必须继续依据 Evidence Gate。

8.
扩图发现相关实体但 relation 本身无法回答问题时，
可以针对这些已发现实体调用 retrieve_kb 获取文本事实。
```

---

# 34. Expansion 必须带 Gap

第二次 Graph 探索不允许：

```text
“再查查”
```

Main 必须给：

```text
gap
expected_gain
```

例如：

```json
{
  "tool": "expand_graph_scope",
  "arguments": {
    "start_entities": [
      "WebRTC"
    ],
    "relation_types": [
      "depends_on",
      "requires"
    ],
    "additional_hops": 1
  },
  "gap": "缺少 PipelineWebRTC 与服务端组件之间的依赖关系证据",
  "expected_gain": "找到 WebRTC 分支下一跳的已审核服务依赖关系"
}
```

---

# 35. Graph Observation

统一返回：

```json
{
  "status": "PROGRESS",
  "new_entities": 3,
  "new_relations": 5,
  "new_graph_evidence": 2,
  "max_depth_reached": 2,
  "frontier_entities": [],
  "relation_summaries": [],
  "admitted_evidence_ids": [],
  "truncated": false,
  "budget": {}
}
```

Main Controller 可以用 Observation 做下一步决策。

但：

> Answer Generator 永远不能把 Observation 直接当事实来源。

最终事实只能来自：

```text
Frozen Snapshot
```

---

# 36. Evidence Gate 适配

关系题：

```text
A 和 B 是什么关系？
```

如果存在：

```text
A -[belongs_to]-> B
```

且 Relation Admission PASS：

```text
可以 SUFFICIENT
```

但是概览题：

```text
A 是什么？
```

只有：

```text
A belongs_to ProductX
```

最多只能支持：

```text
A 的产品归属
```

不能凭一条 belongs_to 就说：

```text
A 的主要功能、业务价值、完整定位
```

所以可能仍然：

```text
PARTIAL
```

参数题同理。

---

# 37. Answer Generator 适配

当前 Answer Generator 已经有：

```text
<graph_relations>
```

这个设计保留。

Snapshot：

```text
[1] 文档 Chunk
[2] Graph Relation
[3] 文档 Chunk
```

回答：

> PipelineWebGL 属于 StampTools 产品体系。[2]

其中 `[2]` 必须真实对应：

```text
PipelineWebGL -[belongs_to]-> StampTools
```

---

# 38. Grounding Reviewer 适配

Reviewer 必须验证：

```text
source entity
relation type
target entity
direction
```

例如：

```text
Claim:
A 属于 B

Evidence:
A -[belongs_to]-> B

→ SUPPORTED
```

但：

```text
Evidence:
A -[belongs_to]-> B
B -[depends_on]-> C

Claim:
A 依赖 C

→ UNSUPPORTED
```

除非未来 Relation Rule 明确允许组合。

Reviewer 不能：

```text
自己查 Graph
自己扩图
自己新增 Evidence
```

---

# 39. `link_entities` 的最终处理

迁移期可以保留为：

```text
legacy alias
```

但完成后 Main Registry 推荐：

```text
retrieve_kb
expand_graph_scope
reuse_evidence
clarify
environment.read_status
web_search（可选）
```

不再同时保留：

```text
link_entities
+
expand_graph_scope
```

两个意思高度重叠的工具。

最终语义：

```text
第一次一跳图谱
= Runtime Bootstrap (支持多根)

后续扩大范围
= expand_graph_scope (支持加深与开辟新根)
```

这比现在清楚很多。

---

# 40. 建议新增模块

建议新增：

```text
rag_knowledge/services/agent_orchestration/
    graph_working_set.py

rag_knowledge/services/agent_orchestration/
    graph_explorer.py

rag_knowledge/services/agent_orchestration/
    graph_admission.py
```

职责：

```text
graph_working_set.py
→ Multi-root Graph state / roots / frontier / visited / paths / budget

graph_explorer.py
→ Bootstrap + expand traversal (Depth & Root expansion)

graph_admission.py
→ Relation Candidate → Evidence
```

不要继续把所有 Agent 状态都塞进：

```text
graph_retrieval.py
```

---

# 41. ExplorationGrant 重新收敛

ExplorationGrant 最终只负责：

```text
step-level tool authorization
identity provenance
hard graph traversal permission
```

不再承担：

```text
Graph dynamic state
Candidate scope
Evidence authorization
```

分别交给：

```text
GraphWorkingSet
Candidate Pipeline
Admission
```

---

# 42. Clarification P0 联动

这是 Graph 架构的前置。

Bootstrap 依赖：

```text
confirmed_entity
```

而现在已经确认 Clarification Bug：

```text
只有 fixed_other
→ 也可以中断回答
```

必须先或同步修复：

```text
meaningful_candidates
<
min_options

→ 不发布普通实体选择卡
```

并且：

```text
fixed_other
不计 meaningful candidate
```

否则很多 Query 根本到不了 Graph Bootstrap。

---

# 43. Mixed Finalizer P0 联动

当前已有明确 Bug：

```text
部分 Claim 有证据
部分 Claim 无证据

→ Reviewer PARTIAL / REVISE

→ 整篇 Candidate 被降级成 General Knowledge
```

Graph Evidence 上线后如果不修，会出现：

```text
图谱明明有证据
↓
最终却说：
“当前知识库中未查询到相关内容”
```

所以必须改成 Claim-level：

```text
Supported Claim
→ 保留 citation / KB / Graph attribution

Unsupported Claim
→ 删除 / rewrite / 单独 General section
```

禁止：

```text
whole candidate relabel
```

---

# 44. Trace mode P0

必须补：

```text
request_mode
requested_agent_orchestration_enabled
effective_agent_orchestration_enabled
```

尤其：

```text
mode=linear
```

请求级覆盖 config 时，Trace 必须真实反映：

```text
effective = false
```

不能继续仅根据：

```text
config
+
has_agent_steps
```

猜。

---

# 45. 原始 Reasoning P0

当前已经确认：

```text
provider raw reasoning
→ SSE
→ 前端可展开
→ Trace 全量保存
```

31 条 Agent 探针已经约：

```text
83.6 MB
```

Graph 扩展之后 reasoning 只会更长。

必须引入明确策略：

```text
reasoning_stream_policy
=
off
|
summarized
|
raw
```

以及：

```text
trace_reasoning_policy
=
off
|
summarized
|
raw
```

还需要：

```text
trace_reasoning_max_chars
```

建议正常生产默认：

```text
SSE = summarized
Trace = summarized + bounded
```

真正用户可见执行过程依赖：

```text
decision_reason
execution_reason
```

而不是 provider 原始内部 reasoning。

---

# 46. Graph SSE 事件

建议新增：

```text
graph_bootstrap_started
graph_bootstrap_completed

graph_scope_expansion_started
graph_scope_expansion_completed

graph_relation_admission_updated
graph_budget_updated
```

用户可以看到：

```text
已查询 PipelineWebRTC 与 PipelineWebGL 的一跳图谱关系。

发现 6 个关联实体、8 条已审核关系，
其中 2 条与当前问题直接相关。

当前仍缺少服务依赖关系，
正在从 WebRTC 分支继续扩大一跳。

本次新增 3 个实体、5 条关系。
```

这是有价值的“Agent 执行透明”。

---

# 47. 配置建议

`[agent_orchestration]`：

```ini
graph_bootstrap_enabled = true
graph_bootstrap_hops = 1

graph_max_hops_per_expansion = 2
graph_max_total_depth = 3

graph_max_expansion_calls = 2

graph_max_entities_total = 24
graph_max_relations_total = 64

graph_relation_admission_enabled = true

agent_graph_working_set_v1 = true
```

不要继续让 Agent 总 Graph Budget 隐式依赖：

```text
GraphExpander intent → max_hops
```

旧逻辑可留给 Legacy Linear。

---

# 48. 实施 Phase

## Phase 0：冻结事故基线

保存四个明确基线：

```text
1.
Candidate V2 下
link_entities 查到 relation
但 relation 不进 EvidencePool

2.
multi_entity_relation
因 relation evidence 不存在
Gate 返回 missing_relation

3.
AgentCandidatePipeline
仍自行 _graph_neighbors()

4.
历史旧 Trace
relation evidence 曾成功进入 EvidencePool
```

---

## Phase 1：Multi-root GraphWorkingSet

新增：

```text
GraphWorkingSet (Multi-root)
GraphEntityState
GraphRelationCandidate
GraphPathCandidate
GraphBudget
```

先不改变生产行为。

---

## Phase 2：Bootstrap Shadow Mode

接入：

```text
confirmed_entities
→ bootstrap_anchor_graph() (支持单根与多根)
```

先只：

```text
建立 Multi-root WorkingSet
写 Trace
产生 Observation
```

暂不影响 Evidence。

验证：

```text
每个 confirmed_entity
正好一次 1-hop bootstrap
```

---

## Phase 3：Relation Admission

实现：

```text
GraphRelationCandidate
↓
Relation Admission
↓
GraphRelationEvidence
```

删除：

```text
V2 relation 永远不能 Evidence
```

这一错误原则。

注意：

> 不是直接恢复所有 `evidence.add_relation()`。

必须有 Admission。

---

## Phase 4：Candidate Pipeline 消费 WorkingSet

将：

```text
AgentCandidatePipeline._graph_neighbors()
```

从 Agent 主路径移除。

改成：

```text
GraphWorkingSet
→ Graph Candidate Generator
```

---

## Phase 5：`expand_graph_scope` (Depth & Root Expansion)

实现：

```text
start_entities 4类授权来源门禁
direction
relation filters
hop budget
expansion signature
NO_PROGRESS
Graph Budget
Trace
SSE
```

---

## Phase 6：Controller + Gate

改：

```text
ControllerState
Controller Prompt
Allowed tools
Evidence Gap
Evidence Gate
```

---

## Phase 7：Answer + Reviewer

打通：

```text
GraphRelationEvidence
↓
Snapshot
↓
Answer
↓
Reviewer
```

---

## Phase 8：联合修复四个 P0

同步完成：

```text
Clarification meaningful candidate gate

Mixed Finalizer claim-level publication

Trace request mode

Raw reasoning disclosure / retention
```

---

## Phase 9：Legacy Cleanup

新链验收后：

```text
link_entities 从 Main Registry 删除

CandidatePipeline hidden graph traversal 删除

“V2 graph relation never Evidence” 删除

旧双状态 Graph 逻辑删除或标 legacy-only
```

---

# 49. 严禁的实施方式

以下全部不算完成：

```text
只删除 candidate_pipeline_v2 判断

只恢复 evidence.add_relation()

只改 Prompt 让 Main 每次先查 Graph

只把 link_entities 改名 expand_graph_scope

Candidate Pipeline 仍偷偷访问 Graph DB

Graph neighbor 自动成为 Query Identity

Root Expansion 偷偷篡改 Query Identity

Bootstrap 查到所有 relation 全部进 EvidencePool

A→B→C 自动推出 A→C

依赖 Reviewer 最后兜底所有 Graph 错误

给 PipelineWebRTC 写特判

给 PipelineWebGL 写特判

为了测试通过关闭 Candidate Pipeline V2
```

---

# 50. 测试：GraphWorkingSet

必须有：

```text
single anchor bootstrap

multi-anchor merge (多根局部图合并)

root expansion 动态新增局部根

entity dedup

relation dedup

frontier computation

visited state

graph revision

max total depth

max entity count

max relation count

duplicate expansion

Query 间不复用 Admission
```

---

# 51. 测试：Relation Admission

覆盖：

```text
approved direct relation + matching query
→ PASS

pending
→ REJECT

rejected
→ REJECT

unknown relation
→ REJECT

wrong entity pair
→ REJECT

right entity, wrong intent
→ REJECT

port question + belongs_to
→ REJECT

relation question + belongs_to
→ PASS

direction reverse claim
→ REJECT

multi-hop inferred claim
→ REJECT
```

---

# 52. Candidate Pipeline 测试

必须明确证明：

> Agent Candidate Pipeline 不再自行 Graph traversal。

可以 mock：

```text
graph_db.list_relations()
```

若 Candidate Pipeline 直接调用：

```text
test fail
```

同时验证：

```text
WorkingSet discovered entity
→ 能贡献 Candidate
```

---

# 53. Identity & Start Entity Guard 测试

例如：

```text
Identity = PipelineWebRTC

Graph discovers:
WebRTC
StampServer
StampTools
```

最后必须：

```text
Identity 仍为 PipelineWebRTC (不受探索影响)
```

另外：

```text
start_entities = 未授权/未观察实体
→ DENIED graph_root_not_authorized

start_entities = 检索已证实实体
→ ALLOWED
```

必须额外覆盖“直接新 Root”场景：

```text
Identity = PipelineWebRTC
GraphWorkingSet 当前只有 PipelineWebRTC 一跳邻域
PipelineWebGL 不在当前 WorkingSet

但 PipelineWebGL 属于：
- 用户显式提及且解析确认实体
  或
- 当前 admitted text evidence 已证实实体

调用：
expand_graph_scope(start_entities=["PipelineWebGL"], additional_hops=1)

预期：
→ ALLOWED
→ resolved_expansion_type = root_expansion
→ PipelineWebGL 成为新的 local root
→ 直接查询 PipelineWebGL 1-hop
→ 不要求先存在 PipelineWebRTC → ... → PipelineWebGL 路径
→ Query Identity 仍保持 PipelineWebRTC（除非 Stage-1 明确发生 Identity Transition）
```

反向测试：

```text
PipelineWebGL 既未被用户确认、未被 Graph 发现、也未被 admitted evidence 证实
→ DENIED graph_root_not_authorized
```

---

# 54. Evidence / Gate 测试

覆盖：

```text
GraphRelationEvidence 可以进 EvidencePool

未 Admission relation 不可以

Snapshot 同时含 Text + Graph

relation question + direct relation
→ SUFFICIENT

relation question + no relation
→ missing_relation

overview + only belongs_to
→ PARTIAL

parameter + only belongs_to
→ 不得 FULL
```

---

# 55. Grounding 测试

覆盖：

```text
A belongs_to B
→ supported

B belongs_to A
→ unsupported

A belongs_to B
+
B depends_on C

Claim A depends_on C
→ unsupported

不存在 citation id
→ fail

Relation 不在 Snapshot
→ fail
```

---

# 56. Deterministic Agent Integration

至少 5 个场景。

### Case 1：Bootstrap 本身足够

问题：

```text
A 属于什么产品？
```

Bootstrap：

```text
A -[belongs_to]-> B
```

目标：

```text
Relation Admission PASS
↓
Gate sufficient
↓
Main finalize
```

无需强制 retrieve。

---

### Case 2：Bootstrap 不足

问题：

```text
A 的主要用途是什么？
```

Bootstrap 只有：

```text
A belongs_to B
```

则：

```text
Main retrieve_kb
↓
获取概览文本
↓
finalize
```

---

### Case 3：需要扩图（Root Expansion 或 Depth Expansion）

问题：

```text
PipelineWebRTC 和 PipelineWebGL 有什么共同父产品？
```

Bootstrap：

```text
Root A: PipelineWebRTC → StampTools
Root B: PipelineWebGL → StampTools
```

Main：

```text
WorkingSet 已直接命中公共关联节点
```

或需要针对 StampServer 开辟新局部根：

```text
expand_graph_scope(start_entities=["StampServer"], additional_hops=1)
```

回答只陈述真实路径，不虚构跨实体关系。

---

### Case 4：NO_PROGRESS

```text
expand
→ new_entities=0
→ new_relations=0
```

Main 不重复。

---

### Case 5：Graph unavailable

```text
Bootstrap unavailable
↓
graph tool unavailable
↓
retrieve_kb
```

正常继续。

---

# 57. PipelineWebRTC 真事故回归

必须真实建立：

```text
PipelineWebRTC direct chunks = 0
```

然后证明：

```text
Identity
↓
Bootstrap
↓
GraphWorkingSet
↓
discovered neighbor
↓
Candidate
↓
Admission
↓
正确证据
```

Trace 必须解释整条路径。

不能只看最终答案正确。

---

# 58. PipelineWebGL → PipelineBuilder 回归

必须证明：

```text
PipelineBuilder
```

即使：

```text
BM25 / Vector 很高
```

也不能污染：

```text
PipelineWebGL
```

回答。

同时 Graph `different_from` 可以帮助 Structural Guard。

---

# 59. Graph Gold Set

新增至少这些类别：

```text
Direct one-hop relation

Multi-entity direct relation

2-hop path

different_from

belongs_to

requires

depends_on

uses / implements

no relation

graph unavailable
```

每类至少 3 条。

---

# 60. Graph 评测指标

必须新增：

```text
Graph Relation Recall

Graph Relation Admission Precision

Wrong Relation Contamination

Path Hallucination Rate

Graph-supported Claim Accuracy

Graph Bootstrap Success Rate

Graph Expansion Success Rate

Graph NO_PROGRESS Rate
```

---

# 61. 真实模型 Micro-chain

必须用真实：

```text
Main
+
Helper
```

至少跑：

```text
Bootstrap sufficient

Bootstrap → retrieve

Bootstrap → expand (depth/root)

expand → retrieve

NO_PROGRESS

Graph unavailable
```

不是只跑 fake `decide_fn`。

---

# 62. HTTP/SSE E2E

必须真实走：

```text
/api/query/stream
```

事件顺序验证：

```text
understanding

identity binding

graph_bootstrap_started

graph_bootstrap_completed

controller decision

(optional)
graph_scope_expansion_started

(optional)
graph_scope_expansion_completed

retrieval

evidence update

finalization

answer

review

publication
```

同时对应同一 Trace。

---

# 63. 重新建立正式 36 条回归

现在 8 月 25 日：

```text
73 Trace
42 Linear
31 Agent probes
```

没有原 36 manifest / runner。

因此必须重新建立：

```text
36-case manifest
```

每条至少保存：

```text
case_id

question

expected mode

expected identity

expected clarification

expected task_type

expected graph bootstrap

expected graph expansion

expected evidence type

expected final answer class
```

runner 强制：

```text
mode=agent
```

---

# 64. 冻结环境

正式 36 条对比必须记录：

```text
SVN revision

working tree state

RAG_CONFIG

config hash

Main model
Main endpoint

Helper model
Helper endpoint

embedding model
embedding endpoint

reranker
reranker endpoint

corpus fingerprint

Chroma fingerprint

graph revision

request mode

allow_general_knowledge
```

否则不能做因果结论。

---

# 65. General Knowledge 评测

正式主 RAG 质量回归：

```text
allow_general_knowledge=false
```

先测：

```text
纯 KB + Graph 能力
```

之后另外跑：

```text
allow_general_knowledge=true
```

测试产品 fallback。

绝不能把：

```text
General Knowledge fallback
```

算成：

```text
RAG 检索成功
```

---

# 66. 延迟指标重新拆分

禁止再：

```text
Clarification 1s
+
Agent Answer 30s
```

算平均 15s 然后宣称提速。

必须分别统计：

```text
clarification latency

graph bootstrap latency

graph expansion latency

retrieval latency

answer generation latency

reviewer latency

answered-only latency

full-agent latency
```

---

# 67. Bootstrap 性能目标

Bootstrap 不应调用 Main LLM。

正常就是：

```text
Graph DB one-hop read
+
deterministic filtering
+
必要时少量 Relation Admission
```

目标建议：

```text
Bootstrap P95 < 100ms
```

如果当前数据库现实达不到，以真实基线为准。

重点是：

> 不允许把“默认一跳”实现成额外一次 LLM 问答。

---

# 68. Relation Admission 成本

不能：

```text
30 relations
→ 30 Helper calls
```

建议：

```text
Hard Rules
↓
Deterministic Admission
↓
剩余 ambiguous relations
↓
Helper batch
```

例如：

```text
一次最多 12 条 ambiguous relation
```

---

# 69. 当前测试回归也必须同时清掉

当前已知：

```text
test_agent_stream_forwards_structured_decision_without_thinking
```

仍失败。

原因：

```text
无条件访问 result.terminal_action
```

在最终 DoD 前必须恢复：

```text
专项测试 0 failure
```

不能带着：

```text
50 pass / 1 fail
```

宣布本 PRD 完成。

---

# 70. Definition of Done

以下全部完成才能改成“已完成”。

## 架构 DoD

```text
[ ] GraphWorkingSet 是 Agent 唯一 Graph 探索状态源（支持多根 Multi-root）

[ ] confirmed_entities 默认且仅执行一次一跳 Bootstrap（多实体支持多根并行构建）

[ ] Bootstrap 不依赖 Main Tool Call

[ ] Bootstrap 与 Root Expansion 均不改变/篡改 Query Identity

[ ] Candidate Pipeline 不再隐藏 Graph traversal

[ ] expand_graph_scope 支持 Depth Expansion（加深）与 Root Expansion（新授权根）

[ ] Root Expansion 可直接查询新的已授权实体，不依赖与原锚点之间先存在已知路径

[ ] GraphWorkingSet 不得退化为“只能从初始锚点向外走”的单根 BFS

[ ] 新 Root 的 depth 从 0 独立计算，max_total_depth 按 per-root local depth 执行

[ ] start_entities 4类授权来源门禁完成

[ ] Runtime 能区分并 Trace `depth_expansion` / `root_expansion`，记录 new_roots 与 authorization source

[ ] hop / entity / relation / call budget 完成

[ ] duplicate / NO_PROGRESS 熔断完成
```

## Evidence DoD

```text
[ ] approved relation 可以经过 Admission 成为 Evidence

[ ] pending / rejected / unknown 不可成为 Evidence

[ ] GraphCandidatePath 与 GraphRelationEvidence 分离

[ ] GraphRelationEvidence 能进入 EvidencePool

[ ] Frozen Snapshot 支持 Text + Graph

[ ] V2 “Graph 永远不能成为 Evidence” 已删除

[ ] 无关一跳关系不会污染 Snapshot

[ ] 多跳路径不会自动产生传递 Claim
```

## Answer / Reviewer DoD

```text
[ ] Answer Generator 可引用 GraphRelationEvidence

[ ] Observation 不可绕过 Snapshot

[ ] Reviewer 校验 source/relation/target/direction

[ ] A→B + B→C 不自动支持 A→C

[ ] relation question 可由合法 relation evidence 满足 Gate

[ ] 参数/概览题不会因为任意 relation 错误 FULL
```

## 历史事故 DoD

```text
[ ] PipelineWebRTC 无直属 chunk 可恢复召回

[ ] PipelineWebGL → PipelineBuilder 不复发

[ ] different_from 可以辅助 Structural Guard

[ ] graph neighbor 不自动获得 Evidence 权限
```

## 联合 P0 DoD

```text
[ ] Clarification meaningful candidate gate 修复

[ ] fixed_other 不计 candidate

[ ] Mixed Finalizer 不 whole-candidate relabel

[ ] Supported Graph / KB Claim 保留 citation

[ ] Trace 保存 request mode

[ ] Agent/Linear 可严格复现

[ ] reasoning SSE/Trace 有明确 policy

[ ] raw reasoning 有长度和 retention 限制
```

## 测试 DoD

```text
[ ] Unit 全绿

[ ] Agent integration 全绿

[ ] Real-model micro-chain 全绿

[ ] HTTP/SSE ↔ Trace 全绿

[ ] 36-case manifest 固定

[ ] 36-case runner 固定

[ ] 36-case 强制 Agent

[ ] 浏览器人工验收完成

[ ] 已知 50/1 测试回归修复
```

## 工程卫生 DoD

```text
[ ] 无 PipelineWebRTC 特判

[ ] 无 PipelineWebGL 特判

[ ] 无双 Agent Graph 主路径

[ ] link_entities 已退出 Main Registry 或明确 legacy-only

[ ] 无调试脚本残留

[ ] 无无关 config 漂移

[ ] 文档只有 DoD 全绿后才移动到“已完成”
```

---

# 71. 最终架构铁律

实施以后，整个系统必须真实满足：

```text
Identity
只能决定“用户在问谁”（由 Stage-1 严格锁定，绝不因图谱探索发生漂移）

Graph Bootstrap
只能默认建立锚点/确认实体的 1-hop 局部认知（多实体并行建立多根）

Graph Exploration Roots
只能代表“Agent 探索图谱的世界起点集合”（严禁未授权实体作为 root）

GraphWorkingSet
只能表示“Agent 当前探索到的多根局部图谱世界”

expand_graph_scope
只能扩大图谱工作集覆盖（支持加深 Depth 与开辟新根 Root，绝不能篡改 Identity）

GraphCandidatePath
只能解释“为什么值得去那里搜索”

Candidate Pipeline
只能基于 GraphWorkingSet 提出候选材料，绝不能私自再次遍历 Graph DB

Relation Admission
只能决定某条图谱关系对当前 Query 是否有 Evidence 资格

Chunk Admission
只能决定某个文本片段对当前 Query 是否有 Evidence 资格

EvidencePool
只能存当前 Query 合法 Evidence

Snapshot
只能冻结 Evidence

Answer Generator
只能根据 Snapshot 回答

Grounding Reviewer
只能验证 Claim 是否被 Snapshot 支持
```

任何层都不能偷偷替下一层做决定。
