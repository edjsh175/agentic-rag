# Retrieval Intent Profile 治理规范

## 1. 目的

`data/retrieval_intent_profiles.json` 是检索意图治理层的唯一业务规则入口。

后续如果要增强某类实体/意图的检索表现，优先改这个配置文件，不要直接在
`rag_knowledge/services/retrieval_quality.py` 或
`rag_knowledge/services/retrieval_strategy.py` 里继续写业务名补丁。

## 2. 什么时候该加 Profile

适合新增 profile 的场景：

- 同一类问题有稳定的实体名或实体别名，例如工具名、模块名、表族名。
- 问题还存在稳定意图，例如“发布”“配置”“字段要求”“数据结构”。
- 命中文档通常来自固定来源、固定章节家族，适合通过 source/section 做轻量偏置。
- 已经出现真实回归，且可以通过固定评测集或回归用例复现。

不适合新增 profile 的场景：

- 只是某一句测试问题没命中，换个问法就消失。
- 只能靠某个完整问句才能命中，没有稳定实体或意图可抽象。
- 需要大幅改写召回策略、分词、向量库或 rerank 才能解决。
- 只是想把某一个来源整体抬高，而没有实体/意图/章节锚点支撑。

## 3. 配置契约

每个 profile 都必须满足：

- `id`：稳定唯一标识，便于测试和排查。
- `entity_aliases` 或 `intent_terms`：至少有一类 query 匹配条件，不能只靠 source 生效。
- 文件编码必须为 `UTF-8`。
- `preferred_sources` 与 `fallback_sources` 不能重叠。
- `candidate_min_k` 如果填写，必须大于 `0`。

字段说明：

- `entity_aliases`
  - 查询里的实体别名，命中后才允许 profile 参与该次检索治理。
- `intent_terms`
  - 查询里的意图词；如果填写，则 query 必须同时命中至少一个意图词。
- `recall_terms`
  - 只用于扩展检索 query，不直接决定 profile 是否命中。
- `section_families`
  - 同一家族章节别名集合，用于章节等价命中和轻量加分。
- `preferred_sources`
  - 倾向来源。只有文档同时命中实体/意图/章节等内容锚点时才会加分，不能靠 source-only 抬分。
- `fallback_sources`
  - 回退来源。只有文档已经命中内容锚点时才会轻量降权，避免把非目标普通问题拉偏。
- `sibling_penalty_groups`
  - 兄弟家族集合。只在文档落到兄弟家族、且没有命中目标家族时降权。
- `candidate_min_k`
  - 该 profile 命中时的最小候选池大小，用于给后处理留出重排空间。

正例：

- 工具实体 + 发布意图 + 目标来源/回退来源 + 固定流程章节。
- 表族实体 + 同义章节家族 + 兄弟表族降权。

反例：

- 只有 `preferred_sources`，没有 `entity_aliases` / `intent_terms`。
- 把整个来源都设为 preferred，试图替代真实内容匹配。
- 兄弟家族和目标家族混在一个无法区分的 penalty group 里。

## 4. 新增或修改 Profile 的步骤

1. 先补测试，再改配置。
2. 至少补下面三类守门之一，必要时多补：
   - 命中类：目标实体/意图能正确提升目标文档。
   - 中性类：普通问题或未命中意图的问题不应被 profile 拉偏。
   - 误伤类：兄弟家族、fallback source、近义但非目标实体不应误抬分。
3. 修改 `data/retrieval_intent_profiles.json`。
4. 跑必跑测试与评估流程。
5. 如果 A/B 或 hardcases 出现回归，先回滚 profile 思路，不要继续叠规则。

## 5. 必跑验证

Profile 改动后至少跑：

```bash
venv\Scripts\python.exe -m pytest tests/test_retrieval_intent.py tests/test_routing_and_structured_boost.py tests/test_retrieval_regression.py -m "not integration"
```

如果本地知识库可用，再跑 integration 回归：

```bash
venv\Scripts\python.exe -m pytest tests/test_retrieval_regression.py -m integration
```

固定数据集 A/B 回归：

```bash
venv\Scripts\python.exe run_retrieval_ab.py data/eval_dataset_hardcases.json --methods hybrid hybrid+quality hybrid+rerank+quality --fail-on-regression
```

说明：

- 结果默认写回 `data/retrieval_ab_results.json`。
- `--fail-on-regression` 会和已有同数据集/同方法结果比较。
- 当前默认关注 `recall@3`、`mrr`、`overall_hit_rate` 三个指标，任一下降都会直接失败。
- `--regression-threshold` 默认允许 1pp 内的性能浮动波动（即 `default=0.01`）。若需在 CI 或验收中启用完全零容忍，请显式传入 `--regression-threshold 0`。
- 如果重建过知识库或 chunk ID，必须先重建/校准 `eval_dataset_hardcases.json`，否则 A/B 可能出现整体归零的假回归。

## 6. 真实问题抽样建议

每次 profile 改动后，建议至少抽样看 3 组问题：

- 表结构类：验证章节家族和兄弟表族不会串。
- 发布流程类：验证 preferred/fallback source 只在内容锚点存在时生效。
- 普通非 profile 类：验证没有无关偏置。

如果抽样发现“无关来源被整体抬高”或“普通问题被 profile 接管”，优先删弱规则，不要继续加补丁。

## 7. 调试信号

排序后的文档元数据里可能出现：

- `intent_profile_boost`
- `intent_profile_penalty`

它们只用于排查 profile 是否生效，不属于对外稳定 API，也不应该被前端或外部流程强依赖。

## 8. Graph 事实约定（Task 8.2）

Profile 同步到图谱（`sync_profiles_to_graph.py`）与运行时评分（`GraphIntentFactProvider`）共用以下约定：

### Field

- canonical 名称为限定名：`{DataTable}.{leaf}`（例如 `管线点表.管点编号`）。
- 禁止再创建裸 `管点编号` Field 实体。
- 评分时同时匹配 full name 与 leaf name。

### Section

- canonical Section 由 Phase B 抽取创建，实体名为 `make_section_entity_name(source, section_path)`。
- profile sync **不得**创建裸 `PipelineBuilder > …` Section 实体。
- 章节家族 alias（如 `点数据结构`）通过 DataTable 的 **approved entity alias** 表达；scorer 从 canonical `defined_in` path 派生 alias path。

### 存在性与门禁

- migration dry-run 区分 `exists_any` 与 `exists_approved`；pending 事实不算运行时满足。
- 部署前运行：`scripts/validate_task81_graph_gate.py --json`（或 `--skip-global-quality` 跳过全图历史债）。
- 判定为 `NEEDS_APPLY` 后，按拆分命令人工审批候选，**禁止** `--approve-all` 与 `--review-status approved` 直写正式库。

### 人工审批示例（拆分命令）

```powershell
.\venv\Scripts\python.exe sync_profiles_to_graph.py --apply --profile-id pipeline_point_table
.\venv\Scripts\python.exe run_graph_build.py review --batch <id> --summary
.\venv\Scripts\python.exe run_graph_build.py review --batch <id> --approve-kind alias --approve-confidence-above 0.79
.\venv\Scripts\python.exe run_graph_build.py review --batch <id> --approve-type DataTable --approve-confidence-above 0.79
.\venv\Scripts\python.exe run_graph_build.py review --batch <id> --approve-relation-type different_from --approve-confidence-above 0.79
.\venv\Scripts\python.exe run_graph_build.py apply --batch <id>
```
