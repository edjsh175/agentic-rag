# 知识图谱语义抽取 MVP-2 任务清单

- **记录日期**：2026-07-09
- **对应 PRD**：`../2026-07-09-知识图谱语义抽取升级整体计划与PRD.md`
- **前置文档**：`MVP任务清单.md`
- **阶段定位**：MVP-1 已完成后，从“能生成 LLM 候选”推进到“候选可归一化、可消歧、可质量评估、可批量审核、可小范围安全应用”。
- **核心原则**：下一步不是让 LLM 抽更多，而是让 LLM 抽出来的东西可控地进入图谱。

---

## 1. 当前前提

第一版 MVP 已完成：

```text
1. graph audit
2. cleanup-stale-links
3. export-manual
4. LLMGraphExtractor 基础版
```

这说明系统已经具备：

```text
1. 能看清图谱健康状态
2. 能清理无效 chunk 证据链接
3. 能导出人工/特殊/seed 事实
4. 能让 LLM 按 Schema 生成候选
5. LLM 结果不会直接污染正式图谱
```

下一阶段的重点不是继续盲目扩大抽取范围，而是解决：

```text
LLM 候选如何变成可信、可审核、可去重、可安全应用的正式图谱事实
```

---

## 2. MVP-2 总目标

把系统从：

```text
能生成 LLM 候选
```

推进到：

```text
能小范围安全应用 LLM 候选，并且不会造成实体重复、类型冲突、人工事实丢失或图谱污染
```

MVP-2 的核心目标：

```text
1. domain_catalog 外置领域种子
2. 候选归一化和实体消歧
3. LLM 候选质量评估
4. 审批体验升级
5. safe rebuild dry-run
6. 小范围 apply 验证
7. 形成第二版质量门禁
```

---

## 3. MVP-2 非目标

这一阶段仍然不要做：

```text
1. 不全库 LLM 自动构建
2. 不默认自动 approve 所有高置信候选
3. 不直接清空 rag_relational.db
4. 不让 LLM 候选绕过 review/apply
5. 不做复杂前端审核工作台
6. 不做 Neo4j 迁移
7. 不急着 GraphRAG 深度融合
```

现在的重点是图谱构建质量，不是问答效果优化。

---

## 4. 任务总览

| 编号 | 任务 | 优先级 | 目标 |
|---|---:|---:|---|
| MVP2-0 | MVP-1 结果复核 | P0 | 确认 audit / cleanup / export / LLM 候选真实可用 |
| MVP2-1 | domain_catalog 外置 | P0 | 把产品/工具/服务/组件种子从 schema 里迁出 |
| MVP2-2 | CandidateNormalizer v1 | P0 | 减少重复候选和命名混乱 |
| MVP2-3 | Entity Resolution 基础版 | P0 | 区分新实体、旧实体、alias、冲突 |
| MVP2-4 | LLM 候选质量评估 | P0 | 建立 precision / evidence 质量抽查流程 |
| MVP2-5 | Review CLI 升级 | P0 | 支持按类型、来源、置信度批量审批 |
| MVP2-6 | Safe Rebuild Dry-run | P1 | 模拟安全重建，不直接改正式图谱 |
| MVP2-7 | 小范围 Apply 验证 | P1 | 只对一个 doc_category 或一批 chunk 应用 |
| MVP2-8 | Quality Gate v2 | P1 | 建立业务实体覆盖率和冲突门禁 |

---

## 5. MVP2-0：复核第一版 MVP 结果

### 目标

先确认第一版 MVP 不是“代码完成”，而是真的能支撑下一阶段。

### 要检查的内容

```text
1. audit 是否能稳定输出 JSON / MD
2. stale_link_count 是否已经清理到 0，或者 dry-run 能准确识别
3. manual_graph_facts.json 是否包含人工关系、特殊关系、seed 事实
4. LLMGraphExtractor 是否默认 disabled
5. --include-llm 是否只生成 extraction_candidates
6. LLM candidates 是否包含 evidence_text / confidence / prompt_version / extractor_version
7. 新增测试是否通过
```

### 验收命令

```powershell
.\venv\Scripts\python.exe run_graph_build.py audit
.\venv\Scripts\python.exe run_graph_build.py cleanup-stale-links --dry-run
.\venv\Scripts\python.exe run_graph_build.py export-manual --output data/manual_graph_facts.json
.\venv\Scripts\python.exe run_graph_build.py extract --limit 20 --include-llm
.\venv\Scripts\python.exe run_graph_build.py list
```

### 验收标准

```text
1. audit 结果可信
2. cleanup 不误删实体/关系
3. manual export 可读、可恢复设计上可用
4. LLM 只写候选，不写正式图谱
```

---

## 6. MVP2-1：domain_catalog 外置

### 目标

把具体领域事实从 `graph_schema.py` 中迁出，避免 schema 和业务词典混在一起。

### 当前问题

目前类似这些内容不应该继续硬编码在 schema 中：

```text
KNOWN_TOOL_NAMES
KNOWN_SERVICE_NAMES
KNOWN_PRODUCT_NAMES
DOC_CATEGORY_TO_PRODUCT
```

### 新增文件

```text
data/domain_catalog.json
```

### 建议结构

```json
{
  "products": [
    {
      "name": "StampServer",
      "aliases": ["StampServer用户手册"],
      "doc_categories": ["StampServer"]
    }
  ],
  "tools": [
    {
      "name": "PipelineBuilder",
      "aliases": ["管线发布工具"],
      "belongs_to": "StampTools"
    }
  ],
  "services": [
    {
      "name": "管线发布服务",
      "aliases": [],
      "belongs_to": "StampServer"
    }
  ],
  "environment_components": [
    {
      "name": "PostgreSQL",
      "aliases": ["Postgres", "postgresql-16"]
    },
    {
      "name": "Apache",
      "aliases": ["httpd"]
    }
  ]
}
```

### 实现要求

```text
1. 新增 DomainCatalogLoader
2. SectionPathExtractor 从 domain_catalog 读取 seed
3. LLMGraphExtractor 可把 domain_catalog 作为上下文提示，但不能只依赖 catalog
4. graph_schema.py 只保留 EntityType / RelationType / validate_relation
5. catalog 加载失败时给出清晰错误或降级提示
```

### 验收标准

```text
1. graph_schema.py 不再硬编码具体产品/工具/服务名
2. 旧规则抽取结果不回归
3. 修改 domain_catalog 后不需要改 Python schema
```

---

## 7. MVP2-2：CandidateNormalizer v1

### 目标

解决 LLM 候选重复、命名不一致、大小写混乱的问题。

### 需要处理的问题

```text
PostgreSQL
postgresql-16
Postgres
PostgreSQL数据库
```

这些不应该全部变成独立实体。

### 第一版能力

```text
1. strip 首尾空格
2. 合并连续空白
3. 中英文括号归一
4. 大小写归一辅助匹配
5. 基于 domain_catalog alias 匹配
6. 同 batch 内重复候选合并
7. evidence_text 合并
8. fingerprint 稳定生成
```

### 不做的能力

```text
1. 不做复杂语义 merge
2. 不让 LLM 自动决定 same_as 后直接合并
3. 不跨批次自动合并高风险实体
```

### 验收标准

```text
1. 同一 batch 内重复候选明显减少
2. alias 命中能合并或生成 alias candidate
3. evidence 不丢失
4. fingerprint 稳定
```

---

## 8. MVP2-3：Entity Resolution 基础版

### 目标

在候选进入正式图谱前，判断它到底是：

```text
1. 新实体
2. 已有实体
3. 已有实体 alias
4. 类型冲突
5. 需要人工确认的疑似重复
```

### 处理规则

```text
1. canonical_name 完全相同 + entity_type 相同：
   复用已有实体，合并 evidence

2. canonical_name 完全相同 + entity_type 不同：
   生成 type_conflict diagnostic，不自动 apply

3. alias 命中：
   生成 alias_of candidate 或绑定已有实体

4. 名称相似但不确定：
   生成 possible_duplicate diagnostic

5. 明确不同：
   保持独立，必要时生成 different_from candidate
```

### 验收标准

```text
1. 不会轻易把 Apache 和 Apache数据服务合并
2. 不会把 PipelineBuilder 误合并到管线发布服务
3. type conflict 必须进入待审，不自动通过
4. possible_duplicate 有样例输出
```

---

## 9. MVP2-4：LLM 候选质量评估

### 目标

建立 LLM 抽取质量评估流程，不靠感觉判断。

### 新增命令建议

```powershell
.\venv\Scripts\python.exe run_graph_build.py quality --batch <batch_id> --llm
```

或：

```powershell
.\venv\Scripts\python.exe run_graph_build.py eval-llm-candidates --batch <batch_id>
```

### 指标

```text
1. total_llm_candidates
2. valid_schema_count
3. invalid_schema_count
4. missing_evidence_count
5. low_confidence_count
6. duplicate_candidate_count
7. type_conflict_count
8. possible_duplicate_count
9. evidence_text_not_found_count
10. high_confidence_candidate_count
```

### 人工抽查样本

每个 batch 至少导出：

```text
1. 高置信实体样本
2. 高置信关系样本
3. 低置信样本
4. 被拒绝样本
5. 类型冲突样本
6. 疑似重复样本
```

### 验收标准

```text
1. 能看出 LLM 候选质量
2. 能定位 Prompt 或 Schema 问题
3. 能决定是否进入小范围 apply
```

---

## 10. MVP2-5：Review CLI 升级

### 目标

让 LLM 候选不需要只能 `approve-all`。

### 新增命令

```powershell
.\venv\Scripts\python.exe run_graph_build.py review --batch <batch_id> --summary

.\venv\Scripts\python.exe run_graph_build.py review --batch <batch_id> --approve-type EnvironmentComponent

.\venv\Scripts\python.exe run_graph_build.py review --batch <batch_id> --approve-relation-type requires

.\venv\Scripts\python.exe run_graph_build.py review --batch <batch_id> --approve-confidence-above 0.90

.\venv\Scripts\python.exe run_graph_build.py review --batch <batch_id> --reject-confidence-below 0.60

.\venv\Scripts\python.exe run_graph_build.py review --batch <batch_id> --approve-source "某文档.docx"
```

### 审批规则

可批量 approve：

```text
1. confidence >= 0.90
2. schema 合法
3. evidence_text 存在
4. evidence_text 能在 chunk 或 section_path 中找到
5. 无 type_conflict
6. 无 possible_duplicate 高风险标记
```

必须人工处理：

```text
1. type_conflict
2. possible_duplicate
3. alias / different_from
4. confidence 介于 0.70 到 0.90 的核心关系
```

默认 reject：

```text
1. missing_evidence
2. invalid_schema
3. confidence < 0.60
```

### 验收标准

```text
1. 可以批量审批安全候选
2. 不需要 approve-all
3. 冲突候选不会被误批
```

---

## 11. MVP2-6：Safe Rebuild Dry-run

### 目标

先模拟安全重建，不真正替换正式图谱。

### 新增命令建议

```powershell
.\venv\Scripts\python.exe run_graph_build.py rebuild-safe --dry-run
```

### dry-run 流程

```text
1. audit 当前图谱
2. export manual facts
3. 检查 stale links
4. 统计将被 supersede 的 rule:* / llm:* facts
5. 统计将被保留的 admin / manual / seed / rule:special facts
6. 运行小范围 extract
7. normalize candidates
8. entity resolution
9. 输出 before/after 预估差异
10. 不执行 apply
```

### 输出

```text
data/rebuild_safe_dry_run_report.json
data/rebuild_safe_dry_run_report.md
```

### 验收标准

```text
1. 能看出会保留哪些人工事实
2. 能看出会替换哪些自动事实
3. 能看出候选新增量
4. 不修改正式图谱
```

---

## 12. MVP2-7：小范围 Apply 验证

### 目标

不要全库应用。只选一个安全范围验证完整链路。

### 推荐范围

优先选择：

```text
1. 单个 doc_category
2. 或 20～50 个 chunk
3. 或一个低风险文档集合
```

不要第一轮选择全库。

### 流程

```text
1. extract --include-llm --limit 50
2. quality --batch --llm
3. review --summary
4. reject invalid / low confidence
5. approve 部分高置信候选
6. apply
7. audit
8. 人工抽查正式图谱
```

### 验收标准

```text
1. 正式图谱中新增业务实体
2. Section 占比略有下降
3. 没有明显重复实体堆积
4. 人工关系未丢
5. stale links 仍为 0
```

---

## 13. MVP2-8：Quality Gate v2

### 目标

建立第二版质量门禁，避免 LLM 扩大后污染图谱。

### 指标

```text
stale_link_count = 0
missing_evidence_count = 0
invalid_schema_count = 0
type_conflict_unresolved_count = 0
high_confidence_without_evidence_count = 0
manual_fact_preserved = true
duplicate_candidate_ratio <= 0.20
section_ratio 不上升
business_entity_count 不下降
```

### 小范围 apply 后建议目标

```text
EnvironmentComponent 增加
Command 增加
Procedure / Step 增加
ConfigItem 增加
Service / Tool 不出现明显错误归属
```

### 验收标准

```text
quality --graph 能明确 PASS / FAIL
失败时能给出原因和样例
```

---

## 14. 推荐执行顺序

严格按这个顺序做：

```text
1. 复核 MVP-1 结果
2. domain_catalog 外置
3. CandidateNormalizer v1
4. Entity Resolution 基础版
5. LLM 候选质量评估
6. Review CLI 升级
7. Safe Rebuild dry-run
8. 小范围 apply 验证
9. Quality Gate v2
```

不要跳过：

```text
CandidateNormalizer
Entity Resolution
Review CLI
```

否则 LLM 抽取一旦扩大，图谱会快速变成重复节点和冲突关系的垃圾场。

---

## 15. 给 Codex 的第一条执行提示词

```text
请根据 `docs/3_待办清单/2026-07-09-知识图谱语义抽取MVP-2任务清单.md`，先实施 domain_catalog 外置和 CandidateNormalizer v1。

目标：
1. 新增 data/domain_catalog.json。
2. 新增 DomainCatalogLoader。
3. 将 graph_schema.py 中具体产品、工具、服务白名单迁移到 domain_catalog。
4. SectionPathExtractor 改为从 DomainCatalogLoader 读取 seed。
5. 新增 CandidateNormalizer，支持 strip、空白合并、中英文括号归一、domain_catalog alias 匹配、同 batch 重复候选合并。
6. 保证现有 graph extraction 测试不回归。
7. 补充 domain_catalog 和 CandidateNormalizer 测试。

限制：
- 不要实现 safe rebuild。
- 不要做全库 LLM 抽取。
- 不要删除正式图谱数据。
- 不要改变 LLMGraphExtractor 的默认 disabled 行为。
```

---

## 16. 给 Codex 的第二条执行提示词

```text
请继续根据 `docs/3_待办清单/2026-07-09-知识图谱语义抽取MVP-2任务清单.md`，实施 Entity Resolution 基础版和 LLM 候选质量评估。

目标：
1. 新增 EntityResolutionService。
2. 处理 same canonical_name + same type、same canonical_name + different type、alias hit、possible_duplicate。
3. 类型冲突生成 diagnostic，不自动 apply。
4. 新增 run_graph_build.py quality --batch <batch_id> --llm 或 eval-llm-candidates 命令。
5. 输出 total_llm_candidates、invalid_schema_count、missing_evidence_count、duplicate_candidate_count、type_conflict_count、possible_duplicate_count。
6. 补充测试。

限制：
- 不要自动 merge 高风险实体。
- 不要自动 approve type_conflict。
- 不要直接写正式 entities / relations。
```

---

## 17. 给 Codex 的第三条执行提示词

```text
请继续根据 `docs/3_待办清单/2026-07-09-知识图谱语义抽取MVP-2任务清单.md`，实施 Review CLI 升级和 Safe Rebuild dry-run。

目标：
1. review 支持 --summary、--approve-type、--approve-relation-type、--approve-confidence-above、--reject-confidence-below、--approve-source。
2. 新增 rebuild-safe --dry-run。
3. dry-run 输出会保留的 manual/admin/seed/rule:special facts，以及会被 supersede 的 rule/llm facts。
4. 输出 data/rebuild_safe_dry_run_report.json 和 md。
5. 补充测试。

限制：
- dry-run 不允许修改正式图谱。
- 不允许清空 rag_relational.db。
- 不允许删除人工事实。
- 不允许 approve-all 作为默认路径。
```

---

## 18. 本阶段完成标准

MVP-2 完成后，应满足：

```text
1. 领域种子已外置到 domain_catalog
2. LLM 候选有基础归一化
3. 同 batch 重复候选明显减少
4. 类型冲突和疑似重复可被识别
5. LLM 候选质量可统计
6. review 支持批量审批安全候选
7. safe rebuild 可以 dry-run
8. 可以小范围 apply 并通过 audit 验证
```

---

## 19. 下一阶段预告

MVP-2 解决的是：

```text
候选能不能变干净
实体能不能不重复
冲突能不能被发现
审批能不能规模化
重建能不能先 dry-run
小范围 apply 是否安全
```

MVP-2 完成后，下一阶段才进入：

```text
MVP-3：safe rebuild 正式版 + 多文档 LLM 批量构建 + GraphRAG 增强验证
```

不要在 MVP-2 完成前全库跑 LLM，也不要在没有 dry-run 的情况下替换正式图谱。
