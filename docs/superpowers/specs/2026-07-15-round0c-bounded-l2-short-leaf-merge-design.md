# Round 0C 有界 L2 短叶子合并设计

日期：2026-07-15
状态：已实现并完成隔离复测；仍为 No-Go

## 1. 背景与已核实事实

Round 0C 隔离复测已经证明 P1 身份修复成立：跨文档 `section_id` 碰撞、`chunk_uid` 重复、prev/next 断链以及非标题块的 element/raw 血缘缺失均为 0。

当前隔离结果为 305 个 Final Chunk：

| 指标 | 实测 | PRD §8.2 | 结论 |
|---|---:|---:|---|
| `<100` | 79/305 = 25.9% | ≤5% | 未达标 |
| `<200` | 150/305 = 49.2% | ≤15% | 未达标 |
| `>1200` | 16/305 = 5.25% | ≤5% | 未达标 |

现有审计脚本计算了三个指标的基础数据，但 Go/No-Go 只判断 `<200`，因此“唯一门禁原因是 lt200”不成立。WebRTC 的 96 个 Element 仅合成 95 个 Chunk，91 次被 `hard_boundary_l1_l2` 阻断，是其低合并率的直接原因。

直接取消 L2 硬边界不可接受：当前合并产物只有一个 `section_id/section_path`，跨叶子合并后若继续沿用第一个叶子的身份，会丢失其他叶子的 Section 归属；Finalizer 也只会渲染第一个路径前缀。

## 2. 目标与非目标

### 目标

1. 修正隔离审计，使 `<100`、`<200`、`>1200` 和四项身份/血缘检查共同决定 Go/No-Go。
2. 在不跨 L1 的前提下，有限合并同一 L1 下相邻的短 L2 叶子正文。
3. 跨叶子合并后保留每个来源 Section 的标题、路径、稳定 ID、Element ID 和 Raw Block ID。
4. 继续保持表格、代码和内嵌图片的原子性。
5. 产出新的隔离报告；未满足全部 PRD 门禁时继续保持 `enter_0g=False`。

### 非目标

1. 不放宽全局字数阈值。
2. 不把 WebRTC 从门禁分母剔除，也不单独冻结临时门槛。
3. 不写正式 `chroma_db`，不调用 `/rebuild`。
4. 本轮不治理已有 `>1200` 原始长 Element 的切分策略；审计必须如实暴露该问题。
5. 不扩展到 DOCX 以外的文档类型。

## 3. 方案选择

采用“门禁纠偏 + 血缘安全的有界 L2 合并”。

拒绝以下方案：

- 仅删除 L2 硬边界：实现最小，但会把多个 Section 伪装成第一个 Section。
- 将 WebRTC 移出分母：不会解决全局 `<100` 与 `>1200`，且改变 PRD 口径。
- 接纳任意长度的下一叶子：纯内存仿真虽可把 WebRTC `<200` 降至约 13%，但会出现把 144 字说明并入 972 字章节的过度合并。

## 4. 合并规则

现有同一路径、同一深层父级和命令引导句规则保持不变。新增规则只处理原本因 L2 变化而触发 `hard_boundary_l1_l2` 的相邻文本叶子。

两个相邻候选只有同时满足下列条件才允许进入同一跨叶子 Bucket：

1. 属于同一文档。
2. 两条路径均非空且 L1 完全相同。
3. L2 不同；不同 L1 仍是硬边界。
4. 两侧均不是 `table`、`code` 或 `embedded_image`。
5. 每个新接纳叶子的正文长度 `<300`。
6. 当前 Bucket 正文总长 `<300`；达到 300 后停止吸收下一叶子。
7. 合并后的正文总长（含标题和分隔符）`≤800`。
8. Element 顺序连续；不跨越中间原子块或空路径标题列表。

这允许 `概述 > 运行环境` 与随后同属 `概述` 的短叶子有限合并，但不会把 `快捷菜单 > 图层管理` 的 144 字内容并入 972 字的 `快捷菜单 > 飞行路径`。

## 5. Section 身份与内容渲染

### Anchor Section

跨 L2 叶子合并时，Final Chunk 的主 `section_path` 设为共同 L1，主 `section_id` 使用现有文档作用域算法基于该 L1 生成。未跨 L2 的现有合并继续使用原来的叶子路径和 `section_id`。

主 Anchor 用于邻接、Chunk 编号和兼容现有单值字段，不代替来源叶子血缘。

### 来源 Section 血缘

`MergeUnit` 和 Final Chunk 元数据新增：

- `source_section_paths: list[str]`
- `source_section_ids: list[str]`

两个列表去重后按首次出现的文档顺序排列，长度一致、一一对应。每个 `source_section_id` 继续使用 `section_id_for(document_key, original_section_path)` 生成。

未跨叶子的 Chunk 也写入单元素列表，以便审计采用统一口径。

### 内容渲染

跨叶子 Bucket 在 `content_markdown` 中为每个来源叶子保留路径标题，再拼接该叶子正文；相同来源路径的连续 Element 只渲染一次标题。Finalizer 只额外渲染共同 L1 Anchor，不重复把第一个叶子冒充整个 Chunk 的标题。

检索文本由共同 L1、各叶子路径和正文共同生成。原文正文不改写。

## 6. 数据流

1. `UnstructuredChapterLoader` 继续产生带原始 `section_path`、Element 和 Raw Block 血缘的 Canonical Element。
2. `documents_to_merge_units` 先执行现有规则；遇到 L2 变化时调用有界短叶子判定。
3. 判定通过后，Bucket 收集各叶子的正文、来源 Section、Element 和 Raw Block；判定不通过时按明确原因 flush。
4. `merge_units_to_documents` 写入 Anchor 字段和来源 Section 列表。
5. Loader Finalizer 渲染 Anchor 与叶子标题，随后 `reassign_chunk_adjacency` 基于 Final Chunk 重算身份和 prev/next。
6. 隔离审计重新加载三份手册，输出完整门禁与跨叶子血缘检查。

## 7. 可审计诊断

诊断分类新增以下明确原因：

- `merge_same_l1_short_leaf`
- `different_l1_hard_boundary`
- `next_leaf_not_short`
- `l2_bucket_target_reached`
- `l2_projected_over_target_max`

原有深层同父合并继续报告 `merge_under_target_min`。报告同时输出每份文档的跨叶子合并次数、涉及的来源路径样例，以及三个长度门禁的 before/after。

新增血缘检查：

1. 每个 Final Chunk 的 `source_section_paths` 与 `source_section_ids` 均非空、等长、顺序稳定。
2. 每对路径和 ID 重新计算后完全一致。
3. 跨叶子 Chunk 的每个来源路径标题均存在于最终内容或可检索文本。
4. `source_element_ids` 与 `source_raw_block_ids` 仍完整保留。
5. 跨文档 Section 碰撞、Chunk UID 重复和 prev/next 断链继续为 0。

## 8. 测试策略

按 TDD 顺序增加以下失败测试后再改生产代码：

1. 同 L1、不同 L2、两侧短正文能够合并。
2. 不同 L1 永不合并。
3. 下一叶子 `≥300` 不参与新增跨 L2 合并。
4. Bucket 达到 300 后不继续吸收第三个叶子。
5. 预计长度超过 800 时不合并。
6. 表格、代码、图片阻断跨叶子合并。
7. 跨叶子 Chunk Anchor 为共同 L1，并保留有序 `source_section_paths/source_section_ids`。
8. 每个叶子标题在最终内容和检索文本中可见。
9. Loader 接线后仍重算唯一 `chunk_uid` 和完整 prev/next。
10. 审计在 `<100`、`<200` 或 `>1200` 任一超标时均返回 No-Go，并列出对应原因。

定向单测通过后运行相关 Loader、身份与审计测试，再执行完整默认 pytest。

## 9. 验收标准

本轮实现验收要求：

1. 上述新增和现有相关测试全部通过。
2. 三手册隔离复测正常完成，不写正式库。
3. 四项现有身份/血缘指标继续为 0。
4. 新增来源 Section 血缘错误为 0。
5. WebRTC 合并率和 `<200` 相比 95 Chunk / 71.6% 有实质改善。
6. 总体三个长度指标均在报告和 Go/No-Go 原因中出现。
7. 只有 `<100≤5%`、`<200≤15%`、`>1200≤5%` 及全部身份/血缘检查同时通过时，报告才允许 `enter_0g=True`。

纯内存仿真表明，有界局部策略预计只能把总体 `<200` 降至约 23%、`<100` 降至约 13.8%；因此本轮预期结果仍为 No-Go。复测后按剩余短块和长块的真实分布另开下一项治理，不在本设计内追加规则。

## 10. 变更边界

预计只修改：

- `rag_knowledge/services/section_chunk_merge.py`
- `scripts/audit_round0c_isolation.py`
- `tests/test_section_chunk_merge.py`
- 对应的 Loader/审计测试文件
- 新一轮隔离报告目录或现有 Round 0C 隔离报告

不顺手重构相邻 Loader、解析器、Chroma、检索或图谱代码。

## 11. 实施结果

2026-07-15 按本设计完成实现与三手册隔离复测，报告位于 `docs/3_待办清单/切块基石治理/进行中-第0C轮-切块合并与隔离验证/L2合并隔离验证/`。

- Final Chunk：`305 → 234`
- `<100`：`25.9% → 14.5%`
- `<200`：`49.2% → 22.6%`
- `>1200`：`5.25% → 6.8%`（长块数量未增加，因合并后分母缩小而上升）
- WebRTC：`95 → 51`，`<200 71.6% → 19.6%`
- 跨文档 Section 碰撞、Chunk UID 重复、prev/next 断链、Element/Raw/来源 Section 血缘错误均为 0

`enter_0g=False`，原因仅为三个长度门禁仍未达到 PRD §8.2。

## 12. 生产 Loader 口径最终复测

随后补齐生产链路中的超长文本切分、过滤后邻接重算，以及同 Section 表格相邻说明的显式 `table_context` 标记。按 PRD §8.2 的普通文本口径复测结果位于 `docs/3_待办清单/切块基石治理/进行中-第0C轮-切块合并与隔离验证/最终隔离验证/`：

- 全部 Chunk：`366 → 282`
- 普通文本门禁样本：`341 → 243`
- `<100`：`2.5%`（PRD `≤5%`）
- `<200`：`9.5%`（PRD `≤15%`）
- `>1200`：`0%`（PRD `≤5%`）
- 跨文档 Section 碰撞、Chunk UID 重复、prev/next 断链、Element/Raw/来源 Section 血缘错误均为 0

结论：Chunk 基石实测门禁通过；但不得仅凭本报告进入 Round 0G。FR-10 整体/category、0E/OCR 纳入范围和 Go 清单冻结仍须单独完成，因此最终报告保持 `enter_0g=False`。
