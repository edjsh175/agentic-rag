# 执行 PRD — 第 3 轮补：全库实 LLM 补抽与抽取优化

- **记录日期**：2026-07-24（完成：2026-07-27）
- **状态**：**已完成**（建设目标 R1–R6c；见 [阶段完成纪要.md](阶段完成纪要.md)）
- **目录**：`docs/3_待办清单/知识图谱语义抽取/已完成-第3轮补-全库实LLM补抽与抽取优化/`
- **母文档**：[`../2026-07-09-知识图谱语义抽取升级整体计划与PRD.md`](../2026-07-09-知识图谱语义抽取升级整体计划与PRD.md)
- **关联总览**：[`../2026-07-13-知识图谱PRD剩余轮次总览.md`](../2026-07-13-知识图谱PRD剩余轮次总览.md)
- **关联第 3 轮**：[`../已完成-第3轮-安全全量重建/执行PRD.md`](../已完成-第3轮-安全全量重建/执行PRD.md) / [第3轮执行验收记录.md](../已完成-第3轮-安全全量重建/第3轮执行验收记录.md)
- **关联问答主线**：[`../已完成-主干锚定检索与关系可答/执行PRD.md`](../已完成-主干锚定检索与关系可答/执行PRD.md) / [`../已完成-锚点约束Chunk过滤与干扰对照实验/执行PRD.md`](../已完成-锚点约束Chunk过滤与干扰对照实验/执行PRD.md)
- **移交后续**（不挡本 PRD 完成）：经验产品化（捞边 CLI / 非法边与泛名压制）、按类目择机扩面；**不**开全库图扩召回
- **前置**：
  1. 第 3 轮 **规则路径** rebuild-safe 已完成（batch / audit / Gate 已落盘）
  2. 第 2 轮 LLM 试点 **GO**；正式库禁止把 stub 当作 LLM 验收
  3. Ollama 可达（`192.168.10.158:11434`，须 `NO_PROXY`），且 `--include-llm` **fail-fast**（禁止空 stub）
- **配置口径**：
  - `[graph_extraction.llm] enabled = false`（全局默认关；靠 CLI `--include-llm`）
  - 含 LLM 的 batch **禁止** `--approve-all`
  - 本阶段 **不** 打开全库 `graph_retrieval.enabled`；**不** 启动第 4 轮 GraphRAG
  - 问答侧锚定过滤/allowlist：**对照已过、默认关**；与本 PRD 建设抽取解耦
---

## 1. 一句话目标

在**不改建设链路架构**的前提下，把第 3 轮未完成的「真实 LLM 补叶子」跑通，并补齐全库实抽所需的**可恢复性与噪声治理**；产出可分拆审批、可 apply 的 `llm_candidates > 0` 批次。

---

## 2. 现状与缺口

### 2.1 现行设计（保持）

```text
规则打底 → 主干邻域内 LLM 补叶子 → staging → 分拆审批 → apply
```

要点（代码已齐）：

| 要点 | 口径 |
|------|------|
| 与问答解耦 | 建设写图；问答只读正式库（intent / 扩召回 / 改写 / 消歧） |
| 默认关 | `enabled=false`；CLI `--include-llm` 临时开 |
| 同 chunk | 先规则后 LLM；指纹碰撞 **先写入保留**（常为规则） |
| LLM 范围 | 不抽 Document/Section/DataTable/Field；可抽 Product/Tool/…/Procedure/Step/Command/ConfigItem 等 |
| 证据 | `evidence_text` 须为原文或 section_path 子串；`confidence ≥ min_confidence`（默认 0.60） |
| 主干冲突 | staging 即可 `rejected` |

### 2.2 缺口（本 PRD 范围）— 收口对照（2026-07-27）

| 优先级 | 方向 | 开工时缺口 | 收口状态 |
|:---:|------|----------|----------|
| **P0** | 真实全库实 LLM | Round3 规则完；实 LLM 曾 stub → `llm_candidates=0` | ✅ 真 LLM 已通；按类目扩批+apply（非无差别 2537 盲抽） |
| **P0** | 成本/耗时 | 无断点续抽 | ✅ `--resume-batch` + stats checkpoint |
| **P1** | 抽取质量 | ConfigItem 碎、Command 少；validate 偏晚 | ✅ 噪声/命令旁路/staging 硬拦/方向 v3；⚠ 非法类型边仍多 |
| **P1** | 证据规则 | 子串过严 | ✅ 空白/全半角松绑 |
| **P2** | 治理 | 死配置 / fingerprint | ✅ 死配置删；keep-first |
| **P2** | 编排 | 按类目分批未产品化 | ⚠ 门控已支持显式类目；两段式捞边仍靠脚本 |

**非目标（仍有效）**：大改架构、把关系原文当可引用事实、第 4 轮 GraphRAG、默认开全库图 chunk 召回、无差别全库一把梭 `--include-llm`。

### 2.3 与问答侧能力对齐（勿混为一谈）

| 能力 | 属于 | 验收结论 | 默认配置 |
|------|------|----------|----------|
| 主干锚定改写 | 问答读路径 | A1/A2 收口（锚定 6/6） | `query_rewrite_enabled=true`（本地） |
| 锚点 Chunk 过滤 | 问答读路径 | 对照：干扰 0.33→0；正例 0.67→1.0 | `anchor_chunk_filter_enabled=false` |
| allowlist 小开图 chunk | 问答读路径 | 代码可行 | `anchor_graph_chunk_enabled=false` |
| 全库图扩召回+fuse | 问答读路径 | **未做第 4 轮 A/B，未准入** | `graph_retrieval.enabled=false` |
| LLM 补叶子 / 分拆审批 | **建设写路径（本 PRD）** | R1–R6c 已 apply | CLI `--include-llm`；全局 `enabled=false` |

**推论**：过滤/锚定「有效」≠ 可开全库扩召回；建设侧叶子增量 ≠ 第 4 轮 GraphRAG 通过。

---
## 3. 分轮计划

### 第一轮（本轮开工）— 断点续抽 / 可恢复 batch

**问题**：全库 `--include-llm` 串行小时级；中断后只能整批重来，已抽 chunk 白费。

**方案（最小改动）**：

```text
1. 每处理完一个 chunk（规则 ± LLM），立刻把进度写入 extraction_batches.stats_json
   - processed_chunk_ids: 已完成 chunk_id 列表
   - extract_progress: running | completed
   - 同步刷新 llm_chunks_considered / skipped / 候选计数
2. CLI：extract --resume-batch <batch_id>
   - 复用同一 batch_id，跳过已处理 chunk
   - include_llm / filters 从原 batch 读取（可与 --include-llm 叠加校验）
3. 未完成 batch 保持 status=draft；完成后 extract_progress=completed
4. 禁止对已 applied / approved 的 batch resume
```

**验收**：

| ID | 标准 |
|----|------|
| R1-1 | 单测：模拟中途中断后 `--resume-batch`，未处理 chunk 补齐，已处理 chunk **不重复**写候选（指纹层亦不膨胀） |
| R1-2 | stats 含 `processed_chunk_ids`、`extract_progress`；resume 后最终 `llm_candidates` 合理累加 |
| R1-3 | CLI 帮助与 `run_graph_build.py extract --resume-batch` 可用 |
| R1-4 | 不改变默认关 LLM、主干邻域门控、分拆审批约束 |

### 第二轮 — 实 LLM 冒烟 + 小批验证

```text
1. 确认 Ollama 可达且非 stub
2. extract --doc-category StampTools --include-llm --limit 20（或邻域子集）
3. 验收 llm_candidates > 0；quality --llm 抽样
4. 可选：故意中断后 --resume-batch 验证现场可恢复
```

**验收**：`llm_candidates > 0`；无 stub；主干冲突仍为 staging rejected。

#### 第二轮实施纪要（2026-07-24）

```text
1. Ollama http://192.168.10.158:11434 可达；模型 qwen3:30b 在 tags 中；非 stub
2. 命令：extract --doc-category StampTools --include-llm --limit 8 --force-rebuild
   （限 8 做冒烟；StampTools 共 67 chunk，扩批可再开）
3. batch_id = d857f7d5-ac49-4b1e-9a0e-18a9a35afefd
4. stats：chunks=8；llm_chunks_considered=8；llm_candidates=61；extract_progress=completed；~108s
5. quality --llm：valid_schema=53；evidence_text_not_found=6；high_confidence≥0.9 共 35；
   type_conflict=0；low_confidence=0
6. LLM 实体类型粗览：ConfigItem=25，Procedure=10，EnvironmentComponent=7；Command/Step=0（噪声/召回问题留给第三轮）
7. 仅 staging，未 apply；产物摘要 data/llm_backfill_round3b_smoke_limit8_summary.json
8. 结论：R2 冒烟 PASS（真实 llm_candidates>0）
```

**验收勾选**：

- [x] Ollama 可达且非 stub
- [x] `llm_candidates > 0`
- [x] `quality --llm` 已跑
- [ ] 故意中断 + `--resume-batch` 现场验证（单测已覆盖；现场可选）
- [ ] 本批未 apply（有意；待分拆审批策略）

### 第三轮 — Command / ConfigItem 噪声治理

```text
1. 对照 Round2 / R2 冒烟：ConfigItem 偏碎、Command 偏少
2. 仅调 prompt / 类型约束 / 可选置信度门槛（最多 2 次迭代）
3. 不引入新抽取架构
```

#### 第三轮实施纪要（2026-07-24）

```text
代码：
1. 新增 prompt v2（llm_graph_extractor_v2.md）；config prompt_version=v2
2. 确定性约束：is_noisy_config_item（格式/CRS/投影/UI 标签）→ diagnostic noisy_config_item
3. maybe_reclassify_as_command：Procedure/Step/ConfigItem 名像 shell 则升为 Command
4. 邻域门控旁路：chunk_has_command_signal 为真时也跑 LLM（抬安装/运维块 Command）
   stats 增加 llm_chunks_command_rich

StampTools limit=8 A/B（同 chunk 集）：
  before d857f7d5…：ConfigItem=25，Procedure=10，Command=0
  after  d88014b1…：ConfigItem=9，Procedure=1，Command=0
  （StampTools 样本几乎无 shell 行，Command=0 属语料；噪声明显下降）

StampServer 命令富集 5 chunk（邻域外，靠 command_rich 旁路）：
  batch 85333740…：llm_chunks_command_rich=5，llm_candidates=29
  Command=12（yum/systemctl/tar 等），runs_command=9
  ConfigItem 仅 /etc/redis/redis.conf（合理）

单测：test_llm_graph_extractor / test_backbone_guard command_rich 门控 — 通过
摘要：data/llm_backfill_round3b_r3_noise_command_summary.json
结论：R3 PASS（ConfigItem 噪声↓；Command 在命令块上召回↑）
```

**验收勾选**：

- [x] prompt / 约束迭代（v2 + 后过滤，≤2 次）
- [x] ConfigItem 冒烟对照下降（25→9）
- [x] Command 在命令富集块上 `>0`（12）
- [x] 未改建设链路架构；默认仍 `enabled=false`
### 第四轮 — 抽取期硬拦与证据松绑（按需）

```text
1. staging 期调用 validate_relation（或等价预检），减少 apply 时才暴露的坏边
2. 证据：在可审计前提下做有限度归一（空白/全半角），禁止自由改写放行
3. fingerprint 碰撞时置信度策略写清（保留先写入 vs max）
4. auto_approve_confidence：实现或删除死配置（二选一，禁止继续挂空）
```

#### 第四轮实施纪要（2026-07-24）

```text
代码：
1. GraphBuilder 写 relation 候选后：两端类型可知则 validate_relation；非法 → staging rejected + diagnostic illegal_relation
2. llm_extractor.evidence_matches：仅折叠空白/全半角标点；evidence_text 原文保留
3. runs_command 合法对扩展：Procedure/Step/Tool/Service → Command（方向仍禁止反）
4. fingerprint 碰撞策略写死并注释：保留先写入 payload/置信度，只合并 evidences
5. 删除 auto_approve_confidence 死配置（含 LLM batch 禁止自动审批）
6. quality --llm 统计 illegal_relation_count / noisy_* diagnostics

现场复抽（StampServer 命令富集 5 chunk）：
  batch 54f20fe4…：Command 实体 12 仍在
  LLM 关系 13 条全部 staging rejected（多为反向 runs_command）
  quality illegal_relation_count=13；evidence_not_found=2
  → 坏边不再拖到 apply 才爆

单测：tests/test_graph_extraction_r4_staging.py — 通过
摘要：data/llm_backfill_round3b_r4_staging_summary.json
结论：R4 PASS
```

**验收勾选**：

- [x] staging 期 `validate_relation` 硬拦
- [x] 证据有限度归一（空白/全半角）
- [x] fingerprint 碰撞策略写清（keep first）
- [x] `auto_approve_confidence` 已删除（非实现自动审批）
### 第五轮 — 全库实 LLM 补抽 + 分拆审批

```text
优先：在已有规则库上增量 LLM 补抽（或带 --resume-batch 的长跑），
而非无必要再整库 rebuild-safe 替换规则事实。

流程：备份 → extract --include-llm（可分 doc_category）→ resume 直至 completed
     → 分拆审批 → apply（confirm 三件套）→ audit / quality --graph
     → 对照第 3 轮 §10 业务实体数量（Command/Procedure/Step 等）
```

报告建议：`data/rebuild_safe_execute_round3_llm.json`（若走 rebuild-safe）或独立 `data/llm_backfill_*.json`。

#### 第五轮实施纪要（2026-07-24 extract / 2026-07-27 apply）

```text
范围：StampTools + StampServer，--include-llm --force-rebuild（非整库 rebuild-safe）
备份：data/backups/rag_relational_pre_r5_llm_backfill_20260724_174124.db
batch：a6309a3d-6bab-484c-b919-74d121dc399a
extract：chunks=238；llm_chunks_considered=102；llm_candidates=745

分拆审批（仅 llm:schema_extractor；规则候选一律 reject）：
  初批 approve 120 / reject 2295+（含非 LLM）
  策略：Command/Procedure/Step/Env/Error conf≥0.85；ConfigItem≥0.9；
        关系仅 runs_command|configured_by|uses_config|has_procedure≥0.85
        且两端已在正式库或本批 entity approve 中

apply 预检失败一次（已修）：
  - 同名类型冲突：导出模型 / 编译发布（Step vs Procedure）→ 保留 Procedure，拒 Step
  - 非法边：PipelineBuilder has_procedure 管线面表（DataTable）→ 拒
  脚本：scripts/r5_fix_preflight_conflicts.py → approved 117；preflight ok

apply 结果（confirm 三件套）：
  entities 956→1070（+114）；relations 1150→1153（+3）；links 6164→6278（+114）
  候选：entity 114 + relation 3（has_procedure）
  关键类型存量（apply 后）：Command 51 / Procedure 17 / Step 16 / ConfigItem 28 /
                          EnvironmentComponent 4 / Error 6
  runs_command 正式边仍为 0（关系侧大多因端点未同批 approve 被拒；叶子实体已入库）

产物：
  data/llm_backfill_r5_stamptools_stampserver.log
  data/llm_backfill_r5_quality_summary.json
  data/llm_backfill_r5_review_plan.json
  data/llm_backfill_r5_apply.log
  data/llm_backfill_r5_apply_summary.json

quality --graph：仍 ok=false（历史 missing_golden_relation / missing_evidence 等）；
  本批未引入 type_conflict_unresolved；不作为 R5 回滚条件。
结论：R5 PASS（增量 LLM 叶子已进正式库；关系边偏少，后续可再批端点齐套的边）
```

#### 第六轮实施纪要（2026-07-27）

```text
门控修复：显式 --doc-category 时 category-scoped LLM（否则 WebRTC/基础环境全跳过）
范围：StampWebRTC + 基础环境，148 chunks；llm_considered=148；category_scoped=135；llm_candidates=801
batch：7c848f88-a994-4162-8dc9-87af94df3214

分拆审批（同 R5 保守策略）：approve 157 / reject 962
  entity：Env 12 / Procedure 69 / Command 37 / Step 1 / ConfigItem 36
  relation：runs_command 2（has_step 173 全拒——端点未齐套或类型不合）

apply：entities 1070→1224（+154）；relations 1156→1158（+2）；links +155
备份：data/backups/rag_relational_pre_r6_apply_20260727_092536.db
产物：data/llm_backfill_r6_*.json / llm_backfill_r6_webrtc_baseenv.log
结论：R6 PASS
```

---

## 4. 与问答的关系

LLM 补的实体/边主要喂：

1. Intent 加减分  
2. 图扩召回（当前生产默认关）  
3. 图辅助改写 / 主干锚定  
4. 回答侧消歧提示  

**不会**把关系 `evidence_text` 当可引用事实写入回答契约。第 4 轮 GraphRAG 当时延后、**不挡**本 PRD；其后已 PASS（见 [已完成-第4轮](../已完成-第4轮-GraphRAG实效验收/)）。

---

## 5. 任务清单（第一轮）

| 编号 | 任务 | 优先级 | 验收 |
|:---:|------|:---:|------|
| L1-0 | 本 PRD 落盘 + 总览挂接 | P0 | ✅ 2026-07-24 |
| L1-1 | `RelationalDB.update_extraction_batch_stats`（或等价） | P0 | ✅ |
| L1-2 | `GraphBuilder._build` 逐 chunk checkpoint | P0 | ✅ |
| L1-3 | `build_*` / CLI `--resume-batch` | P0 | ✅ |
| L1-4 | 单测覆盖中断续跑 | P0 | ✅ `tests/test_graph_extraction_resume.py` |

### 第一轮实施纪要（2026-07-24）

```text
1. stats_json 增加 processed_chunk_ids / extract_progress（running|completed）
2. 每 chunk 处理后落盘；中断后可用 extract --resume-batch <id>
3. 未完成 batch 再跑同 filters 会提示 resume / force-rebuild，避免静默返回半成品
4. 单测：中断续跑、applied 拒绝 resume、incomplete 阻挡重复 extract
```

---

## 6. 成功标准（本 PRD 整体）

- [x] 第一轮：断点续抽代码 + 单测通过（2026-07-24）
- [x] 第二轮：真实 `llm_candidates > 0` 小批冒烟（非 stub）（2026-07-24，batch `d857f7d5-…`）
- [x] 第三轮：ConfigItem 噪声下降 + Command 在命令块召回（2026-07-24）
- [x] 第四轮：staging `validate_relation` + 证据松绑 + 死配置清理（2026-07-24）
- [x] R4b：prompt v3 few-shot + 抽后方向翻转（2026-07-24；命令块 pending 关系 0→13）
- [x] 第五轮：StampTools+StampServer 实 LLM 扩批 + 分拆审批 + apply（2026-07-27；batch `a6309a3d-…`；+114 entities）
- [x] 含 LLM batch 仍禁止 `--approve-all`；主干冲突/非法边未进正式库
- [x] 业务叶子相对 Round3 规则基线有可解释增量（Command/Procedure/Step/ConfigItem 等）
- [x] 未做成 GraphRAG / 未打开全库 `graph_retrieval.enabled`
- [x] R5b：端点齐套后回写 3 条 `has_procedure`（batch `8d323357-…`，2026-07-27；relations 1153→1156）
- [x] R6 门控：显式 `--doc-category` 时允许类目内 LLM（`llm_chunks_category_scoped`；单测已加）
- [x] R6：StampWebRTC + 基础环境 extract/审批/apply（2026-07-27；batch `7c848f88-…`；+154 entities / +2 relations）
- [x] R6b：捞齐套 `has_step`（先补 Procedure/Step conf≥0.80 非 diagnostic）→ +101 entities / +8 `has_step`（batch `6c91c1f4-…`）
- [x] R6c：放行 `possible_duplicate` diagnostic Procedure/Step（conf≥0.85）→ +49 entities / +48 `has_step`（batch `5bbb2199-…`）
- [x] 收口对齐：建设目标达成；明确「全量」=按类目分批，禁止无差别盲抽；与问答扩召回解耦（2026-07-27）
- [x] 阶段文档收口：迁入 `已完成-第3轮补-*` + [阶段完成纪要.md](阶段完成纪要.md)（2026-07-27）
- [ ] （**移交后续，不挡完成**）经验产品化：两段式捞边 CLI、非法边压制、泛名治理；或扩博客/实景三维等类目

---

## 6.1 收口判断与下一刀（2026-07-27）

### 是否开「全量抽取」？

**开**：按 `doc_category` 分批 `--include-llm`（可 `--resume-batch`）→ 实体先 apply → 关系二段捞齐套 → 禁止 `--approve-all`。  
**不开**：对 2537 chunk / 尤其 ~2094「其他」做无差别一次全库盲抽；也**不得**用「叶子抽满」替代打开 `graph_retrieval.enabled`。

「全库实 LLM」在本 PRD 的工程含义 = **链路与门禁已具备、可按批覆盖剩余语料**；不是「必须立刻打完所有 chunk」。

### 架构要不要推倒？

**不要。** 保持：

```text
规则打底 →（门控内）LLM 补叶子 → staging → 分拆审批 → apply
```

优先把 R5–R6c 经验**产品化进编排/SOP**，必要时小改 extractor（非法边、泛名），不大改建设链路。

### 经验融合清单（应收进流程）

| 经验 | 融合方式 | 状态 |
|------|----------|:---:|
| 显式 `--doc-category` → 类目内 LLM | `llm_chunks_category_scoped` 门控 | ✅ 已代码化 |
| 实体→关系两段式 | 正式子命令/SOP（先 entity apply，再 relation recovery） | ⚠ 现有 `scripts/r6b_*` / `r6c_*`，待收成 CLI |
| Step 恢复阈值可略松（如 0.80） | **仅 recovery 配置**；勿改全局默认审批阈值 | ⚠ 脚本内硬编码 |
| `possible_duplicate` 可审后抬；`type_conflict` 永不自动 | 审批分码策略 | ⚠ R6c 脚本已示范 |
| apply 前 `inspect_batch` + 冲突降级 | SOP / 可选 preflight-fix | ⚠ 运维已用，未产品化 |
| 方向 few-shot + 抽后翻转 | prompt v3 + pipeline | ✅ |
| 非法类型边（Procedure→ConfigItem 等） | 抽取侧 prompt/后处理再压 | ☐ 待做 |
| 泛名实体（如「编辑」） | 黑名单/启发过滤或进图后降权 | ☐ 待做 |

### 建议优先序（移交后续工作，不挡本 PRD 完成）

```text
1. 【建设·产品化】两段式捞边 CLI + 审批策略文件化（对齐 is_safe_review_candidate）
2. 【建设·质量】压制非法边 + 泛名实体（减审批垃圾）
3. 【建设·扩面】按需 --doc-category 博客/实景三维/耕地保护（禁止无差别「其他」盲抽）
4. 【问答·体验】本地试用 anchor_chunk_filter（及必要时 allowlist）
5. ~~【延后】第 4 轮 GraphRAG A/B~~ ✅（2026-07-27 PASS）；全库生产默认 `graph_retrieval.enabled=true` 仍未批准
```

> 本清单跟踪见总览 §3；本目录已标 **已完成**。

---

## 附录 A — R4b 方向纠偏纪要（2026-07-24）

根因：口语「命令→目标」与 schema「执行者→Command」不一致，而非单纯模型能力。

```text
代码：
1. prompt v3：Relation direction CRITICAL + few-shot 正/反例；config prompt_version=v3
2. runs_command 合法对再扩：EnvironmentComponent → Command
3. 抽后纠偏：runs_command/configured_by/uses_config/has_procedure/has_step/solved_by
   若正向非法且反向合法 → 交换端点，properties.direction_flipped=true，diagnostic 留痕
4. stats.relation_direction_flipped / quality.relation_direction_flipped_count

命令富集 5 chunk 复抽对照：
  R4  (54f20fe4…)：LLM 关系 pending=0 / rejected_illegal=13
  R4b (492c6e77…)：LLM 关系 pending=13 / rejected=1；stats_flipped=0
  （本轮 LLM 已按 few-shot 直接出对方向，翻转兜底未触发；仍保留）

残留：部分边端点类型暂未知时 staging 无法硬拦（如 has_step→Command 若 Procedure 类型未入库索引）；
      审批时仍须看类型。Command→Command 类边继续拒。

摘要：data/llm_backfill_round3b_r4b_direction_fix_summary.json
结论：方向问题可工程治理；prompt 示例 + 翻转兜底有效
```

---

## 7. 关键文件

| 路径 | 角色 |
|------|------|
| `rag_knowledge/services/graph_extraction/pipeline.py` | 抽取编排 + checkpoint / resume + 类目门控 |
| `rag_knowledge/services/graph_extraction/llm_extractor.py` | LLM 抽取 + 噪声/命令信号 |
| `rag_knowledge/services/graph_extraction/prompts/llm_graph_extractor_v3.md` | 方向 few-shot prompt |
| `rag_knowledge/repository/relational_db.py` | batch stats 持久化 |
| `run_graph_build.py` | `--resume-batch` / apply confirm CLI |
| `scripts/r6_split_review_and_apply.py` | R6 保守分拆审批+apply（示范） |
| `scripts/r6b_recover_has_step.py` | 两段式齐套 `has_step` 回收（待收成正式 CLI） |
| `scripts/r6c_recover_diagnostic_has_step.py` | `possible_duplicate` 放行示范 |
| `tests/test_graph_extraction_resume.py` / `test_backbone_guard.py` | 续抽与门控回归 |
| `config.ini` `[graph_extraction.llm]` | 默认 `enabled=false`；`prompt_version=v3` |

---

## 8. 建议命令（第二轮起 / 收口后扩面）

```powershell
# 按类目扩批（推荐「全量」形态；禁止无差别打「其他」全库）
.\venv\Scripts\python.exe run_graph_build.py extract `
  --doc-category StampWebRTC --doc-category 基础环境 --include-llm --force-rebuild

# 中断后续跑
.\venv\Scripts\python.exe run_graph_build.py extract --resume-batch <batch_id>

# 质量与审批：分拆 review → inspect → apply（须 confirm 三件套；禁止 --approve-all）
.\venv\Scripts\python.exe run_graph_build.py quality --batch <batch_id> --llm

# 关系二段捞（当前脚本；待收成正式子命令）
# .\venv\Scripts\python.exe scripts\r6b_recover_has_step.py
# .\venv\Scripts\python.exe scripts\r6c_recover_diagnostic_has_step.py
```

---

## 9. 风险与约束

```text
1. resume 必须复用同一 batch；不得静默新建 batch 导致审批分裂
2. 已 approved/applied batch 禁止 resume
3. 指纹碰撞仍「先写入保留」；续跑不得用 LLM 覆盖已有规则候选
4. 长跑前停止占用 rag_relational.db 的后端写路径；读路径按现场约定
5. 全库实抽耗时长属预期；靠 resume + 按类目分批，不靠无差别盲抽 / 默认并行打爆 Ollama
6. 建设侧叶子增量不得解读为可开 graph_retrieval.enabled；第 4 轮另立 A/B
7. possible_duplicate 放行须留痕；type_conflict 禁止自动抬升
8. 两段式捞边脚本写正式库前仍须备份 + confirm 三件套
```
