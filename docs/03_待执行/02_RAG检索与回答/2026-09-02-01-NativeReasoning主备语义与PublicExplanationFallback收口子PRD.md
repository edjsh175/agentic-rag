# 子 PRD 01：Native Reasoning 主备语义与 Public Explanation Fallback 收口

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 基线日期 | 2026-09-02 |
| 状态 | **已完成** |
| 所属总 PRD | `2026-09-02-Agent原生Reasoning与证据纠错透明执行流重构总PRD.md` |
| 目标 | **把 native reasoning 恢复为 Main 用户可见思考的主路径；public explanation 只在本次 Main 调用没有 native reasoning 时兜底。** |
| 非目标 | 本阶段不做统一 ModelStreamRunner 大重构，不改 Reviewer 协议，不提前发布 Candidate 正文。 |

---

# 1. 当前根因

当前代码已经能拿到 provider reasoning，但实际用户体验仍以简短总结为主，至少存在：

```text
reasoning_stream_policy 默认 summarized
public_explanation 每个 Main 阶段主动生成
summary event 与前端消费不一致
native reasoning 与 fallback 同时出现
```

因此第一阶段要先恢复正确主备语义，验证问题根因。

---

# 2. 第一性原则

正确关系只有：

```text
本次 Main call 有 native reasoning
→ 展示 native reasoning
→ 不展示 public_explanation

本次 Main call 没有 native reasoning
→ 展示 public_explanation fallback
```

禁止：

```text
先 public explanation
→ 后 native reasoning
```

也禁止：

```text
有 native reasoning
→ summary 后再展示
```

作为 Agent 默认体验。

---

# 3. 实施范围

重点检查并修改：

```text
rag_knowledge/config.py
rag_knowledge/services/execution_explanation.py
rag_knowledge/services/agent_orchestration/runtime.py
rag_knowledge/services/rag.py
web/src/api/index.ts
web/src/types/index.ts
web/src/utils/agentBlockProjector.ts
相关测试
```

## 3.1 配置

Agent 默认用户流改为：

```text
reasoning_stream_policy = token
```

`summary/summarized` 若保留，只服务 Trace/历史压缩，不作为普通 Agent 用户默认模式。

## 3.2 Public explanation

当前 `generate_public_explanation()` 不得继续在 Main Controller / Answer / Rewrite 进入模型调用前无条件执行。

优先方案：

```text
native reasoning available → 不创建 fallback block
native reasoning unavailable → 由 deterministic stage fallback 生成简短说明
```

首阶段不要求删除整个文件，但必须消除“每次再调用一个 LLM 生成一句说明”的默认生产路径。

## 3.3 前端来源标识

必须能明确区分：

```text
contentSource = native_reasoning
contentSource = public_explanation
```

fallback 不得被标成“模型原生推理”。

---

# 4. 关键实现难点：何时知道本次没有 reasoning

不能只看 provider capability：

```text
can_request = true
```

并不代表本次一定返回 reasoning。

因此需要对单次 call 记录：

```text
reasoning_requested
reasoning_available
reasoning_chars
```

如果调用运行期间已经收到第一段 native reasoning：

```text
立即创建 ReasoningBlock
```

如果直到 call 结束都没有 reasoning：

```text
补一个 fallback explanation
```

如果产品要求等待期间不能完全空白，可以使用 Activity 状态：

```text
正在处理…
```

但 Activity 不得冒充 reasoning。

---

# 5. 验收案例

## Case A：Qwen3.5 / Ollama 返回 thinking

必须看到：

```text
llm_reasoning_start
llm_reasoning_delta × N
llm_reasoning_end(reasoning_available=true)
```

UI：

```text
Main · ... · 模型原生推理
```

且同一 call 不出现 `public_explanation`。

## Case B：不支持 reasoning 的模型

必须：

```text
reasoning_available=false
→ fallback explanation
```

UI 明确标记“公开执行说明”或等价语义。

## Case C：支持 reasoning 但本次没有返回

Trace 必须记录：

```text
reasoning_requested=true
reasoning_available=false
```

UI 使用 fallback，不伪造 native reasoning。

---

# 6. DoD

- [x] Agent 默认 reasoning 策略为 token。
- [x] native reasoning 存在时逐 delta 到达前端。
- [x] 同一个 Main call 有 native reasoning 时不显示 public explanation。
- [x] 无 native reasoning 时有 fallback。
- [x] fallback 不再默认增加第二次 Main LLM 调用。
- [x] fallback 明确不是模型原生推理。
- [x] Controller、Answer、Rewrite 当前现有路径均先通过这一主备语义验收。
- [x] 单测覆盖有 reasoning / 无 reasoning / capability 与实际输出不一致三种场景。
- [x] 至少一条真实模型请求看到连续多段 native reasoning，而非一两句总结。

---

# 7. 禁止提前结项

仅修改：

```ini
reasoning_stream_policy=token
```

不能宣布该子 PRD 完成。

必须同时证明：

```text
fallback 主备关系正确
+
真实 UI 只显示正确来源
```
