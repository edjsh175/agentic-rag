# 子 PRD 04：Reviewer Claim Finding 与 Main Rewrite 纠错透明闭环

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 基线日期 | 2026-09-02 |
| 状态 | **待执行** |
| 所属总 PRD | `2026-09-02-Agent原生Reasoning与证据纠错透明执行流重构总PRD.md` |
| 前置 | 子 PRD 01、02、03 |
| 目标 | **把 Reviewer 揪出错误的真实结构化事实与 Main Rewrite 的 native reasoning 串成一个用户可理解、可核对、可追踪的纠错闭环。** |
| 核心裁决 | Reviewer 展示结构化 Finding；Main 展示真实 Rewrite reasoning；两者不得互相替代。 |

---

# 1. 当前问题

当前 REVISE 路径已经有真实业务闭环：

```text
Candidate V1
→ Helper Grounding Reviewer
→ claim_reviews
→ rewrite_actions
→ Main Grounded Rewrite
→ Candidate V2
→ Reviewer #2
```

但用户主界面通常只能感知：

```text
正在核对回答与证据…
刚刚的答案被拒绝了，正在重新组织…
```

这会把最有价值的部分抹掉：

```text
哪里错了
为什么错
证据缺口是什么
Reviewer 要求怎么处理
Main 实际准备怎么修
修完后为什么可以发布
```

---

# 2. 第一性原则

纠错透明度不是展示更多“状态文案”，而是展示两类真实事实：

## A. Reviewer Finding

```text
系统认定哪里存在 grounding 问题
```

来源必须是 Reviewer 已通过协议校验的：

```text
verdict
coverage
summary
claim_reviews
rewrite_actions
```

## B. Main Rewrite Reasoning

```text
Main 如何响应这些 Finding 并修改 Candidate
```

来源必须是 Provider native reasoning。

因此：

```text
Reviewer Finding ≠ Rewrite reasoning
```

用户应看到二者连续发生。

---

# 3. Reviewer Finding 用户可见投影

## 3.1 REVISE 时必须展示真实问题

最低可见信息：

```text
本轮发现多少个需要修正的 Claim
Claim 的用户可理解文本/摘要
status：unsupported / contradicted / 其他需修正状态
Reviewer summary / reason 的安全摘要
对应 rewrite action 的用户可理解动作
```

示例：

```text
证据审查发现 1 个需要修正的表述

× “PipelineWebGL 使用 WebRTC 进行实时三维数据传输”
  当前证据：未支持
  原因：当前 Evidence Snapshot 没有证明 PipelineWebGL 与 WebRTC 传输机制之间的关系。
  修正：删除该无依据机制描述，保留直接受证据支持的用途说明。
```

## 3.2 不得泄露无价值内部字段

默认不直接向普通用户倾倒：

```text
Snapshot UUID
内部 claim_id（若没有产品意义）
raw JSON
protocol retry diagnostics
内部 score
完整 Reviewer prompt
```

Debug/Trace 继续保留。

## 3.3 支持证据展示

若 `claim_reviews` 已有合法 Evidence ID，可在用户层显示简洁证据指向；不得由前端自行猜引用。

---

# 4. Main Rewrite Reasoning 接线

Reviewer Finding 出现后，下一段用户可见模型 reasoning 必须来自：

```text
stage = grounded_retry
role = main
contentSource = native_reasoning
```

预期语义可能包括：

```text
识别 Reviewer 指出的错误
判断错误是无支持、冲突还是范围过宽
识别必须保留的 supported claims
识别必须删除/缩限的表述
检查是否存在由错误 Claim 派生的相邻结论
按 Frozen Evidence 重建安全表述
```

不允许用：

```text
“正在根据审查结果修正回答”
```

替代这一真实 reasoning 主路径。

---

# 5. Activity / System Event 的职责

Activity 仍然有价值，但职责仅是生命周期：

```text
◉ 正在核对回答与证据…
⚠ 发现需要修正的内容
◉ 正在重新组织答案…
✓ 第二次证据审查通过
```

它不承担：

```text
详细解释哪里错
详细解释 Main 怎么修
```

这些分别属于 Finding 与 Reasoning。

---

# 6. 多轮 Reviewer Resume

如果存在：

```text
REVISE
→ 补检
→ 再生成
→ 再审
→ 再 REVISE
```

必须按轮次可追踪：

```text
review_round
rewrite_round
call_id
candidate_version
```

UI 不得把第二轮 Finding 覆盖第一轮，导致用户无法理解演化过程。

---

# 7. PASS / NO_SAFE_ANSWER / ERROR

## PASS

保持克制：

```text
✓ 证据审查通过
```

可作为 Activity 原位完成，不要求展示 Reviewer 自由 reasoning。

## NO_SAFE_ANSWER / 无法安全发布

必须清楚显示：

```text
为什么无法安全发布
是证据不足、冲突未解决还是 Reviewer/协议异常
```

但不得泄露内部异常栈。

## Reviewer Error

应显示系统事实：

```text
当前证据审查服务异常，未发布未经审查的候选回答。
```

并保持 fail-closed。

---

# 8. 前端数据模型要求

实现前需要在两种方案中择一，并在实施记录中说明原因：

### 方案 A：新增 ReviewFindingBlock

适合结构化展示多个 Claim、状态、动作和证据。

### 方案 B：扩展现有 Activity/SystemEvent

只有在能够自然承载结构化 Claim 列表、不退化成 JSON 文本、不污染 SystemEvent 语义时才允许。

第一性原则：

```text
语义清楚 > 为了维持历史“四类 Block”而硬塞
```

---

# 9. 测试案例

## Case A：单 Claim unsupported

```text
V1
→ REVISE
→ 1 Finding
→ Rewrite reasoning
→ V2
→ PASS
```

必须验证 UI 顺序。

## Case B：contradicted Claim

必须明确展示“与证据冲突”，不能泛化成“证据不足”。

## Case C：多个 Claim

Finding 列表与 rewrite_actions 一一对应，不丢失、不错配。

## Case D：Rewrite 后仍 REVISE

第二轮不能覆盖第一轮；最终不得误发布。

## Case E：Reviewer error

Candidate 不发布；系统提示清晰。

---

# 10. DoD

- [ ] REVISE 时主界面不再只有 generic 一句提示。
- [ ] 至少展示安全裁剪后的 Claim-level Finding。
- [ ] Finding 来源可追踪到 Reviewer 结构化结果。
- [ ] rewrite_action 与对应问题可匹配。
- [ ] Main Rewrite native reasoning 紧随纠错事实真实流出。
- [ ] 多轮纠错不会覆盖旧轮次。
- [ ] Reviewer PASS/失败生命周期用户可理解。
- [ ] Reviewer 自由 reasoning 不被伪装成结构化 Finding。
- [ ] Reviewer error 继续 fail-closed。
- [ ] 真实模型至少完成一次 REVISE→Rewrite→PASS UI/Trace 对账。

---

# 11. 禁止实现

- 禁止仅把 `review.summary` 放进一条 notice 就宣布完成。
- 禁止前端从 Candidate 文本自行推断错误 Claim。
- 禁止用 Reviewer 自由 reasoning 替代正式 claim_reviews。
- 禁止用固定 Rewrite 文案替代 Main native reasoning。
- 禁止为了展示纠错而提前曝光 Candidate V1 全文作为正式答案。
