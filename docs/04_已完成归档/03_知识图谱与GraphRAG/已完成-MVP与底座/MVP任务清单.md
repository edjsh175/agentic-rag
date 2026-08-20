# 知识图谱语义抽取 MVP 任务清单

- **记录日期**：2026-07-09
- **对应 PRD**：`../2026-07-09-知识图谱语义抽取升级整体计划与PRD.md`
- **MVP 目标**：先完成图谱健康诊断、安全清理、人工事实保护和 LLMGraphExtractor 基础能力，不直接做全量 LLM 自动构建。
- **执行原则**：小步提交、每步可测试、默认不破坏正式图谱、所有 LLM 结果只进入候选表。

---

## 0. MVP 范围

本 MVP 只做四类能力：

```text
1. audit：图谱健康检查
2. cleanup：清理 stale entity_chunk_links
3. export-manual：导出人工/特殊/seed 事实
4. LLMGraphExtractor 基础版：Schema 约束的 LLM 候选抽取
```

本 MVP 不做：

```text
1. 不做全量 safe rebuild
2. 不做复杂前端审核页面
3. 不做大规模自动 approve
4. 不迁移 Neo4j
5. 不做 GraphRAG 排序优化
6. 不清空 rag_relational.db
7. 不删除人工实体或人工关系
```

---

## 1. 任务总览

| 编号 | 任务 | 优先级 | 结果物 | 是否改正式图谱 |
|---|---|---:|---|---|
| MVP-0 | 基线保护与现状确认 | P0 | 当前图谱统计、测试基线 | 否 |
| MVP-1 | Graph Audit | P0 | `graph_audit_report.json/md` | 否 |
| MVP-2 | Cleanup Stale Links | P0 | stale link 清理命令与测试 | 只删无效 link |
| MVP-3 | Export Manual Facts | P0 | `manual_graph_facts.json` | 否 |
| MVP-4 | LLMGraphExtractor 基础版 | P0 | LLM 候选批次 | 否 |
| MVP-5 | LLM 候选校验与测试 | P0 | schema validation 测试 | 否 |
| MVP-6 | CLI 与文档收口 | P1 | 命令说明、验收命令 | 否 |

---

## 2. MVP-0：基线保护与现状确认

### 目标

在任何修改前，先确认当前状态，避免后续改动破坏正式库或测试基线。

### 要做的事

```text
1. 检查当前 Git 工作区状态
2. 确认 data/rag_relational.db 是否存在
3. 确认 Chroma 当前 chunk 数
4. 记录当前 entities / relations / entity_chunk_links 数量
5. 记录当前 pytest 基线，不要求一次修完所有历史失败，但要知道新增改动不能扩大失败面
```

### 建议命令

```powershell
git status --short
.\venv\Scripts\python.exe run_graph_build.py list
.\venv\Scripts\python.exe -m pytest tests/test_knowledge_graph.py tests/test_graph_extraction.py -q
```

### 验收标准

```text
1. 明确当前图谱统计
2. 明确当前测试基线
3. 不对正式图谱做任何写入
```

---

## 3. MVP-1：Graph Audit

### 目标

新增图谱健康检查能力，把“Section 过多、业务实体过少、stale links”等问题变成稳定指标。

### 新增命令

```powershell
.\venv\Scripts\python.exe run_graph_build.py audit
```

可选参数：

```powershell
.\venv\Scripts\python.exe run_graph_build.py audit --output-json data/graph_audit_report.json --output-md data/graph_audit_report.md
```

### 输出文件

```text
data/graph_audit_report.json
data/graph_audit_report.md
```

### 必须统计的指标

```text
1. entity_counts：按 entity_type 统计实体数量
2. relation_counts：按 relation_type 统计关系数量
3. total_entities
4. total_relations
5. total_entity_chunk_links
6. section_ratio
7. business_entity_ratio
8. stale_link_count
9. stale_link_samples
10. orphan_entity_count
11. orphan_entity_samples
12. duplicate_canonical_name_count
13. type_conflict_count
14. manual_fact_count
15. seed_fact_count
16. rule_fact_count
17. llm_fact_count
18. document_entity_coverage
```

### business entity 定义

第一阶段可将以下类型视为业务实体：

```text
Product
Tool
Service
Module
DataTable
Field
ConfigItem
Format
Procedure
Step
Error
Solution
EnvironmentComponent
Command
```

不计入业务实体：

```text
Document
Section
```

### stale link 定义

```text
entity_chunk_links.chunk_id 在当前 Chroma collection 中不存在
```

### document_entity_coverage 示例

```json
{
  "source": "xxx.docx",
  "section_count": 120,
  "business_entity_count": 8,
  "coverage_score": 0.12
}
```

### 实现建议

```text
1. 新增 GraphAuditService
2. 不修改正式图谱
3. Chroma chunk_id 获取逻辑要复用现有 VectorStore / storage 配置
4. audit 命令默认只读
5. JSON 用于机器读取，MD 用于人工阅读
```

### 测试要求

新增或补充测试：

```text
tests/test_graph_audit.py
```

至少覆盖：

```text
1. entity_counts 正确
2. section_ratio / business_entity_ratio 正确
3. stale_link_count 正确
4. 无 Chroma 时给出可理解诊断，不直接崩溃
5. audit 不会删除或写入 entities / relations / links
```

### 验收标准

```text
1. run_graph_build.py audit 可以执行
2. 输出 JSON 和 MD
3. 能发现 stale links
4. 能显示 Section 占比和业务实体占比
5. 不改变正式图谱数据
```

---

## 4. MVP-2：Cleanup Stale Links

### 目标

清理 Chroma 重建后残留在 `entity_chunk_links` 中的无效 chunk 证据链接。

### 新增命令

```powershell
.\venv\Scripts\python.exe run_graph_build.py cleanup-stale-links
```

建议支持 dry-run：

```powershell
.\venv\Scripts\python.exe run_graph_build.py cleanup-stale-links --dry-run
```

### 行为要求

```text
1. 默认先支持 --dry-run，只打印将删除的 link 数量和样例
2. 非 dry-run 时只删除 entity_chunk_links 中 chunk_id 不存在的记录
3. 不删除 entities
4. 不删除 relations
5. 不删除 aliases
6. 不删除 fields / procedures / procedure_steps
7. 删除前后输出 audit 摘要
```

### 输出示例

```text
stale links before: 906
stale links deleted: 906
stale links after: 0
entities deleted: 0
relations deleted: 0
```

### 实现建议

```text
1. 可复用 GraphAuditService 中的 stale link 检测逻辑
2. 删除操作必须事务化
3. 删除前记录样例，便于人工确认
4. dry-run 必须是默认推荐路径
```

### 测试要求

新增或补充测试：

```text
tests/test_graph_cleanup.py
```

至少覆盖：

```text
1. dry-run 不删除 link
2. 非 dry-run 只删除 stale links
3. 有效 link 保留
4. entities / relations 数量不变
5. 删除后 audit stale_link_count = 0
```

### 验收标准

```text
1. cleanup-stale-links --dry-run 能看到待删除数量
2. cleanup-stale-links 能清理 stale links
3. 清理后 audit stale_link_count = 0
4. 不误删实体和关系
```

---

## 5. MVP-3：Export Manual Facts

### 目标

在后续 safe rebuild 前，先能导出人工事实、seed 事实和特殊关系，解决“重建会不会丢人工关系”的问题。

### 新增命令

```powershell
.\venv\Scripts\python.exe run_graph_build.py export-manual --output data/manual_graph_facts.json
```

### 导出范围

第一阶段导出：

```text
1. created_by in ('admin', 'manual') 的 entities
2. created_by in ('admin', 'manual') 的 relations
3. created_by in ('seed', 'rule:special') 的 entities / relations / aliases
4. aliases 中人工或特殊来源的数据
5. 与人工实体相关的 entity_chunk_links，如果 link 本身不是 stale
```

如果当前表里 created_by 不统一，则先兼容：

```text
admin
manual
seed
rule:special
rule:special_relations
```

### 输出结构建议

```json
{
  "exported_at": "2026-07-09T00:00:00",
  "schema_version": "v1",
  "entities": [],
  "relations": [],
  "aliases": [],
  "entity_chunk_links": [],
  "summary": {
    "entities": 0,
    "relations": 0,
    "aliases": 0,
    "entity_chunk_links": 0
  }
}
```

### 实现建议

```text
1. 新增 GraphManualFactExporter
2. 只读导出，不做 restore
3. stale links 默认不导出，并在 summary 里记录 skipped_stale_links
4. 导出内容要包含 entity canonical_name、entity_type、properties、created_by
5. relation 要包含 source/target canonical_name，避免跨库恢复时依赖旧 ID
```

### 测试要求

新增或补充测试：

```text
tests/test_graph_manual_export.py
```

至少覆盖：

```text
1. admin/manual entity 被导出
2. rule 自动 entity 默认不导出
3. admin/manual relation 被导出
4. stale link 不导出或被明确标记 skipped
5. 导出 JSON 可被 json.loads 解析
```

### 验收标准

```text
1. 能生成 data/manual_graph_facts.json
2. 文件包含 summary
3. 人工关系不会被遗漏
4. 不对正式图谱做任何写入
```

---

## 6. MVP-4：LLMGraphExtractor 基础版

### 目标

新增一个通用语义抽取器，用 Schema 约束 LLM，从 chunk 中抽取业务实体和关系候选。第一版只进入 `extraction_candidates`，不直接写正式图谱。

### 关键原则

```text
1. 不为具体文档写规则
2. 不把 LLM 输出直接写入 entities / relations
3. 所有候选必须有 evidence_text
4. 所有候选必须有 confidence
5. 所有候选必须通过 schema validation
6. LLM 调用失败时生成 diagnostic，不中断整个 batch
```

### 建议新增文件

```text
rag_knowledge/services/graph_extraction/llm_extractor.py
rag_knowledge/services/graph_extraction/prompts/llm_graph_extractor_v1.md
```

如果当前 `graph_extraction` 是单文件模块，可先按项目现有结构放置，但不要把 prompt 硬编码在大函数里。

### 配置项

建议在配置中新增：

```ini
[graph_extraction.llm]
enabled = false
provider = openai
model = gpt-5.5-thinking
temperature = 0
max_retries = 2
min_confidence = 0.60
auto_approve_confidence = 0.90
prompt_version = v1
extractor_version = v1
```

默认必须：

```text
enabled = false
```

避免无意触发 LLM 成本。

### 输入

```json
{
  "chunk_id": "xxx",
  "source": "xxx.docx",
  "doc_category": "StampServer",
  "section_path": "安装部署 > Redis 安装",
  "content": "chunk 原文"
}
```

### 输出候选

LLM 返回严格 JSON，内部转换为 extraction_candidates：

```json
{
  "entities": [
    {
      "name": "PostgreSQL",
      "entity_type": "EnvironmentComponent",
      "confidence": 0.92,
      "evidence_text": "PostgreSQL安装"
    }
  ],
  "relations": [
    {
      "source_name": "StampServer",
      "relation_type": "requires",
      "target_name": "PostgreSQL",
      "confidence": 0.82,
      "evidence_text": "PostgreSQL安装"
    }
  ],
  "aliases": [],
  "diagnostics": []
}
```

### 第一版允许抽取的 EntityType

第一版建议只开放有限类型，避免一开始太散：

```text
Product
Tool
Service
Module
EnvironmentComponent
Procedure
Step
Command
ConfigItem
Error
Solution
```

暂不开放或低优先级：

```text
FilePath
Port
Parameter
```

这些可以先进入 ConfigItem / Command properties。

### 第一版允许抽取的 RelationType

第一版建议只开放：

```text
belongs_to
requires
depends_on
has_procedure
has_step
runs_command
uses_config
configured_by
causes
solved_by
defined_in
alias_of
different_from
```

### Prompt 必须包含的约束

```text
1. 只能抽取原文明确支持的事实
2. 不得根据常识补充文档未出现的关系
3. 每条 entity / relation 都必须有 evidence_text
4. evidence_text 必须来自输入文本或 section_path
5. entity_type / relation_type 必须来自允许列表
6. 不确定时降低 confidence
7. 无法判断时输出 diagnostics
8. 输出严格 JSON，不输出解释性文字
```

### GraphBuilder 接入方式

第一版建议新增参数：

```powershell
.\venv\Scripts\python.exe run_graph_build.py extract --limit 20 --include-llm
```

行为：

```text
1. 默认不启用 LLM
2. 只有 --include-llm 或 config enabled=true 时启用
3. 规则抽取器仍先执行
4. LLMGraphExtractor 后执行
5. 所有 LLM 结果进入 extraction_candidates
6. batch stats 里区分 rule candidate 和 llm candidate
```

### 测试要求

新增或补充测试：

```text
tests/test_llm_graph_extractor.py
tests/test_graph_extraction_llm_pipeline.py
```

至少覆盖：

```text
1. LLM 输出合法 JSON 能转成 candidates
2. 非法 JSON 生成 diagnostic，不中断 batch
3. 缺少 evidence_text 的候选被拒绝或降级为 diagnostic
4. 非法 entity_type / relation_type 被拒绝
5. confidence 低于 min_confidence 的候选不进入 approved 状态
6. enabled=false 时不会调用 LLM
7. --include-llm 时 batch stats 记录 llm 候选数量
8. LLM 候选不会直接写入正式 entities / relations
```

### 验收标准

```text
1. 关闭 LLM 时，现有规则抽取行为不回归
2. 开启 LLM 时，能生成 LLM extraction_candidates
3. 所有 LLM candidates 有 created_by=llm:schema_extractor
4. 所有 LLM candidates 有 prompt_version / extractor_version
5. 所有 LLM candidates 有 evidence_text / confidence
6. 不直接写正式图谱
```

---

## 7. MVP-5：LLM 候选校验与最小 Normalization

### 目标

第一版不做复杂 Entity Resolution，但必须有基础校验，防止 LLM 输出污染候选表。

### 要做的事

```text
1. 新增 schema validation
2. 检查 entity_type 是否允许
3. 检查 relation_type 是否允许
4. 检查 evidence_text 是否非空
5. 检查 confidence 是否在 0 到 1
6. 对 name 做基础 normalize：strip、空白合并、全角半角括号归一
7. 同一 batch 内重复 candidate 用 fingerprint 合并 evidence
```

### 暂不做

```text
1. 不做复杂 LLM merge 判断
2. 不做跨批次 same_as 自动合并
3. 不做自动 different_from
4. 不做主动学习
```

### 验收标准

```text
1. 明显非法候选进不去候选表
2. 同 batch 重复候选不会大量堆积
3. 低置信候选不会被自动通过
```

---

## 8. MVP-6：CLI 与文档收口

### 目标

让下一位开发者或 Codex 能明确知道如何运行 MVP。

### 需要补充的命令说明

```powershell
.\venv\Scripts\python.exe run_graph_build.py audit
.\venv\Scripts\python.exe run_graph_build.py cleanup-stale-links --dry-run
.\venv\Scripts\python.exe run_graph_build.py cleanup-stale-links
.\venv\Scripts\python.exe run_graph_build.py export-manual --output data/manual_graph_facts.json
.\venv\Scripts\python.exe run_graph_build.py extract --limit 20 --include-llm
```

### 需要补充的文档

```text
1. 在 run_graph_build.py help 中能看到新命令
2. 在 PRD 或本任务清单中记录最终命令
3. 如果新增配置项，需要更新 config.ini 示例或注释
```

### 验收标准

```text
1. CLI help 可读
2. 文档与实际命令一致
3. 失败时错误信息可理解
```

---

## 9. MVP 总体验收命令

建议最终至少运行：

```powershell
.\venv\Scripts\python.exe run_graph_build.py audit
.\venv\Scripts\python.exe run_graph_build.py cleanup-stale-links --dry-run
.\venv\Scripts\python.exe run_graph_build.py export-manual --output data/manual_graph_facts.json
.\venv\Scripts\python.exe run_graph_build.py extract --limit 20 --include-llm
.\venv\Scripts\python.exe run_graph_build.py list
.\venv\Scripts\python.exe -m pytest tests/test_graph_audit.py tests/test_graph_cleanup.py tests/test_graph_manual_export.py tests/test_llm_graph_extractor.py -q
.\venv\Scripts\python.exe -m pytest tests/test_knowledge_graph.py tests/test_graph_extraction.py -q
```

如果当前项目默认全量 pytest 仍有历史失败，至少要求：

```text
1. 新增测试全部通过
2. 相关旧测试不新增失败
3. 失败列表与修改前一致或减少
```

---

## 10. 给 Codex 的第一条执行提示词

```text
请根据 `docs/3_待办清单/2026-07-09-知识图谱语义抽取MVP任务清单.md` 实施 MVP-0 到 MVP-3：

1. 新增 graph audit，只读输出图谱健康报告。
2. 新增 cleanup-stale-links，支持 --dry-run，非 dry-run 只删除无效 entity_chunk_links，不删除实体和关系。
3. 新增 export-manual，导出 admin/manual/seed/rule:special 事实到 JSON，不修改正式图谱。
4. 补充对应测试。

限制：
- 不要实现 LLMGraphExtractor。
- 不要清空 rag_relational.db。
- 不要删除 entities / relations。
- 不要改变现有规则抽取结果。
- 所有新增命令都挂到 run_graph_build.py。
```

---

## 11. 给 Codex 的第二条执行提示词

```text
请继续根据 `docs/3_待办清单/2026-07-09-知识图谱语义抽取MVP任务清单.md` 实施 MVP-4 和 MVP-5：

1. 新增 LLMGraphExtractor 基础版。
2. 新增 prompt 文件 `llm_graph_extractor_v1.md`。
3. 支持严格 JSON 解析、schema validation、confidence、evidence_text、prompt_version、extractor_version。
4. 通过 `run_graph_build.py extract --limit 20 --include-llm` 启用。
5. LLM 输出只进入 extraction_candidates，不直接写正式图谱。
6. 补充 tests/test_llm_graph_extractor.py 和 pipeline 测试。

限制：
- 默认配置必须 disabled，避免无意调用模型。
- 不做全量自动 approve。
- 不做 safe rebuild。
- 不做复杂 Entity Resolution。
```

---

## 12. 当前优先级结论

先做：

```text
audit
cleanup-stale-links
export-manual
```

再做：

```text
LLMGraphExtractor 基础版
schema validation
候选表接入
```

最后再考虑：

```text
safe rebuild
复杂 Entity Resolution
GraphRAG 深度融合
```

不要一上来让 LLM 全库跑，也不要一上来清库重建。当前最重要的是先获得可审计、可保护、可回滚的图谱构建底座。
