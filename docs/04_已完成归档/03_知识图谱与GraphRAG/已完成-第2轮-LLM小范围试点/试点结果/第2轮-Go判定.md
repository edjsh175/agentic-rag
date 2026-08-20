# Round-2 LLM 试点 Go/No-Go

- **日期**：2026-07-14
- **结论**：**GO**（可进入第 3 轮安全全量重建准备）
- **Batch**：`4f735904-07f9-471b-ace7-73cbf95f0171`
- **范围**：`doc_category=StampTools`，`--include-llm --limit 80`
- **模型**：`qwen3:30b` @ `http://192.168.10.158:11434`
- **备份**：`data/backups/rag_relational_pre_round2.db`

---

## 1. 门槛对照

| 指标 | 门槛 | 实测 | 结果 |
|------|------|------|------|
| 人工抽检 precision（实体+关系） | ≥ 70% | **82.9%**（35 条非 diagnostic，29/35） | PASS |
| `invalid_schema` + `missing_evidence` 占比 | < 15% | 33/284 ≈ **11.6%**（missing_evidence=0） | PASS |
| `type_conflict` 进入 apply | 0 | 0 | PASS |
| apply 后新增 stale links | 否 | stale=0 | PASS |
| 人工/seed/profile 事实 | 无丢失 | export-manual summary 不变；Task 8.1 Gate **PASS** | PASS |
| 业务实体类型 ≥ 3 类非 Section/DataTable | 是 | 新增 **Procedure/Step/ConfigItem/EnvironmentComponent/Error/Module** | PASS |

---

## 2. 抽取与审批摘要

```text
extract stats:
  chunks=80
  rule_candidates=864
  llm_candidates=331

quality --llm:
  total_llm_candidates=284
  high_confidence(≥0.9)=67
  invalid_schema=33
  evidence_text_not_found=27
  type_conflict=0

分拆审批（禁止 approve-all）:
  approved 并 apply：entity 64 + relation 14 = 78
  拒绝：schema 非法边、低置信、section 疑似重复、泛化 EnvironmentComponent、规则重候选等
```

正式库变化：

| 指标 | pre_round2 | post_round2 | Δ |
|------|----------:|----------:|--:|
| entities | 1130 | 1194 | +64 |
| relations | 2683 | 2697 | +14 |
| entity_chunk_links | 4004 | 4068 | +64 |
| llm_fact_count | 0 | 78 | +78 |
| business_entity_count（Gate） | 99 | 163 | +64 |

新增业务类型计数（post audit）：Procedure 18、Step 3、ConfigItem 39（+28）、EnvironmentComponent 6、Error 5、Module 1。

---

## 3. 工程修复（本轮必要）

qwen3 默认 thinking 会导致 `format=json` 下 `message.content` 为空并大量超时。已在 `llm_extractor.py`：

1. Ollama 请求增加顶层 `"think": false`
2. content 为空时回退读取 `thinking`
3. HTTP timeout 60s → 180s

---

## 4. 风险与第 3 轮建议

```text
1. LLM 仍会产出 schema 非法边（如 Tool--belongs_to-->Tool、ConfigItem--configured_by-->Tool 方向颠倒）
   → 第 3 轮全量前必须保留分拆审批 + apply 前 inspect_batch
2. 部分 ConfigItem/EnvironmentComponent 偏细碎（坐标系代码、纹理格式等）
   → 可在 prompt / 后处理中收紧 EnvironmentComponent 与纯数字 ConfigItem
3. Command 在本批 apply 中几乎未留下（原始候选仅 1 条）
   → 不阻断 GO；第 3 轮可观察全库是否补足
4. 第 1 轮遗留的 Document→深层 Section 边仍在，留给 rebuild-safe 收敛
```

---

## 5. 交付物索引

```text
# 现归档位置（2026-07-20 data/ 整理后）
data/archive/graph_rounds/graph_audit_pre_round2.json
data/archive/graph_rounds/graph_audit_post_round2.json
data/archive/graph_rounds/manual_graph_facts_pre_round2.json
data/archive/graph_rounds/manual_graph_facts_post_round2.json
data/archive/graph_rounds/graph_round2_batch_export.json
data/archive/graph_rounds/graph_round2_approve_ids.json
data/archive/graph_rounds/graph_round2_quality.json
# 备份：文档路径 data/backups/rag_relational_pre_round2.db（以现场是否仍存在为准）
docs/3_待办清单/知识图谱语义抽取/已完成-第2轮-LLM小范围试点/试点结果/泄漏样本20.json
docs/3_待办清单/知识图谱语义抽取/已完成-第2轮-LLM小范围试点/试点结果/泄漏样本20.md
docs/3_待办清单/知识图谱语义抽取/已完成-第2轮-LLM小范围试点/试点结果/人工抽检样本.csv
docs/3_待办清单/知识图谱语义抽取/已完成-第2轮-LLM小范围试点/试点结果/人工抽检精度.json
docs/3_待办清单/知识图谱语义抽取/已完成-第2轮-LLM小范围试点/试点结果/第2轮-Go判定.md
```

路径约定见 `docs/5_操作指南与规范/data目录约定.md`。
