# 管理端完整检索证据链 UX 规格（FR-09）

状态：设计规格，**本轮不改** `AdminChunksView.vue` / API / 用户侧 SourcePanel。

---

## 1. 目标

默认用户界面仍只展示 **已引用证据**。调试与管理视图必须能查看：

1. **cited** — 支撑答案事实、正文出现 `[n]` 的来源
2. **retrieved_uncited** — 进入检索上下文但未被回答引用
3. **gap** — 问题要求但未覆盖的章节/步骤（对照 gold `required_facts` 或规划锚点）
4. **conflict** — 同一事实键存在多值的证据对

---

## 2. 建议 API 形状（未来）

`GET /admin/qa-debug/{request_id}` 或问答响应附加 `evidence_chain`（仅 admin / debug flag）：

```json
{
  "question": "",
  "answer": "",
  "evidence_chain": {
    "cited": [
      {"index": 1, "source": "", "section_path": "", "chunk_id": "", "snippet": ""}
    ],
    "retrieved_uncited": [
      {"index": 4, "source": "", "section_path": "", "chunk_id": "", "drop_reason": "not_cited"}
    ],
    "gaps": [
      {"required_fact": "TCP 外网部署", "status": "missing"}
    ],
    "conflicts": [
      {
        "key": "tls-listening-port",
        "values": [
          {"value": "5439", "source": "", "chunk_id": ""},
          {"value": "5349", "source": "", "chunk_id": ""}
        ]
      }
    ]
  }
}
```

约束：

- 普通用户响应 **不** 默认下发完整链，避免信息过载与敏感原文扩散。
- `drop_reason` 至少覆盖：`not_cited` / `filtered_invalid_index` / `budget_trim` / `rerank_drop`。

---

## 3. 管理 UI 信息架构

单页分区（自上而下）：

1. 问题与最终回答
2. 证据漏斗：`retrieved → context → cited` 数量条
3. 四栏列表：cited / uncited / gaps / conflicts
4. 原始检索列表（可折叠），按 score 排序

交互：

- 点击条目高亮对应 chunk 正文
- conflict 行用并列值，不做「采纳」单选

---

## 4. 与 Round 0E 关系

- 本文件只冻结字段与展示职责。
- 正式实现排在 Chunk 基石改善与 FR-10 打分器对比之后，避免在旧短 Chunk 上「美化」错误证据链。
