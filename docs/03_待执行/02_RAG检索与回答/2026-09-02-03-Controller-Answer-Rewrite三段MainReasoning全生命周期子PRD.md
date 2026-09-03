# 子 PRD 03：Controller / Answer / Rewrite 三段 Main Reasoning 全生命周期

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 基线日期 | 2026-09-02 |
| 状态 | **待执行** |
| 所属总 PRD | `2026-09-02-Agent原生Reasoning与证据纠错透明执行流重构总PRD.md` |
| 前置 | 子 PRD 01、02 |
| 目标 | **确保 Main Controller、Main Answer Generator、Main Grounded Rewrite 三个真实语义阶段全部通过同一 Model Stream 主通道逐 delta 展示 native reasoning。** |
| 核心约束 | Answer/Rewrite 正文仍仅进入 Candidate buffer；reasoning 可见不等于正文提前发布。 |

---

# 1. 为什么需要单独一份子 PRD

只修 Controller reasoning 仍然无法达到用户目标。

Main 在一个 RAG turn 中至少承担三种不同的认知工作：

```text
Controller：决定下一步做什么
Answer：基于冻结证据总结、筛选、组织答案
Rewrite：根据 Reviewer 的错误发现纠正 Candidate
```

三种 reasoning 都是用户希望看到的真实工作过程。

---

# 2. Controller 生命周期

目标链路：

```text
Controller prompt
→ ModelStreamRunner
→ native reasoning delta → SSE/Trace/UI
→ content delta → JSON buffer
→ protocol validation
→ AgentDecision
→ decision ExecutionEvent
```

用户可见：

```text
Main · Controller · 模型原生推理
```

不得退化成只显示：

```text
decision.reason
```

`decision.reason` 可作为结构化执行事实或 fallback 辅助，但不是 native reasoning。

---

# 3. Answer Generator 生命周期

输入：

```text
用户问题
+ ConversationContext
+ Frozen Evidence Snapshot / AnswerGenerationContext
+ Grounding / citation 规则
```

目标链路：

```text
Main Answer
→ native reasoning delta → SSE/Trace/UI
→ content delta → Candidate V1 buffer
→ Candidate status（内部/Trace）
→ Reviewer
```

## 3.1 Answer reasoning 必须真实可见的内容类型

如果模型本身产生，应允许用户看到例如：

```text
哪些证据直接支持问题
哪些只是 RELATED_CONTEXT
哪些证据互相重复
哪些证据存在冲突
哪些事实不能跨实体继承
哪些引用应支撑哪些 Claim
Coverage 只能做 partial 还是可 full
最终答案结构如何组织
```

不得由代码人工生成这些推理内容。

## 3.2 Candidate 正文仍不发布

硬约束：

```text
model_content_delta(answer_generation)
→ Candidate V1 buffer
→ NOT MarkdownBlock
→ NOT onToken final answer
```

只有 Grounding 后：

```text
final_answer
```

才进入正式正文。

---

# 4. Grounded Rewrite 生命周期

输入：

```text
Candidate V1
+ Frozen Evidence Snapshot
+ Reviewer claim_reviews
+ rewrite_actions
+ immutable supported claims
```

目标链路：

```text
Main Rewrite
→ native reasoning delta → SSE/Trace/UI
→ content delta → Candidate V2 buffer
→ Reviewer #2
```

用户应能看到真实纠错 reasoning，例如模型实际输出：

```text
Reviewer 指出的 Claim C3 缺少证据支持……
该错误来自把相关模块能力归到了目标实体……
需要删除这一谓词，同时保留已支持 Claim……
```

但不得通过 Prompt 强迫模型复述内部敏感数据或人为制造“戏剧化思考”。

目标是**透传实际 native reasoning**，不是让模型表演。

---

# 5. Stage / Call Identity

必须稳定区分：

```text
agent_controller
answer_generation
grounded_retry
```

每个实际调用必须独立 `call_id`。

Reviewer resume 多轮时不得复用导致前端覆盖错误，例如：

```text
grounded_retry_v2
```

若可能发生多次 Rewrite，应使用可区分轮次的 call_id：

```text
grounded_retry_1
grounded_retry_2
```

具体命名由实现决定，但必须可追踪。

---

# 6. 顺序保证

SSE/Block 顺序必须忠于真实发生时间：

```text
Controller reasoning
→ Decision
→ Tool
→ Tool result
→ Controller reasoning
→ Finalize decision
→ Answer reasoning
→ Review activity/finding
→ Rewrite reasoning（如有）
→ Review #2
→ Final Answer
```

不得因为异步队列造成：

```text
Tool 已完成
但 reasoning block 后补到 Tool 后面错误位置
```

需要对 call/event sequence 做测试。

---

# 7. 中文 reasoning

继续继承当前 Main reasoning 中文约束：

```text
若 provider 暴露 native reasoning，要求自然语言分析从第一段开始使用简体中文；
代码、JSON 字段、API、专有名词可以保留原文。
```

但需要承认：Prompt 只能影响模型，不构成绝对协议保证。

验收应记录：

```text
实际 reasoning 语言
```

若模型仍返回英文，应判为模型/Prompt 行为问题，而不是后端重新翻译后冒充原始 reasoning。

---

# 8. 测试矩阵

至少：

| Case | Controller | Answer | Rewrite | 预期 |
| --- | --- | --- | --- | --- |
| 普通一次检索 PASS | ✓ | ✓ | - | 两段 reasoning |
| 二次补检 PASS | ✓×多轮 | ✓ | - | reasoning 与 Tool 交错 |
| Reviewer REVISE | ✓ | ✓ | ✓ | 三类 reasoning 全出现 |
| 无 native reasoning | fallback | fallback | fallback（若发生） | 不冒充原生 |
| Answer 生成异常 | ✓ | error | - | Candidate 不发布 |
| Rewrite 异常 | ✓ | ✓ | error | V1/V2 均不得误发布 |

---

# 9. DoD

- [ ] Controller reasoning 逐 delta 可见。
- [ ] Answer reasoning 逐 delta 可见。
- [ ] Rewrite reasoning 逐 delta 可见。
- [ ] 三者均走子 PRD 02 的单一 Model Stream 实现。
- [ ] 三阶段 call_id/stage 不串线。
- [ ] Answer content 仍被服务端 buffer。
- [ ] Rewrite content 仍被服务端 buffer。
- [ ] reasoning 与 Tool/Review 顺序真实。
- [ ] 中文约束真实模型至少做一次验收。
- [ ] 真实 REVISE 请求能够看到 Answer reasoning + Rewrite reasoning 两段不同的模型原生推理。

---

# 10. 禁止实现

- 禁止用 Answer 阶段的 `public_explanation` 冒充总结思考。
- 禁止只给 Controller 开 reasoning。
- 禁止把 Candidate content 复制进 ReasoningBlock。
- 禁止把 Reviewer summary 伪造成 Rewrite reasoning。
- 禁止前端根据 stage 名称伪造不存在的 reasoning block。
