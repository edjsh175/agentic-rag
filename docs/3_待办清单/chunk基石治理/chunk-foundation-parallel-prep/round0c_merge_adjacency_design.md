# Round 0C：短 Section 合并与邻接设计（冻结稿）

状态：生产模块已接线（`section_chunk_merge` → `FileLoader` DOCX）。P1 身份修复与同 L1 有界短 L2 叶子合并已完成隔离复测：`section_id` 文档作用域、`chunk_uid` 全局唯一、Final 后重算 prev/next，Element/Raw/来源 Section 血缘随合并保留。报告见 `../chunk-foundation-round0c-l2-isolation/`；**不写正式 Chroma**。当前仍为 No-Go。

---

## 1. 输入 / 输出

**输入**：`UnstructuredChapterLoader` 产出的 Canonical Element 序列（现有 DOCX 结构化解析）。

**输出（仿真单位 `MergedUnit`）**：

```text
unit_id
source
section_path / section_id
content_markdown / searchable_text
content_type
char_len
merged_from          # element_order 列表
chunk_index_global
chunk_index_in_section
prev_unit_id / next_unit_id
```

正式 Round 0C 再映射为 PRD FR-05 的 `prev_chunk_id` / `next_chunk_id` / `source_snapshot_hash` 等。

---

## 2. 合并规则（FR-04）

1. 同一父级（`section_path[:-1]`）下的短叶子正文可按 `element_order` 合并。
2. 标题路径前缀保留；合并后正文拼接，中间用空行。
3. 引导句（含「执行以下命令」「如下命令」）与后续命令/配置行 **强制粘连**，即使合计将超过目标下限。
4. 目标区间 300～800 字；软上限 1200；超过软上限停止并入下一块。
5. 一级路径变化仍为硬边界；同一 L1 下不同 L2 仅允许相邻、非原子、正文 `<300` 的短叶子有限合并，Bucket 达到 300 后停止，渲染后上限 800。
6. `content_type in {table, code}` 默认可独立成块，但携带当前 `section_path` 父级上下文（现有表格已带 path；Spike 保持）。
7. 允许低于 300：独立定义、单配置项、原子命令+解释、表格关联块、硬边界无法合并。

---

## 3. 邻接（FR-05）

仿真完成后按全局顺序写 `prev_unit_id` / `next_unit_id`。

与现状差异：

| 能力 | 现状 | 0C 目标 |
|---|---|---|
| 检索邻居 | `get_neighbor_chunks` 按 `source` + `section_index` 窗口 | 同 Section `prev/next` 优先，再扩相邻 Section |
| `section_index=0` 默认 | scanner 可填默认 0，邻居脆弱 | 依赖稳定 `section_id` / path，不靠默认 0 |

正式接线前检索侧保持现状，避免半完成元数据污染。

---

## 4. 表格（FR-06 摘要）

- 大表按重复表头行组切分（现有 `_split_markdown_table` 继续用）。
- 每个表块保留父级章节；向量侧用 `searchable_text`。
- Spike 阶段只统计表格块占比与 path 覆盖，不做新切分算法。

---

## 5. Spike 入口

- 模块：[`rag_knowledge/services/section_chunk_merge.py`](../../../rag_knowledge/services/section_chunk_merge.py)（生产）；`chunk_merge_spike.py` 仅作 re-export
- 接线：`FileLoader._load_text` 对 `.docx` 在 split 前调用 `apply_technical_manual_merge`
- 脚本：[`scripts/spike_short_section_merge.py`](../../../scripts/spike_short_section_merge.py)
- 测试：[`tests/test_section_chunk_merge.py`](../../../tests/test_section_chunk_merge.py)、[`tests/test_loader_section_merge.py`](../../../tests/test_loader_section_merge.py)

成功标准（Spike / 隔离对照）：相对未合并，`<100` / `<200` 比例下降；硬边界与命令粘连单测通过。正式库短块门禁见母 PRD §13（隔离实测后冻结）。

---

## 6. 2026-07-15 有界 L2 隔离复测

跨 L2 Chunk 以共同 L1 为 Anchor，并记录有序 `source_section_paths` / `source_section_ids` 与叶子标题。

| 指标 | 修正口径基线 | 有界 L2 后 | PRD |
|---|---:|---:|---:|
| Final Chunk | 305 | 234 | - |
| `<100` | 25.9% | 14.5% | ≤5% |
| `<200` | 49.2% | 22.6% | ≤15% |
| `>1200` | 5.25% | 6.8% | ≤5% |

WebRTC：`95 → 51`，`<200 71.6% → 19.6%`。跨文档 Section 碰撞、Chunk UID 重复、prev/next 断链以及 Element/Raw/来源 Section 血缘错误均为 0。

结论：`enter_0g=False`；真实门禁原因为 `<100`、`<200`、`>1200` 三项，不冻结临时阈值。

---

## 7. 2026-07-15 生产 Loader 口径最终复测

接入超长文本切分、过滤后邻接重算和显式同 Section `table_context` 标记后，按 PRD §8.2 普通文本口径复测：

| 指标 | before | after | PRD |
|---|---:|---:|---:|
| 全部 Chunk | 366 | 282 | - |
| 普通文本门禁样本 | 341 | 243 | - |
| `<100` | 33.7% | 2.5% | ≤5% |
| `<200` | 59.2% | 9.5% | ≤15% |
| `>1200` | 3.5% | 0% | ≤5% |

身份、邻接和全部来源血缘检查继续为 0 缺陷。证据：`../chunk-foundation-round0c-final-isolation/`。

结论：Chunk 基石实测门禁通过；Round 0G 仍因 FR-10 整体/category、0E/OCR 纳入范围和 Go 清单未完成而保持 `enter_0g=False`。
