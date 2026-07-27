# 执行 PRD — 经验产品化：两段式捞边与抽取硬化

- **记录日期**：2026-07-27
- **状态**：**第 1–2 刀已完成**；R7 类目扩面已移交完成；可择机问答过滤观察 / 第 4 轮
- **目录**：`docs/3_待办清单/知识图谱语义抽取/进行中-经验产品化-两段式捞边与抽取硬化/`
- **前置**：[`../已完成-第3轮补-全库实LLM补抽与抽取优化/阶段完成纪要.md`](../已完成-第3轮补-全库实LLM补抽与抽取优化/阶段完成纪要.md)
- **关联总览**：[`../2026-07-13-知识图谱PRD剩余轮次总览.md`](../2026-07-13-知识图谱PRD剩余轮次总览.md)
- **入口**：
  ```powershell
  .\venv\Scripts\python.exe run_graph_build.py recover-relations `
    --source-batch <id> --relation-type has_step --output-json data/recover_relations_plan.json
  # 确认计划后：
  .\venv\Scripts\python.exe run_graph_build.py recover-relations ... --stage
  .\venv\Scripts\python.exe run_graph_build.py apply --batch <new_id> --confirm-db-path ... --confirm-batch <new_id> --confirm-backup ...
  ```

---

## 1. 一句话目标

把 R5b/R6b/R6c 的「实体先齐套 → 再捞合法关系」从一次性脚本收成**可重复 CLI**，并小改抽取侧减少非法边/泛名垃圾；**不**开全库图召回。

---

## 2. 范围

| 做 | 不做 |
|----|------|
| `recover-relations` dry-run / stage /（再走既有 apply） | 无差别全库 LLM 盲抽 |
| 支持 `has_step` / `has_procedure` / `runs_command` 等可配置类型 | 自动 `--approve-all` |
| 可选抬升 `possible_duplicate`（分码；禁 `type_conflict`） | 打开 `graph_retrieval.enabled` |
| 泛名实体黑名单（recovery + 抽取侧最小过滤） | 推倒抽取架构 |

---

## 3. 分轮

### 第一轮（本轮）— recover-relations CLI

```text
1. RelationRecoveryService：扫描 rejected LLM 候选 → 计划实体补齐 + 合法关系
2. CLI：run_graph_build.py recover-relations --dry-run | --stage
3. stage 产出 approved batch；正式写入仍走 apply + confirm 三件套
4. 单测：齐套边可捞、缺端点不捞、type_conflict 不抬
```

### 第二轮 — 抽取硬化

```text
1. LLM 同批实体类型索引 → early_check_relation_endpoints
2. 两端类型可知且非法 → diagnostic illegal_relation_pair（不进 relations）
3. 可翻转关系（runs_command 等）优先纠偏方向再验收
4. 一端未知仍放行，留给 staging（与 R4 行为一致）
```

**验收（2026-07-27）**：`early_check_relation_endpoints` + `test_llm_extractor_rejects_illegal_relation_pair_early` 通过。

---

## 4. 成功标准

- [x] `recover-relations --dry-run` 可复现计划摘要（2026-07-27；对 R5/R6 批 dry-run 可跑通）
- [x] `--stage` 生成可 apply 的 approved batch；须走既有 apply 门禁（单测覆盖）
- [x] 单测通过：`tests/test_relation_recovery.py`
- [x] 总览挂接本目录
- [x] 泛名实体黑名单接入 recovery + LLM 抽取（`is_generic_entity_name`）
- [x] 非法类型边在 LLM 抽取侧早拒（同批类型可知时；`illegal_relation_pair`）
- [x] （可选）本地试用 anchor_chunk_filter：`config.ini` 已开；2026-07-27 retrieve-only A/B/C 复测干扰率 0.33→0
- [x] （可选移交）按类目扩抽：R7 博客/实景三维/耕地保护已完成（2026-07-27；见 [R7 纪要](../已完成-R7类目扩面-博客实景三维耕地保护/阶段完成纪要.md)）
- [x] 第 4 轮 GraphRAG 检索 A/B：PASS（2026-07-27；生产默认 on 未批准；见 [第4轮纪要](../已完成-第4轮-GraphRAG实效验收/阶段完成纪要.md)）
