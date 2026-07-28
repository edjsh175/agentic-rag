# data/ 目录约定

- **生效日期**：2026-07-20
- **最近复核**：2026-07-28（Round 3/4 产物状态已更新，见 §4）
- **目的**：区分 live 真源 / 运行时文件与历史生成物，避免文档把归档路径或缺失产物写成「已完成」。

## 1. 留在 `data/` 根目录

| 类别 | 路径（示例） | 说明 |
|------|-------------|------|
| 跟踪真源 | `domain_catalog.json`、`product_relation_backbone.json`、`product_relation_backbone_preview.json`、`retrieval_intent_policies.json`、`migrations/`、`structured_retrieval_regression.json` | Git/SVN 白名单跟踪 |
| 运行时 | `file_index.json`、`ingestion_decisions.json`、`document_profile_map.json`、`agents.json`、`chats/`、`qa_traces/`、`rag_relational.db`、`graph_apply_audit.jsonl`、`chunk_hit_stats.json` | 服务读写；勿随意搬迁 |
| 工作目录 | `rebuild/`（RebuildCoordinator 进行中） | 完成后可将整次操作目录迁入 archive |
| 当前默认 CLI 产出 | `graph_audit_report.*`、`manual_graph_facts.json`、`eval_dataset*.json`、`retrieval_ab_results.json` | 新跑仍可先写根目录，轮次验收后再归档 |

产品主干预览页（`/admin/graph?source=product_backbone_preview`）读写的是 `product_relation_backbone_preview.json`；正式 seed 同步用 `product_relation_backbone.json`。

## 2. 历史生成物：`data/archive/`

| 子目录 | 内容 |
|--------|------|
| `graph_rounds/` | 第 1/2/2.5 轮 audit、export、批次元数据、review 报告 |
| `rebuild_reports/` | `rebuild-safe` dry-run / execute 报告 |
| `rebuild/` | 已完成的 RebuildCoordinator 工作目录 |
| `quality_audit/` | 离线质量审计 |
| `chunk_audit/` | 历史 parse/lineage spike（含原文，勿提交） |
| `backups/` | `*.bak`、一次性 index/eval 备份 |
| `task81/` | Task 8.1 候选导出等 |
| `retrieval_ab_results/` | 带时间戳的 A/B 结果快照 |

索引说明：`data/archive/INDEX.txt`。整个 `data/archive/` 已被 `.gitignore` 忽略。

## 3. 路径对照（整理后）

| 旧文档常见路径 | 现位置 |
|----------------|--------|
| `data/graph_audit_pre_round2.json` 等轮次 audit | `data/archive/graph_rounds/` |
| `data/rebuild_safe_dry_run_pre_round3.*` | `data/archive/rebuild_reports/` |
| `data/task81_profile_sync_candidates.json` | `data/archive/task81/` |
| `data/chunk_audit/_spike_*` | `data/archive/chunk_audit/` |
| `data/retrieval_ab_results_archive/` | `data/archive/retrieval_ab_results/` |
| `data/rebuild/<op-id>/`（已完成） | `data/archive/rebuild/<op-id>/` |

新 spike 默认仍可写 `data/chunk_audit/`（见 `scripts/spike_parse_lineage.py`）；跑完后迁入 archive。

## 4. Round 3 / 后续口径（更新至 2026-07-28）

- **不要**把「有 dry-run 归档」或「有 RebuildCoordinator before.db」当成 Round 3 完成。
- Round 3 规则路径完成至少需要：`rebuild_safe_execute_round3.*`、执行日志、`graph_audit_post_round3.json`、填写后的验收记录与 §10 表。
- **现场（2026-07-22 起）**：上述 execute 产物与 `data/backups/rag_relational_pre_round3*.db` **已存在**；实 LLM 由「第 3 轮补」按类目补抽完成；第 4 轮 GraphRAG A/B 已 PASS。
- 导航以 [知识图谱PRD剩余轮次总览](../3_待办清单/知识图谱语义抽取/2026-07-13-知识图谱PRD剩余轮次总览.md) 与 [第4轮阶段总结](../3_待办清单/知识图谱语义抽取/已完成-第4轮-GraphRAG实效验收/2026-07-27-阶段总结.md) 为准。
