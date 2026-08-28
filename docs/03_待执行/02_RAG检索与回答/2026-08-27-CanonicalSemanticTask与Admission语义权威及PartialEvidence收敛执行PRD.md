# Canonical Semantic Task、Admission 语义权威与 Partial Evidence 收敛执行 PRD

## 0. 文档信息

**文档类型**：执行 PRD  
**状态**：实施中（架构收口完成，待统一验收）
**日期**：2026-08-27  
**实施性质**：事故根因修复 + 架构收口，不新增业务功能  
**核心目标**：彻底消除“原始歧义 Query / Controller 临时检索 Query / 澄清后真实问题”三套语义同时参与 Evidence Admission 的架构错误，使 Text Evidence、Graph Relation Evidence、Coverage、Answer、Grounding 全部服从同一个 Canonical Semantic Task。

### 0.1 本 PRD 的权威边界

### 0.2 三份 8·27 PRD 的共同权威声明（2026-08-28）

三份 PRD 的分工固定为：`SemanticTaskContext` 是用户语义唯一权威；
`EntityCandidateResolver + IdentityResolution` 是身份唯一权威；
`AgentCandidatePipeline` 只生成候选；Text Evidence 的唯一 Admission 实现是
`TextEvidenceAdmissionService.qualify()`；Graph Relation Evidence 由 Graph Admission
规则裁决；Coverage 的唯一状态是 `FULL / PARTIAL / NONE`；Reviewer 决定
Claim ↔ Evidence ↔ Support Scope；Finalizer 决定最终发布模式。

本 PRD 中此前任何 `pipeline.admit()` 代码片段、调用改造说明或把
`AgentCandidatePipeline` 描述为 Text Admission 实现的段落，均标记为
**superseded**，仅保留事故背景。它们不得作为后续实现依据。

Support Scope 对 Coverage 判定的细化，以 Evidence PRD 为准；本 PRD 不另行
定义 Coverage 状态。三份文档在全部 DoD、真实模型与 HTTP/SSE 验收完成前均
保持“实施中”，不得单独归档。

本 PRD **不推翻**以下两份现有 PRD 的主架构：

1. `2026-08-26-Identity-Candidate-Evidence-Grounding分层与检索召回解耦重构执行PRD.md`
2. `2026-08-26-Agent锚点图谱Bootstrap与自主范围扩展及GraphEvidence执行PRD.md`

继续保留：

```text
Identity
≠ Candidate
≠ Admitted Evidence
≠ Claim Support
```

以及：

```text
GraphWorkingSet
≠ EvidencePool
```

Graph Relation 是否具有 Evidence 资格，仍以 Graph PRD 和 `RelationRule.evidence_intents` 为政策权威。

**本 PRD 是以下语义的最终权威：**

```text
当前 Query 到底在回答什么
Admission 应使用哪个问题语义
Retrieval Query 是否有权修改 Evidence 资格
Coverage 的唯一状态协议
PARTIAL 与 NO_KNOWLEDGE 的边界
```

如旧代码、旧测试或旧 PRD 段落存在以下假设：

```text
Controller retrieval query
→ 可以直接作为 Evidence Admission question
```

或：

```text
raw user_question
→ 在 Clarification 完成后仍可重新解释 Evidence intent
```

则以本 PRD 为准，旧语义废止。

---

# 1. 事故基线

## 1.1 真实事故

用户输入：

```text
pipeline
```

系统完成实体澄清后，用户选择：

```text
PipelineWebRTC
```

此时真实语义已经收敛为：

```text
PipelineWebRTC 的相关信息
```

当前 Trace 已确认系统实际找到：

```text
Graph:
PipelineWebRTC -[belongs_to]-> WebRTC

Text candidates:
PipelineWebRTC WebRTC 应用部署
/data/html
权限设置
外网部署 IP 配置调整
```

但最终：

```text
EvidencePool = 0
coverage = NONE
final_mode = no_knowledge
```

用户看到：

```text
知识库未查询到相关内容
```

这是错误结果。

正确结果应该至少能够诚实发布：

```text
PipelineWebRTC 在知识图谱中归属于 WebRTC 体系。
现有资料还表明它作为 WebRTC 应用参与部署，涉及 /data/html 与外网 IP 配置。
当前资料不足以说明其完整业务功能和用途。
```

即：

```text
Evidence != empty
Coverage = PARTIAL
Final = grounded_partial
```

---

# 2. 已确认根因

本事故不是一个单点 Bug，而是三个架构错误叠加。

## 2.1 根因 A：Graph Admission 使用 raw user_question

当前 Graph Relation Admission 的调用链仍存在：

```python
question=conv.user_question
```

澄清后仍然传入：

```text
pipeline
```

而不是：

```text
PipelineWebRTC 的相关信息
```

导致 Graph Admission 重新解释已经完成澄清的用户意图。

---

## 2.2 根因 B：裸子串匹配把 `pipeline` 识别成 `ip`

当前精确参数判断包含：

```python
EXACT_PARAMETER_TERMS = (..., "ip", "url")
```

并使用：

```python
term in query
```

于是：

```text
pipeline
   ↓
包含字符 "ip"
   ↓
exact_parameter = true
   ↓
normalized intent = config
   ↓
belongs_to 不允许作为 config Evidence
   ↓
REJECT
```

这不是 PipelineWebRTC 特例，而是所有 ASCII 短 token 裸子串匹配都会出现的系统性误判。

---

## 2.3 根因 C：Text Admission 被 Controller 临时 Retrieval Query 反向收窄

Controller 为了检索，可以生成：

```text
PipelineWebRTC 功能与用途概述
```

这是一个 **Search Plan**。

但当前 Candidate Admission 又拿这条临时 Query 判断 Evidence intent，于是所有部署类 chunk 被判：

```text
entity_relevance = HIGH
intent_relevance = LOW
REJECT
```

最终形成错误等式：

```text
Search Query
=
Answer Semantics
=
Evidence Permission
```

这违反 Identity / Candidate / Evidence 分层本身的设计目标。

---

## 2.4 根因 D：Coverage 协议本身存在双词汇

Controller Prompt 使用：

```text
FULL / PARTIAL / NONE
```

但 `FinalizationHandler._coverage_verdict()` 当前实际返回：

```text
SUFFICIENT / PARTIAL / NONE
```

这是新的双状态源。

Coverage 必须只有一套协议。

---

# 3. 第一性原则

如果今天从零设计，本系统必须满足以下五条铁律。

## 3.1 一个用户 Turn 只有一个 Canonical Semantic Task

在 Clarification 完成之后：

```text
原始词
历史歧义
Controller 搜索词
Graph 搜索词
```

都不能再重新定义用户真正的问题。

必须存在唯一语义权威：

```text
SemanticTaskContext
```

其核心事实是：

```text
resolved_question
primary_entity
mentioned_entities
task_type
answer_intent
requested_facets
```

---

## 3.2 Retrieval Query 只是 Search Plan，不是权限

定义：

```text
Retrieval Query
= 为了找到资料而生成的临时搜索表达式
```

它可以：

```text
改写
扩展关键词
针对 gap 定向搜索
切换 vector / bm25 / hybrid
```

它不可以：

```text
修改 Identity
修改 Canonical Question
修改 Answer Intent
修改 Evidence Admission Policy
修改 Coverage Contract
```

核心不变量：

```text
retrieval_query
≠ admission_question
```

---

## 3.3 Evidence Admission 判断“能证明什么”，Coverage 判断“够不够”

这两个概念必须彻底分离。

### Admission

回答：

```text
这条 Evidence 对当前 Canonical Task 是否具有事实资格？
```

### Coverage

回答：

```text
所有已通过 Admission 的 Evidence 合起来，对用户问题覆盖了多少？
```

因此：

```text
有合法部署事实
但没有完整功能介绍
```

必须表达为：

```text
Admission = PASS（部署事实）
Coverage = PARTIAL
```

而不是：

```text
Admission = REJECT ALL
Coverage = NONE
```

---

## 3.4 Clarification 只解决 Identity 时，不得偷偷发明新的用户意图

用户输入：

```text
pipeline
```

用户只选择：

```text
PipelineWebRTC
```

这只说明：

```text
Identity = PipelineWebRTC
```

不等于用户明确问了：

```text
主要功能
完整产品概览
用途
架构
```

默认应得到：

```text
resolved_question = "PipelineWebRTC 的相关信息"
answer_intent = general_qa
requested_facets = []
```

而不是：

```text
answer_intent = definition
requested_facets = [function, purpose]
```

如果用户原问题本身已有明确意图，例如：

```text
pipeline 怎么部署？
```

澄清选择 PipelineWebRTC 后，则必须保留：

```text
resolved_question = "PipelineWebRTC 怎么部署？"
answer_intent = deployment
```

Identity Clarification 不能丢失原始明确意图，也不能凭空新增意图。

---

## 3.5 NO_KNOWLEDGE 只允许表示真正的 NONE

严格定义：

```text
NONE
= 当前 Canonical Task 没有任何可引用、已 Admission PASS 的相关 Evidence
```

如果存在任意合法、可发布的部分事实：

```text
Graph relation
Direct text fact
Configuration fact
Deployment fact
Procedure fact
```

则不允许输出：

```text
知识库未查询到相关内容
```

此时最多是：

```text
PARTIAL
```

---

# 4. 目标架构

最终链路必须收敛为：

```text
User Utterance
   ↓
Stage-1 Understanding
   ↓
Identity Resolution / Clarification
   ↓
Canonical SemanticTaskContext
   │
   │ resolved_question
   │ primary_entity
   │ task_type
   │ answer_intent
   │ requested_facets
   ▼
Main Controller
   │
   ├─ RetrievalRequest A
   │    query = "PipelineWebRTC 功能与用途概述"
   │
   ├─ RetrievalRequest B
   │    query = "PipelineWebRTC WebRTC 应用部署"
   │
   └─ Graph Expansion Request
          ↓
Candidate Generation / GraphWorkingSet
          ↓
Admission
   ┌──────┴────────┐
   │               │
Text Admission   Relation Admission
   │               │
   └──────┬────────┘
          │
          │ 两者全部读取同一个 SemanticTaskContext
          ▼
Query-scoped EvidencePool
          ↓
Coverage
FULL / PARTIAL / NONE
          ↓
Immutable Snapshot
          ↓
Answer
          ↓
Grounding Reviewer
          ↓
Publication
```

禁止出现：

```text
RetrievalRequest.query
→ 修改 Admission intent
```

或：

```text
raw user_question
→ Clarification 后重新推翻 SemanticTask
```

---

# 5. SemanticTaskContext 成为唯一语义权威

## 5.1 不新增重复的 `EvidenceContract` 对象

当前系统已经存在：

```python
SemanticTaskContext
```

以及最终生成阶段的：

```python
AnswerGenerationContext.answer_contract
```

第一性原则下，不应再增加第三个平行 Contract。

职责固定为：

```text
SemanticTaskContext
= 当前用户问题的 canonical semantic authority

RetrievalRequest
= 临时搜索计划

answer_contract
= Finalization 后的发布模式契约
```

三者不得互相代替。

---

## 5.2 `SemanticTaskContext` 扩展

建议目标结构：

```python
@dataclass(frozen=True)
class SemanticTaskContext:
    resolved_question: str
    primary_entity: str | None
    mentioned_entities: tuple[str, ...]

    # 结构类型
    task_type: str

    # 回答语义
    answer_intent: str

    # 用户显式要求的事实维度；开放式 general_qa 可为空
    requested_facets: tuple[str, ...]

    # 可观测性
    intent_source: str

    confidence: float
    entity_binding_required: bool = False
```

### `task_type`

只表达结构：

```text
unbound
single_entity
multi_entity_relation
```

### `answer_intent`

只表达回答政策语义：

```text
definition
comparison
deployment
procedure
config
troubleshooting
general_qa
multi_entity_relation
```

**严禁继续把 `single_entity` 当作 Evidence Intent。**

---

# 6. Answer Intent 规范化

## 6.1 优先级

`answer_intent` 的来源优先级：

```text
1. 用户原问题中明确表达的意图
2. Stage-1 resolved_question 中明确表达的意图
3. multi_entity_relation 结构任务
4. Clarification-only 的纯实体选择 → general_qa
5. 最终 fallback → general_qa
```

不得使用：

```text
Controller 后续生成的 retrieval query
```

来修改 `answer_intent`。

---

## 6.2 intent_source

Trace 至少区分：

```text
explicit_user
stage1_resolved
clarification_default
structural_relation
fallback
```

用于事故排查。

---

# 7. ASCII Token Boundary 修复

## 7.1 目标

以下必须为 False：

```text
is_exact_parameter_query("pipeline")
is_exact_parameter_query("PipelineWebRTC")
is_exact_parameter_query("shipping")
```

以下必须为 True：

```text
IP
IP 地址
ip地址
ip=192.168.1.1
URL
url地址
port 8080
端口
参数
路径
```

---

## 7.2 实现原则

中文词可继续按中文子串处理。

ASCII 短 token 必须使用 ASCII token boundary，而不是 Python 裸 `in`：

```text
(?<![A-Za-z0-9_])ip(?![A-Za-z0-9_])
```

同类规则适用于：

```text
ip
url
port
```

不得加入：

```python
if query == "pipeline":
    ...
```

不得为 PipelineWebRTC 建特例。

---

## 7.3 归属位置

通用 Query Surface / Intent 解析逻辑不应长期放在 `relation_policy.py`。

优先收敛至：

```text
rag_knowledge/services/query_surface.py
```

由：

```text
DialogueUnderstanding
Graph Admission fallback
其他 query intent consumers
```

共用。

`relation_policy.py` 只负责 Relation Policy，不重新实现通用 Query Intent Parser。

---

# 8. Clarification 收敛规则

修改 `collapse_clarification_selection()`。

## 8.1 纯实体澄清

输入：

```text
question = "pipeline"
selected = "PipelineWebRTC"
```

输出必须：

```text
resolved_question = "PipelineWebRTC 的相关信息"
primary_entity = PipelineWebRTC
task_type = single_entity
answer_intent = general_qa
requested_facets = []
```

---

## 8.2 保留原明确意图

输入：

```text
question = "pipeline 怎么部署"
selected = "PipelineWebRTC"
```

输出：

```text
resolved_question = "PipelineWebRTC 怎么部署"
answer_intent = deployment
requested_facets = [deployment]
```

不能退化成：

```text
PipelineWebRTC 的相关信息
```

---

# 9. RetrievalRequest 与 Admission Question 彻底解耦

## 9.1 RetrievalRequest

Controller 的工具参数继续允许：

```json
{
  "query": "PipelineWebRTC 功能与用途概述",
  "intent": "conceptual_overview",
  "mode": "hybrid"
}
```

但这里的 `intent` 只表示：

```text
retrieval strategy hint
```

不是：

```text
answer_intent
```

建议在内部变量和 Trace 中明确命名：

```text
retrieval_intent
```

避免继续和 Semantic Task 的 `answer_intent` 混淆。

---

## 9.2 Text Admission 调用边界（已 superseded 的旧 Pipeline 表述已移除）

目标调用关系：

```python
pipeline.generate(
    retrieval_query,
    ...
)

TextEvidenceAdmissionService(...).qualify(
    candidate,
    semantic_task=conv.semantic_task,
    target_entity=target,
    ...
)
```

而不是：

```python
TextEvidenceAdmissionService(...).qualify(
    candidate,
    candidate,
    ...
)
```

`generate()` 可以看到 Retrieval Query。

`admit()` 的语义权威只能来自 `SemanticTaskContext`。

---

# 10. Text Candidate Admission 重构

## 10.1 新职责

Text Admission 判断：

```text
该 chunk 是否能支持 Canonical Semantic Task 中至少一个合法事实维度？
```

不判断：

```text
是否已经足够完整回答整个问题
```

后者属于 Coverage。

---

## 10.2 general_qa 规则

当：

```text
answer_intent = general_qa
primary_entity = PipelineWebRTC
```

且 Candidate：

```text
PipelineWebRTC 上传到 /data/html 目录
```

如果：

```text
entity_relevance = HIGH
事实内容明确
无 sibling conflict
无 hard boundary 违规
```

则允许：

```text
intent_relevance = HIGH
Admission = PASS
```

因为用户只要求“PipelineWebRTC 的相关信息”，部署事实本身就是合法相关事实。

---

## 10.3 显式功能问题仍保持严格

用户明确问：

```text
PipelineWebRTC 的主要功能是什么？
```

此时：

```text
answer_intent = definition
requested_facets = [function]
```

Candidate：

```text
PipelineWebRTC 上传到 /data/html
```

必须继续：

```text
entity_relevance = HIGH
intent_relevance = LOW
Admission = REJECT
```

本 PRD **不是放宽所有 Admission**。

它修复的是：

```text
不要让 Retrieval Query 凭空制造“显式功能问题”
```

---

## 10.4 Semantic Helper 边界

Helper Admission payload 必须包含：

```text
canonical resolved_question
answer_intent
requested_facets
primary_entity
candidate_text
candidate_provenance
```

Retrieval Query 可以作为 debug 字段存在，但 Prompt 必须明确：

```text
retrieval_query 仅用于说明 Candidate 如何被找到，不得作为 Evidence 语义权威。
```

Helper 不得：

```text
改写主体
扩大 answer_intent
绕过 hard policy
```

---

# 11. Graph Relation Admission 重构

## 11.1 删除 raw-question intent authority

废止：

```python
_normalized_evidence_intent(question, task_type)
```

作为主路径。

Graph Admission 目标接口：

```python
admit_relation(
    candidate,
    *,
    semantic_task,
    working_set,
    target_entities,
)
```

内部：

```text
policy_intent = semantic_task.answer_intent
canonical_question = semantic_task.resolved_question
```

---

## 11.2 Relation Policy 仍是第一硬权威

第一层：

```python
is_answer_evidence_relation(
    relation_type,
    semantic_task.answer_intent,
)
```

返回 False：

```text
直接 REJECT
Helper 无权翻案
关键词无权翻案
```

返回 True 后，才判断 Query-level relevance。

---

## 11.3 删除重复的 exact-parameter belongs_to 特判

当前存在：

```text
is_exact_parameter
+ belongs_to
→ reject
```

当 `answer_intent` 已经 canonical 化后，这一层不再应该重新猜。

例如：

```text
answer_intent = config
```

`RelationRule.evidence_intents` 本身就会决定 `belongs_to` 是否允许。

政策只保留一个 Authority。

---

## 11.4 general_qa 下结构关系可以成为部分事实

Canonical Task：

```text
PipelineWebRTC 的相关信息
answer_intent = general_qa
```

Relation：

```text
PipelineWebRTC -[belongs_to]-> WebRTC
```

若：

```text
approved
relation policy allows general_qa
primary endpoint aligned
```

则：

```text
PASS
```

它能证明：

```text
PipelineWebRTC 在图谱中归属于 WebRTC 体系
```

不能证明：

```text
PipelineWebRTC 的完整功能
```

后者由 Coverage / Grounding 约束。

---

# 12. EvidencePool 语义

EvidencePool 只保存：

```text
Text Admission PASS
Graph Relation Admission PASS
```

但允许不同 Evidence 支持不同事实维度。

例如：

```text
E1 relation:
PipelineWebRTC belongs_to WebRTC

E2 text:
PipelineWebRTC 上传到 /data/html

E3 text:
外网部署时需要修改 IP 配置
```

它们都可以进入同一 Query-scoped EvidencePool。

不能因为没有：

```text
“主要功能”
```

就把 E1/E2/E3 从 EvidencePool 删除。

---

# 13. Coverage 统一为唯一三态

全链统一：

```text
FULL
PARTIAL
NONE
```

删除：

```text
SUFFICIENT
```

作为运行时 Coverage 状态。

若有历史兼容读取，只能在边界做一次映射：

```text
SUFFICIENT → FULL
```

内部不得继续双写。

---

## 13.1 FULL

定义：

```text
Evidence 已覆盖用户显式要求的全部核心事实维度，且满足必需关系/实体约束。
```

---

## 13.2 PARTIAL

定义：

```text
至少存在一个与 Canonical Task 直接相关、可引用、Admission PASS 的事实，
但仍有一个或多个用户要求的重要维度缺失。
```

典型：

```text
用户问 PipelineWebRTC 相关信息

已有：
belongs_to WebRTC
部署目录
IP 配置

缺少：
完整功能定义
```

结果：

```text
PARTIAL
```

---

## 13.3 NONE

仅当：

```text
EvidencePool 中没有任何 Canonical Task 可用 Evidence
```

才能返回。

---

# 14. Coverage 不再依赖任意文档数量阈值

当前 `_coverage_verdict()` 存在类似：

```text
non_relation_docs < 2
→ PARTIAL
```

文档数量不是事实覆盖度。

禁止继续使用：

```text
chunk 数量
relation 数量
```

直接代表 Coverage。

Coverage 应基于：

```text
SemanticTask.requested_facets
multi_entity required entities / relations
Evidence supported facets
```

对于开放式：

```text
answer_intent = general_qa
requested_facets = []
```

建议安全策略：

```text
有合法事实 → PARTIAL
完全无证据 → NONE
```

除非 Stage-1 已把问题收敛成一个可闭合的明确 intent，否则不要把“相关信息”轻易标成 FULL。

---

# 15. Finalization 行为

## 15.1 PARTIAL 可以合法发布

当：

```text
admissibility = VALID
coverage = PARTIAL
```

且：

```text
补检预算耗尽
或 Main 判断继续补检预期收益不足
```

允许：

```text
finalize(answer_mode="partial")
```

进入 Answer Generator。

---

## 15.2 NONE 才进入 no_knowledge

```text
coverage = NONE
```

才能发布：

```text
知识库未查询到相关内容
```

`PARTIAL` 不允许映射成 `no_knowledge`。

---

# 16. Answer Generator 规则

Answer Context 必须包含：

```text
original_question
resolved_question
SemanticTask.answer_intent
requested_facets
coverage
missing_facts
Evidence Snapshot
```

PARTIAL 时明确要求：

```text
1. 先回答证据已经确认的事实；
2. 不把缺失维度补成模型常识；
3. 最后明确说明当前资料缺什么。
```

禁止：

```text
有部分证据时整句回答“没有相关内容”
```

---

# 17. Grounding Reviewer 规则

Reviewer 仍遵守已有 Helper-authoritative 协议。

但必须统一：

```text
Question = Canonical resolved_question
Coverage Contract = SemanticTaskContext
Evidence = Frozen Snapshot
Candidate = Answer Generator 输出
```

不得拿：

```text
Controller retrieval query
```

做 Reviewer Question。

Coverage 语义继续保持：

```text
FULL / PARTIAL / NONE
```

且：

```text
coverage 衡量 Evidence 对 Question 的覆盖
verdict 衡量 Candidate 是否被 Evidence 支撑
```

二者不混淆。

---

# 18. Trace / 可观测性要求

每条 Query 至少可看到：

```text
original_question
resolved_question
primary_entity
task_type
answer_intent
requested_facets
intent_source
```

每次 Retrieval：

```text
retrieval_query
retrieval_intent
mode
gap
expected_gain
```

每次 Text Admission：

```text
canonical_question
answer_intent
candidate_chunk_id
candidate_source_generators
entity_relevance
intent_relevance
verdict
reason
```

每次 Relation Admission：

```text
canonical_question
answer_intent
relation_id
relation_type
policy_allowed
verdict
reason
```

Finalization：

```text
admissibility
coverage = FULL|PARTIAL|NONE
missing_facts
missing_relations
evidence_count
answer_mode
```

必须能够从 Trace 一眼证明：

```text
Controller 搜索了“功能与用途”
但 Admission 仍然依据“PipelineWebRTC 的相关信息”
```

这不是异常，而是正确分层。

---

# 19. 代码改造范围

## 19.1 `rag_knowledge/services/dialogue_understanding.py`

职责：

```text
扩展 SemanticTaskContext
产生 answer_intent / requested_facets / intent_source
Clarification 后保留原明确意图
纯实体 Clarification 默认 general_qa
```

重点：

```text
build_semantic_task_context()
collapse_clarification_selection()
SemanticTaskContext.from_dict()
SemanticTaskContext.to_dict()
```

---

## 19.2 `rag_knowledge/services/query_surface.py`

职责：

```text
统一 Query token boundary
通用 intent surface 判断
```

增加或集中：

```text
ASCII boundary matcher
is_exact_parameter_query()
infer_answer_intent()
```

禁止多个模块分别维护 `ip/url/port` 判断。

---

## 19.3 `rag_knowledge/services/relation_policy.py`

职责：

```text
只维护 relation 的稳定 deterministic policy
```

保留：

```text
RELATION_RULES
RelationRule.evidence_intents
is_answer_evidence_relation()
relation_query_terms()
```

移除或降级：

```text
通用 Query intent parser 权威
```

---

## 19.4 `rag_knowledge/services/agent_orchestration/graph_admission.py`

职责：

```text
只根据 Canonical Semantic Task + Relation Policy 做 Relation Admission
```

删除：

```text
raw question 重新归一化意图
重复 exact parameter policy
```

---

## 19.5 `rag_knowledge/services/agent_candidate_pipeline.py`

职责：

```text
Candidate Generation 继续吃 retrieval_query
Admission 改吃 semantic_task
```

保留：

```text
显式功能问题 + deployment-only → REJECT
```

新增：

```text
general_qa + direct target factual deployment chunk → PASS
```

---

## 19.6 `rag_knowledge/services/rag.py`

这是最关键的 plumbing 收口点。

必须保证：

```text
handle_retrieve.query
→ generate / rerank

conv.semantic_task
→ text admission
→ helper semantic admission
→ graph admission
```

所有 `graph_admission_service.admit_relation()` 调用点统一改造。

所有旧 Candidate Pipeline Admission 调用点统一改为
`TextEvidenceAdmissionService.qualify()`。

禁止只修一个入口。

---

## 19.7 `rag_knowledge/services/agent_orchestration/runtime.py`

职责：

```text
Finalization coverage 统一为 FULL/PARTIAL/NONE
Coverage 基于 semantic task，而不是 retrieval query
删除 SUFFICIENT 主协议
删除文档数量 = coverage 的隐含假设
PARTIAL 合法 finalize
```

---

## 19.8 `rag_knowledge/services/helper_grounding_reviewer.py`

确认：

```text
输入 question 必须是 resolved_question
coverage 只有 FULL/PARTIAL/NONE
```

本轮不重写 Reviewer 主协议。

---

## 19.9 `rag_knowledge/services/answer_finalizer.py`

确认：

```text
PARTIAL + PASS → grounded_partial
NONE → no_knowledge / NO_SAFE_ANSWER
```

不得把 PARTIAL 归零。

---

# 20. 实施 Phase

## Phase 0：冻结事故基线

保存以下真实事故：

```text
Trace:
41fef9c0bc444239b692300583a521e1

原始输入:
pipeline

澄清:
PipelineWebRTC

已知 Graph:
PipelineWebRTC -[belongs_to]-> WebRTC

已知 Text:
PipelineWebRTC 部署 /data/html / IP 配置

错误结果:
Evidence = 0
no_knowledge
```

同时冻结当前非集成测试 baseline。

当前已知最近结果：

```text
1325 passed
21 deselected
```

实施前重新执行并记录真实 baseline；如果工作区已有并发修改，先区分与本 PRD 无关的变化。

---

## Phase 1：SemanticTaskContext 扩展

完成：

```text
answer_intent
requested_facets
intent_source
```

并完成 Clarification 语义保留。

### 验收

```text
pipeline + PipelineWebRTC selection
→ general_qa

pipeline 怎么部署 + PipelineWebRTC selection
→ deployment
```

---

## Phase 2：Query Surface Token Boundary

统一 ASCII token matcher。

### 验收

```text
pipeline → not exact_parameter
PipelineWebRTC → not exact_parameter
IP 地址 → exact_parameter
url地址 → exact_parameter
port 8080 → exact_parameter
```

---

## Phase 3：Text Admission 与 Retrieval Query 解耦

完成：

```text
generate(retrieval_query)
admit(semantic_task)
```

并修改 Helper payload。

### 验收

Controller 即使检索：

```text
PipelineWebRTC 功能与用途概述
```

Semantic Task 如果是：

```text
PipelineWebRTC 的相关信息
```

则部署 chunk 仍可 PASS。

---

## Phase 4：Graph Admission 语义权威收敛

完成：

```text
RelationRule.evidence_intents
+ SemanticTask.answer_intent
```

成为唯一 Policy 路径。

删除 `_normalized_evidence_intent(raw_question, task_type)` 主路径。

### 验收

```text
pipeline 澄清到 PipelineWebRTC
→ belongs_to 不再因 ip 子串被拒绝
```

---

## Phase 5：Coverage 单一协议

统一：

```text
FULL / PARTIAL / NONE
```

移除内部 `SUFFICIENT`。

Coverage 改为 semantic-task-aware。

### 验收

```text
有 belongs_to + deployment facts
缺完整功能
→ PARTIAL
```

不是：

```text
NONE
```

---

## Phase 6：Answer / Reviewer 对齐

确保：

```text
Answer
Reviewer
Finalizer
```

全部使用同一个 resolved_question / answer_intent / coverage vocabulary。

---

## Phase 7：专项回归

先跑本 PRD 专项测试。

全部通过后，再跑相关 Graph / Candidate / Controller / Grounding 测试。

---

## Phase 8：全量非集成测试

执行：

```powershell
.\venv\Scripts\python.exe -m pytest
```

要求：

```text
0 failed
```

旧测试如依赖：

```text
retrieval query = admission question
raw pipeline → config
SUFFICIENT coverage
```

必须迁移到新语义，不得倒逼恢复旧架构。

---

## Phase 9：真实模型与 HTTP/SSE 验收

使用当前正式本地模型配置执行。

必须重新启动实际后端，确保：

```text
candidate_pipeline_v2 = true
```

不能使用 09:04 那条 `candidate_pipeline_v2=false` 的旧 Trace 作为新实现验收。

---

# 21. 单元测试清单

至少新增以下测试。

## 21.1 Query Surface

```text
test_exact_parameter_ascii_term_requires_boundary
test_pipeline_does_not_match_ip_parameter
test_pipelinewebrtc_does_not_match_ip_parameter
test_ip_address_matches_exact_parameter
test_url_chinese_suffix_matches_exact_parameter
test_port_numeric_query_matches_exact_parameter
```

---

## 21.2 Semantic Task

```text
test_clarification_only_selection_defaults_to_general_qa
test_clarification_preserves_explicit_deployment_intent
test_clarification_preserves_explicit_config_intent
test_task_type_and_answer_intent_are_independent
```

---

## 21.3 Text Admission

```text
test_general_qa_admits_direct_entity_deployment_fact
test_explicit_function_question_rejects_deployment_only_fact
test_retrieval_query_does_not_override_semantic_answer_intent
test_semantic_helper_receives_canonical_question
test_wrong_sibling_still_rejected_under_general_qa
```

---

## 21.4 Graph Admission

```text
test_graph_admission_uses_semantic_answer_intent
test_pipeline_raw_text_cannot_reclassify_graph_intent_after_clarification
test_belongs_to_passes_for_generic_entity_info
test_belongs_to_rejects_when_policy_disallows_config
test_helper_cannot_override_relation_policy_reject
```

---

## 21.5 Coverage

```text
test_coverage_enum_is_full_partial_none_only
test_partial_when_valid_facts_exist_but_requested_facets_missing
test_none_only_when_no_admitted_evidence
test_general_qa_with_valid_facts_is_not_none
test_no_knowledge_not_selected_for_partial
test_partial_can_finalize_when_requested
```

---

# 22. Deterministic Integration

## Case A：本事故核心

输入：

```text
pipeline
→ clarify
→ PipelineWebRTC
```

知识：

```text
PipelineWebRTC belongs_to WebRTC
PipelineWebRTC 上传到 /data/html
外网部署涉及 IP 配置
```

Controller 故意生成：

```text
PipelineWebRTC 功能与用途概述
```

期望：

```text
SemanticTask.answer_intent = general_qa
Graph belongs_to = PASS
至少一个 PipelineWebRTC deployment text = PASS
EvidencePool > 0
Coverage = PARTIAL
```

最终不得：

```text
no_knowledge
```

---

## Case B：显式功能问题

输入：

```text
PipelineWebRTC 的主要功能是什么？
```

仅有：

```text
deployment chunk
belongs_to relation
```

期望：

```text
deployment text 对 function intent 不得伪装 PASS
belongs_to 只能支持归属事实
Coverage = PARTIAL 或 NONE（取决于可发布 relation evidence）
不得编造功能
```

允许回答：

```text
目前只能确认它归属于 WebRTC 体系；现有证据不足以说明主要功能。
```

---

## Case C：显式配置问题

输入：

```text
PipelineWebRTC 的 IP 地址怎么配置？
```

期望：

```text
answer_intent = config
config text PASS
belongs_to 按 policy REJECT
```

---

## Case D：污染回归

输入：

```text
PipelineWebGL
```

候选：

```text
PipelineBuilder
```

期望：

```text
wrong sibling 不进入 EvidencePool
```

本 PRD 不允许以“支持 partial”为理由放宽 Identity 污染。

---

# 23. 真实模型 Micro-chain

至少执行：

## Micro 1

```text
pipeline
→ clarification PipelineWebRTC
→ Controller overview retrieval query
→ Text Admission uses general_qa semantic task
```

检查：

```text
retrieval query 与 admission question 可以不同
```

---

## Micro 2

```text
GraphRelationAdmission
PipelineWebRTC belongs_to WebRTC
```

检查：

```text
policy_intent = general_qa
PASS
不出现 config 误判
```

---

## Micro 3

```text
PARTIAL Evidence
→ Answer
→ Reviewer PASS/PARTIAL
→ grounded_partial
```

至少连续运行 3 次，确认不是偶发成功。

---

# 24. HTTP/SSE ↔ Trace 真实 E2E

完整复现：

```text
POST /api/query/stream
question = pipeline
```

用户选择：

```text
PipelineWebRTC
```

验收 Trace 必须包含：

```text
candidate_pipeline_v2 = true
confirmed_entity = PipelineWebRTC
resolved_question = PipelineWebRTC 的相关信息
answer_intent = general_qa
```

允许 Controller Retrieval Query：

```text
PipelineWebRTC 概览
PipelineWebRTC 功能与用途概述
```

但 Admission Trace 必须显示：

```text
canonical_question = PipelineWebRTC 的相关信息
answer_intent = general_qa
```

至少：

```text
Graph relation PASS >= 1
或合法 text Evidence PASS >= 1
```

在当前已知知识下，预期：

```text
EvidencePool > 0
Coverage = PARTIAL
final_mode = grounded_partial
```

最终回答必须：

```text
包含已确认事实
明确资料缺口
不编造完整功能
```

最终回答不得：

```text
知识库未查询到相关内容
```

除非验收时正式数据已经变化，且 Trace 能证明所有相关 Evidence 的确不存在。

---

# 25. 浏览器 UX 验收

人工检查：

```text
1. 用户看到正常 Clarification。
2. 选择 PipelineWebRTC 后不再次重复澄清。
3. Agent 可以继续自由生成不同 Retrieval Query。
4. 最终能够发布部分答案。
5. Sources 中 Graph relation / text citation 可正常显示。
6. 页面不显示“有证据但 no_knowledge”的矛盾状态。
7. SSE 与 Trace 的 coverage / final_mode 一致。
```

---

# 26. 评测指标

## 26.1 Semantic Authority Drift

定义：

```text
Admission 使用的 semantic context
是否与当前 SemanticTaskContext 一致
```

目标：

```text
0 drift
```

---

## 26.2 Exact Parameter False Positive

负样本至少覆盖：

```text
pipeline
PipelineWebRTC
shipping
description
```

目标：

```text
0 false positive
```

---

## 26.3 Wrong Entity Contamination

继续沿用原 Identity/Candidate PRD 指标。

目标：

```text
PipelineWebGL → PipelineBuilder contamination = 0
```

---

## 26.4 Partial Evidence Salvage

构造：

```text
实体相关事实存在
但完整 overview/function 不存在
```

目标：

```text
不得全部降为 NONE
```

---

## 26.5 NO_KNOWLEDGE Precision

所有 `no_knowledge` 样本必须证明：

```text
Admission PASS Evidence = 0
```

不能只是：

```text
Coverage != FULL
```

---

## 26.6 延迟

本 PRD 原则上不新增 LLM 调用。

因此 P95 不应因本 PRD 出现明显上升。

目标：

```text
相对当前 V2 baseline P95 增幅 <= 5%
```

若超过，必须给出阶段耗时 Trace 解释。

---

# 27. 严禁实施方式

## 27.1 禁止 Pipeline 特例

禁止：

```python
if "pipeline" in query:
    skip_ip_match = True
```

---

## 27.2 禁止把 general_qa 变成“所有同实体 chunk 全 PASS”

仍然必须保留：

```text
Hard Boundary
Structural Guard
Entity Relevance
Semantic Admission
Grounding
```

---

## 27.3 禁止让 Helper 推翻 Relation Policy

```text
Policy REJECT
→ Helper PASS
```

绝对禁止。

---

## 27.4 禁止新增第二套 Semantic Contract

不新增：

```text
EvidenceContract
AdmissionContract
CanonicalQuestionState
```

与 `SemanticTaskContext` 并列承担同一事实。

如果实现确实需要新对象，必须先证明 `SemanticTaskContext` 无法承担，而不是为了局部方便复制状态。

---

## 27.5 禁止保留 SUFFICIENT/FULL 双写

内部协议只允许：

```text
FULL / PARTIAL / NONE
```

---

## 27.6 禁止通过恢复旧测试让全量变绿

旧测试如果断言：

```text
pipeline → exact_parameter
retrieval query → admission question
SUFFICIENT
```

应迁移旧测试。

不得把代码改回错误语义。

---

# 28. Legacy Cleanup

新链测试通过后删除：

```text
Graph Admission raw-question intent normalization 主路径
重复 exact-parameter relation 特判
SUFFICIENT runtime coverage
以 retrieval query 作为 Text Admission question 的调用方式
无效旧测试 fixture / assertion
```

不得留下：

```text
new path + legacy fallback
```

长期双轨。

---

# 29. Definition of Done

以下全部满足才允许标记“已完成”。

## 架构 DoD

- [ ] `SemanticTaskContext` 是 Clarification 后唯一 Canonical Semantic Authority。
- [ ] `task_type` 与 `answer_intent` 职责彻底分离。
- [ ] Clarification-only 选择实体不会凭空制造 conceptual overview / function intent。
- [ ] 原问题已有明确 intent 时，Clarification 后仍保留该 intent。
- [ ] Retrieval Query 只影响 Candidate Generation / Retrieval Strategy。
- [ ] Text Admission 不再使用 Controller Retrieval Query 作为问题权威。
- [ ] Graph Admission 不再使用 raw user_question 重新解释已澄清 intent。
- [ ] `RelationRule.evidence_intents` 仍是 Graph Relation Evidence Policy Authority。
- [ ] ASCII `ip/url/port` 使用 token boundary，不使用裸子串。
- [ ] `pipeline` / `PipelineWebRTC` 不再误命中 `ip`。
- [ ] general_qa 可以保留直接相关的部分事实。
- [ ] 显式 function/config/deployment 等问题仍保持 intent-specific Admission。
- [ ] Wrong Entity / sibling contamination 防线不退化。
- [ ] Coverage 全链只存在 `FULL/PARTIAL/NONE`。
- [ ] `SUFFICIENT` 不再作为内部主状态。
- [ ] Evidence Admission 与 Coverage 完全分离。
- [ ] `PARTIAL` 不会自动变成 `no_knowledge`。
- [ ] `NONE` 才允许 `no_knowledge`。
- [ ] Answer 与 Reviewer 都使用 canonical resolved_question。
- [ ] Grounding Reviewer 不受 Retrieval Query 语义污染。

## 测试 DoD

- [ ] Query Surface 专项测试全绿。
- [ ] Semantic Task / Clarification 专项测试全绿。
- [ ] Candidate Admission 专项测试全绿。
- [ ] Graph Admission 专项测试全绿。
- [ ] Coverage / Finalization 专项测试全绿。
- [ ] PipelineWebRTC 事故 deterministic regression 通过。
- [ ] PipelineWebGL → PipelineBuilder 污染回归通过。
- [ ] 全量非集成测试 0 failed。
- [ ] 真实模型 Micro-chain 连续 3 次通过。
- [ ] HTTP/SSE ↔ Trace E2E 通过。
- [ ] 浏览器人工 UX 验收通过。

## 事故最终 DoD

在正式 V2 链路重新执行：

```text
pipeline
→ PipelineWebRTC
```

必须满足：

```text
candidate_pipeline_v2 = true
Identity = PipelineWebRTC
SemanticTask.answer_intent = general_qa
pipeline != ip
Graph belongs_to 不因 config 误判被拒绝
合法 PipelineWebRTC 部分事实可进入 EvidencePool
Coverage != NONE（在当前正式知识数据未变化的前提下）
final_mode != no_knowledge
```

且回答不得声称知识库中并不存在的 PipelineWebRTC 完整功能。

---

# 30. 最终验收判据

本 PRD 真正完成的标准不是：

```text
把 PipelineWebRTC 这一个问题答出来
```

而是任何类似场景都满足：

```text
用户真实语义
只有一个 Canonical Authority

Search Plan
只能决定去哪里找
不能决定什么是真相

Admission
只决定一条证据能证明什么

Coverage
决定证据整体够不够

Grounding
决定最终 Claim 有没有证据
```

最终架构必须能稳定表达：

```text
“我确实找到了一些关于它的可靠事实，
这些事实足以回答一部分，
但不足以回答全部。”
```

而不是继续在：

```text
全答
或
完全没知识
```

两个极端之间摆动。

这才是本事故的根因级修复。
