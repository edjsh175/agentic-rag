# 知识图谱执行 PRD — 第 1 轮：规则图谱补全

- **记录日期**：2026-07-13
- **状态**：**已完成（代码 + 正式库 apply）** — 2026-07-14
- **正式 batch**：`26a70b94-6a91-465b-869b-a501ba37ab73`
- **备份**：`data/backups/rag_relational_pre_round1_2026-07-14.db`
- **轮次编号**：Round-1 / MVP-3A
- **母文档**：`../2026-07-09-知识图谱语义抽取升级整体计划与PRD.md`
- **前置条件**：MVP-1/2 工程能力已落地；Chroma 与 `file_index.json` 一致
- **周期建议**：3–5 个工作日
- **是否启用 LLM**：**否**

### 实施纪要（2026-07-14）

```text
1. SectionPathExtractor：按 ">" 逐级建 Section；Document 只 has_section 到首级；中间/叶子均挂 evidence link；defined_in 仍指向叶子
2. DataSpecTableRelationExtractor：数据规范域下同名点/线/面的 DataTable --has_section--> 结构 Section
3. schema：has_section 允许 Section→Section、DataTable→Section
4. EntityResolution：Section 不做 substring possible_duplicate（否则父路径被叶子挡住）
5. make_section_entity_name：全角括号/空白与 CandidateNormalizer 对齐（修复 missing_relation_endpoint）
6. 正式库：704→1130 entities，1623→2683 relations；has_section 667→1727；Task 8.1 Gate PASS
7. 抽检：PipelineBuilder > 数据规范 --has_section--> 管线点表 / 面表数据结构 均存在
8. Section 占比上升属预期；quality --graph 仍有历史 missing_evidence（不阻断本轮结构目标）
```

---

## 1. 本轮要解决的问题

当前规则抽取把完整 `section_path` 当作单个叶子 Section，导致：

```text
❌ 无 PipelineBuilder > 数据规范 中间节点
❌ 无 数据规范 --has_section--> 管线点表 / 面表数据结构
❌ 无 管线点表 与 面表数据结构 的业务关联边
✅ 仅有 文档 --has_section--> 完整路径叶子
✅ 仅有 Tool/Product --defined_in--> 叶子 Section
```

本轮目标：**在不启用 LLM 的前提下**，让图谱具备可遍历的章节层级和数据规范表结构关系。

> 2026-07-14 口径校正：本轮是“结构地基”轮，不负责解决业务实体大量缺失。`domain_catalog.json` 作为 seed 正常可用，但规则抽取仍只会在 `section_path` / `content_type` 等结构线索明确时产出 Tool / Service / DataTable 等业务实体。白名单外、正文语义中出现的 Procedure / Step / Command / ConfigItem 等，留给第 2 轮 LLM 小范围试点解决。

本轮完成后，`Section` 实体数可能上升，因为会新增中间章节节点；这不是退化。验收重点应放在 `has_section` 层级边、DataSpec 表结构关系、stale link 和既有人工/seed 事实是否保持稳定。

---

## 2. 产品目标

### 2.1 必须达成

```text
1. 按 section_path 的 ">" 逐级创建 Section 实体
2. 自动生成父子 has_section 边（父 Section --has_section--> 子 Section）
3. 文档只 has_section 到第一级 Section（或保持文档→最深叶子之一，需在实现中统一并写测试）
4. 数据规范域下 *点表 / *线表 / *面表 / *数据结构 的模式化业务边
5. Phase B 遗留：管线面表 defined_in Section 可生成候选并通过 quality gate
```

### 2.2 本轮不做

```text
1. 不启用 LLM 抽取
2. 不做全库 rebuild-safe 正式替换
3. 不修改 GraphRAG 融合逻辑
4. 不做前端
5. 不承诺显著提升 Tool / Service / Procedure / Step / Command / ConfigItem 覆盖率
```

---

## 3. 技术方案

### 3.1 SectionPathExtractor 升级

**文件**：`rag_knowledge/services/graph_extraction/__init__.py`

对 `section_path = "A > B > C"`：

```text
创建 Section 实体：
  - {source}::A
  - {source}::A > B
  - {source}::A > B > C   （与现有 make_section_entity_name 一致）

创建 has_section 边：
  - Document --has_section--> {source}::A
  - {source}::A --has_section--> {source}::A > B
  - {source}::A > B --has_section--> {source}::A > B > C

保留现有：
  - Tool/Service/Product/DataTable --defined_in--> 叶子 Section（最深路径）
  - Document 级 entity_chunk_links
```

**注意**：中间 Section 节点也需要 `entity_chunk_links`（可指向同一 chunk 或仅叶子有 link——在实现说明中二选一并写测试）。

### 3.2 DataSpecTableRelationExtractor（新规则模块，名称可调整）

**触发条件**（模式化，非单文档硬编码）：

```text
section_path 含 DATA_SPEC_KEYWORDS（已有：数据规范 等）
且路径段匹配：
  - *点表 / *线表 / *面表
  - *点数据结构 / *线数据结构 / *面数据结构
```

**产出关系**（候选类型需在 `graph_schema.validate_relation` 中允许）：

```text
管线点表 --has_section--> 点数据结构     （或 contains，若 schema 新增）
管线面表 --has_section--> 面表数据结构
同级表之间默认不自动建边，除非 chunk 正文有明确「包含」证据
```

若 `contains` 未在 schema 中，优先复用 `has_section`（Section/DataTable 类型约束需在 `validate_relation` 放宽或新增 Table→Section 规则）。

### 3.3 Pipeline 接入

**文件**：`rag_knowledge/services/graph_extraction/pipeline.py`

```text
context = section_extractor.extract(chunk)
combined.extend(context)
combined.extend(DataSpecTableRelationExtractor().extract(chunk, context))  # 新增
combined.extend(TableFieldExtractor().extract(chunk, context))
...
```

### 3.4 Schema 变更（最小）

**文件**：`rag_knowledge/models/graph_schema.py`

- 评估 `has_section` 的 source/target 类型约束，允许 `Section → Section`、`DataTable → Section`
- 不新增单文档特例

---

## 4. 任务清单

| 编号 | 任务 | 优先级 | 验收 |
|:---:|------|:---:|------|
| R1-0 | 基线 audit + 记录当前「管线点表/面表」无边案例 | P0 | baseline JSON 归档 |
| R1-1 | SectionPathExtractor 逐级 Section + has_section 链 | P0 | 单元测试 ≥3 条路径用例 |
| R1-2 | DataSpec 表-结构关系规则 | P0 | StampTools 手册路径用例通过 |
| R1-3 | schema validate_relation 更新 | P0 | 新边类型不触发 rejected |
| R1-4 | 增量 extract + review + apply（仅新 batch） | P0 | 正式库可查到父子边 |
| R1-5 | Phase B 面表 Section diagnostic 下降 | P1 | dry-run 无 missing_section_entity |
| R1-6 | 文档与 CLAUDE.md 口径同步 | P2 | 一节「章节层级规则」 |

---

## 5. 实施步骤

```text
Step 1  只读基线
        run_graph_build.py audit
        run_graph_build.py quality --graph
        额外抽样 20 条“应抽出业务实体但只形成 Section”的服务器漏抽样本，作为第 2 轮 LLM 试点输入

Step 2  实现 + 单元测试（isolated_storage，不写正式库）
        tests/test_section_path_hierarchy.py（新建）
        tests/test_graph_extraction.py 增补

Step 3  停止后端 → extract --force-rebuild（不加 --include-llm）
        预期 stats：rule_candidates 较上轮增加（中间 Section + 新边）

Step 4  review
        --approve-kind relation --approve-relation-type has_section
        分拆审批 entity / field

Step 5  apply（带 confirm 参数）+ quality --graph

Step 6  专项 SQL/CLI 验证两条路径：
        管线点表 与 面表数据结构 之间是否出现预期关联或共同父 Section
```

---

## 6. 验收标准

### 6.1 自动化

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_section_path_hierarchy.py tests/test_graph_extraction.py -q
.\venv\Scripts\python.exe run_graph_build.py quality --graph --profile full
# 期望：无新增 BLOCK 级 error；stale_link_count = 0
```

### 6.2 图谱结构（正式库抽检）

```text
✅ 存在 Section 实体：StampTools用户手册::PipelineBuilder > 数据规范
✅ 存在边：...::PipelineBuilder > 数据规范 --has_section--> ...::PipelineBuilder > 数据规范 > 管线点表
✅ Tool/DataTable --defined_in--> 叶子 Section 仍保留
✅ Task 8.1 已写入的 alias / different_from / has_field 不被覆盖丢失
```

### 6.3 指标（对比 R1-0 基线）

```text
Section 实体数上升（因中间节点）
has_section 关系数明显上升
DataTable 实体数不变或略增
业务实体占比可能仅略升，甚至因 Section 增多而短期下降；这不作为本轮失败条件
是否真正从 Section-heavy 转向 Business-entity-aware，留给第 2–3 轮通过 LLM 试点和安全全量重建验收
```

---

## 7. 风险与回滚

| 风险 | 对策 |
|------|------|
| 中间 Section 爆炸式增长 | 仅对 approved chunk 的 path 去重；同一 path 只建一次实体 |
| 与现有 Section 命名冲突 | 复用 `make_section_entity_name`，不引入新命名规则 |
| apply 失败 | 使用 apply 前备份；`export-manual` 已导出人工事实 |

回滚：用 apply 前 `--confirm-backup` 指向的 SQLite 备份恢复。

---

## 8. 交付物

```text
1. 代码：SectionPathExtractor 升级 + DataSpec 规则 + 测试
2. 报告：data/archive/graph_rounds/graph_audit_round1_after.json（原 `data/graph_audit_round1_after.json`）
3. 文档：本节 PRD 顶部状态改为「已完成」并填日期
```

---

## 9. 给 Codex 的执行提示

```text
请根据本目录 `执行PRD.md` 实施 R1-1 到 R1-4：
1. 只改规则抽取，不启用 LLM
2. 为 section_path 逐级创建 Section 和 has_section 父子边
3. 为数据规范下的点表/线表/面表与对应数据结构添加模式化关系
4. 补充单元测试后，在 isolated_storage 下跑通，再说明正式库 apply 步骤
```
