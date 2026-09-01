# 对话 Agent 检索编排 · Harness 边界重划执行 PRD（V2.1）

| 项目 | 内容 |
|:---|:---|
| **文档版本** | **V2.1 · Harness 边界重划执行 PRD** |
| **基线日期** | 2026-09-01 |
| **继承基线** | `对话Agent检索编排_PRD_V1.6.md`（动态证据探索终态版，实施中）；8·27 三份收口 PRD（已归档，验收 Commit `6a05e62`）；V2.0 架构 PRD（本版已并入） |
| **核心架构定位** | **Identity Anchor 提供方向不设轨道 + Agentic Retrieval 允许失败/改写/重探索 + Working/Citable 二层证据协议 + Claim-level Grounding 最终把关** |
| **核心修正** | **不把「防漂移」提前到检索 ACL；允许 Agent 提出错误假设、搜索错误方向、用 Observation 自我纠正。Harness 只防止错误假设被升级为系统事实，Reviewer 防止错误归属被升级为最终答案。** |
| **本版新增** | **① Working Evidence / Citable Evidence 二层协议（§四）；② Phase C0 二层化前置门槛（§十三）；③ RetrievalGrant/ProvenanceGrant 职责重划（§七）；④ 歧义 ≠ 非法身份 边界精确化（§八）；⑤ Reviewer → Agent 反馈回路正式架构（§十二）；⑥ 终态措辞修正（§十八/十九）；⑦ §4.3 Citable Qualification 枚举修正 + §4.3.1 判定协议（评审 P0，必须）；⑧ §4.7 Working Visibility / Compaction 协议（评审 P1，强烈建议）；⑨ §4.8 多实体 attributed_entity_ids 前瞻（非阻塞）；⑩ §4.3.2 Citable Promotion Authority（第二轮评审 P0）；⑪ §8.3 Identity Transition & Evidence Epoch + §8.3.1 自由假设/合法实体 ID 工具协议（第二轮评审 P0/P1）；⑫ §10.1 Retrieval Observation & Accounting（第二轮评审 P1）；⑬ §12.4 Reviewer Feedback Contract Schema（第二轮评审 P0）；⑭ §12.5 Evidence Delta & No-Progress（第二轮评审 P0）；⑮ §12.6 Snapshot V2 Merge & Versioning（第二轮评审 P1）；⑯ §14.1 Terminal / Publication Taxonomy（第二轮评审 P1）** |
| **状态** | **第二轮评审通过（2026-09-01，首轮 P0/P1 + 第二轮 P0×4 / P1×4 生命周期与状态机修订已全部并入）· 可进入实施：Phase A → B → C0 → C1 → C2 → C3 → D → E（C0 为前置门槛，不得跳过）** |

> **本文定位**：本文由 **V2.0 架构 PRD 评审收敛**而成，已从「架构方向」收敛为「可执行 PRD」。V2.0 原文并入本版（不再单独保留）。评审通过后即可按 §十三 Phase 顺序实施。
> **阅读前提**：8·27 三份收口 PRD 已把 **Evidence Admission 与 Claim-level Grounding** 两层做成协议并通过 Deterministic Gold（Text Admission 18/18、Grounding Reviewer 50/50、Identity/Clarification 37/37）。本文不是否定它们，而是在它们的基线上**重划 Agent 探索层与证据引用层的职责边界**。

---

# 一、为什么需要 V2.1

## 1.1 继承基线

V1.6 已经完成两阶段 Agent 架构：

```text
Stage 1：语义任务理解（SemanticTaskContext）+ IdentityScope 身份锁
Stage 2：Agent ReAct 自主执行 + Tool-level ExplorationGrant 动态证据准入
```

8·27 又完成了三层权威收口：

```text
SemanticTaskContext            = 用户语义唯一权威
EntityCandidateResolver / IdentityScope = 身份唯一权威
TextEvidenceAdmissionService.qualify()  = 文本证据准入唯一权威
HelperGroundingReviewer + Claim Support Matrix = Claim ↔ Evidence 归属唯一权威
```

## 1.2 当前结构矛盾：防漂移被提前到「检索前 ACL」

V1.6 的 ExplorationGrant 与 Runtime Harness 仍在 **探测阶段** 用「检索授权」来挡防漂移。当前代码里实际存在的拦截：

| # | 拦截点 | 代码位置 | 语义 |
|:--|:--|:--|:--|
| 1 | `identity_not_confirmed` | `exploration_grant.py` `authorize()`；`runtime.py` `_entity_tool_denial()` | 身份未确认时，带 `target_entity` 的检索一律拒绝 |
| 2 | `different_from` 被排除出图谱探索授权 | `exploration_grant.py` `_GRAPH_GRANT_RELATIONS = SCOPE_TRAVERSAL_RELATIONS - {"different_from"}` | 明确 `different_from` 的兄弟实体无法通过图关系成为合法探索目标 |
| 3 | `broadening_after_target_rejection` / `target_already_rejected` | `runtime.py` `_remember_rejected_target()` | 一个 target 被拒后，本轮后续无 target 的实体工具调用被 BLOCK |
| 4 | `target_not_authorized` | `exploration_grant.py` `authorize()` | 目标必须有直接来源（用户点名/澄清/继承）或图谱关系，否则拒绝授权 |
| 5 | `identity_binding_required_before_retrieval` | `runtime.py` `_unbound_identity_denial()` | ambiguous / unresolved 且需绑定时，先澄清后取证 |

把这些连起来，当前 Agent 的合法路径越来越像：

```text
PipelineWebGL confirmed
    ↓
只能搜某些实体
    ↓
Graph 必须授权
    ↓
target 失败不能 broaden
    ↓
代码决定下一条合法路径
```

这**不是 Agent**，是 **workflow**。Anthropic 对 workflow 与 agent 的区分正中这个问题：workflow 是代码预先规定路径，agent 是模型动态决定自己的过程与工具使用方式；「需要灵活、模型驱动决策」本身就是使用 agent 的理由。OpenAI 的 Agent guardrails 指导同样把「系统级边界（能访问哪些数据源、哪些高危动作不能执行）」与「用 instructions 帮助 Agent 做正确决策」分开，而不是把所有决策都编码成 Guard。

## 1.3 8·27 已经建成的「最终裁决」层

关键事实：**防漂移的最终裁决其实已经存在，并且很强。**

```text
Evidence Admission：TARGET_DIRECT → TARGET_SPECIFIC / PASS
                    RELATED_CONTEXT → CONTEXT_ONLY / PASS
                    CONFLICT / IRRELEVANT → REJECT
Claim Support Matrix：TARGET_ATTRIBUTION 只能引用 TARGET_SPECIFIC；
                      CONTEXTUAL_FACT 可引用 CONTEXT_ONLY / TARGET_SPECIFIC；
                      RELATION_CLAIM 只能引用 RELATION_SPECIFIC
HelperGroundingReviewer：原子 Claim 拆分 + 逐条归属核对 + rewrite_action
```

- `document_entity != target` 已**不再是** hard reject（8·27 §15 废止）；
- `没有 entity_chunk_link` / `没有 graph_path` 已**不再是** hard reject；
- `PipelineBuilder` 证据进入 EvidencePool 后，只能支撑 CONTEXTUAL_FACT（比较/纠偏/上下文），**不能**直接支撑 `PipelineWebGL supports X`；
- 即使 Agent 搜到 `PipelineBuilder` 的 chunk，最终想把它写成 `PipelineWebGL` 的属性，Claim Support Matrix + Reviewer 会把它打回。

**但要指出当前实现的一个关键语义**（这是 V2.1 需要二层化的根因）：

```text
当前 EvidencePool 的语义 = 「当前允许引用的证据」
TextEvidenceAdmissionService.admitted_documents() 对 REJECT 直接过滤 → REJECT 从 Main 的 Observation 里彻底消失
```

也就是说：今天 CONFLICT / IRRELEVANT 的 chunk **不只不能引用，Main 连看都看不到**。这导致「Agent 搜到 PipelineBuilder → 想观察它、拿它纠偏」这条认知路径在代码层是断的。

## 1.4 错位结论

> **最终裁决已经很严了，但探测阶段还在用「检索 ACL」提前挡，且 CONFLICT 证据连「看」都不给 Main 看。**

成熟范式是：**允许 Agent 先搜错，再通过 Observation 自我纠正；防漂移交给 Citable Evidence Qualification 与 Claim-level Grounding 在「证据/答案」两处把门。** 而不是在检索前用 `identity_not_confirmed` / `different_from` 禁搜 / `broadening_after_target_rejection` / `graph_relation_only` 把 Agent 的每一步搜索方向都预先固定，也不是把 CONFLICT chunk 从 Main 的视野里整体抹掉。

解决路径**不是删除 Admission**，而是把「准入」拆成两层：

```text
Working Evidence Admission：归属判定进 Working（供认知更新），不删候选
Citable Evidence Qualification：只有满足 Support Scope 的证据才能进 Citable → Frozen Snapshot
```

这不是新造的「自研方向」，而是把业界已有的成熟模式组合起来：

| 模式 | 参考 |
|:--|:--|
| Agentic RAG（决定是否检索 → 检索 → 评估 → 不合适改写重搜） | LangGraph Agentic RAG |
| Self-RAG（动态决定是否检索，对 passages 与生成自我反思） | arXiv 2310.11511 |
| CRAG（先评价检索质量，再 corrective retrieval） | arXiv 2401.15884 |
| Retrieval / Execution / Output rail 分层 | NVIDIA NeMo Guardrails |
| Claim-level grounding / faithfulness（回答拆原子 Claim 逐条核验） | RAGChecker（arXiv 2408.08067）、NVIDIA RAG Blueprint Response Groundedness |

---

# 二、第一性原则

如果今天从零设计，以下八条铁律必须同时成立。

## P1. Identity Anchor ≠ Retrieval ACL

身份事实（谁是用户说的主体、它与谁是 `different_from`）是**模型决策上下文**，不是**搜索权限清单**。

```text
错误：只有确认了 PipelineWebGL，才能搜任何东西。
正确：确认了 PipelineWebGL，意味着不能把 PipelineWebGL 重新解释成 PipelineBuilder；
     但为了回答「它和 PipelineBuilder 的区别」，可以合法搜 PipelineBuilder。
```

## P2. 防漂移的三个位置必须分离

| 位置 | 职责 | 强度 |
|:--|:--|:--|
| 身份层 | 防止已确认主体被 alias / fuzzy / reranker / graph linker 重新绑定 | **hard**（保持） |
| Working Evidence Admission | 不提前删除可用于认知更新的相关/冲突候选；只做归属标注 | **标注，不删**（本版重划） |
| Citable Evidence Qualification | 只有满足明确 Support Scope 的证据才能进 Citable / Frozen Snapshot | **hard（最终裁决）** |

Harness 只负责第 1 层与第 3 层的**结构合法性**（ID 不可伪造、证据必须来自冻结 Snapshot）；Working 层的语义归属由 Attribution + Main 认知负责。

## P3. 检索允许失败，Observation 是纠偏输入

模型第一次搜索方向错误**不是系统错误**。成熟行为允许：

```text
Target = PipelineWebGL
    ↓
Agent 先搜 PipelineWebGL → 资料不足
    ↓
Agent 推测 PipelineBuilder 名字接近，也许相关
    ↓
搜 PipelineBuilder → Observation：主要是编译/发布工具，且 Graph 明确
    PipelineBuilder different_from PipelineWebGL
    ↓
Agent 认知更新：这不是用户目标，不能把 Builder 的功能给 WebGL
    ↓
换方向 → 搜「PipelineWebGL 三维管线」→ 获得真正相关证据
```

真正要求正确的是**最后的认知更新**，而不是「第一次搜索方向必须由代码证明合法」。

## P4. 不提前删除「错误实体」的 chunk，给 chunk 加 attribution，分层路由

不要因为 `document_entity = PipelineBuilder` 就把 chunk 整体 REJECT 并抹除。让它**进入 Working Evidence**，携带：

```text
evidence_class: CONFLICT / RELATED_CONTEXT / ...
support_scope: NONE / CONTEXT_ONLY / TARGET_SPECIFIC
mentioned_entities / relation_to_subject / direct_attribution / relevance
```

Main 看到「这条资料很相关，但主要主体是 PipelineBuilder」后，可以用来**比较、纠偏、决定下一次检索、识别用户是否叫错名字**，但**它不会进入 Citable EvidencePool**，不能拿来做 `PipelineWebGL supports X`。这正对应 NVIDIA Guardrails「不是所有风险都必须在 Retrieval 前解决」的分层思路。

## P5. 最终把关是 Claim-level Grounding

假设 Main 生成：

```text
C1: PipelineWebGL 支持三维管线浏览。          → 主体 PipelineWebGL + 证据 E7（TARGET_SPECIFIC）→ supported
C2: PipelineWebGL 可以将管线数据编译发布。     → 主体 PipelineBuilder（证据 E12，subject mismatch）→ REVISE
```

Reviewer 不只看「整体是否合理」，而是把回答拆成**原子 Claim**，逐个核对 Claim 归属（TARGET_ATTRIBUTION / CONTEXTUAL_FACT / RELATION_CLAIM）与 Evidence 的 Support Scope。

## P6. Harness 只保留真正适合硬编码的东西

```text
Entity ID 不可伪造                   → 硬限制
Evidence ID / Snapshot 不可伪造      → 硬限制
ACL / KB 权限（kb_name、review_status）→ 硬限制
Tool schema / budget / timeout      → 硬限制
```

其余的「方向纠正」交给 Identity Anchor + Observation + Reviewer。

## P7. 歧义 ≠ 非法身份；身份确认（反问）与检索准入分离

歧义主体的澄清（8·14 反问 PRD 的 J3/家族卡）**仍然保留**——回答的归属需要身份。但「搜索」不应被身份 ACL 锁死：**身份决定「能不能把话说死」，不决定「能不能先查资料」。**

边界精确化见 §八。未绑定时可以先做宽检索、围绕 verified candidates 做 Observation；但**未绑定不得产生最终 answer_subject**，必要时仍须 clarify；用户明确点名的不存在实体（非法身份）**不得静默改绑**。

## P8. 8·27 的硬成果不得回退

- Wrong Entity Contamination（PipelineWebGL → PipelineBuilder）= 0 的目标**不回退**；
- 明确 `different_from` 的兄弟实体 chunk，最终不得被当作 `TARGET_SPECIFIC` 支撑目标属性 Claim——这条**从「检索前禁止搜」变成「Citable/答案处禁止越权」**，但目标不变；
- J3×Pipeline*「非法锚不检索」的产品级保护（点名 D3 不得静默正锚检索）保留在**答案归属策略**层；
- `TextEvidenceAdmissionService.qualify()` 的四类判定（PASS/REJECT + support_scope）**不回退**；本版重划的是 **REJECT 的语义**（从「从视野消失」改为「不入 Citable，仍可入 Working」）与下游路由，不是判定逻辑本身。

---

# 三、目标架构

```text
             Identity Anchor
                   │
             提供方向，不设轨道
                   ↓
             Main Agent
          Plan / Hypothesize
                   ↓
            Broad Retrieval
           （允许多路、允许搜「疑似相关」的实体）
                   ↓
            Working Evidence
               ┌──────────────┐
               │ TARGET_SPECIFIC   │
               │ CONTEXT_ONLY      │   ← 认知更新 / 比较 / 纠偏 / 换方向
               │ CONFLICT          │
               │ IRRELEVANT / LOW  │
               └──────────────┘
                   ↓
            Main Reflect
          ┌────────┴────────┐
          │                 │
      不够/有冲突          足够
          │                 │
      改 query              ↓
      换假设         Citable Qualification
      查相关实体             │
          │         Citable EvidencePool
          │                 ↓
          │         Frozen EvidenceSnapshot
          │                 ↓
          │          Generate Answer
          │                 ↓
          │           Atomic Claims
          │                 ↓
          │          Grounding Reviewer
          │                 │
          │    ┌────────────┴───────────┐
          │    │                        │
          │ subject mismatch       supported
          │ unsupported                │
          │    │                        │
          │ 告诉 Main 具体哪错          │
          │    │                        │
          │ Rewrite / Retrieve          │
          └────┴────────────┬───────────┘
                            ↓
                        Publication
```

一句话：**Agentic RAG + Reflection + Evaluator/Optimizer + Working/Citable 二层证据 + Claim-level Grounding 的组合。** 不是一个新的自研方向，而是把成熟模式组合起来。

---

# 四、Working Evidence / Citable Evidence 二层协议（本版核心）

## 4.1 为什么必须分两层

当前 `EvidencePool` 的语义是「**当前允许引用的证据**」，`admitted_documents()` 对 REJECT 直接过滤，导致：

```text
CONFLICT chunk
到底：
A. 不进入任何 EvidencePool
B. 进入 Working Evidence，但不能进入 Citable Evidence
```

正确答案是 **B**。A 会让 Agent 的「搜错 → 观察 → 纠偏」认知路径在代码层断裂；而如果不分层直接放行（C），错误实体可能进入最终 Answer Snapshot，防漂移回退。因此必须把「准入」拆成两层：**Working（认知层）** 与 **Citable（引用层）**。

## 4.2 二层流水线

```text
RetrievalCandidate
        ↓
Attribution Qualification
（现有 TextEvidenceAdmissionService.qualify()，四类判定不变）
        ↓
WorkingEvidencePool
        │
        ├── TARGET_SPECIFIC
        ├── CONTEXT_ONLY
        ├── CONFLICT
        └── IRRELEVANT / LOW
        ↓
Citable Qualification
（support_scope 契约 + grant/identity/snapshot 结构校验）
        ↓
CitableEvidencePool
        ↓
Frozen EvidenceSnapshot
```

> **Visibility/Compaction 发生位置**：`WorkingEvidencePool → Main Observation` 之间（§4.7）——Working 层保留全部结果，但「Main 看到什么」由展示协议决定（高价值全文、低价值摘要/聚合）。

## 4.3 每层职责与判定

| 层 | 职责 | 对 Main 可见 | 进入判定 | 最终去向 |
|:--|:--|:--|:--|:--|
| **Working Evidence Admission** | 归属标注（evidence_class / support_scope / relation_to_subject / relevance），**不删除候选** | **可见（按 §4.7 展示协议：高价值全文，低价值摘要/聚合；含 CONFLICT / IRRELEVANT）** | 归属判定：TARGET_DIRECT / RELATED_CONTEXT / CONFLICT / IRRELEVANT | Working EvidencePool |
| **Citable Evidence Qualification** | 只有满足明确 Support Scope 的证据才能被 Claim 引用 | 可见（Main 可查看与选用，但**无权决定升级**；eligibility 由 §4.3.1 系统协议自动决定，见 §4.3.2） | **§4.3.1 精确枚举**（`qual.verdict == PASS` 且 `evidence_class ↔ support_scope` 成对匹配；Graph 走 `RELATION_SPECIFIC`）且通过 `grant_id / identity_scope_id` 结构校验 | Citable EvidencePool |
| **Frozen EvidenceSnapshot** | 冻结不可变；ID 不可伪造；Answer 唯一事实来源 | 快照可审计 | CitableEvidencePool 全体（按 grant fingerprint 隔离） | Claim 引用 |

### 4.3.1 Citable Qualification 判定协议（精确枚举，评审 P0）

> **概念澄清**：`TARGET_SPECIFIC / CONTEXT_ONLY / RELATION_SPECIFIC` 是 **`support_scope`**；`TARGET_DIRECT / RELATED_CONTEXT / CONFLICT / IRRELEVANT` 是 **`evidence_class`**。二者不可混写。禁止把 support_scope 值当 evidence_class 枚举使用（如 `if evidence_class in {TARGET_SPECIFIC, CONTEXT_ONLY, RELATION_SPECIFIC}`）——一旦按字面实现，Citable 路由会整体错掉。

**文本证据（Text Citable Qualification）**：

```text
citable(text) =
    qual.verdict == PASS
    && (
        (evidence_class == TARGET_DIRECT   && support_scope == TARGET_SPECIFIC)
        OR
        (evidence_class == RELATED_CONTEXT && support_scope == CONTEXT_ONLY)
    )
    && grant_id / identity_scope_id 结构校验通过
```

**图谱证据（Graph Citable Qualification）**：

```text
citable(graph) =
    source_type == graph_relation
    && support_scope == RELATION_SPECIFIC
    && relation_relevance == DIRECT
    && grant_id / identity_scope_id 结构校验通过
```

**对照：不可 citable（只能存在于 Working，不进 Citable / Frozen Snapshot）**：

```text
evidence_class == CONFLICT     → verdict REJECT
evidence_class == IRRELEVANT   → verdict REJECT
support_scope  == NONE
```

### 4.3.2 Citable Promotion Authority（第二轮评审 P0）

> **双权威修正**：同一张表里写「由 Main Reflect 决定是否升级」，又规定 §4.3.1 是严格确定性判定，二者矛盾。若实现者按「Main 决定升级」写，会让 Main 获得「证据升格权」，等于把刚拿掉的 Harness 语义越权又从证据侧放回来。本版明确：

```text
Main：
  「我觉得 E12 有用」
  ≠
  E12: citable=true
```

正确权威关系（唯一）：

```text
TextEvidenceAdmission / GraphAdmission
        ↓
Citable Qualification（§4.3.1 系统协议）
        ↓
citable = true / false        ← 系统协议决定，无第二权威
```

Main 唯一能做的三件事：

```text
1. 看 Working Evidence（含 CONFLICT / IRRELEVANT，按 §4.7 展示协议）；
2. 决定下一步搜什么 / 够不够 / 是否 finalize；
3. finalize 时指定重点使用 Citable 层中的哪些合法证据（focus）。
```

禁止：

```text
Main 把 citable=false 升格为 true；
Main 把 REJECT（CONFLICT / IRRELEVANT / NONE）改判为 PASS；
任何实现以「Main Reflect 决定升级」为路由入口。
```

约束：Main 的 focus 选择不得改变 §4.3.1 的 eligibility；若 Main 判断「某 CONFLICT 证据其实关键」，唯一合法路径是它作为 Working Observation 触发**重新检索 / 换方向**，而非把它直接改判为 citable。

## 4.4 与现有代码的映射

| 现有代码 | 二层化后 |
|:--|:--|
| `TextEvidenceAdmissionService.qualify()` | **不变**：继续产出 evidence_class + support_scope（PASS/REJECT + scope） |
| `admitted_documents()` 对 REJECT 过滤消失 | **重划**：REJECT 不再从 Main 视野消失；CONFLICT/IRRELEVANT 进 Working，不升级进 Citable |
| `EvidencePool`（当前语义=可引用） | **重划**：语义收窄为 Working 认知层；「可引用」由 Citable 层单独承载 |
| `EvidenceSnapshot`（finalize 冻结） | **不变**：只冻结 Citable 层；Working 层不进入 Snapshot |
| `evidence_gate.py::evaluate_rules()`（grant_id/identity_scope_id/support_scope 校验） | **不变**：作为 Citable Qualification 的结构兜底 |
| `helper_grounding_reviewer.py`（Claim Support Matrix） | **不变**：Reviewer 只消费 Citable（Frozen Snapshot）中的证据 |

## 4.5 二层的「是什么 / 不是什么」约束

```text
CONFLICT：可以存在于 Working Context，不能进入最终 Citable Snapshot。
IRRELEVANT / LOW：可以存在于 Working Context（供识别叫错名/换方向），不能进入 Citable Snapshot。
CONTEXT_ONLY：可进 Citable，只能支撑 CONTEXTUAL_FACT（Claim Support Matrix 保持）。
TARGET_SPECIFIC：可进 Citable，可支撑 TARGET_ATTRIBUTION。
```

> **这条是实施的第一裁决规则**：任何实现若让 CONFLICT / IRRELEVANT 进入 Frozen EvidenceSnapshot，或让 REJECT 直接从 Working 层丢失（回到 A 行为），都不符合本协议。
> **可见 ≠ 全量塞入**：Working 层对 Main 的可见性遵循 §4.7 展示协议——「保留并可审计」与「全文进入 Controller Context」是两件事。

## 4.6 与 8·27 Gold 的兼容性

8·27 Text Admission Gold（18 条）断言的是 `qualify()` 的输出（evidence_class / support_scope / PASS/REJECT 判定），**判定逻辑本版不变**，因此 18 条 Gold 保持回归通过。本版新增的是 **REJECT 后的下游路由**（Working vs Citable），因此：

- 8·27 Gold：**保持回归，不回退**；
- Phase C0 须**新增二层路由 Gold**：`CONFLICT → Working 可见，Citable 不可见`、`CONTEXT_ONLY → Working + Citable 均可见（但只支撑 CONTEXTUAL_FACT）`、`TARGET_SPECIFIC → Working + Citable 均可见`。

## 4.7 Working Evidence Visibility / Compaction 协议（评审 P1）

> 原则不变：**Working 层保留全部 qualification 结果并可审计，不无痕删除。** 但「保留」≠「全文永远塞给 Main」——否则 80 candidates × 2~3 retrieve 会把 Controller Context 淹没在大量 IRRELEVANT 垃圾里，反而降低 Main 判断力。

### 4.7.1 展示优先级

```text
TARGET_SPECIFIC  → 高优先级，全文进 Main Observation
CONTEXT_ONLY     → 高优先级，全文进 Main Observation
CONFLICT         → 高优先级，全文/摘要进 Main Observation（纠偏价值高，必须让 Main 看见）
IRRELEVANT / LOW → 保留 attribution/provenance 与拒绝原因；
                   Observation 只展示 top-N / representative items；
                   其余聚合统计，不进 Main 全文
```

### 4.7.2 Working Evidence Summary 示例

Main（Controller）看到的是**结构化摘要 + 高价值样本**，不是 38 个 chunk 全文：

```text
Working Evidence Summary
TARGET_SPECIFIC: 3
CONTEXT_ONLY:    2
CONFLICT:        2
IRRELEVANT:      31

Conflict observations:
E12 PipelineBuilder
  relation_to_subject=DIFFERENT_ENTITY
  reason=explicit_different_from

E18 管线发布服务
  relation_to_subject=RELATED_ENTITY

Irrelevant remainder:
29 candidates omitted from full context
  （保留 attribution / provenance / 拒绝原因，可审计）
```

### 4.7.3 审计与指标口径

```text
REJECT 从 Main 视野消失率 = 0
重新定义为：
  每个 REJECT 的 qualification 结果必须被 Working 层保留并可审计；
  高价值 REJECT（CONFLICT）必须进入 Main Observation；
  低价值 IRRELEVANT 可以压缩/聚合，但不得被系统无痕删除。
```

## 4.8 多实体引用（`attributed_entity_ids`）——非阻塞前瞻

多实体场景（如 `PipelineWebGL 和 PipelineBuilder 有什么区别？`）下，`answer_subjects = [PipelineWebGL, PipelineBuilder]`。

当前对场景 2 的表述「PipelineBuilder 证据合法进入 Citable 的 `CONTEXTUAL_FACT`」**略保守**：对 PipelineBuilder **自身**的 Claim（`PipelineBuilder supports X`），该证据应可为 `TARGET_ATTRIBUTION`，而不只是 PipelineWebGL 的 `CONTEXTUAL_FACT`。

长期方向（不构成当前开工阻塞项，可在 Phase C0/D 自然实现）：

```text
Citable Evidence 增加可选字段：
  attributed_entity_ids: [ent_pipelinebuilder]

Reviewer 据此判定：
  Claim "PipelineBuilder supports X"
    Evidence.attributed_entity == PipelineBuilder → TARGET_ATTRIBUTION supported
  Claim "PipelineWebGL supports X"
    Evidence.attributed_entity == PipelineBuilder → subject mismatch → REVISE
```

约束：单实体场景（8·27 Gold）行为不变；`attributed_entity_ids` 为可选增强，不得引入第二套判定。

---

# 五、四层成熟范式

## Layer 1 · Identity Anchor（稳定任务状态，不是限制清单）

不要只给 Main：

```text
target_entity = PipelineWebGL
```

要给：

```text
Answer Subject:
  entity_id: ent_xxx
  name: PipelineWebGL
  provenance: user_clarification

Known distinctions:
  PipelineBuilder != PipelineWebGL
  管线发布服务 != PipelineWebGL

Relevant relations:
  PipelineWebGL belongs_to StampTools

Instruction:
  These are identity facts, not search restrictions.
  You may investigate related or conflicting entities.
  Do not silently transfer their properties to PipelineWebGL.
```

关键区别：**Identity Context ≠ Retrieval ACL**。它是模型决策上下文。

## Layer 2 · Agentic Retrieval（允许失败、改写、重探索）

模型流程从：

```text
第一次搜索方向必须由代码证明合法
```

改为：

```text
搜错 → 看结果 → 判断结果不好 → 改 query → 换方向
```

对应「决定是否检索 → Retrieve → 评估 retrieved documents → 不合适 rewrite → 再检索 → 合适 → generate」（LangGraph Agentic RAG）；Self-RAG / CRAG 同理。**完全允许 PipelineBuilder 出现在探索路径中**——只要最后的认知更新正确，且它不进入 Citable 支撑目标属性。

## Layer 3 · Attribution-rich Evidence（不提前删，带归属，分层路由）

Retrieval 返回：

```text
EvidenceCandidate {
    chunk_id: ...
    text: ...
    document_entity: PipelineBuilder
    mentioned_entities: [PipelineWebGL]
    relation_to_answer_subject: DIFFERENT_ENTITY
    direct_attribution: false
    relevance: high
}
```

归属判定后进 Working（Main 可见），Main 用它做比较 / 纠偏 / 决定下一次检索 / 识别用户叫错名字；只有满足 Support Scope 的才升级进 Citable，**不能**直接做 `PipelineWebGL supports X`。NVIDIA Guardrails 的 retrieval rail / execution rail / output rail 分层是同一个思路。

## Layer 4 · Claim-level Grounding（最终把关）

Reviewer 拆原子 Claim，逐个核对：

```text
Claim C1 → Evidence E7 → 主体 PipelineWebGL + 「支持三维管线浏览」→ supported
Claim C2 → Evidence E12 → 主体 PipelineBuilder + 「数据编译发布」→ subject mismatch → REVISE
```

Reviewer **只消费 Frozen EvidenceSnapshot（Citable 层）**。对应 RAGChecker 的 faithfulness 与 NVIDIA RAG Blueprint 的 Response Groundedness（检查 response 中 claim 是否受 retrieved context 支持；self-reflection 同时检查 context relevance 与 response groundedness）。

---

# 六、Harness 边界重划（核心表）

| 当前能力 | 推荐处理 | 说明 |
|:--|:--|:--|
| `identity_not_confirmed → 禁止 retrieve` | **删除 / 弱化** | 身份决定「能不能把话说死」，不决定「能不能先查资料」；歧义主体仍由反问解决归属，但检索不被身份 ACL 锁死 |
| `different_from → 禁止搜 sibling` | **删除** | 允许 Agent 搜到并观察兄弟实体，用它纠偏/比较；越权由 Citable 层与 Grounding 层拦 |
| `different_from → 告诉 Main 两者不同` | **保留并加强** | 作为 Identity Anchor 的 `Known distinctions` 结构化提示，明确「不是别名、证据不自动支持目标属性」 |
| `broadening_after_target_rejection → BLOCK` | **基本删除** | 一次 target 被拒不代表同轮不能换方向；拒绝结果作为 Observation 回给 Main |
| `Graph relation 才允许探索某实体` | **删除** | 图谱关系是**探索线索**，不是**探索许可证**；`target_not_authorized` 不应成为 Agent 换方向的硬闸 |
| Evidence attribution 信息 | **保留并增强** | `evidence_class / support_scope / mentioned_entities / relation_to_subject / direct_attribution` 都要进 Observation（Working） |
| **Working / Citable 分层** | **新增（核心）** | REJECT 不再从 Main 视野消失；CONFLICT/IRRELEVANT 进 Working，不升级进 Citable（§四） |
| Retrieval relevance grader | **保留，最好模型语义判断** | 保留 `intent_relevance`；ambiguous 场景交给 Helper 语义判定而非字符串/元数据硬判 |
| Claim ↔ Evidence grounding | **强化** | 原子 Claim + Claim Support Matrix 已是强项；继续强化「告诉 Main 具体哪错」（RETRIEVAL_GAP → 反馈回 Agent 循环，§十二） |
| Entity ID 不可伪造 | **硬限制** | 保留 Snapshot 校验 / candidate resolver 权威 |
| Evidence ID / Snapshot 不可伪造 | **硬限制** | 保留 Immutable Snapshot、`grant_id` / `identity_scope_id` / `support_scope` 冻结校验 |
| ACL / KB 权限 | **硬限制** | `kb_name`、`review_status=approved` 保留 |
| Tool schema / budget / timeout | **硬限制** | 保留 tool_cycle_detected / retrieve_budget / exploration_fuse 等终止机制 |

一句话工程化版本：

> **不要用 Harness 防止 Agent「想错」；允许它提出错误假设、搜索错误方向并利用 Observation 自我纠正。Harness 只防止错误假设被升级为系统事实，Reviewer 防止错误归属被升级为最终答案。Working 层给 Main 认知自由，Citable 层保持引用纪律。**

---

# 七、RetrievalGrant / ProvenanceGrant 职责重划

## 7.1 现状

`ExplorationGrantResolver` 今天的 `authorize()` 同时承担了两类职责：

```text
① 结构职责：KB ACL、kb_name、review_status、provenance、query scope fingerprint、snapshot 一致性
② 语义许可：这个实体「值不值得搜」、Graph 是否授权它成为搜索目标、它是不是 confirmed Identity
```

PRD 主张的「Graph relation 从探索许可证降级为探索线索」只针对 ②；**不是删除 Grant，而是删除 Grant 的「语义搜索许可权」。**

## 7.2 重划后：负责 / 不负责

```text
RetrievalGrant / ProvenanceGrant

负责：
- KB ACL（kb_name）
- review_status（approved）
- tenant
- provenance（来源链）
- query scope fingerprint（同 query 不同 Identity/Grant 不得串缓存）
- snapshot consistency（Citable 冻结一致性）

不负责：
- 这个实体语义上值不值得搜
- Graph 是否授权它成为搜索目标
- 它是不是 confirmed Identity
```

> 一句话：**Grant 管「能不能访问」，不管「值不值得探索」。** 语义上值不值得搜由 Identity Anchor + Main 判断；是不是合法身份由 IdentityScope 判断；探索目标是否越权引用由 Citable Qualification + Claim Support Matrix 判断。

## 7.3 保留的硬校验（不回退）

```text
grant_id / identity_scope_id / support_scope 冻结校验
query scope fingerprint（Cache 不得串用）
Frozen EvidenceSnapshot 不可变性
```

---

# 八、Identity 边界精确化（歧义 ≠ 非法身份）

> 本版必须写死的边界：**「放宽探索」≠「恢复 fuzzy silent rebind」。** 两件事分得清清楚楚：

## 8.1 合法歧义 vs 非法身份

```text
pipeline（合法歧义表达）
→ 可围绕 verified candidates 做 exploratory retrieval（Working 层）
→ 但不能产生最终 answer_subject
→ 必要时 clarify（J3 / 家族卡保持）
```

```text
用户明确点名一个不存在的 D3（非法身份）
→ 不得静默改绑成 PipelineWebGL
→ 不得伪造 entity_id
→ 不得把某合法实体当作 D3 来检索并最终回答
```

核心区别：**歧义 ≠ 非法身份。**

| 场景 | 探索 | 反问 | answer_subject | 检索最终以谁为准 |
|:--|:--|:--|:--|:--|
| `pipeline`（合法歧义） | 允许（围绕 verified candidates 宽检索） | 必要时 | 未绑定前不得产出 | 澄清确认后 |
| 点名不存在的 D3（非法身份） | 不围绕 D3 伪造检索 | 告知不存在/请重新表述 | 不得产出 | 不得静默改绑合法实体 |

## 8.2 约束

- `answer_subject` 只能来自 IdentityScope 的 `confirmed_entity` / `confirmed_entities`（用户点名、澄清确认、候选解析、继承）；
- `pipeline` 这类合法歧义：Working 检索允许，Citable 引用仍需绑定后的 `identity_scope_id` 才成立（未绑定证据不得进 Frozen Snapshot 支撑 target attribution）；
- 非法身份（D3）：不因「放宽探索」而恢复 fuzzy silent rebind；`hallucinated_candidate` Gold（Identity 37 中的 2 条）保持回归。

## 8.3 Identity Transition & Evidence Epoch（第二轮评审 P0）

> **问题**：V2.1 新增了「pipeline → identity ambiguous → 先做 Working Retrieval → 用户澄清选择 PipelineWebGL」的流程。但这些检索是在 `identity = ambiguous` 语境下 qualification 的。用户澄清后 IdentityScope 已变，旧 Working 证据**不得自动继承 Citable 权限**——否则「搜索阶段悄悄决定证据权威」的回潮。

引入 `evidence_epoch`：

```text
Epoch 1
  identity = ambiguous（pipeline）
  W1 PipelineBuilder / W2 PipelineWebGL / W3 PipelineWebRTC
    （qualification 语境 = ambiguous）

  用户澄清选择 PipelineWebGL
        ↓
Epoch 2
  identity = PipelineWebGL（confirmed）
  Epoch 1 证据 → 状态 = STALE_FOR_CITATION
    （仍保留在 Trace / Working 历史，可审计，不得删除）
  W2 若继续使用：
    必须按 Epoch 2 的 frozen SemanticTaskContext + IdentityScope + answer_subject
    重新 Qualification；
    或按现有更保守策略：澄清回调后直接重新 Retrieval
```

规则：

- Identity 变化产生新 `evidence_epoch`；旧 epoch 证据**不可自动升级**进 Citable；
- `STALE_FOR_CITATION` ≠ 删除：旧证据保留 Working/Trace，只是不再具备「自动进入当前 epoch 的 Citable」的资格；
- 重新 Qualification 必须以**当前 frozen SemanticTaskContext + Identity Anchor** 为语境，**不得**以 Agent 临时写的检索 query 为权威（与 §12.6 同一原则，防止 search plan 反向修改 evidence authority）。

### 8.3.1 自由搜索假设 vs 合法实体事实（工具协议，第二轮评审 P1）

「Agent 可以提出错误假设」与「Entity ID 不可伪造」需要正式的工具字段协议：

```text
search_focus_text     → 自由文本，Agent 可随意填写搜索假设
                         （如 "pipeline 可能是某个部署工具"）
focus_entity_id       → 必须经 Registry 验证；不存在 / 未注册 → 拒绝该字段，不伪造
```

约束：

```text
search_focus_text 只是搜索线索，永远不能自动变成 IdentityBinding；
focus_entity_id 才具有身份语义，只能来自 IdentityScope 的 confirmed / candidate；
自由文本假设与合法实体事实是两层，工具 schema 必须分开，不得混在一个字段里。
```

---

# 九、Identity Anchor 协议（正式协议，不是自然语言提醒）

每轮 Main 都看到**结构化** Identity Anchor，而不是临时 Prompt 提醒：

```text
Identity Anchor
---------------
answer_subject:
  PipelineWebGL
  binding: user_confirmed

known_confusions:
  - PipelineBuilder     relation: different_from
  - 管线发布服务         relation: different_from

Relevant relations:
  - PipelineWebGL belongs_to StampTools

Reminder:
  You may investigate these entities.
  They are not aliases of the answer subject.
  Evidence about them does not automatically support
  claims about PipelineWebGL.
```

约束：

- `answer_subject` 只能来自 IdentityScope 的 `confirmed_entity` / `confirmed_entities`（用户点名、澄清确认、候选解析、继承），不可被模型改写；
- `known_confusions` 来自图谱 approved `different_from`（8·27 §17：这是高价值负向关系）；
- Anchor 只做消歧提示，**明确不是事实来源**（沿用现有 `entity_hint_section` 的「仅用于消歧，不作为事实来源」定位，结构化升级）。

---

# 十、Observation 协议（带归属的 EvidenceCandidate）

Tool 返回不再是「一堆 chunk」或「DENIED」，而是（进入 Working 层，全部可见）：

```text
Observation
-----------
Search: PipelineBuilder

Evidence E12:
  document_entity: PipelineBuilder
  relation_to_subject: DIFFERENT_ENTITY
  evidence_class: CONFLICT
  support_scope: NONE
  relevance_to_question: HIGH
  citable: false          ← 结构路由标记：不进 Citable，仅 Working

Evidence E13:
  document_entity: PipelineBuilder
  mentions: PipelineWebGL
  relation_to_subject: MIXED
  evidence_class: RELATED_CONTEXT
  support_scope: CONTEXT_ONLY
  relevance_to_question: HIGH
  citable: true           ← 可进 Citable，但只支撑 CONTEXTUAL_FACT
```

Main 自己决定：

```text
E12：可以帮助理解区别；不能证明 WebGL（Working 认知用）
E13：需要检查里面具体哪句话描述 WebGL；可引用为 CONTEXTUAL_FACT
```

而不是代码替它直接：

```text
E12 → 直接消失
E13 → 直接消失
```

`CONFLICT` / `CONTEXT_ONLY` 等 Attribution 判定依然存在，作为**信息**给 Main（Working）；同时由 `evaluate_rules()` 做 Citable 结构兜底（`grant_id` / `identity_scope_id` / `support_scope` 合法性校验保持 hard）。

## 10.1 Retrieval Observation & Accounting（第二轮评审 P1）

> **问题**：真实事故是 DENIED 调用污染 retrieve attempt，把「工具没执行」也算进「检索次数被用完」。PRD 只说 budget 是 hard limit，没定义什么才算「执行了一次检索」。

定义检索生命周期计数（每次 retrieve 调用逐项记录）：

```text
retrieval_requested     Agent 发起一次检索请求
guard_rejected          未真正进入 Retriever（schema 非法 / ACL denied / tool 失败）
retrieval_executed       真正进入 Retriever 并发出召回请求
returned_candidates      Retriever 返回的候选数
working_added            进入 Working 层的证据数
citable_added            进入 Citable 层的证据数
gap_support_added        本次补检对当前 gap 新增的支撑数（§12.5）
```

budget 扣减规则：

```text
guard_rejected
  → 可消耗 Agent step
  → 不消耗 executed retrieval attempt

retrieval_executed
  → 消耗 executed retrieval attempt
```

这一区分直接决定：

```text
requested = 4, executed = 2    （一半被 guard 拦下）
≠
executed = 4, returned = 0     （真的执行了四次但无命中）
```

不能因为前者把 Agent 的检索预算耗光；「检索没执行」在 §14.1 中对应 `retrieval_blocked`，在答案层不得伪装成「知识库没有相关内容」。

---

# 十一、Main Reflect 与纠偏路径

Main 的决策循环从「选择合法路径」变为「提出假设 → 检索 → 评估 Observation → 更新认知」：

| 阶段 | 行为 | 系统支持 |
|:--|:--|:--|
| Plan / Hypothesize | 提出可能相关实体与检索方向（含「疑似相关」的兄弟实体） | Identity Anchor 提供方向，不设轨道 |
| Broad Retrieval | 多路检索（vector / bm25 / graph / web），允许搜到 `different_from` 实体 | 不再被 ACL 拦截；仍受 budget / timeout / schema 硬限 |
| Working Evidence | 每条证据带 attribution 进池，全部对 Main 可见 | `evidence_class` / `support_scope` / `citable` 标记 |
| Reflect | 判断「够不够 / 有没有冲突 / 方向对不对」 | Observation 携带归属信息；`gap / expected_gain` 契约保留 |
| 认知更新 | 确认 PipelineBuilder ≠ WebGL，换方向或收口 | `different_from` 作为 `Known distinctions` 提示 |
| Citable 升级 | 只有满足 Support Scope 的证据进 Citable | Citable Qualification + grant/identity/snapshot 结构校验 |
| Generate | 组装答案 + 原子 Claim 引用（只引用 Citable / Frozen Snapshot） | Answer Generator 感知 Support Scope（8·27 §29） |
| Grounding | 逐条核对 | Reviewer + Claim Support Matrix |

---

# 十二、Reviewer → Agent 反馈回路（正式架构）

## 12.1 当前代码流程

```text
AgentLoop
↓
finalize
↓
EvidenceSnapshot 冻结
↓
AgentLoop 已终止
↓
Answer Generator
↓
Reviewer
↓
Grounded Rewrite
↓
Reviewer #2
```

当前 `REVISE` 只能「在快照内改写」，**无法**触发新的检索——因为 AgentLoop 已经终止，Snapshot 已经冻结。

## 12.2 目标架构

想让 `REVISE` 能回到检索，必须**选择正式架构**，而不是让 AnswerFinalizer 偷调 Retrieval：

```text
Agent
↓
Snapshot V1（Citable 冻结）
↓
Answer V1
↓
Reviewer
        │
        ├─ PASS → publish
        │
        ├─ REVISE_REWRITE → grounded rewrite（快照内改写，保持）
        │
        └─ RETRIEVAL_GAP
              ↓
        Feedback Contract
          （missing claim / subject mismatch 的具体 gap）
              ↓
        Agent Resume（Main Controller 重新接管工具决策）
              ↓
        Retrieve（按 gap 定向补检）
              ↓
        Snapshot V2（新增证据并入并重新冻结）
              ↓
        Answer V2 → Reviewer（PASS / 再进入回路，受 budget 硬限）
```

## 12.3 约束（不回退）

1. **Main Controller 是唯一工具决策者**：`RETRIEVAL_GAP` 必须回流给 Agent（`Agent Resume`），**不得**由 AnswerFinalizer / Reviewer 自己调用 Retrieval；
2. `Snapshot V2` 复用 V1 证据时按当前 **frozen SemanticTaskContext + Identity Anchor** 重新 Qualification（8·27 §45 保持）；不得以 Agent 临时检索 query 为 Qualification 权威（§12.6，防止 search plan 反向修改 evidence authority）；
3. 回路受 `retrieve_budget` / `exploration_fuse` / 总轮次硬限约束，禁止无限补检；
4. `REVISE_REWRITE`（快照内改写）与 `RETRIEVAL_GAP`（补检后重答）是两个不同分支，实现不得混用。

## 12.4 Reviewer Feedback Contract Schema（第二轮评审 P0）

> **问题**：§12.2 画出了 `RETRIEVAL_GAP → Feedback Contract → Agent Resume`，但没定义 Reviewer 输出什么字段、哪个 enum 触发补检。实现者很可能把现有 `verdict = REVISE` 直接扩展成 `verdict = RETRIEVAL_GAP`，而现有 Reviewer / Finalizer / Gold / parser 都依赖旧 enum——污染 verdict 语义会连带破坏既有判定。

**不污染原 verdict**，正式定义：

```text
verdict:
  PASS | REVISE | NO_SAFE_ANSWER          ← 原语义保持不变

repair_mode:
  NONE | REWRITE | RETRIEVE                ← 新字段，描述「补修方式」

retrieval_feedback:                        ← 仅当 repair_mode == RETRIEVE 时携带
  gap_id
  affected_claim_ids
  missing_fact
  subject_entity_ids
  deficiency_type                          ← 如 NO_DIRECT_EVIDENCE / SUBJECT_MISMATCH /
                                               CONTEXTUAL_MISSING / GRAPH_EDGE_MISSING
  reason
```

Reviewer **只允许描述缺口**：

```text
✓  「C2 缺 PipelineWebGL 默认端口的直接证据」
✓  「受影响的 claim: C2；目标主体: PipelineWebGL」
✗  「请调用 vector 检索 "PipelineWebGL port"」
✗  指定 tool / query / 检索策略
```

> 后者让 Reviewer 越权成 Controller，违反「Main Controller 唯一工具决策者」。Reviewer 描述「缺什么」，Agent 决定「怎么查」。`RETRIEVAL_GAP` 不是 verdict 值，而是 `verdict=REVISE && repair_mode=RETRIEVE` 的联合语义，供 Agent Resume 触发。

## 12.5 Evidence Delta & No-Progress（第二轮评审 P0）

> **问题**：只有 budget/fuse 防无限循环，没有「当前 gap 没有获得新支撑就终止」的协议。§12.2 直接假设 Retrieve 后就有 Snapshot V2，但补检完全可能一无所获。

定义 delta 与终止协议：

```text
Snapshot V1 → 补检
working_delta      = 本次补检新增进 Working 的证据数
citable_delta      = 本次补检新增进 Citable 的证据数
gap_support_delta  = 本次补检对 retrieval_feedback 的 gap 新增的支撑数
```

```text
gap_support_delta == 0
  → 本次补检对该 gap 没有新支撑
  → 状态 = GAP_NOT_IMPROVED（对同 gap 不重复补检同一方向）
  → 多次同 gap 无改善 → gap_exhausted → 终止该 gap 的补检循环

全部相关 gap 均 exhausted
  → 状态 = NO_PROGRESS
  → 不得再进入下一轮 Agent Resume
  → 进入 Publication 的分类（§14.1），按实际拥有材料如实输出
```

- 允许换方向（不同 gap / 不同 hypothesis）继续补检，受 budget 硬限；
- `NO_PROGRESS / gap_exhausted` 必须可审计（Trace 记录每次补检的 delta）；
- 「无新支撑」≠「重答一遍」：不再重复 Answer 生成。

## 12.6 Snapshot V2 Merge & Versioning（第二轮评审 P1）

> **问题**：「新增证据并入并重新冻结」一句话没说清：同一 chunk 重复命中怎么办、qualification 结果变化怎么办、V1 是否被修改、citation_id 是否重编号。

```text
Snapshot V1 永不修改（immutable）
Snapshot V2 = 新对象
  new snapshot_id
  new evidence_version（= V1.version + 1）

同一物理 chunk 重复命中：
  Working 可以记录多个 retrieval observation（append-only）
  Citable 只保留一份合法事实实例（按稳定 evidence key 去重）

qualification 变化：
  E1 第一次 TARGET_SPECIFIC、第二次因语境不同变 CONTEXT_ONLY
  → Citable 中只保留当前 epoch 下 qualification 对应的实例
  → 不原地改 V1；V2 是新对象

Citation IDs：
  每个 Snapshot 内重新稳定编号（V2 内 [1..n] 与 V1 内 [1..m] 各自独立）
```

重新 Qualification 的权威语境：

```text
按当前 frozen SemanticTaskContext + Identity Anchor 重新 Qualification
✗ 不得按 Agent 临时写的检索 query（如 "PipelineWebGL 功能"）改判
  —— 否则 search plan 会反向修改 evidence authority，正是本版一直在消灭的模式
```

约束：Working observation append-only（可追加、不覆盖）；Citable 按 stable key 去重后仍是单实例；V1 永不变更，任何「原地修改 V1」实现均违规。

---

# 十三、分阶段实施（评审确认后按此顺序）

> 原则：**行为先收权、再放宽，避免「重构 + 放宽」同时发生。** 特别地：**必须先完成 C0 二层化，才能做 C1–C3 的探索解锁**，否则直接放宽会同时踩「Main 看不到错误方向」与「错误实体进 Snapshot」两个坑。

## Phase A：Identity Anchor 结构化（不动检索行为）

- 把现有 `entity_hint_section` 升级为结构化 `Identity Anchor` 协议（`answer_subject / known_confusions / relevant_relations / reminder`）。
- 保留现有检索拦截不动。
- **验收**：纯提示层改动，8·27 全部 Gold 与全量非集成测试 0 failed。

## Phase B：Observation 携带 attribution（不动拦截）

- `retrieve_kb` / `expand_graph_scope` 的 Observation 结构化输出 `document_entity / mentioned_entities / relation_to_subject / evidence_class / support_scope / relevance / citable`。
- 检索计数按 §10.1 拆分（requested / guard_rejected / executed / returned / working_added / citable_added），Trace 可见「真正执行过几次检索」。
- **验收**：Trace 能看到每条证据的 attribution；拦截行为不变。

## Phase C0：WorkingEvidencePool / CitableEvidencePool 二层化（关键前置门槛）

- 把当前 `EvidencePool` 语义收窄为 Working 认知层；新增 Citable 层与 `citable` 路由标记；
- `admitted_documents()` 对 REJECT 的「消失」行为改为「进 Working、不进 Citable」；`qualify()` 四类判定本身不动；
- Frozen EvidenceSnapshot 只冻结 Citable 层；
- **新增二层路由 Gold**（§4.6）：
  - `CONFLICT → Working 可见，Citable 不可见`；
  - `CONTEXT_ONLY → Working + Citable 可见（只支撑 CONTEXTUAL_FACT）`；
  - `TARGET_SPECIFIC → Working + Citable 可见`。
- **验收**：二层 Gold 全过；8·27 Gold（含 Text 18）全量回归；REJECT 从视野消失的旧行为测试**明确更新为新语义**；Citable 层 CONFLICT 进入率 = 0。

## Phase C1：different_from 探索解锁

- `_GRAPH_GRANT_RELATIONS` 不再排除 `different_from`；`different_from` 实体可被探索并在 Working 层观察。
- **A/B**：新行为下 `Wrong Entity Contamination` 仍为 0、`Target Attribution Claim Error Rate` 仍为 0，否则回退。
- **验收**：PipelineWebGL 防漂移 Gold 不回退；`PipelineWebGL 和 PipelineBuilder 有什么区别？` 两实体均可检索（不依赖 comparison regex）。

## Phase C2：target rejection broadening 解锁

- `broadening_after_target_rejection` / `target_already_rejected` 降级：一次 target 被拒作为 Observation 回给 Main，不再 BLOCK 同轮后续探索。
- **A/B**：同上；Multi-entity 探索成功率不下降。
- **验收**：场景 5（一次 target 被拒后换方向）通过；Rejected target 记忆仍用于「防重复无效探索」而非「封死路径」。

## Phase C3：identity_not_confirmed / target_not_authorized 解锁

- `identity_not_confirmed` 拦带 target 检索、`target_not_authorized` 的 graph-relation-only 授权，均降级为决策上下文；
- `identity_binding_required_before_retrieval` 降级：未绑定不再默认禁检索，可围绕 verified candidates 宽检索；
- **同时**按 §八 收紧边界：未绑定不得产出 `answer_subject`；非法身份（D3）不得静默改绑；`hallucinated_candidate` Gold 保持；
- 澄清回调按 §8.3 处理 `evidence_epoch`：身份变化后旧证据 `STALE_FOR_CITATION`，不得自动继承 Citable；重新 Qualification 以 frozen SemanticTaskContext + Identity Anchor 为语境。
- **A/B**：同上；`三维管线管理 / PipelineWebRTC` Recall 不回退。
- **验收**：场景 3 / 7 通过；未绑定证据不得进 Frozen Snapshot 支撑 target attribution。

## Phase D：Reviewer → Agent 反馈回路

- 按 §十二 目标架构实现 `RETRIEVAL_GAP → Feedback Contract → Agent Resume → Snapshot V2 → Answer V2`；`REVISE_REWRITE` 与 `RETRIEVAL_GAP` 分支分离；
- Feedback Contract 按 §12.4 Schema 实现（`repair_mode` / `retrieval_feedback`，Reviewer 只描述缺口、不产出 query/tool）；
- 补检终止按 §12.5 实现（`gap_support_delta` / `gap_exhausted` / `NO_PROGRESS`，禁止无增量循环）；
- Snapshot V2 按 §12.6 实现（V1 immutable / dedup / evidence_version / 按 frozen SemanticTaskContext + Identity Anchor 重新 Qualification）。
- **验收**：REVISE 后一轮内补检成功率显著上升（与无反馈回路基线对比）；Main Controller 唯一工具决策者约束有测试断言（AnswerFinalizer 不得偷调 Retrieval）；gap exhausted 终止路径与 Publication 分类（§14.1）有专项测试。

## Phase E：清理兼容层

- 删除已无用的 `broadening_after_target_rejection` / `target_already_rejected` 等残留语义与旧测试断言；单一行为真源。
- **验收**：`git diff --check` 通过；无旧新双轨长期并存。

---

# 十四、验收场景（评审确认后固化为 Gold）

| # | 场景 | 期望 |
|:--|:--|:--|
| 1 | `PipelineWebGL 怎么配置？` | 身份锁定 PipelineWebGL；PipelineBuilder chunk 若被召回 → Working 可见但 `citable=false`（CONFLICT），不得支撑 WebGL 属性 |
| 2 | `PipelineWebGL 和 PipelineBuilder 有什么区别？` | 两实体均可检索；`answer_subjects=[WebGL, Builder]`：Builder 证据对 Builder 自身 Claim 可作 TARGET_ATTRIBUTION（§4.8），对 WebGL 的 Claim 仍 subject mismatch；不依赖 comparison regex |
| 3 | Agent 认为「PipelineBuilder 可能相关」主动检索 | **允许检索**；Observation 返回 `relation_to_subject=DIFFERENT_ENTITY / CONFLICT / citable=false`；Main 据此纠偏，不污染答案 |
| 4 | Agent 检索 PipelineBuilder 后想写 `PipelineWebGL supports X` | Citable 层该证据不可引用 + Claim Support Matrix + Reviewer 拦截（subject mismatch）→ REVISE |
| 5 | 一次 target 被拒后 Agent 想换方向 | 不再 BLOCK；作为 Observation 继续，Main 自主决定下一步 |
| 6 | `different_from` 实体 | 不作为探索禁搜项；作为 `known_confusions` 提示 + Working 层 CONFLICT 证据（`citable=false`） |
| 7 | 歧义主体（如只写「pipeline」） | 反问仍保留；未绑定可围绕 verified candidates 宽检索，但不得产出最终 answer_subject，未绑定证据不进 Citable |
| 8 | 点名不存在实体（D3） | 不静默改绑、不伪造 entity_id、不按 D3 最终作答；`hallucinated_candidate` Gold 保持 |
| 9 | 上一轮 A，本轮「它呢？」 | Identity 继承 A（保持） |
| 10 | A/B 均有证据但缺关系证据，回答声称 A 依赖 B | Guard 不得把两侧独立证据拼成关系事实（保持） |
| 11 | 相同 query，不同 Identity Anchor / Grant | Cache 不得串用（保持 `grant fingerprint`） |
| 12 | 复用上一轮证据 | 按当前 frozen SemanticTaskContext + Identity Anchor 重新 Qualification（保持；不得以 Agent 临时检索 query 为权威，§12.6） |
| 13 | CONFLICT chunk 路由 | Working 层对 Main 可见（认知更新）；Citable / Frozen Snapshot 层**不得出现**（二层 Gold） |
| 14 | Reviewer 判定 RETRIEVAL_GAP | 触发 Feedback Contract → Agent Resume → 定向补检 → Snapshot V2 → Answer V2（受 budget 硬限）；AnswerFinalizer 不得偷调 Retrieval |
| 15 | Trace | 能解释每个 chunk 为何进 Working、为何升级/不升级进 Citable、每条 Claim 为何 supported / unsupported |
| 16 | 大批量 REJECT（如 80 candidates） | Working 摘要聚合：IRRELEVANT 只展示 top-N + 聚合统计（保留 attribution/provenance 可审计）；CONFLICT 全文/摘要进 Main；Citable 层不出现任何 REJECT |
| 17 | 多实体（A 和 B 区别） | `attributed_entity_ids` 生效：B 证据对 B 自身 Claim 为 TARGET_ATTRIBUTION，对 A 的 Claim 为 subject mismatch（§4.8） |
| 18 | Reviewer 输出 RETRIEVAL_GAP 反馈 | 输出按 §12.4 Schema：`verdict=REVISE && repair_mode=RETRIEVE` + `retrieval_feedback`（gap_id / affected_claim_ids / missing_fact / subject_entity_ids / deficiency_type / reason）；**不得包含检索 query / tool 指定**；旧 `verdict` enum 不被污染 |
| 19 | 补检无增量（gap exhausted） | `gap_support_delta=0` → `GAP_NOT_IMPROVED` → 多次无改善 → `gap_exhausted / NO_PROGRESS` → 终止补检，按 §14.1 如实输出，不得重复补检同一方向 |
| 20 | 澄清/身份变化后的旧证据（evidence_epoch） | `pipeline → 澄清为 PipelineWebGL` 后：旧 epoch 证据标记 `STALE_FOR_CITATION`（保留可审计）；复用须按新 epoch 的 frozen SemanticTaskContext + Identity Anchor 重新 Qualification 或重新检索；不得自动继承 Citable |
| 21 | 检索未执行（retrieval_blocked） | `guard_rejected` / ACL denied 不得伪装成「知识库没有相关内容」；Publication 状态按 §14.1 分类可回溯 |

## 14.1 Terminal / Publication Taxonomy（第二轮评审 P1）

> **问题**：V2.1 重构了整个 Retrieval 生命周期，却没同步重构最终失败状态。必须区分「真正检索过但没结果」与「根本没检索成功」，否则以后仍可能出现「检索根本没执行 → 却告诉用户知识库没有相关内容」。

```text
grounded_full          有足够 Citable 支撑，全部 claim 通过 Grounding          → 正常发布
grounded_partial       部分 claim 有 Citable 支撑，其余 gap exhausted          → 按有依据部分如实作答 + 明确指出缺失项
retrieved_no_hits      真正执行过 Retrieval，返回 0 个候选                      → 知识库未查询到相关内容
retrieved_no_support   Working 有内容但 Citable = 0（全 REJECT）                → 知识库未查询到可支撑的内容
retrieval_blocked      未执行 Retrieval（ACL denied / guard_rejected / 工具失败）→ 如实告知检索未执行，禁止伪装成「无相关内容」
clarification_required 身份必须确认才能归属回答                                  → 反问澄清
no_safe_answer         Reviewer 判定没有安全答案                                 → 拒答/安全兜底
reviewer_error         Reviewer 服务异常                                         → 服务异常，不产出假「无相关内容」
```

约束：

- `retrieval_blocked` ≠ `retrieved_no_hits`：前者是系统侧未执行，后者是执行了无结果，二者的用户话术与归因必须分开；
- 每次 Publication 必须能回溯到上面的状态分类，Trace 可解释「为什么最终是这句话」。

---

# 十五、核心质量指标

| 指标 | 目标 | 说明 |
|:--|:--|:--|
| 单实体 sibling 串货率（Wrong Entity Contamination） | **0%** | PipelineWebGL → PipelineBuilder |
| Target Attribution Claim Error Rate | **0%（核心 Gold）** | RELATED_CONTEXT 被错误提升为 Target Attribute Claim |
| 明确 `different_from` 实体越权支撑目标属性 | **0%** | 由 Citable Qualification + Claim Support Matrix 保证 |
| **Citable / Frozen Snapshot 层 CONFLICT / IRRELEVANT 进入率** | **0%** | 二层协议核心指标 |
| **REJECT 判定结果保留率（可审计）** | **100%** | 每个 REJECT 的 qualification 结果被 Working 层保留并可审计（§4.7.3） |
| **高价值 REJECT（CONFLICT）进入 Main Observation 率** | **100%** | 纠偏价值高，必须让 Main 看见 |
| **低价值 IRRELEVANT 无痕删除率** | **0%** | 可压缩/聚合，但不得消失于审计之外 |
| **Main Context 噪声淹没率（IRRELEVANT 全文占比）** | **趋零** | Compaction 协议生效后，低价值 chunk 不进 Main 全文 |
| Multi-entity 探索成功率 | **≥ 现状** | 降级 ACL 后不得下降 |
| 检索「疑似相关」实体成功率 | 上升 | Agent 能搜到并观察兄弟实体 |
| 无授权实体进入最终 Citable EvidencePool 污染率 | **0%** | 由 Citable Qualification 而非检索 ACL 保证 |
| Working 层无 provenance chunk 比例 | **0%** | 保持 |
| 身份重绑定率（alias/fuzzy 把已确认主体改义） | **0%** | IdentityScope + Snapshot 硬限 |
| 非法身份静默改绑率（D3 → WebGL） | **0%** | 8.1 边界 |
| Reviewer 一次补检后支撑率 | 显著上升 | Phase D 反馈回路 |
| Reviewer Feedback Contract 归因率（RETRIEVAL_GAP 均携带 gap_id + affected_claim_ids） | **100%** | §12.4 |
| Reviewer 越权产出检索 query / tool 率 | **0%** | Reviewer 只描述缺口（§12.4） |
| 补检无增量循环率（同 gap 无新支撑仍重复补检） | **0%** | §12.5 gap_support_delta |
| identity 变化后旧证据自动继承 Citable 率 | **0%** | §8.3 evidence_epoch |
| Snapshot V1 原地修改率 | **0%** | §12.6 immutable |
| `retrieval_blocked` 被伪装成「无相关内容」率 | **0%** | §14.1 |
| 8·27 Gold 回归 | **全过** | Identity 37/37、Text 18/18、Reviewer 50/50 |
| 二层路由 Gold | **全过** | §4.6 新增用例 |

---

# 十六、禁止事项

1. **禁止把「探索放宽」理解成「Citable 放宽」**：CONFLICT / IRRELEVANT 只允许存在于 Working Evidence（供 Main 认知更新与纠偏），**不得进入 Citable EvidencePool / Frozen EvidenceSnapshot**；Claim Support Matrix 不回退。放宽的是 Agent 探索的 ACL 与 Working 层可见性，不是 Citable 引用层。
2. **禁止把 REJECT 语义改回「从 Main 视野消失」**（即 §四 A 行为）：CONFLICT/IRRELEVANT 至少要对 Main 可见，否则「Agent 搜错 → 观察 → 纠偏」无法成立。
3. **禁止为了省 Admission 重新把 `document_entity` 做成硬 filter，或恢复 Identity Allowlist**（8·27 §88 延续）。
4. **禁止让 Agent 自己修改全局 Identity / Scope**：Tool 参数只能提出探索目标，不能 `scope.add_entity(...)`。
5. **禁止把「搜到了 PipelineBuilder」等同「主体从 WebGL 切成 Builder」**：Identity 与 Exploration 严格分离（V1.6 §12.4 延续）。
6. **禁止一次删除全部探测期 ACL**：Phase C1–C3 必须逐项 A/B，单败单项回退；**且必须先完成 C0 二层化**。
7. **禁止新增第二套 Semantic Contract / 第二套 Admission**：继续以 `SemanticTaskContext` / `TextEvidenceAdmissionService.qualify()` 为唯一权威；Working/Citable 是同一套判定的两层路由，不是第二套判定。
8. **禁止绕过 Immutable Snapshot 与 `grant_id / identity_scope_id / support_scope` 校验**：硬限制不回退。
9. **禁止 J3×Pipeline* 静默正锚**：点名 D3 仍不得写入 `backbone_canonical` 并按之检索（8·14 FR-3 保持）。
10. **禁止 AnswerFinalizer / Reviewer 自行调用 Retrieval**：`RETRIEVAL_GAP` 必须回流给 Agent（Main Controller 唯一工具决策者）。
11. **禁止把 REJECT 的 qualification 结果无痕删除**：Working 层必须保留并可审计（§4.7.3）；低价值 IRRELEVANT 可压缩/聚合展示，但不得消失于审计之外。
12. **禁止把 `support_scope` 值当 `evidence_class` 枚举使用**（§4.3.1）：Citable 判定必须成对匹配，否则路由直接错掉。
13. **禁止 Main / 任何角色把 `citable=false` 升格为 `true`**：Citable eligibility 由 §4.3.1 系统协议自动决定，Main 只决定「搜什么 / 够不够 / finalize / focus 选用合法证据」，无证据升格权（§4.3.2）。
14. **禁止 Reviewer 输出检索 query / 指定 tool**：Feedback Contract 只描述缺口（missing_fact / affected_claim_ids / subject_entity_ids / deficiency_type），`repair_mode=RETRIEVE` 由 Agent 决定具体怎么查（§12.4）；不得污染原 `verdict` enum（`RETRIEVAL_GAP` 不是 verdict 值）。
15. **禁止无增量补检循环**：同 gap `gap_support_delta=0` 不得重复补检同一方向；多次无改善 → `gap_exhausted`；全部 gap exhausted → `NO_PROGRESS` 终止（§12.5）。
16. **禁止 identity 变化后旧 Working 证据自动继承 Citable 权限**：澄清/换主体产生新 `evidence_epoch`，旧证据 `STALE_FOR_CITATION`；复用须按新 epoch 的 frozen SemanticTaskContext + Identity Anchor 重新 Qualification 或重新检索（§8.3）。
17. **禁止原地修改 Snapshot V1**：V2 必须是新对象（new snapshot_id / evidence_version）；Working observation append-only；Citable 按稳定 evidence key 去重（§12.6）。
18. **禁止把「未执行检索」（retrieval_blocked / guard_rejected）伪装成「知识库没有相关内容」**：Publication 状态必须按 §14.1 分类如实输出。
19. **禁止用 Agent 临时检索 query 作为重新 Qualification 的权威**：重新 Qualification 一律以当前 frozen SemanticTaskContext + Identity Anchor 为语境（§8.3 / §12.6）。

---

# 十七、Definition of Done（评审确认后生效）

### 架构 DoD

- [ ] `Identity Anchor` 成为结构化协议（answer_subject / known_confusions / relevant_relations / reminder）。
- [ ] Observation 携带每条证据的 attribution（document_entity / relation_to_subject / evidence_class / support_scope / citable）。
- [ ] **Working / Citable 二层落地**：REJECT 不再从 Main 视野消失；CONFLICT/IRRELEVANT 只进 Working；Frozen Snapshot 只冻结 Citable。
- [ ] **Citable Qualification 按 §4.3.1 成对枚举实现**：无 `evidence_class in {support_scope 值}` 混用。
- [ ] **Working Visibility / Compaction 协议落地（§4.7）**：REJECT 判定结果保留可审计；高价值 CONFLICT 进 Main Observation；低价值 IRRELEVANT 压缩聚合，不无痕删除。
- [ ] **Citable Promotion Authority 落地（§4.3.2）**：Main 无证据升格权；eligibility 由系统协议决定，无第二权威。
- [ ] **Identity Transition & Evidence Epoch 落地（§8.3）**：identity 变化产生新 epoch；旧证据 `STALE_FOR_CITATION`；自由文本假设与合法实体 ID 工具字段分离（§8.3.1）。
- [ ] **Retrieval Accounting 落地（§10.1）**：requested / guard_rejected / executed 分开计数；guard_rejected 不消耗 executed retrieval attempt。
- [ ] **Reviewer Feedback Contract Schema 落地（§12.4）**：`repair_mode` / `retrieval_feedback` 正式字段；Reviewer 不产出 query/tool；原 verdict enum 不污染。
- [ ] **Evidence Delta & No-Progress 落地（§12.5）**：gap_support_delta / gap_exhausted / NO_PROGRESS 终止路径。
- [ ] **Snapshot V2 Merge & Versioning 落地（§12.6）**：V1 immutable / dedup / evidence_version / 按 frozen SemanticTaskContext + Identity Anchor 重新 Qualification。
- [ ] **Terminal / Publication Taxonomy 落地（§14.1）**：grounded_full / partial / no_hits / no_support / blocked / clarification / no_safe_answer / reviewer_error 分类可回溯。
- [ ] 探测期 ACL 逐项降级完成（顺序 = C0 → C1 → C2 → C3）：`different_from` 禁搜、`broadening_after_target_rejection`、`identity_not_confirmed` 拦检索、`graph_relation-only` 均不再作为 Agent 探索的硬闸。
- [ ] `RetrievalGrant / ProvenanceGrant` 职责重划完成：负责 ACL/provenance/fingerprint/snapshot，不负责「实体值不值得搜 / 是否 confirmed」。
- [ ] 身份重绑定仍为硬限制；反问 / J3 / 非法锚不检索仍保留在答案归属策略层；`歧义 ≠ 非法身份` 边界（§八）落地。
- [ ] Evidence Admission + Claim Support Matrix + Grounding Reviewer 唯一权威，无第二套 Admission；Working/Citable 是同一套判定的两层路由。

### 行为 DoD

- [ ] `PipelineWebGL → PipelineBuilder` Wrong Entity Contamination = 0（核心 Gold）。
- [ ] 三维管线管理 / PipelineWebRTC 事故 Recall 不回退。
- [ ] Multi-entity（A 和 B 区别 / 协同部署）成功率不下降。
- [ ] Target Attribution Claim Error Rate 核心 Gold = 0。
- [ ] 明确 `different_from` 实体不得越权支撑目标属性 Claim。
- [ ] `REVISE_REWRITE`（快照内改写）与 `RETRIEVAL_GAP`（补检后重答）两个分支不混用；Main Controller 唯一工具决策者。

### 测试 DoD

- [ ] 8·27 全部 Deterministic Gold 回归通过（Identity 37 / Text 18 / Reviewer 50）。
- [ ] **二层路由 Gold 全过（§4.6）**；REJECT 旧「消失」行为测试已更新为新语义。
- [ ] 全量非集成测试 0 failed。
- [ ] C0–C3 逐项 ACL 降级 A/B 有书面记录，未达标单项已回退。
- [ ] Reviewer → Agent 反馈回路专项测试通过（含 AnswerFinalizer 不得偷调 Retrieval 断言）。
- [ ] 第二轮 P0/P1 专项测试通过：Citable 升格权（§4.3.2）、Feedback Schema（§12.4）、gap exhausted（§12.5）、evidence epoch（§8.3）、Snapshot 不可变（§12.6）、Publication 分类（§14.1）。
- [ ] 真实模型 Micro-chain 连续 3 次通过。

### 可观测性 DoD

- [ ] Trace 可见每条证据的 attribution 与 `citable` 路由标记。
- [ ] Trace 可回答「哪一层放错」（Working Admission / Citable Qualification / Answer / Reviewer）。
- [ ] Agent 步骤级 Trace 可解释每次换方向的依据（Observation 触发）。

### 工程卫生 DoD

- [ ] 不新增实体名特判（pipeline / 三维管线管理 等）。
- [ ] 不通过人工补 Graph 掩盖 Recall 问题。
- [ ] 不保留旧新双轨长期并存（探测期 ACL 残留清理完成）。
- [ ] `git diff --check` 通过；文档 DoD 全过才可移档。

---

# 十八、最终架构判定标准

V2.1 完成后，以下四句话必须同时成立：

```text
1. 用户明确的主体永远不会被模糊重绑定（IdentityScope + Snapshot 硬限）；
   合法歧义（pipeline）允许围绕 verified candidates 探索，但不产出 answer_subject；
   非法身份（D3）永不静默改绑。

2. Agent 可以提出错误假设、搜索错误方向（含搜到 different_from 兄弟实体），
   并通过 Observation 自我纠正——代码不再预先规定合法搜索路径。

3. Working Evidence Admission 不提前删除可用于认知更新的相关/冲突候选；
   Citable Evidence Qualification 只允许满足明确 Support Scope 的证据
   进入 Frozen Snapshot——Harness 防止错误假设升级为系统事实，
   Reviewer 防止错误归属升级为最终答案。

4. Entity/Evidence ID 不可伪造、ACL/KB 权限、Tool schema/budget/timeout 仍是硬限制。
```

如果只做到其中一半（例如只放宽检索、却把 Citable 引用层一并放宽，或只拆二层却不解锁探索），都不算 V2.1 完成。

---

# 十九、一句话终态定义

> **Working Evidence Admission 不提前删除可用于认知更新的相关/冲突候选；Citable Evidence Qualification 只允许满足明确 Support Scope 的证据进入 Frozen Snapshot。Identity Anchor 提供方向、不设轨道；Agent 可以搜错并自我纠正；Harness 管「不可伪造」与「不可越权」，Reviewer 管「答得对不对」。**
