# missing_evidence 历史债清零纪要（2026-07-27）

## 背景

- Task 8.1 / 第 4 轮书面例外：全图曾有 **104** 条 `rule:phase_b` 实体无 `entity_chunk_links`（`missing_evidence`）。
- 口径：不挡 GraphRAG 检索结论，但挡「生产默认开图」准入叙事。
- 处理原则（见路线规划）：可恢复证据 → 补 link；禁止伪造 evidence；禁止一刀切删边。

## 现状（清债前盘点）

正式库 `data/rag_relational.db` 实测：

| 项 | 数量 |
|----|-----:|
| 历史口头口径 | 104 |
| 清债前实测 | **2** |
| 清债后 | **0** |

说明：第 3 轮安全重建 / 主干与后续 apply 已消化掉绝大多数 Phase B 无证据实体；残留 2 条为产品主干 Service。

## 本轮动作

1. 备份：`data/backups/rag_relational_pre_missing_evidence_fix_20260727_152513.db`
2. 为下列实体补 `entity_chunk_links`（证据均来自 live Chroma 正文，非伪造）：

| 实体 | chunk_id | source |
|------|----------|--------|
| 影像发布服务 | `chk_1528ea0c1092cefdf25c95a2`（Stamp服务部署） | `repair:missing_evidence_20260727` |
| GRID发布服务 | 同上（同块含 GRID 小节） | `repair:missing_evidence_20260727` |

3. 复跑：`run_graph_build.py quality --graph --profile full`

## 验收

| 指标 | 结果 |
|------|------|
| `stats.missing_evidence_count` | **0** |
| `errors` 中 `missing_evidence:*` | **无** |
| `stale_link_count` | 0 |

产物：`data/graph_quality_post_missing_evidence_fix.json`

## 仍未绿（非本债范围，另开）

`quality --graph` 仍 `ok=false`，剩余 errors：

1. `missing_golden_relation:PipelineBuilder:belongs_to:StampTools`
2. `missing_golden_relation:管线发布服务:belongs_to:StampServer`
3. `illegal_relation`：`StampManager(Product) belongs_to 运维管理层(Module)`（`created_by=admin`）
4. `high_confidence_without_evidence:1`：`UE材质集添加蓝图`（`llm:schema_extractor`）

以上不影响「104 → 0 missing_evidence」结论；若要 `ok=true`，需另开金边补齐 / 非法边治理 / LLM 无证据实体处理。

## 结论

- **missing_evidence 历史债已清零**（口头 104 条例外可关闭）。
- **仍不**据此把生产模板改为 `graph_retrieval.enabled=true`（还需金边/非法边与运维观察）。
