# Identity / Candidate / Evidence / Grounding 分层与检索召回解耦重构执行 PRD

## 0. 文档信息

**文档类型**：执行 PRD
**实施目标**：重构 Agent RAG 中 Identity、Candidate Retrieval、Evidence Admission、Grounding 四层职责，解除 `Identity Scope = Retrieval Scope = Evidence Scope` 的错误耦合。
**核心事故基线**：

- `PipelineWebRTC`：目标实体无直属 chunk，但合法证据存在于 `WebRTC / StampServer / StampWebRTC` 等资料中；当前 `ExplorationGrant.target_entities` 被直接用于 pre-TopK `document_entity` 硬过滤，导致 Hybrid Search 在搜索前候选即归零。
- `PipelineWebGL → PipelineBuilder`：历史事故证明，如果完全放开实体边界，Hybrid Search 会召回名字或技术语义相近但主体错误的 chunk，最终污染回答。

**本轮目标不是“放宽 EvidenceScope”**，而是：

> 在保持 Query Identity 严格锁定和错误实体污染防护的前提下，将 Candidate Retrieval 从 Identity Scope 中解耦，建立 Multi-path Candidate Generation、Entity + Intent Admission 与 Claim-level Grounding Review 的分层架构。

---

# 1. 第一性原则

本轮所有设计和实施必须满足以下总原则：

```text
Identity
≠
Candidate
≠
Admitted Evidence
≠
Claim Support
```

四者含义分别为：

```text
Identity
= 用户到底在问谁

Candidate
= 哪些 chunk 值得继续检查

Admitted Evidence
= 哪些 chunk 对“当前主体 + 当前问题意图”具有证据资格

Claim Support
= 最终回答中的某个 Claim 是否被冻结证据真正支持
```

禁止重新出现以下等式：

```text
target_entity
=
allowed_document_entity
=
admissible evidence
=
claim support
```

---

# 2. 架构总原则

本轮架构统一收敛为：

> **身份严格、数据边界严格、候选多路且有界、明显错误尽早淘汰、语义实体与意图资格独立判断、最终 Claim 再做 Grounding。**

完整链路：

```text
Query
  ↓
Understanding
  ↓
Identity Resolution
  ↓
Identity Guard
  │
  │ 主体不可漂移
  ▼
Hard Data Boundary
ACL / KB / review_status / 真隔离边界
  ↓
Multi-path Candidate Generation
  ├─ document_entity exact
  ├─ entity_chunk_links
  ├─ mentioned_entities
  ├─ graph expansion
  ├─ exact lexical
  ├─ BM25
  └─ Vector
  ↓
Cheap Structural Guard
  │
  │ 明确 sibling conflict / 真越权 / 无合法来源
  ▼
Merge + Dedup + Fusion
  ↓
Reranker
  ↓
Semantic Entity + Intent Admission
  │
  ├─ PASS
  │    ↓
  │ Query-scoped EvidencePool
  │    ↓
  │ Immutable Snapshot
  │    ↓
  │ Answer
  │    ↓
  │ Claim-level Grounding Review
  │
  └─ REJECT
```

---

# 3. 本轮必须解决的问题

## 3.1 P0：Identity 与 Retrieval 强耦合

当前 `ExplorationGrant`：

```python
@property
def admissible_entities(self):
    return frozenset(self.target_entities)
```

而 `retrieval_strategy.py` 的 pre-TopK filter 类似：

```python
targets = scope.target_entities
admissible = targets or scope.admissible_entities

if admissible:
    document_entity IN admissible
```

对于：

```text
target_entities = [PipelineWebRTC]
materialized_chunk_ids = []
```

实际变成：

```text
WHERE document_entity IN ["PipelineWebRTC"]
```

由于知识库：

```text
PipelineWebRTC 直属 chunks = 0
```

导致：

```text
Vector = 0
BM25 = 0
Hybrid = 0
```

检索模型本身没有失败，而是在检索前被结构过滤清空。

---

## 3.2 P0：Graph 授权语义错误

当前图谱关系策略存在：

```text
scope_traversal = true
```

容易被理解成：

```text
graph neighbor
→ 可作为 RetrievalScope
→ 可作为 EvidenceScope
```

本轮必须彻底修正为：

```text
graph relation
→ Candidate Expansion

不是

graph relation
→ Evidence Authorization
```

例如：

```text
PipelineWebRTC
    belongs_to
        ↓
WebRTC
```

只能说明：

> WebRTC 相关资料值得进入候选搜索区域。

不能说明：

> WebRTC 的所有 chunk 都可以成为 PipelineWebRTC 的证据。

---

## 3.3 P0：缺少 Entity + Intent Admission

当前系统容易出现两种极端：

### 错主体

```text
问题：PipelineWebGL 的用途？

候选：
PipelineBuilder 用于……

→ 不得进入 EvidencePool
```

### 主体正确但主题错误

```text
问题：
PipelineWebRTC 的主要功能？

候选：
PipelineWebRTC 上传到 /data/html

→ 实体正确
→ 但只支持 deployment
→ 不足以支持“主要功能”
```

因此：

```text
Entity Admission
```

不得仅判断：

```text
chunk 是否属于 target_entity
```

必须正式定义为：

```text
Entity + Intent Admission
=
Target Entity Relevance
+
Query Intent Relevance
```

---

# 4. 明确职责边界

## 4.1 Identity Layer

### 可以决定

```text
用户当前问题的目标实体是谁
是否已澄清
是否沿用确认过的历史主体
哪些兄弟实体明确不能重新绑定
```

### 不可以决定

```text
哪些 chunk 是候选
哪些 document_entity 可以搜索
某个 chunk 是否为 Evidence
某个 Claim 是否成立
```

### 示例

```text
target = PipelineWebRTC

excluded_rebinding:
- PipelineWebGL
- PipelineBuilder
- 其他明确 sibling / different_from 对象
```

但：

```text
excluded_rebinding
```

是：

> 禁止把问题主体重新解释成它们。

不是：

> 这些实体出现在哪篇文档里，该文档就全部不能搜索。

---

# 5. Hard Data Boundary

必须独立于 Identity。

这里只处理真正的硬约束：

```text
KB
ACL / 用户权限
tenant
review_status
真实产品或知识库隔离
显式 doc_category（仅在业务语义确属硬边界时）
```

例如：

```text
review_status != approved
→ 无论多相关都不能检索

KB != 当前 KB
→ 不允许访问
```

但不得继续使用：

```text
document_entity == target_entity
```

作为通用 Hard Boundary。

---

# 6. Multi-path Candidate Generation

## 6.1 核心规则

各 Candidate Generator 必须：

> **并列贡献候选。**

禁止实现成：

```text
先：
ScopeResolver / ExplorationGrant
→ allowed_entities

然后：
所有 Retriever
都只能在 allowed_entities 中搜索
```

这种实现即使改名为：

```text
CandidateScope
```

也视为本轮失败。

---

## 6.2 Candidate Generator 列表

首版至少实现以下来源。

### Generator A：Direct Document Entity

条件：

```text
document_entity == target_entity
```

意义：

```text
强 provenance
强 Candidate signal
```

但：

```text
document_entity match
≠
直接进入 EvidencePool
```

---

### Generator B：Entity → Chunk Link

基于图谱或实体索引中已经存在的：

```text
Entity
→ Chunk
```

显式链接。

例如：

```text
PipelineWebRTC
→ chunk A
→ chunk C
```

这些 chunk 可作为强候选。

---

### Generator C：Mentioned Entities

索引期逐步支持：

```text
chunk.metadata.mentioned_entities
```

或等价的独立 Entity-Chunk Mapping。

例如：

```json
{
  "document_entity": "WebRTC",
  "mentioned_entities": [
    "PipelineWebRTC"
  ]
}
```

则该 chunk 可以成为：

```text
PipelineWebRTC
```

的强候选。

禁止推导：

```text
mentioned_entities contains X
→ 直接 Evidence PASS
```

---

### Generator D：Graph Expansion

输入：

```text
target_entity
```

通过：

```text
relation_policy
```

寻找有 Candidate Expansion 资格的相邻实体。

例如：

```text
PipelineWebRTC
→ belongs_to → WebRTC
```

然后只将：

```text
WebRTC
```

作为搜索区域 / 候选生成路径。

禁止：

```text
WebRTC all chunks
→ EvidencePool
```

---

### Generator E：Exact Lexical

针对技术实体：

```text
PipelineWebRTC
StampServer
PipelineWebGL
```

执行：

```text
exact
case-insensitive
canonical alias
```

词法搜索。

这是内部技术名、产品名、命令、API、类名等的重要召回通道。

---

### Generator F：BM25

BM25 保留为高召回 Candidate Generator。

必须受：

```text
Hard Data Boundary
```

约束。

但不得默认被：

```text
document_entity == target
```

限制。

---

### Generator G：Vector

Vector Search 同样作为独立 Candidate Generator。

适合：

```text
概念性表达
同义表达
用户未精确使用文档原词
```

同样只接受：

```text
Hard Data Boundary
```

而不是 Identity hard filter。

---

# 7. Candidate Budget

不得所有 Retriever 无限召回。

首版要求独立预算。

推荐初始值可配置，例如：

```text
Direct document_entity      Top 20
Entity→Chunk                Top 20
Mentioned Entity            Top 20
Graph Expansion             Top 30
Exact lexical               Top 20
BM25                        Top 40
Vector                      Top 40
```

合并后：

```text
Raw candidates
↓
Dedup
↓
最多保留 80
↓
Fusion / RRF
↓
Reranker Top 20
↓
Entity + Intent Admission
```

具体数值允许通过 Gold Set 调整。

但以下原则不得改变：

```text
每路独立贡献
+
总池有预算
```

禁止重新做：

```text
统一 Entity Scope
→ 所有 Retriever 共享同一 prefilter
```

---

# 8. Candidate 数据模型

建议新增统一：

```python
CandidateResult
```

概念字段：

```text
chunk_id
document
source_generators

target_entity

document_entity
mentioned_entities

entity_links
graph_paths

lexical_score
bm25_score
vector_score
fusion_score
rerank_score

structural_flags
```

其中：

```text
source_generators
```

允许多值：

```text
[
  "graph_expansion",
  "exact_lexical",
  "bm25"
]
```

同一 chunk 被多个 Retriever 命中时：

```text
Merge
```

而不是复制多个候选。

---

# 9. Graph Relation Policy 重构

当前：

```text
scope_traversal = true / false
```

语义过于接近 Scope Authorization。

首版可以兼容保留字段，但业务语义必须改为：

```text
candidate_expansion
```

长期建议 RelationRule 至少区分：

```text
candidate_expansion = none / weak / medium / strong
```

例如：

| RelationCandidate ExpansionEvidence Authorization |        |       |
| ------------------------------------------------- | ------ | ----- |
| belongs\_to                                       | strong | never |
| has\_module                                       | strong | never |
| has\_service                                      | strong | never |
| implements                                        | strong | never |
| depends\_on                                       | medium | never |
| uses                                              | medium | never |
| related\_to                                       | weak   | never |
| different\_from                                   | none   | never |

首版若不修改 schema，可继续使用：

```text
scope_traversal
```

但所有调用点必须重新定义为：

```text
是否允许作为 Candidate Expansion Edge
```

不得再用于 Evidence Admission。

---

# 10. Cheap Structural Guard

在 Reranker 前增加廉价 Guard。

目标：

> 提前杀掉确定错误，避免浪费 Reranker 和模型资源。

允许使用确定性规则。

## 10.1 Hard Reject 条件

例如：

```text
ACL 不允许
review_status 非 approved
KB 越界
明确 excluded sibling
且不存在目标实体 mention / entity link / 合法 graph provenance
```

例如：

```text
target = PipelineWebRTC

candidate:
document_entity = PipelineBuilder
mentioned_entities 不包含 PipelineWebRTC
无 entity link
无 graph provenance
只有模糊 lexical "Pipeline"

→ REJECT
```

---

## 10.2 不允许 Hard Reject 的条件

```text
document_entity != target_entity
```

本身不能成为 reject 原因。

例如：

```text
document_entity = WebRTC
正文 exact PipelineWebRTC
graph_path = PipelineWebRTC belongs_to WebRTC

→ 必须保留
```

---

# 11. Fusion + Reranker

Multi-path Candidate 合并后执行：

```text
Dedup
↓
Fusion
↓
Rerank
```

Fusion 首版可继续使用现有成熟方法：

```text
RRF
或
现有 weighted fusion
```

禁止本轮同时大规模发明新排序算法。

本轮重点是：

```text
候选来源解耦
```

不是：

```text
重新发明 Ranking
```

Reranker 只回答：

> 当前 Query 下哪个候选更相关？

不得回答：

```text
这个候选已经是合法 Evidence
```

---

# 12. Entity + Intent Admission

## 12.1 独立协议

新增独立阶段：

```text
Candidate
→ EntityIntentAdmission
```

输入至少包括：

```text
query
semantic_task
target_entity

candidate chunk text
document_entity
mentioned_entities
entity links
graph provenance

retrieval sources
rerank score
```

输出建议：

```json
{
  "verdict": "PASS | REJECT",
  "entity_relevance": "HIGH | MEDIUM | LOW | CONFLICT",
  "intent_relevance": "HIGH | MEDIUM | LOW | NONE",
  "reason": "...",
  "admission_signals": []
}
```

---

# 13. Entity Admission 与 Grounding 的严格区别

## Admission 回答

> 这个 chunk 有没有资格服务“当前实体 + 当前问题”？

例如：

```text
目标：
PipelineWebRTC

问题：
主要功能是什么？
```

### Candidate A

```text
PipelineWebRTC 用于……
```

结果：

```text
entity_relevance = HIGH
intent_relevance = HIGH
PASS
```

### Candidate B

```text
PipelineWebRTC 上传到 /data/html
```

结果：

```text
entity_relevance = HIGH
intent_relevance = LOW
REJECT / 或不进入本问题 EvidencePool
```

### Candidate C

```text
PipelineBuilder 用于……
```

结果：

```text
entity_relevance = CONFLICT
REJECT
```

---

## Grounding Reviewer 回答

> Answer 中某个 Claim 是否被已经冻结的 Evidence Snapshot 支持？

禁止 Grounding Reviewer：

```text
重新扩大检索范围
重新解释 target_entity
授权新候选
```

---

# 14. Admission 实现策略

不要把 Entity Admission 全部硬编码为 Python 规则。

推荐：

```text
Cheap deterministic signals
+
semantic relevance
```

结构：

```text
Candidate
↓
Deterministic Fast Checks
↓
明显 PASS / 明显 REJECT
↓
剩余模糊 Candidate
↓
Semantic Admission
```

可用信号：

```text
exact target mention
canonical alias mention
entity_chunk_link
document_entity
section_entity
graph provenance
excluded sibling conflict
rerank score
query intent
section title
```

对于真正语义模糊的候选：

```text
可由当前 Main / Helper 中指定模型做严格结构化判断
```

不得建立大量 case-by-case Python 特例。

---

# 15. Query-scoped Evidence

所有 Admission PASS 结果只对：

```text
当前 Query / 当前 SemanticTask
```

有效。

定义：

```text
Candidate
→ Admission PASS
→ Query-scoped EvidencePool
```

禁止：

```text
Admission PASS
→ 永久写回 evidence_targets
→ 后续 Query 默认授权
```

原因：

```text
entity relevant
≠
支持关于实体的任意问题
```

---

# 16. EvidencePool 与 Immutable Snapshot

Admission PASS 后：

```text
EvidencePool
```

必须：

```text
只包含当前 Query admitted candidates
```

进入 Answer 前冻结：

```text
Immutable Evidence Snapshot
```

冻结后：

```text
Answer Generator
Reviewer
Citation
```

全部使用同一个 Snapshot。

禁止：

```text
Answer 过程中再次补 chunk
Reviewer 自己搜索
Publication 换另一套 Evidence
```

---

# 17. ExplorationGrant 重构

当前：

```text
ExplorationGrant
```

混合了：

```text
Identity authorization
Retrieval scope
Materialized chunk
Graph traversal
```

本轮至少拆清语义。

## 17.1 Grant 应保留

```text
step-level tool authorization
target identity provenance
graph traversal budget
max_hops
max_entities
tool permission
```

## 17.2 Grant 不再承担

```text
所有 Retriever 的统一 document_entity prefilter
最终 Evidence authorization
```

以下逻辑必须退出核心 Agent Retrieval：

```python
@property
def admissible_entities(self):
    return frozenset(self.target_entities)
```

如果为兼容 legacy 保留：

```text
必须禁止 Agent Candidate Pipeline 将该字段解释成唯一搜索实体集合。
```

---

# 18. retrieval\_strategy.py 改造要求

当前 `_build_filter()` 中类似：

```python
targets = scope.target_entities
admissible = targets or scope.admissible_entities

scope_branches.append({
    "document_entity": {"$in": admissible}
})
```

对于 Agent 新链必须停止承担：

```text
Identity → document_entity pre-TopK hard filter
```

新 `_build_filter()` 只保留真正 Hard Boundary：

```text
kb_name
review_status
doc_category（只有确认为硬边界时）
ACL / tenant 等
```

各 Candidate Generator 如需：

```text
document_entity exact
```

必须作为自己的：

```text
Candidate Generator query
```

而不是全局 filter。

---

# 19. BM25 改造要求

当前 `bm25_store.py`：

```python
if not norm_scope.is_structurally_admissible(doc_ent, chunk_id):
    continue
```

Agent 新链必须取消：

```text
Identity Scope 驱动的统一结构 admission
```

BM25 只接受：

```text
Hard Boundary
```

以及自身 Generator 特定约束。

Entity Exact / Graph Search 如需缩窄范围，应由对应 Generator 自己产生候选。

---

# 20. evidence\_scope.py 的目标状态

Legacy 非 Agent 路径如暂时仍依赖：

```text
EvidenceScope
```

可以兼容保留。

但 Agent 主链不得再让一个 EvidenceScope 同时承担：

```text
Identity
+
Candidate
+
Admission
```

建议逐步把 Agent 主链的：

```text
EvidenceScope
```

退化为：

```text
最终 admitted evidence contract
```

或者彻底由：

```text
QueryEvidencePool
```

替代。

禁止继续通过：

```text
admissible_entities
```

控制所有 Retriever。

---

# 21. 需要新增或明确的数据结构

建议至少增加：

## 21.1 CandidateResult

```text
CandidateResult
```

## 21.2 CandidateProvenance

记录：

```text
generator
graph path
entity link
lexical exact
document_entity match
scores
```

## 21.3 AdmissionResult

```text
verdict
entity_relevance
intent_relevance
reason
signals
```

## 21.4 QueryEvidence

```text
chunk_id
target_entity
query_intent
admission_result
candidate_provenance
```

---

# 22. Trace / 可观测性

本轮必须新增 Trace，以便出现错召回时能够解释：

```text
为什么这个 chunk 被找到？
为什么没有在 Structural Guard 淘汰？
为什么 Admission PASS？
为什么最后被用于 Claim？
```

每个 Candidate 至少记录：

```text
chunk_id
candidate_sources
document_entity
mentioned target?
graph path
lexical score
bm25 score
vector score
fusion score
rerank score
structural_guard
admission verdict
admission reason
```

Trace 必须可以回答：

```text
PipelineWebRTC 的最终证据为什么来自 WebRTC 文档？
```

例如：

```text
document_entity = WebRTC
candidate_sources =
  graph_expansion
  exact_lexical
  bm25

graph_path =
PipelineWebRTC --belongs_to--> WebRTC

entity_relevance = HIGH
intent_relevance = HIGH
admission = PASS
```

---

# 23. 不允许的实现方式

以下方案一律视为不符合 PRD。

## 23.1 CandidateScope 换皮

```text
先计算 allowed_entities
→ 全部 Retriever 只能搜这些实体
```

即使类名从：

```text
EvidenceScope
```

改成：

```text
CandidateScope
```

也不允许。

---

## 23.2 Graph Neighbor 自动 Evidence

禁止：

```text
belongs_to
→ neighbor entity
→ neighbor chunks automatically admitted
```

---

## 23.3 全库无限 Hybrid

禁止：

```text
取消 entity filter
→ 全库 Vector/BM25 Top1000
→ 全交给 Reviewer
```

---

## 23.4 Reviewer 兜底所有污染

禁止：

```text
所有错误 chunk 进入 Answer
→ 指望 Grounding Reviewer 最后救回来
```

明显错误必须尽早淘汰。

---

## 23.5 永久 evidence\_targets

禁止本轮实现：

```text
chunk metadata:
evidence_targets = [...]
```

作为永久查询授权。

---

## 23.6 Case-by-case 特例

禁止：

```python
if target == "PipelineWebRTC":
    allow WebRTC
```

以及类似业务名称特判。

---

# 24. 索引期改造

## Phase 1 可不要求全量重建

优先使用已有：

```text
document_entity
graph entity links
graph relations
raw text
```

完成新 Candidate Pipeline。

---

## Phase 2 建议补齐

```text
mentioned_entities / entity_chunk_links
```

最好由实体抽取流程产生标准化：

```text
entity_id
canonical_name
chunk_id
mention type
confidence
```

而不是仅存字符串列表。

这样可以支持：

```text
Entity → Text Unit
```

式召回。

---

# 25. 实施阶段

## Phase 0：事故基线冻结

必须保存现状 Trace：

### Case A

```text
PipelineWebRTC
```

记录：

```text
target_entities
materialized_chunk_ids
vector count
bm25 count
最终原因
```

必须证明当前确实存在：

```text
pre-TopK document_entity hard filter
→ 0 result
```

### Case B

历史：

```text
PipelineWebGL
→ PipelineBuilder
```

作为污染事故基线。

---

# 26. Phase 1：抽离 Hard Boundary

目标：

```text
RetrievalStrategy pre-filter
```

只负责：

```text
KB
review_status
ACL / tenant
真正硬隔离
```

验收：

```text
target_entity 不再自动进入通用 Chroma prefilter
```

注意：

**此阶段不得单独上线。**

必须和后续 Candidate + Admission 同分支实施，避免短暂恢复实体污染。

---

# 27. Phase 2：Candidate 数据模型

新增：

```text
CandidateResult
CandidateProvenance
```

建立统一 merge/dedup 接口。

要求：

```text
同一个 chunk 多路命中
→ 一个 Candidate
→ provenance 聚合
```

---

# 28. Phase 3：并行 Candidate Generators

按顺序实施：

```text
1. document_entity exact
2. entity→chunk
3. graph→search path
4. exact lexical
5. BM25
6. Vector
7. mentioned_entities（如索引已支持）
```

禁止依赖统一 entity allowlist。

---

# 29. Phase 4：Graph Candidate Expansion

改造：

```text
ExplorationGrantResolver
relation_policy
Graph retrieval
```

确保：

```text
Graph
只产生 Candidate Expansion provenance
```

而不是：

```text
admissible_entities
```

Evidence 授权。

---

# 30. Phase 5：Cheap Structural Guard

先覆盖确定性事故：

```text
wrong sibling
different_from
明确冲突 document_entity
无目标 mention
无 entity link
无 graph provenance
```

但只有信号组合足够明确时才能 reject。

---

# 31. Phase 6：Fusion + Reranker

接入多路 Candidate。

必须保证：

```text
reranker 输入不再只是旧 EvidenceScope 已经截断后的候选
```

---

# 32. Phase 7：Entity + Intent Admission

实现：

```text
Admission protocol
```

覆盖：

```text
实体错
实体对但问题主题错
实体对且主题对
模糊语义
```

Admission PASS 后才允许进入：

```text
EvidencePool
```

---

# 33. Phase 8：Query-scoped Evidence Snapshot

确保：

```text
Admission PASS
→ QueryEvidencePool
→ Freeze Snapshot
→ Answer
→ Reviewer
```

禁止中途扩大。

---

# 34. Phase 9：清理旧语义

在新链测试通过后再删除 Agent 主链中的：

```text
Identity → admissible_entities
target_entities → document_entity global filter
Graph → EvidenceScope authorization
BM25 global structural admission
```

Legacy 路径如仍使用：

```text
EvidenceScope
```

必须明确注释：

```text
legacy only
```

不得继续污染 Agent 新链。

---

# 35. 对抗 Gold Set

本轮必须建立核心实体组：

```text
PipelineWebGL
PipelineBuilder
PipelineWebRTC
StampWebRTC
WebRTC
StampServer
```

测试至少包括以下场景。

---

## Case 1：目标实体有直属资料

```text
A 有 document_entity=A
```

期望：

```text
Direct generator 命中
正确回答
```

---

## Case 2：目标实体无直属 chunk，但其他文档明确描述它

PipelineWebRTC 型事故。

期望：

```text
Direct = 0

但：
Entity Link / Exact Lexical / Graph / BM25
能召回正确 chunk

Admission PASS
最终有回答
```

---

## Case 3：名称高度相似兄弟实体

```text
PipelineWebGL
vs
PipelineBuilder
```

期望：

```text
PipelineBuilder 不进入 EvidencePool
```

---

## Case 4：技术相关但主体不同

```text
PipelineWebRTC
vs
WebRTC
```

如果 WebRTC chunk：

```text
只讲 WebRTC 通用机制
```

不得自动成为：

```text
PipelineWebRTC 主要功能
```

证据。

---

## Case 5：同实体不同意图

```text
target = PipelineWebRTC
query = 主要功能
```

部署位置类 chunk：

```text
Entity HIGH
Intent LOW
```

不得挤掉功能证据。

---

## Case 6：完全没有证据

期望：

```text
Candidate 可有噪声
↓
Admission 全 REJECT / insufficient
↓
NO_SAFE_ANSWER
```

禁止外部知识填充。

---

# 36. 核心验收指标

不得只验证：

```text
PipelineWebRTC 能回答了
```

至少统计以下指标。

## 36.1 Gold Evidence Recall\@K

目标：

```text
新架构 >= 当前
```

并重点提升：

```text
跨 document_entity 合法证据
```

召回。

---

## 36.2 Wrong Entity Contamination

定义：

```text
最终 admitted Evidence
中属于明确错误主体的 chunk 比例
```

要求：

```text
不得高于当前安全基线
```

PipelineWebGL / PipelineBuilder 必须作为 P0。

---

## 36.3 Entity Admission Precision

人工 Gold：

```text
哪些 chunk 应 PASS
哪些应 REJECT
```

统计：

```text
Precision
Recall
```

禁止只测 Precision。

---

## 36.4 Intent Admission Accuracy

覆盖：

```text
实体正确
但意图不匹配
```

样本。

---

## 36.5 Grounding Accuracy

在冻结 Snapshot 后：

```text
PASS
REVISE
NO_SAFE_ANSWER
```

必须保持现有安全基线或提升。

---

## 36.6 NO\_SAFE\_ANSWER Accuracy

特别测试：

```text
全库有很多相似资料
但确实没有目标实体答案
```

系统仍必须安全拒答。

---

## 36.7 P95 Latency

Candidate 多路化不能无限增加耗时。

必须记录：

```text
candidate generation
fusion
rerank
admission
answer
review
```

阶段耗时。

---

# 37. 单元测试

至少新增：

```text
test_identity_does_not_become_global_document_filter
test_direct_entity_generator
test_entity_chunk_generator
test_graph_expansion_is_candidate_only
test_exact_lexical_candidate
test_bm25_not_identity_prefiltered
test_vector_not_identity_prefiltered

test_structural_guard_rejects_conflicting_sibling
test_structural_guard_keeps_cross_document_valid_candidate

test_admission_entity_match_intent_match
test_admission_entity_match_intent_mismatch
test_admission_entity_conflict

test_admitted_evidence_is_query_scoped
test_grounding_cannot_expand_evidence
```

---

# 38. 集成测试

必须覆盖：

```text
Candidate Generation
→ Merge
→ Reranker
→ Admission
→ Snapshot
→ Answer
→ Grounding
```

不能只 mock Retrieval。

---

# 39. 真实模型 Micro-chain

至少：

### Micro A

```text
PipelineWebRTC
Direct = 0
Graph / lexical 命中合法 cross-document chunk
Admission PASS
Answer PASS
```

### Micro B

```text
PipelineWebGL
召回 PipelineBuilder 噪声
Admission REJECT
最终无污染
```

### Micro C

```text
正确实体错误 intent chunk
Admission REJECT / LOW
```

---

# 40. 完整真实 E2E

完整跑：

```text
Understanding
→ Identity
→ Candidate Generation
→ Fusion
→ Rerank
→ Admission
→ Snapshot
→ Answer
→ Reviewer
→ Publication
```

每个 Gold 场景至少连续多轮验证稳定性。

禁止：

```text
偶发一次通过
```

即认为完成。

---

# 41. Trace 验收

每个真实测试必须能从 Trace 回答：

```text
1. target_entity 是谁？
2. 哪些 Generator 找到了 Candidate？
3. Graph 为什么扩到某实体？
4. 哪些 Candidate 被 Structural Guard 删除？
5. Reranker 顺序是什么？
6. Admission 为什么 PASS / REJECT？
7. Snapshot 最后包含哪些 chunk？
8. Final Claim 分别由哪些证据支持？
```

缺任一关键阶段，不通过可观测性 DoD。

---

# 42. 迁移原则

严禁：

```text
先删除旧 document_entity 门禁
→ 再慢慢补 Admission
```

必须：

```text
新 Candidate Pipeline
+
Structural Guard
+
Admission
先完整建立

↓

Gold / E2E 双向验证

↓

再移除旧全局实体门禁
```

---

# 43. 双跑建议

如果实现成本允许，在切换前增加测试态：

```text
legacy candidates
new candidates
```

同时记录：

```text
legacy retrieved
new retrieved
new admitted
```

但生产回答仍走旧路径。

用 Gold Set 对比：

```text
新增了哪些正确 Evidence
新增了哪些错误 Candidate
Admission 是否正确处理
```

通过后再切换。

---

# 44. 回滚策略

必须提供 Feature Flag，例如概念上：

```text
agent_candidate_pipeline_v2
```

旧链保留短期回滚能力：

```text
OFF
→ legacy identity-bound retrieval

ON
→ new multi-path candidate pipeline
```

但旧链只能作为迁移期回滚。

PRD 完成后不得形成长期两套架构并存。

---

# 45. 代码重点区域

实施前必须逐一审查：

```text
rag_knowledge/services/exploration_grant.py
rag_knowledge/services/evidence_scope.py
rag_knowledge/services/retrieval_strategy.py
rag_knowledge/services/bm25_store.py
rag_knowledge/services/relation_policy.py

rag_knowledge/services/agent_orchestration/runtime.py
rag_knowledge/services/agent_orchestration/models.py
rag_knowledge/services/agent_orchestration/evidence_gate.py

rag_knowledge/services/rag.py
rag_knowledge/services/qa_trace.py
```

并搜索所有：

```text
target_entities
admissible_entities
is_structurally_admissible
materialized_chunk_ids
scope_traversal
document_entity
```

调用点。

不能只修改：

```text
retrieval_strategy.py
```

而遗留 BM25、Agent Runtime、Gate 等旁路继续使用旧语义。

---

# 46. 文档与命名约束

新代码中禁止继续使用模糊的：

```text
Scope
```

同时表达不同概念。

推荐命名：

```text
IdentityScope
HardRetrievalBoundary
CandidateResult
CandidateProvenance
AdmissionResult
QueryEvidencePool
EvidenceSnapshot
```

若 Legacy 类型保留：

```text
EvidenceScope
ExplorationGrant
```

必须明确职责和兼容状态。

---

# 47. Definition of Done

以下所有项全部完成后，PRD 才可标记完成。

-  Identity 不再自动转成 Agent 全局 `document_entity` pre-filter。
-  Hard Data Boundary 与 Identity 分离。
-  至少 5 类 Candidate Generator 独立贡献候选。
-  不存在统一隐式 Candidate Entity Allowlist。
-  Graph 已明确改为 Candidate Expansion。
-  Graph relation 本身不能直接使 chunk 进入 EvidencePool。
-  CandidateResult / provenance 可追踪。
-  Structural Guard 能提前杀掉明确错误 sibling。
-  `document_entity != target` 不再自动 reject。
-  Reranker 使用多路合并候选。
-  Entity + Intent Admission 已成为独立阶段。
-  Admission 与 Grounding 协议职责分离。
-  Admission PASS 只产生 Query-scoped Evidence。
-  不存在永久 `evidence_targets` 授权。
-  Snapshot 在 Answer 前冻结。
-  Reviewer 无权扩大 Snapshot。
-  PipelineWebRTC 无直属 chunk 场景恢复正确召回。
-  PipelineWebGL → PipelineBuilder 历史事故不复发。
-  主体正确但 Intent 错误的 chunk 不污染回答。
-  无证据场景继续正确 NO\_SAFE\_ANSWER。
-  Gold Evidence Recall\@K 有明确测量结果。
-  Wrong Entity Contamination 不劣于旧方案。
-  Entity Admission Precision / Recall 有结果。
-  Intent Admission Accuracy 有结果。
-  Grounding Accuracy 不退化。
-  P95 Latency 在可接受预算内。
-  新增单元测试全部通过。
-  新增集成测试全部通过。
-  真实模型 Micro-chain 全部通过。
-  完整真实 E2E 多轮稳定通过。
-  Trace 能解释 Candidate → Admission → Evidence → Claim 全链。
-  浏览器实际问答验证完成。
-  Legacy 临时兼容逻辑已清理或明确标记待移除。
-  不存在 PipelineWebRTC 等实体名特判。
-  `git diff` / SVN 工作树无无关改动与临时文件。
-  文档状态由“待实施”改为“已完成”必须在以上 DoD 全绿之后。

---

# 48. 最终验收判据

这次重构只有同时满足下面两个方向才算成功。

## 正向能力

```text
该找到的时候能找到。
```

例如：

```text
PipelineWebRTC 没有直属 document_entity chunk

但正确证据存在于：
WebRTC
StampServer
其他合法资料

→ 能召回
→ Admission PASS
→ 正确回答
```

## 反向安全

```text
不该用的时候绝不使用。
```

例如：

```text
PipelineWebGL
→ PipelineBuilder

PipelineWebRTC
→ 只讲通用 WebRTC 的无关 chunk
```

必须：

```text
REJECT
```

所以本 PRD 的最终工程目标不是单纯：

```text
提高 Recall
```

也不是单纯：

```text
提高 Precision
```

而是：

> **在 Identity 不漂移、错误实体污染不反弹、Claim Grounding 不退化的前提下，解除 Identity 对 Candidate Retrieval 的错误硬限制，恢复合法跨文档、跨实体载体证据的召回能力。**

---

# 49. 最终架构铁律

本轮完成后，代码必须真实满足：

```text
Identity
≠
Candidate
≠
Admitted Evidence
≠
Claim Support
```

并坚持：

```text
Identity
只能锁主体

Candidate Generator
只能提出“值得继续看”的材料

Graph
只能扩展 Candidate 搜索路径

Structural Guard
只能淘汰明确错误

Reranker
只能排序相关性

Entity + Intent Admission
只能决定当前 Query 下是否具备证据资格

Evidence Snapshot
只能冻结已经准入的证据

Grounding Reviewer
只能检查最终 Claim 是否被 Snapshot 支持
```

任何一层都不得越权替下一层做决定。

这就是本轮重构的最终边界。