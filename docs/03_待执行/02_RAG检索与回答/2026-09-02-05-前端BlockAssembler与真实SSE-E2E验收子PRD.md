# 子 PRD 05：前端 Block Assembler 与真实 SSE / E2E 验收

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 基线日期 | 2026-09-02 |
| 状态 | **待执行** |
| 所属总 PRD | `2026-09-02-Agent原生Reasoning与证据纠错透明执行流重构总PRD.md` |
| 前置 | 子 PRD 01–04 |
| 目标 | **把统一模型流与执行事实稳定投影成用户可见 Block Stream，并通过真实浏览器/SSE/模型验收证明 Controller、答案总结、纠错三类 reasoning 都连续可见。** |
| 非目标 | 不重写整个聊天页面视觉风格，不修改 RAG 检索正确性规则。 |

---

# 1. 当前前端事实

现有：

```text
web/src/api/index.ts
→ SSE parse
→ callbacks

ChatView.vue
→ AgentBlockProjector

AgentBlockProjector
→ ReasoningBlock / ToolBlock / ActivityBlock / SystemEventBlock

AgentStepStream.vue
→ 展示 block
```

现有 `handleReasoningDelta()` 已能原位拼接文本，所以本阶段重点不是“再做一个 Reasoning 组件”，而是：

```text
统一协议消费
顺序稳定
来源标识正确
纠错 Finding 表达
状态单点化
真实 E2E 验证
```

---

# 2. 第一性原则

前端只能做：

```text
assemble
project
render
```

不能做：

```text
根据文字猜模型是否有 reasoning
根据 decision 猜 Tool 是否真的执行
根据 Candidate 猜 Reviewer 错误
根据时间推断 Publication 是否通过
```

所有业务事实必须来自后端协议。

---

# 3. Block Assembler 目标

可以继续演进 `AgentBlockProjector`，也可以重命名为更准确的 `AgentBlockAssembler`；不强制为了命名重构。

但职责必须收敛为：

```text
ModelStreamEvent
+
User-visible ExecutionEvent
+
Final Answer
→ ordered AssistantBlock[]
```

## 3.1 ReasoningBlock

来源：

```text
native model reasoning event
或无 native reasoning 时的明确 fallback event
```

必须区分：

```text
contentSource = native_reasoning
contentSource = public_explanation
```

同一个 call 不应同时创建两块重复说明。

## 3.2 ToolBlock

继续只由真实：

```text
tool_start
→ tool_result
```

驱动。

## 3.3 Review Finding

按子 PRD 04 的最终裁决实现结构化展示，不得塞 raw JSON。

## 3.4 MarkdownBlock

只能来自：

```text
final_answer
```

---

# 4. Reasoning 展示行为

## 4.1 运行中

应持续显示：

```text
最新非空行摘要
+
展开体不断追加完整 reasoning
```

不得每个 delta 创建一张新卡片。

## 4.2 完成后

保留完整本轮 reasoning，可折叠/展开。

## 4.3 大量 delta 性能

需要避免每个字符导致不必要的整棵聊天树重渲染。

在 Vue 当前架构下至少验证：

```text
连续数百个 reasoning delta
→ 页面不卡死
→ block 顺序不乱
→ 文本不丢
```

如需要批量到 animation frame 更新，可以实施；不要为了仿 Harness 强行引入 React/Cordis 架构。

---

# 5. 自由滚动不变量

用户已经明确要求生成期间可以自由浏览历史内容。

硬约束：

```text
用户当前接近底部
→ 可自动跟随新内容

用户主动上滑
→ 后续 reasoning/tool/review delta 不得强制拉回底部
```

当前 `autoFollowBottom` 设计可以保留，只需用真实连续 reasoning 压测。

不得仅通过删除 `scrollDown()` 调用粗暴解决，而应验证：

```text
事件频繁到达时 near-bottom 状态仍正确
```

---

# 6. 顺序与并发

必须保证一个真实 turn 的用户可见序列和真实时间一致，例如：

```text
Reasoning(controller #1)
Tool #1
Reasoning(controller #2)
Tool #2
Reasoning(controller #3)
Reasoning(answer)
Review Activity
Review Finding
Reasoning(rewrite)
Review complete
Markdown final answer
```

测试异步边界：

```text
reasoning_end 与 tool_start 紧邻
review finding 与 rewrite reasoning 紧邻
final_answer 与 review completed 紧邻
```

不得因为 Vue reactive 更新/sequence counter 导致旧块被插到新块后。

---

# 7. SSE 协议消费完整性

前端类型和 parser 必须覆盖后端真正会发送的用户相关事件。

禁止再次出现：

```text
后端已经发送某事件
→ TypeScript union 没定义
→ API parser 没回调
→ 事件静默丢失
```

应有协议一致性测试，至少验证：

```text
已声明用户可见 wire event
↔ parser
↔ callback/projector
```

如果旧 `llm_reasoning_summary` 被新架构废弃，应明确删除/兼容迁移，而不是继续放任半接线。

---

# 8. 历史状态清理

一个 Agent turn 的用户可见执行状态最终只能有一个主来源：

```text
blocks
```

检查并清理与其重复承担执行流职责的长期状态：

```text
thinking
agentTools
timelineItems
其他 legacy execution arrays
```

只删除已经被新路径完全替代的生产路径；历史消息兼容可在反序列化层做一次性转换，不维护双写。

---

# 9. 单元测试

至少覆盖：

## Reasoning

- start 不立即制造空白脏 block（如当前策略仍需要延迟创建）。
- 首个 delta 创建 block。
- 多 delta 原位拼接。
- end 正确完成。
- native reasoning 与 fallback 互斥。
- Controller / Answer / Rewrite stage 分开。

## Review

- REVISE 创建真实 Finding。
- 多 Claim 展示不丢失。
- Rewrite reasoning 排在 Finding 后。
- 第二轮 Review 不覆盖第一轮。

## Tool

- start/result 单块生命周期。
- denied/error 状态不伪造成成功。

## Markdown

- Candidate event 不创建正式 Markdown。
- final_answer 才创建/更新最终正文。

---

# 10. HTTP / SSE E2E

必须使用真实 FastAPI SSE 入口，而不是直接调用 projector。

至少覆盖：

## E2E-A：普通知识问题，一次检索 PASS

断言顺序：

```text
Controller native reasoning
→ Tool
→ Controller native reasoning/finalize
→ Answer native reasoning
→ Reviewer lifecycle
→ final_answer
```

## E2E-B：二次补检

断言至少两次 Controller reasoning 与 Tool 真实交错。

## E2E-C：REVISE → Rewrite → PASS

这是本次最关键用例。

必须同时看到：

```text
Answer native reasoning
Reviewer Claim Finding
Rewrite native reasoning
Reviewer #2 PASS
final_answer
```

## E2E-D：无 native reasoning 模型

只出现 fallback，不出现伪 native reasoning。

## E2E-E：Reviewer error

Candidate 不发布，用户得到安全错误提示。

---

# 11. 真实模型验收

单元测试与 mock stream 不能作为最终验收。

至少选择当前实际支持 reasoning 的 Main 模型完成真实请求，例如当前配置环境中的：

```text
qwen3.5:9b（Ollama）
或当前实际接入且返回 reasoning_content 的 DeepSeek 模型
```

每次验收记录：

```text
model/provider
call_id
stage
reasoning_available
reasoning_chars
前端实际 block 字符数
Trace reasoning chars
最终 verdict
```

目标不是固定字数，而是证明：

> **Provider 返回多少实际 reasoning，主界面就连续收到对应真实内容，而不是只出现 1–2 句 public explanation。**

---

# 12. 浏览器人工 UX 验收

必须人工检查：

- [ ] reasoning 实时增长，不是结束后一次出现。
- [ ] Controller reasoning 能看清“为什么调用工具”。
- [ ] Answer reasoning 能看清“如何总结证据和组织答案”。
- [ ] REVISE 时能看清“哪里错了”。
- [ ] Rewrite reasoning 能看清“Main 如何根据错误修正”。
- [ ] 最终答案只有审核后才出现。
- [ ] 用户上滑后页面不会抢回底部。
- [ ] reasoning 很长时折叠/展开可用。
- [ ] Tool 卡片不会被大量 reasoning delta 打乱。
- [ ] fallback 文案与原生 reasoning 标签不会混淆。

---

# 13. Trace / UI 对账

对每个 Main call：

```text
Trace.call_id
Trace.stage
Trace.reasoning_chars
```

必须能找到 UI 对应 ReasoningBlock。

若：

```text
reasoning_available=true
```

但 UI 没有 native block，直接失败。

若：

```text
reasoning_available=false
```

但 UI 标成模型原生推理，直接失败。

REVISE 时：

```text
Trace claim_reviews
↔ UI Finding
↔ rewrite call_id
```

必须可追踪。

---

# 14. DoD

- [ ] Model Stream/Execution Event 能稳定组装为单一 `blocks` 用户流。
- [ ] Controller/Answer/Rewrite reasoning 均持续增量显示。
- [ ] Reviewer Finding 结构化可见。
- [ ] Candidate 不泄露成正式答案。
- [ ] 自由滚动通过连续 delta 人工验收。
- [ ] SSE parser/type/projector 协议无静默缺口。
- [ ] legacy 重复用户执行状态完成清理或有明确兼容边界。
- [ ] 前端单测通过。
- [ ] build/typecheck 通过。
- [ ] HTTP/SSE E2E A–E 通过。
- [ ] 真实模型普通 PASS 用例通过。
- [ ] 真实模型 REVISE→Rewrite→PASS 用例通过。
- [ ] 浏览器人工 UX 全项通过。
- [ ] Trace/UI 字符数与 call_id 对账通过。

---

# 15. 禁止提前结项

以下都不算完成：

```text
“ReasoningBlock 单测通过”
“SSE 能收到 delta”
“真实模型 Controller 有思考”
“Reviewer REVISE 有一条提示”
```

必须最终在一个真实纠错请求中看到：

```text
Controller reasoning
→ Tool
→ Answer reasoning
→ Claim Finding
→ Rewrite reasoning
→ PASS
→ Final Answer
```

这才是本轮重构的完整产品验收。
