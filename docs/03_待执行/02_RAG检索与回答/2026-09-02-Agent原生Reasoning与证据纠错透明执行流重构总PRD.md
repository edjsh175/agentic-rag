# Agent 原生 Reasoning 与证据纠错透明执行流重构总 PRD

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 基线日期 | 2026-09-02 |
| 状态 | **待执行** |
| 所属域 | `02_RAG检索与回答` |
| 任务性质 | **架构收敛 / 模型流协议重构 / 用户可见 Agent 执行流纠偏** |
| 目标对象 | Main Controller、Main Answer Generator、Main Grounded Rewrite、Provider 原生 reasoning、SSE、QA Trace、Agent Block Stream、Reviewer 结构化纠错事实 |
| 不改对象 | EvidencePool 的事实授权语义、Candidate/Publication Gate、Helper Grounding Reviewer 的裁决权、检索/图谱业务规则、Linear/Pipeline 的固定阶段 UX |
| 总目标 | **建立一条从 Provider 原始 native reasoning 到用户界面的无损 Model Stream 主通道；让 Controller 决策思考、答案总结思考、证据纠错思考全部实时可见；将 Reviewer 的真实 Claim 级纠错事实穿插展示；仅在没有 native reasoning 时使用简短公开说明兜底，同时继续禁止未经 Grounding 的 Candidate 正文提前发布。** |
| 替代关系 | **本 PRD 是 Agent reasoning / 用户可见纠错流的新权威入口。它替代 `2026-08-24-Agent全链路透明执行流PRD.md` 与 `2026-08-25-Agent用户可见Block流与执行详情分层架构迁移PRD.md` 中关于 native reasoning、public explanation、Reviewer REVISE 可见语义、Model Stream→UI 投影的旧裁决；两份旧 PRD 中 Candidate/Publication 隔离、Tool 真实生命周期、Trace 全量可观测、Agent/Linear 模式隔离等不冲突条款继续继承。** |
| 子 PRD | 01 主备语义与 fallback；02 统一 ModelStreamRunner；03 三段 Main reasoning 全生命周期；04 Reviewer 纠错事实与 Rewrite 闭环；05 前端 Block/UX 与真实 E2E 验收 |

---

# 1. 为什么必须重新立总 PRD

当前项目并非“没有 reasoning 能力”。真实代码已经具备：

```text
Provider 原始流
→ LLMStreamPart(reasoning/content)
→ llm_reasoning_start/delta/end
→ SSE
→ web API callback
→ AgentBlockProjector
→ ReasoningBlock
→ AgentStepStream
```

但是产品表现仍长期只有简短总结语句。根因不是单一 UI 问题，而是多个层级的语义发生了偏移：

```text
native reasoning 已经存在
        ↓
reasoning_stream_policy 默认 summarized
        ↓
真实 reasoning delta 被 Runtime 主动缓冲
        ↓
llm_reasoning_summary 前端又没有形成稳定用户可见主路径
        ↓
与此同时每个 Main 阶段先调用 generate_public_explanation()
        ↓
该 Prompt 明确要求“一到两句，不输出逐步推理”
        ↓
用户稳定看到的自然是简短总结，而不是真实 reasoning
```

因此继续调整：

```text
卡片样式
折叠组件
阶段文案
ReasoningBlock CSS
```

都不能从根本上解决问题。

本次必须修正的是：

> **数据源优先级、模型流协议、业务阶段接线、Reviewer 纠错展示和前端投影五个边界。**

---

# 2. 当前事实基线（As-Is）

以下以 2026-09-02 代码为准，而不是依据历史 PRD 推断。

## 2.1 Provider 层已经能够区分 reasoning 与 content

`rag_knowledge/llm_http.py` 已存在统一：

```python
LLMStreamPart("reasoning", delta)
LLMStreamPart("content", delta)
```

并能够读取至少：

```text
Ollama message.thinking
OpenAI-compatible delta.reasoning_content / delta.reasoning
Google thought
```

所以 Provider Adapter 不是当前主要缺口。

## 2.2 默认 reasoning 策略仍是 summarized

`rag_knowledge/config.py` 当前默认：

```text
reasoning_stream_policy = summarized
trace_reasoning_policy = summarized
```

`config.ini / config-local.ini / config-mix.ini` 当前未形成一个明确的 Agent 主路径 `token` 单点配置基线。

因此代码“支持 delta”并不等于生产/本地真实请求“实际逐 delta 发布”。

## 2.3 Controller 会收集真实 reasoning，但 summary 模式不实时 emit

`AgentLoop._adecide_via_llm()` 当前逻辑：

```text
part.kind == reasoning
→ reasoning_parts.append(delta)
→ 只有 policy == token 才 emit LLM_REASONING_DELTA
```

所以 native reasoning 可真实到达 Python，却不一定到达 UI。

## 2.4 Answer Generator 同样受 stream policy 控制

`RAGService._stream_agent_query()` 中：

```text
Answer native reasoning
→ thinking_parts
→ token 时才 yield llm_reasoning_delta

Answer content
→ answer_parts buffer
→ Reviewer / Publication Gate 后再 final_answer
```

Candidate 正文缓冲是正确设计；native reasoning 被 summary 化不是目标设计。

## 2.5 Grounded Rewrite 与 Controller/Answer 的 reasoning 实现已经分叉

Grounded Rewrite 当前有自己的一套：

```text
achat_stream_parts
→ reasoning_delta callback
→ content buffer
```

并没有与 Controller/Answer 共享一个真正的模型流执行器。

这意味着同一种“Main 调用”有多份：

```text
capability 判断
reasoning start/end
buffer
统计
异常
Trace
SSE
fallback
```

实现，未来必然继续漂移。

## 2.6 public_explanation 当前不是 fallback，而是常规并行路径

`rag_knowledge/services/execution_explanation.py` 明确要求：

```text
不要输出隐藏思考过程或逐步推理
只用简体中文，以一到两句说明本阶段如何处理
```

但当前 Controller / Answer / Rewrite 都可能在 native reasoning 调用前先创建 `public_explanation`。

因此当前语义近似：

```text
public_explanation = 常规主路径
native reasoning = 可选附加路径
```

而用户原始目标应为反向：

```text
native reasoning = 主路径
public_explanation = 无 native reasoning 的 UX fallback
```

## 2.7 前端已经具备连续 ReasoningBlock 拼接能力

`AgentBlockProjector.handleReasoningDelta()` 当前会：

```text
existing.text += data.delta
```

`AgentStepStream.vue` 也能够展开完整 text，并在 running 时展示最新行。

因此第一阶段不需要先重写视觉组件证明效果。

## 2.8 Reviewer 真实纠错信息存在，但用户主界面过度压缩

Helper Grounding Reviewer 已输出结构化：

```text
verdict
coverage
summary
claim_reviews
rewrite_actions
```

当前主界面更多显示：

```text
正在核对回答与证据…
刚刚的答案被拒绝了，正在重新组织…
```

这不足以体现系统真正发生的：

```text
哪个 Claim 错了
错误类型是什么
为什么当前 Evidence Snapshot 不支持
Main 后续打算删除/缩限/改写什么
```

---

# 3. 第一性原则：从零设计时系统应有哪些事实源

## 3.1 只有三类用户可见事实源

### A. Model Stream

回答：

> **模型此刻正在输出什么？**

来源只能是 Provider 原始模型流：

```text
reasoning
content
tool-call delta（若未来主模型直接输出）
```

其中用户可见 reasoning 必须来自 native reasoning channel。

### B. Agent Execution Event

回答：

> **系统实际上发生了什么？**

来源是 Runtime / Tool / Evidence / Reviewer / Publication：

```text
decision
tool_started
tool_completed
evidence_changed
review_started
review_completed
rewrite_requested
publication
error
```

它不是模型的“想法”，是系统事实。

### C. Public Explanation Fallback

回答：

> **当当前 Main 调用没有 native reasoning 时，如何避免用户面对长时间空白？**

它只是兼容 UX。

因此硬关系：

```text
Model Stream ≠ Execution Event ≠ Public Explanation
```

三者不得再互相伪装。

---

# 4. 顶层架构裁决

## 4.1 Native reasoning 是 Main 阶段第一优先级可见过程

以下三个阶段，只要 Provider 实际返回 native reasoning，就必须实时逐增量展示：

```text
Main Controller
Main Answer Generator
Main Grounded Rewrite
```

不得出现：

```text
模型返回完整 reasoning
→ 后端主动摘要
→ 用户只看到 public_explanation
```

## 4.2 答案总结阶段同样必须展示 reasoning

Answer Generator 不是“只负责吐正文”的黑盒。

当它基于冻结 Evidence Snapshot 执行：

```text
证据取舍
直接证据 / 上下文证据边界判断
冲突判断
Claim 规划
引用规划
部分回答边界
最终结构组织
```

其 native reasoning 与 Controller reasoning 同等属于用户可见 Main reasoning。

因此：

```text
Answer reasoning → 实时可见
Answer content → Candidate V1 buffer，不直接发布
```

## 4.3 Reviewer 纠错后，Main Rewrite reasoning 必须完整进入同一主通道

当 Reviewer 返回 REVISE：

```text
Reviewer Claim Findings
        ↓
用户可见结构化错误事实
        ↓
Main Grounded Rewrite native reasoning
        ↓
用户可见实时纠错思考
        ↓
Candidate V2 buffer
        ↓
Reviewer #2
        ↓
Publication
```

“正在重新组织答案”只能作为 lifecycle/activity 信息，不能替代真实 Rewrite reasoning。

## 4.4 Reviewer 自由 reasoning 不作为默认主聊天内容

Reviewer 应展示其**真实结构化裁决产物**：

```text
claim_id
claim text/status
unsupported / contradicted / supported
summary
rewrite_action
合法 evidence refs（适合用户层时）
```

而不是为了“看起来透明”再展示 Reviewer 的自由推理文本。

理由不是隐藏，而是：

```text
结构化裁决
> 可验证
> 可与 RewriteContract 一一对应
> 比自由 reasoning 更适合解释“系统认定哪里错了”
```

## 4.5 Candidate 与 Published Answer 永久分离

硬不变量：

```text
Generated ≠ Published
```

允许：

```text
Answer native reasoning → 实时显示
Rewrite native reasoning → 实时显示
```

禁止：

```text
Candidate V1 content → 未审先显示为正式回答
Candidate V2 content → 未二审先显示为正式回答
```

正式 Markdown 仍只能来自 Grounding 后的 `final_answer`。

---

# 5. 新目标数据流

```text
                         ┌─────────────────┐
                         │  LLM Provider   │
                         └────────┬────────┘
                                  │ raw stream
                                  ▼
                         ┌─────────────────┐
                         │ ModelStreamRunner│
                         └────────┬────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
                    ▼                           ▼
           ModelStreamEvent                Content Buffer
                    │                           │
          ┌─────────┴─────────┐        ┌────────┼─────────┐
          │                   │        │        │         │
          ▼                   ▼        ▼        ▼         ▼
        Trace                SSE   Controller  V1       V2
                              │      JSON      Candidate Candidate
                              ▼
                       Frontend Assembler
                              │
                              ▼
                       Reasoning Block
```

Agent 业务事实独立：

```text
Controller / Tool / EvidencePool / Reviewer / Publication
                         │
                         ▼
                 ExecutionEventBus
                    │           │
                    ▼           ▼
                  Trace        SSE
                                 │
                                 ▼
                        User-visible Projection
```

最终 UI 是两条真实流的组合：

```text
Model Stream
+
Execution Event
+
Published Answer
```

而不是：

```text
内部事件
→ 再总结成几句话
→ 冒充模型思考
```

---

# 6. Model Stream 统一协议

本 PRD 不要求照搬 DeepSeek Harness 实现，但要求采用同类第一性边界。

建议统一事件族：

```text
model_call_started
model_reasoning_started
model_reasoning_delta
model_reasoning_finished
model_content_delta
model_call_finished
```

至少包含：

```json
{
  "call_id": "agent_controller_2",
  "role": "main",
  "stage": "agent_controller",
  "provider": "ollama",
  "model": "qwen3.5:9b",
  "sequence": 17
}
```

Reasoning delta：

```json
{
  "type": "model_reasoning_delta",
  "data": {
    "call_id": "answer_generator_v1",
    "role": "main",
    "stage": "answer_generation",
    "delta": "当前证据中……"
  }
}
```

关键原则：

> **ModelStreamRunner 只描述模型流，不知道 UI 是卡片、折叠行还是 Timeline。**

---

# 7. Public Explanation 的新定义

## 7.1 只允许作为 fallback

必须满足：

```text
当前调用属于用户可见 Main 阶段
AND
没有可用 native reasoning
```

才允许产生公开说明。

禁止：

```text
先发 public_explanation
再发 native reasoning
```

## 7.2 fallback 不应默认新增一次昂贵的 LLM 调用

第一性原则优先：

```text
Provider 不支持 native reasoning
→ 使用确定性阶段说明
```

例如：

```text
Controller：正在根据当前问题、证据状态和可用工具决定下一步。
Answer：正在根据已冻结的可引用证据组织回答。
Rewrite：正在根据证据审查结果修正未被支持的表述。
```

若未来确有产品证据证明动态 public explanation 的收益明显，再单独立需求；当前不得为了 fallback 再调用一次 Main LLM，增加延迟和语义漂移。

## 7.3 Provider 声称支持但本次没有 reasoning

区分：

```text
capability_can_request = true
reasoning_available = false
```

该情况必须进入 Trace，不能静默伪装为“正常 native reasoning”。

用户层可保留一次简短阶段说明，但不得标记为“模型原生推理”。

---

# 8. 三段 Main 生命周期统一约束

## 8.1 Controller

```text
Model reasoning → SSE/Trace/UI
Model content → JSON buffer
JSON protocol validate
→ AgentDecision
→ Execution decision event
```

用户看到：

```text
Main · Controller · 模型原生推理
→ Tool
→ Main · Controller · 模型原生推理
```

而不是只看到 `decision.reason`。

## 8.2 Answer Generator

```text
Frozen Evidence Snapshot
→ Main
→ reasoning delta → SSE/Trace/UI
→ content delta → Candidate V1 buffer only
→ Reviewer
```

用户应能看到模型如何：

```text
筛选证据
识别上下文边界
处理冲突
规划引用
决定哪些事实不能写
组织最终结构
```

## 8.3 Grounded Rewrite

```text
Candidate V1
+ Reviewer claim_reviews
+ rewrite_actions
+ Frozen Snapshot
→ Main Rewrite
→ reasoning delta → SSE/Trace/UI
→ content delta → Candidate V2 buffer only
→ Reviewer #2
```

用户应能看到真正的纠错过程：

```text
Reviewer 指出了哪个 Claim
为什么它无支持/冲突
哪些已支持 Claim 必须保留
哪些文本必须删除/缩限
如何根据证据改写
```

---

# 9. Reviewer 用户可见纠错协议

## 9.1 REVISE 不再只是一句 generic system notice

当 Reviewer 判定：

```text
verdict = REVISE
```

用户主界面至少能够表达：

```text
证据审查发现 N 个需要修正的 Claim

× Claim C3：……
  状态：Unsupported
  原因：当前 Evidence Snapshot 未提供该事实关系的支持
  动作：删除 / 缩限 / 按证据纠正
```

不得泄露内部敏感调试字段，但不得把真实 Claim Finding 压缩成：

```text
“答案没通过，正在重新组织。”
```

## 9.2 PASS 路径保持克制

正常 PASS 不需要刷大量日志。

可以：

```text
✓ 证据审查通过
```

或者仅将 Activity 原位更新为完成。

重点透明度应集中在：

```text
错误发生
→ 错在哪里
→ Main 如何修
→ 最后通过
```

---

# 10. 前端目标 Block 模型

继续继承现有“用户可见 Block 不等于全量 Trace”的思想，但需要收敛语义。

建议用户可见核心：

```text
ReasoningBlock
ToolBlock
ReviewFindingBlock / Review Activity
SystemEventBlock
MarkdownBlock
```

是否把 ReviewFinding 单独作为第五类 Block，由子 PRD 04/05 在实现前根据现有 ActivityBlock 演进成本裁决；**不得为了保持“四类 Block”形式上的整齐而把 Claim Finding 塞成无法表达结构的纯字符串。**

第一性原则优先级：

```text
信息语义正确
>
历史 union 类型数量不变
```

---

# 11. 用户目标体验

一个发生纠错的完整 turn 应接近：

```text
● Main · Controller · 模型原生推理
用户询问的是 PipelineWebGL 的主要用途……
当前主体已经确认……
证据池为空，因此先做一次定向检索……

◇ 知识库检索
PipelineWebGL 主要用途 功能定位
✓ 新增若干候选 / 可引用证据

● Main · Controller · 模型原生推理
当前已有直接支持用途的证据……
继续检索收益有限，进入回答生成……

● Main · Answer · 模型原生推理
Evidence [1]、[3] 可直接支持……
Evidence [5] 属于相关模块，不能把其 WebRTC 能力归到当前实体……
因此答案应围绕……

◉ 正在核对回答与证据……

⚠ 证据审查发现 1 个需要修正的 Claim
× “PipelineWebGL 使用 WebRTC 实时传输……”
  当前 Evidence Snapshot 不支持该关系。

● Main · Rewrite · 模型原生推理
Reviewer 指出的确是跨实体属性漂移……
我要删除 WebRTC 机制描述，保留直接证据支持的三维管线展示能力……
同时检查相邻句是否依赖这一错误前提……

✓ 第二次证据审查通过

[最终正式答案]
PipelineWebGL …… [1][3]
```

这才是本 PRD 的产品验收参照。

---

# 12. Trace 与可观测性

每次用户可见 Main model call 必须记录：

```text
call_id
role
stage
provider
model
reasoning_requested
reasoning_capability
reasoning_available
reasoning_chars_in
reasoning_chars_emitted
content_chars
fallback_explanation_used
elapsed_ms
error
```

关键验收指标：

```text
reasoning_chars_in == reasoning_chars_emitted
```

允许的差异只有：

```text
明确配置的安全截断 / 存储上限
```

但主聊天实时流不得因为 `trace_reasoning_max_chars` 被误截断。

Trace 的存储策略与用户实时展示策略必须解耦。

---

# 13. 配置原则

## 13.1 不再让“产品默认行为”依赖隐式 summarized

Agent 模式下新的目标默认：

```text
reasoning_stream_policy = token
```

若未来需要：

```text
never
```

只能是显式产品/隐私配置，而不是当前错误主路径的延续。

## 13.2 summary 不再作为普通用户实时 reasoning 模式

`summary/summarized` 若保留，只允许用于：

```text
Trace 压缩
历史存档
调试导出
```

不得再作为“有 native reasoning 但不给用户逐增量”的默认用户体验。

---

# 14. Forbidden Implementation

以下任一出现即判定实现方向错误。

## F-01 只把配置改成 token 就宣布完成

这只能证明现有链路可能工作，不能解决重复实现和 fallback 主备倒置。

## F-02 继续每个 Main 阶段先生成 public_explanation

这与“native reasoning 主路径”直接冲突。

## F-03 用 decision.reason 冒充 native reasoning

`decision.reason` 是结构化决策结果，不是 Provider reasoning stream。

## F-04 Answer reasoning 不展示，只展示 Controller reasoning

用户明确要求答案总结阶段也透明。

## F-05 Reviewer REVISE 仍只显示一行泛化文案

必须至少展示经过裁剪的真实 Claim-level Finding。

## F-06 展示 Rewrite status，但不展示 Rewrite native reasoning

“正在重新组织”不是纠错思考。

## F-07 为了像 Harness 而提前流 Candidate 正文

Publication Gate 不得退化。

## F-08 Controller / Answer / Rewrite 继续长期维护三份 streaming 模板代码

必须收敛单点 ModelStreamRunner / 等价单一执行器。

## F-09 前端重新推断业务事实

UI 不得根据文字猜 Reviewer status、Tool success 或 Evidence state。

## F-10 通过隐藏事件而不是修正协议实现“界面变干净”

Trace 与 Runtime 事实必须保留。

---

# 15. 子 PRD 拆分与实施顺序

## 子 PRD 01：Native Reasoning 主备语义与 Public Explanation Fallback 收口

目标：

```text
native reasoning = 主路径
public explanation = fallback
```

完成后应首先验证：开启 qwen3.5 / DeepSeek reasoning 时，用户能看到真实长 reasoning，而不是一两句总结。

## 子 PRD 02：统一 ModelStreamRunner 与单一协议

目标：消除 Controller / Answer / Rewrite 重复模型流实现，建立单点真源。

## 子 PRD 03：Controller + Answer + Rewrite 三段 Main Reasoning 全生命周期接线

目标：确保三个 Main 阶段全部逐 delta 进入 SSE / Trace / UI，且 Answer/Rewrite content 继续内部 buffer。

## 子 PRD 04：Reviewer Claim Finding → Main Rewrite 纠错透明闭环

目标：把“为什么错 → 怎么修 → 再审”完整可视化。

## 子 PRD 05：前端 Block Assembler、自由滚动、兼容清理与真实 E2E

目标：将新协议稳定投影到用户界面，并用真实模型证明不是只有单元测试能工作。

实施顺序：

```text
01
↓
02
↓
03
↓
04
↓
05
```

允许 01 做最小验证后立即观察体验；不允许跳过 02 长期在旧重复代码上继续打补丁。

---

# 16. 总 DoD

只有以下全部满足，才允许总 PRD 改为“已完成”。

## 架构

- [ ] Model Stream 与 Execution Event 职责分离。
- [ ] Controller / Answer / Rewrite 使用单一 ModelStreamRunner 或等价单点执行器。
- [ ] `public_explanation` 不再是 native reasoning 存在时的并行主路径。
- [ ] 不存在三份长期重复的 reasoning streaming 生命周期实现。

## Native Reasoning

- [ ] Controller native reasoning 逐 delta 可见。
- [ ] Answer Generator native reasoning 逐 delta 可见。
- [ ] Grounded Rewrite native reasoning 逐 delta 可见。
- [ ] Provider 返回 reasoning 时不再只展示简短 public explanation。
- [ ] 没有 native reasoning 时存在明确 fallback，且不会冒充“模型原生推理”。

## Answer Safety

- [ ] Candidate V1 content 不提前进入正式 Markdown。
- [ ] Candidate V2 content 不提前进入正式 Markdown。
- [ ] 只有 `final_answer` / Publication 后正文成为正式答案。

## Reviewer / 纠错

- [ ] REVISE 时用户能看到真实 Claim-level 问题摘要。
- [ ] Claim Finding 与 rewrite_actions 能一一追踪。
- [ ] Main Rewrite reasoning 能看到其如何响应 Reviewer 纠错。
- [ ] Reviewer #2 PASS/失败生命周期用户可理解。

## Trace

- [ ] 每个 Main call 都有独立 `call_id + stage`。
- [ ] reasoning chars 输入/发布计数可核对。
- [ ] 无 reasoning fallback 可观测。
- [ ] 全量 Runtime / Reviewer 事实继续保留，不因 UI 收敛丢失。

## 前端

- [ ] Reasoning delta 到达即原位增量显示。
- [ ] Controller/Answer/Rewrite 按真实发生顺序穿插 Tool/Review。
- [ ] 用户上滑后 streaming 不强制拉回底部。
- [ ] 未知 ExecutionEvent 不自动变成主聊天垃圾节点。
- [ ] Linear/Pipeline 不被 Agent Block 污染。

## 测试

- [ ] Provider parser 专项测试。
- [ ] ModelStreamRunner 单元测试。
- [ ] Controller/Answer/Rewrite 三段 reasoning 专项测试。
- [ ] Reviewer REVISE→Rewrite→PASS 纠错闭环测试。
- [ ] 前端 projector/assembler 流式测试。
- [ ] HTTP/SSE E2E。
- [ ] 至少使用当前真实 Main reasoning 模型完成真实在线验收。
- [ ] 至少一例真实 REVISE→Rewrite→PASS 能在 UI/Trace 中看到完整纠错链。

---

# 17. 完成判定纪律

禁止使用：

```text
“代码已经接了”
“事件已经定义了”
“单测通过了”
“页面上已经有 Reasoning 卡片”
```

作为总 PRD 完成依据。

最终必须能够用一个真实请求证明：

```text
Controller reasoning
→ Tool
→ Controller reasoning
→ Answer reasoning
→ Reviewer finding
→ Rewrite reasoning（若 REVISE）
→ Reviewer final verdict
→ Published Answer
```

并且 Trace 能逐 call 对账。

如果真实模型只返回：

```text
reasoning_available=false
```

则该请求只能验收 fallback，不能验收 native reasoning 主路径。

---

# 18. 最终裁决

本次重构的目标不是“让 RAG 看起来更像 DeepSeek Harness”。

最终原则是：

> **真实模型输出优先于生成的说明；真实系统事实优先于 UI 推断；Main 的决策、答案综合、证据纠错三类 reasoning 同等透明；Reviewer 用可验证的结构化 Finding 解释错误；Candidate 与正式 Publication 永久隔离。**

当这五件事同时成立时，当前“只有总结性简短语句”的问题才算真正从根因上解决。
