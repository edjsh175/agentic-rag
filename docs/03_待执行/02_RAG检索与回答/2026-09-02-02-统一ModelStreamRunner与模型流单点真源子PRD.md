# 子 PRD 02：统一 ModelStreamRunner 与模型流单点真源

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 基线日期 | 2026-09-02 |
| 状态 | **已完成** |
| 所属总 PRD | `2026-09-02-Agent原生Reasoning与证据纠错透明执行流重构总PRD.md` |
| 前置 | 子 PRD 01 已验证 native reasoning 主备关系 |
| 目标 | **把 Controller / Answer / Rewrite 三套重复的 streaming 生命周期收敛为一个 ModelStreamRunner / 等价单一执行器，并建立 Model Stream 单点协议。** |
| 非目标 | 不改变 Agent 决策权，不改变 Evidence/Reviewer 业务语义，不改最终回答门禁。 |

---

# 1. 问题定义

当前同一种 Main LLM streaming 行为分散在多个业务模块：

```text
AgentLoop._adecide_via_llm
RAGService Answer Generation
Grounded Rewrite
Query contextualizer 等其他独立调用
```

重复处理：

```text
provider capability
think flag
num_predict
reasoning start/delta/end
content buffer
reasoning_chars
elapsed_ms
异常
record_model_call
Trace/SSE callback
```

这已经造成不同阶段策略不一致。

第一性原则：

> **Provider 原始流只解析一次；模型调用生命周期只实现一次；业务模块只决定如何消费 content，不重复实现 stream plumbing。**

---

# 2. 目标边界

新增或收敛出一个单一模型流执行器，例如：

```python
ModelStreamRunner.run(...)
```

名称可依据项目风格调整，但职责必须唯一。

输入至少包括：

```text
endpoint
messages
call_id
role
stage
thinking policy
format/json schema
num_predict
num_ctx
timeout
temperature
```

输出统一为 ModelStreamEvent / 回调 + 最终 buffer。

---

# 3. 协议建议

内部事件至少覆盖：

```text
model_call_started
model_reasoning_started
model_reasoning_delta
model_reasoning_finished
model_content_delta
model_call_finished
```

每个事件共享：

```text
call_id
role
stage
provider
model
sequence/timestamp（按现有能力选择）
```

最终结果结构至少包含：

```text
content
reasoning_available
reasoning_chars
content_chars
elapsed_ms
error/fallback
```

---

# 4. 业务模块如何消费

## Controller

```text
reasoning event → UI/Trace
content delta → JSON buffer
最终 content → AgentDecision protocol validate
```

## Answer Generator

```text
reasoning event → UI/Trace
content delta → Candidate V1 buffer
```

## Grounded Rewrite

```text
reasoning event → UI/Trace
content delta → Candidate V2 buffer
```

业务模块不得再自己构造 reasoning start/end 生命周期。

---

# 5. Public fallback 的归属

fallback 判断应在统一模型调用生命周期或其紧邻上层完成，避免每个阶段分别判断。

期望：

```text
ModelStreamRunner 完成 call
→ reasoning_available?
   ├─ true：已经发 native reasoning
   └─ false：上层用户可见策略触发 stage fallback
```

不要让 ModelStreamRunner 直接知道 Vue/ReasoningBlock。

---

# 6. Trace 与 SSE

必须保证同一事件对象或同一规范化数据源能够：

```text
→ Trace
→ SSE
```

不得维护：

```text
Trace 一套 reasoning 字段
SSE 另一套 reasoning 字段
```

需要兼容旧 wire event 时，可短期做 adapter：

```text
ModelStreamEvent
→ legacy llm_reasoning_* wire adapter
```

但 adapter 必须有删除计划，不得成为第二套真源。

---

# 7. 迁移范围

优先迁移：

```text
Main Controller
Main Answer Generator
Main Grounded Rewrite
```

其他 helper/上下文化调用不要求本子 PRD 同时全部迁完，除非它们复用该执行器不会扩大风险。

原则：先把用户可见 Main 生命周期单点化。

---

# 8. 测试要求

单元测试至少覆盖：

```text
reasoning + content 正常交错
只有 content
只有 reasoning 后 content 为空
provider error
stream 中途 error
JSON mode
reasoning chars 统计
content chars 统计
call_id/stage 不串线
```

架构测试：

- 禁止 Controller / Answer / Rewrite 再直接复制 `async for part in achat_stream_parts` 的完整生命周期模板。
- 若仍存在直接调用，必须说明为何不属于用户可见 Main stream。

---

# 9. DoD

- [x] 建立 ModelStreamRunner 或等价单一执行器。
- [x] Controller 改用单一执行器。
- [x] Answer Generator 改用单一执行器。
- [x] Grounded Rewrite 改用单一执行器。
- [x] reasoning/content 的统计、异常、生命周期只有一个生产实现。
- [x] Trace/SSE 使用同一规范化模型流事实。
- [x] 旧重复逻辑删除，不保留长期双轨。
- [x] 全部专项测试通过。
- [x] `git diff` 不出现无关重构。

---

# 10. 禁止实现

- 禁止为了“抽象漂亮”顺手重构整个 LLM 层。
- 禁止改变 Helper Reviewer 的 verdict 权限。
- 禁止把 Tool ExecutionEvent 混入 ModelStreamRunner。
- 禁止让 ModelStreamRunner 依赖前端 Block 类型。
- 禁止保留三个旧实现作为 fallback。
