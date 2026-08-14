# Profile 关系沉淀到知识图谱 PRD

> **进度说明（2026-07-28）**：第一阶段（候选抽取、CLI sync、审批工作台、Task 8.1/8.2 正式库 apply）**已完成**。第二阶段「legacy Profile 自动瘦身」**仍待办**。现行待办见 [待办清单.md](../待办清单.md)。

## 1. 背景

当前 RAG 系统已经完成：

- 文档解析与结构化切块
- Chroma 向量检索
- BM25 混合检索
- Query Planner
- Query Contextualizer
- Graph-RAG 接入
- Entity Guard
- Retrieval Intent Profile
- Evaluation Dataset Governance

其中 `data/retrieval_intent_profiles.json` 作为“检索治理中间层”，已经解决了一批具体检索问题，例如：

- `PipelineBuilder` 与“管线发布服务”混淆
- `管线点表 / 管线线表 / 管线面表` 之间串台
- `DOMBuilder` 查询被错误引向非目标来源
- 某些结构化表格问题召回不足

但是当前 Profile 中混合了两类内容：

```text
1. 稳定领域知识关系
2. 检索策略参数
```

例如：

```json
{
  "entity_aliases": ["管线点表", "点数据结构"],
  "recall_terms": ["管点编号", "地面高程"],
  "section_families": [
    ["PipelineBuilder > 数据规范 > 管线点表", "PipelineBuilder > 数据规范 > 点数据结构"]
  ],
  "preferred_sources": ["StampTools"],
  "sibling_penalty_groups": [
    ["管线点表", "点数据结构", "管线线表", "线表数据结构", "管线面表", "面表数据结构"]
  ]
}
```

其中：

```text
管线点表 alias_of 点数据结构
PipelineBuilder has_table 管线点表
管线点表 has_field 管点编号
管线点表 different_from 管线线表
```

这些属于稳定知识关系，应该沉淀进知识图谱。

而：

```text
preferred_sources
fallback_sources
candidate_min_k
intent_terms
```

属于检索策略，不应该写入知识图谱。

因此需要新增一层：

```text
Profile → Graph 关系沉淀机制
```

让 Profile 从“长期规则补丁”变成“发现图谱缺口的中间层”。

---

## 2. 当前问题

### 2.1 Profile 承担了过多知识职责

当前 Profile 不只是控制召回，还隐含了领域关系。

例如：

```text
管线点表 / 点数据结构 是同义或等价章节
管线点表 / 管线线表 / 管线面表 是兄弟表族
管点编号 / 地面高程 是管线点表字段
DOMBuilder 属于 StampTools
```

这些关系如果长期只存在 Profile 中，会导致：

- 图谱不完整
- Graph Retriever 无法利用这些关系
- Profile 越写越重
- 同一类知识在 Profile 和 Graph 中重复维护

### 2.2 图谱与 Profile 职责边界不清

当前理想架构应该是：

```text
知识事实 → Knowledge Graph
检索策略 → Retrieval Intent Profile
查询意图 → Query Planner
实体识别 → Entity Guard / Graph Retriever
召回融合 → Hybrid + Graph Fusion
```

但当前实际状态是：

```text
Profile = 检索策略 + 部分领域知识 + 部分临时补丁
```

这会让系统长期演化困难。

### 2.3 图谱缺口无法自动沉淀

当前如果 Profile 修复了一个检索问题，例如：

```text
管线点表字段要求
```

即使验证通过，这些知识也不会自动进入图谱。

后续如果删除 Profile，图谱仍然不知道：

```text
PipelineBuilder has_table 管线点表
管线点表 has_field 管点编号
```

导致 Profile 无法逐步瘦身。

---

## 3. 产品目标

建立 Profile-Guided Graph Enrichment 机制。

目标：

```text
从 Profile 中提取稳定领域关系
        ↓
生成图谱候选关系
        ↓
人工审核 / 规则校验
        ↓
写入知识图谱
        ↓
Graph Retriever 可直接使用
        ↓
Profile 逐步瘦身
```

最终实现：

```text
Profile 负责检索治理
Graph 负责知识事实
```

---

## 4. 非目标

本阶段不做：

- 不做 LLM 自动大规模抽取
- 不直接删除现有 Profile
- 不把所有 Profile 字段写入图谱
- 不自动 approved 所有候选关系
- 不修改主 RAG 问答逻辑
- 不重建知识库
- 不重建 Chroma
- 不改变现有 Graph-RAG 主链路

本阶段只做：

```text
Profile 中稳定知识关系的识别、候选生成、校验、可控写入。
```

---

## 5. 核心原则

### 5.1 图谱只保存领域事实

可以入图：

```text
alias_of
different_from
belongs_to
has_table
has_field
defined_in
```

不入图：

```text
preferred_sources
fallback_sources
candidate_min_k
intent_terms
recall boost
penalty weight
```

### 5.2 先 dry-run，后写入

第一版必须支持：

```bash
python sync_profiles_to_graph.py --dry-run
```

输出候选关系，不修改数据库。

只有显式执行：

```bash
python sync_profiles_to_graph.py --apply
```

才允许写入。

### 5.3 默认 pending，不默认 approved

Profile 推导出的关系不是原始文档抽取结果，第一版默认：

```text
review_status = pending
created_by = rule:profile_sync
```

除非是明确安全规则，例如已存在实体 alias，可以在配置中允许自动 approved。

### 5.4 不能覆盖人工审核结果

如果图谱中已经存在同样关系：

```text
source_entity_id
target_entity_id
relation_type
```

则不能重复创建。

如果已有关系状态为：

```text
approved
rejected
```

不得自动覆盖。

---

## 6. 映射规则

### 6.1 entity_aliases → alias / alias_of

Profile 示例：

```json
{
  "entity_aliases": ["管线点表", "点数据结构"]
}
```

生成候选：

```text
实体：管线点表
alias：点数据结构
```

或：

```text
管线点表 alias_of 点数据结构
```

建议优先落到 `aliases` 表。

规则：

- 第一个 alias 作为 canonical entity name
- 后续 alias 写入 aliases 表
- 如果实体不存在，则创建 pending entity
- 如果 alias 已存在，不重复写入

示例：

```text
Entity:
管线点表

Alias:
点数据结构
```

### 6.2 section_families → defined_in / has_table / belongs_to

Profile 示例：

```json
{
  "section_families": [
    ["PipelineBuilder > 数据规范 > 管线点表", "PipelineBuilder > 数据规范 > 点数据结构"]
  ]
}
```

解析：

```text
Tool: PipelineBuilder
Section: 数据规范
Table: 管线点表 / 点数据结构
```

生成候选：

```text
PipelineBuilder has_table 管线点表
管线点表 belongs_to PipelineBuilder
管线点表 defined_in PipelineBuilder > 数据规范 > 管线点表
```

如果同一 family 下有多个末级章节名：

```text
管线点表
点数据结构
```

生成：

```text
管线点表 alias_of 点数据结构
```

或 alias 记录。

### 6.3 sibling_penalty_groups → different_from

Profile 示例：

```json
{
  "sibling_penalty_groups": [
    ["管线点表", "点数据结构", "管线线表", "线表数据结构", "管线面表", "面表数据结构"]
  ]
}
```

不能简单两两 different_from。

需要先识别 alias group：

```text
管线点表 = 点数据结构
管线线表 = 线表数据结构
管线面表 = 面表数据结构
```

然后生成不同 canonical 之间的 different_from：

```text
管线点表 different_from 管线线表
管线点表 different_from 管线面表
管线线表 different_from 管线面表
```

禁止生成：

```text
管线点表 different_from 点数据结构
```

因为它们是同义/等价关系。

### 6.4 recall_terms → has_field 候选

Profile 示例：

```json
{
  "recall_terms": ["点数据结构", "管点编号", "地面高程", "字段名", "说明"]
}
```

其中：

```text
管点编号
地面高程
```

可能是字段，应生成：

```text
管线点表 has_field 管点编号
管线点表 has_field 地面高程
```

但：

```text
字段名
说明
```

属于通用词，不应入图。

第一版规则：

- 长度小于 2 的词跳过
- 通用词黑名单跳过
- `字段名`、`说明`、`数据`、`设置`、`配置`、`路径` 等通用词跳过
- 优先从 section_family 的目标表实体挂接字段
- 生成状态默认 pending

### 6.5 preferred_sources / fallback_sources

不直接入图。

例如：

```json
{
  "entity_aliases": ["DOMBuilder"],
  "preferred_sources": ["StampTools"],
  "fallback_sources": ["StampServer"]
}
```

不生成：

```text
DOMBuilder preferred_source StampTools
DOMBuilder fallback_source StampServer
```

但可以生成候选提示：

```text
possible:
DOMBuilder belongs_to StampTools
```

这类候选必须人工确认，不自动写入。

---

## 7. 新增模块

### 7.1 新增服务

```text
rag_knowledge/services/profile_graph_sync.py
```

职责：

- 读取 `data/retrieval_intent_profiles.json`
- 校验 Profile 格式
- 解析可沉淀关系
- 生成候选实体、alias、关系
- 支持 dry-run
- 支持 apply
- 避免重复写入
- 输出同步报告

### 7.2 新增 CLI

```text
sync_profiles_to_graph.py
```

命令：

```bash
python sync_profiles_to_graph.py --dry-run
```

```bash
python sync_profiles_to_graph.py --apply
```

可选参数：

```bash
--profile-id pipeline_point_table
--review-status pending
--json
```

### 7.3 输出示例

dry-run 输出：

```text
Profile: pipeline_point_table

Will create entities:
- 管线点表 [DataTable]
- 管点编号 [Field]
- 地面高程 [Field]

Will create aliases:
- 管线点表 alias 点数据结构

Will create relations:
- PipelineBuilder has_table 管线点表
- 管线点表 belongs_to PipelineBuilder
- 管线点表 has_field 管点编号
- 管线点表 has_field 地面高程
- 管线点表 different_from 管线线表
- 管线点表 different_from 管线面表

Skipped:
- 字段名：generic recall term
- 说明：generic recall term
- preferred_sources: StampTools：strategy-only field
```

---

## 8. 数据模型要求

复用当前图谱 schema：

```text
entities
aliases
relations
entity_chunk_links
```

不新增数据库表。

新增字段只写到：

```text
created_by = rule:profile_sync
evidence_text = profile:<profile_id>:<field>
review_status = pending
confidence = 0.7
```

示例：

```text
created_by:
rule:profile_sync

evidence_text:
profile:pipeline_point_table:section_families
```

---

## 9. 候选关系状态

默认：

```text
review_status = pending
```

允许配置：

```bash
python sync_profiles_to_graph.py --apply --review-status approved
```

但不建议默认使用。

---

## 10. 去重规则

写入前必须检查：

### Entity 去重

按 normalized name 查重。

如果实体已存在：

```text
复用已有 entity_id
```

如果不存在：

```text
创建 pending entity
```

### Alias 去重

如果：

```text
entity_id + alias
```

已存在，则跳过。

### Relation 去重

如果：

```text
source_entity_id + target_entity_id + relation_type
```

已存在，则跳过。

---

## 11. 失败与保护机制

### 11.1 非法关系不写入

必须调用现有：

```text
validate_relation()
```

如果 schema 不允许：

```text
skip
record diagnostic
```

### 11.2 不能写入空实体

以下跳过：

```text
空字符串
纯数字
通用词
过短词
```

### 11.3 不能覆盖 rejected

如果图谱中已有 rejected 关系，不自动改为 pending 或 approved。

### 11.4 apply 前必须 dry-run 可通过

如果 dry-run 存在严重错误：

```text
invalid profile
illegal relation
ambiguous alias group
```

则 apply 失败。

---

## 12. 测试要求

新增测试：

```text
tests/test_profile_graph_sync.py
```

覆盖：

### 12.1 entity_aliases 测试

输入：

```json
{
  "entity_aliases": ["管线点表", "点数据结构"]
}
```

期望：

```text
创建 管线点表 entity
创建 点数据结构 alias
```

### 12.2 section_families 测试

输入：

```json
{
  "section_families": [
    ["PipelineBuilder > 数据规范 > 管线点表"]
  ]
}
```

期望：

```text
PipelineBuilder has_table 管线点表
管线点表 belongs_to PipelineBuilder
```

### 12.3 sibling_penalty_groups 测试

输入：

```json
{
  "sibling_penalty_groups": [
    ["管线点表", "点数据结构", "管线线表", "线表数据结构"]
  ]
}
```

期望：

```text
管线点表 alias 点数据结构
管线线表 alias 线表数据结构
管线点表 different_from 管线线表
```

不得生成：

```text
管线点表 different_from 点数据结构
```

### 12.4 recall_terms 测试

输入：

```json
{
  "recall_terms": ["管点编号", "地面高程", "字段名", "说明"]
}
```

期望：

```text
管线点表 has_field 管点编号
管线点表 has_field 地面高程
```

不得生成：

```text
管线点表 has_field 字段名
管线点表 has_field 说明
```

### 12.5 preferred_sources 测试

输入：

```json
{
  "preferred_sources": ["StampTools"]
}
```

期望：

```text
不创建图谱关系
只输出 strategy-only skipped diagnostic
```

### 12.6 幂等性测试

连续执行两次 apply：

```text
实体数量不重复增加
alias 不重复
relations 不重复
```

---

## 13. 验收命令

必须通过：

```bash
venv\Scripts\python.exe -m pytest tests/test_profile_graph_sync.py -q
```

必须通过已有图谱测试：

```bash
venv\Scripts\python.exe -m pytest tests/test_graph_extraction.py tests/test_graph_retrieval.py -q
```

必须通过全量测试：

```bash
venv\Scripts\python.exe -m pytest -q
```

如果本地真实图谱可用，再跑：

```bash
venv\Scripts\python.exe -m pytest -m integration -q
```

---

## 14. 验收标准

### 功能验收

完成后应支持：

```bash
python sync_profiles_to_graph.py --dry-run
```

输出候选实体、alias、relations、skipped diagnostics。

支持：

```bash
python sync_profiles_to_graph.py --apply
```

写入 pending 图谱关系。

### 数据验收

以当前 Profile 为例，应生成候选：

```text
管线点表 alias 点数据结构
管线线表 alias 线表数据结构
管线面表 alias 面表数据结构

PipelineBuilder has_table 管线点表
PipelineBuilder has_table 管线线表
PipelineBuilder has_table 管线面表

管线点表 has_field 管点编号
管线点表 has_field 地面高程

管线点表 different_from 管线线表
管线点表 different_from 管线面表
管线线表 different_from 管线面表

DOMBuilder belongs_to StampTools
```

其中 `DOMBuilder belongs_to StampTools` 第一版可以只生成 candidate，不自动 apply。

### 架构验收

完成后，架构应变为：

```text
Profile:
- intent_terms
- candidate_min_k
- preferred_sources
- fallback_sources
- 少量 recall_terms

Graph:
- alias
- belongs_to
- has_table
- has_field
- different_from
- defined_in
```

---

## 15. 风险

### 风险 1：把检索策略误写成知识事实

缓解：

```text
preferred_sources / fallback_sources / candidate_min_k 永不自动入图。
```

### 风险 2：alias 和 different_from 混淆

缓解：

```text
先识别 alias group，再生成 different_from。
同组内禁止 different_from。
```

### 风险 3：recall_terms 泛词污染图谱

缓解：

```text
通用词黑名单。
默认 pending。
不自动 approved。
```

### 风险 4：图谱重复写入

缓解：

```text
写入前按 entity / alias / relation 唯一键查重。
```

---

## 16. 推荐实施顺序

### Step 1

实现 dry-run 解析器。

只输出候选，不写数据库。

### Step 2

补单元测试。

覆盖 alias、section_families、sibling_penalty_groups、recall_terms、preferred_sources。

### Step 3

实现 apply。

默认 pending。

### Step 4

跑当前 Profile dry-run。

人工审查候选关系。

### Step 5

apply 后跑 Graph Retriever 回归。

验证以下问题：

```text
管线点表规范
管线线表字段要求
DOMBuilder 如何发布影像
PipelineBuilder 如何发布管线
```

### Step 6

瘦身 Profile。

把已经沉淀进图谱的 alias、field、different_from 从 Profile 中逐步减少。

---

## 17. 最终目标状态

完成后：

```text
Profile 不再长期保存领域关系。
Profile 只作为检索治理入口和图谱缺口发现层。
稳定关系沉淀进 Knowledge Graph。
Graph Retriever 直接利用这些关系提升召回。
```

最终架构：

```text
用户问题
  ↓
Query Contextualizer
  ↓
Query Planner
  ↓
Entity Guard
  ↓
Graph Retriever ← Knowledge Graph ← Profile Sync
  ↓
Hybrid Retrieval
  ↓
Fusion + Rerank
  ↓
LLM Answer
```

长期目标：

```text
Profile 越来越薄
Graph 越来越完整
RAG 规则补丁越来越少
```
