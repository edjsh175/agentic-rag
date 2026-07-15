# 知识图谱执行 PRD — 第 2.5 轮：产品关系主干

- **记录日期**：2026-07-14
- **状态**：**已完成（seed sync + 分拆审批 + apply）** — 2026-07-14
- **正式 batch**：`def72329-d322-467a-b111-bc455a8529a7`
- **备份**：`data/backups/rag_relational_pre_round2_5.db`
- **轮次编号**：Round-2.5 / MVP-3B+
- **母文档**：`../2026-07-09-知识图谱语义抽取升级整体计划与PRD.md`
- **前置条件**：第 1 轮已完成；第 2 轮 LLM 试点 **GO**（证明流程可用，**不**等于可跳过本轮）
- **后置门禁**：本轮已 apply；可进入第 3 轮准备（仍须遵守主干边界）
- **周期建议**：材料齐后 2–4 个工作日
- **是否启用 LLM**：**否**（本轮只做官方关系 seed）

### 材料交接纪要

```text
交付日期：2026-07-14
原件文件名：002产品体系.pptx；2024StampGIS三维产品白皮书.docx；STAMP产品架构截图.png
放置路径：docs/3_待办清单/知识图谱语义抽取/已完成-第2.5轮-产品关系主干/产品关系材料/原始材料/
确认人：用户指示「继续」推进草案入库（审核底稿仍可后续修订）
规范化 JSON 路径：data/product_relation_backbone.json（40 entities / 40 relations）
seed batch id：def72329-d322-467a-b111-bc455a8529a7
apply 日期：2026-07-14
```

### 实施纪要（2026-07-14）

```text
1. 新增 ProductBackboneGraphSyncService + sync_product_backbone_to_graph.py
2. domain_catalog 补 StampGIS三维产品及产品别名（架构层 Module 身份留在 backbone）
3. inspect_batch / export-manual / safe_rebuild dry-run 识别 seed:* 保留
4. 分拆审批 entity/alias/relation 后 apply：+36 entities / +40 relations / +34 aliases
5. 主干边抽检 40/40；Task 8.1 Gate PASS；stale=0
```

---

## 1. 本轮要解决的问题

```text
❌ 图谱缺少公司已确认的产品/工具/服务关系主干
❌ 若直接全库 LLM，易发明或颠倒归属、与真实产品线矛盾
❌ domain_catalog 只有实体身份与简单 belongs_to 线索，不足以表达完整官方关系边界
```

本轮目标：**把业务方提供的产品关系固化为版本化 seed，写入正式库，作为后续规则/LLM 的主干与边界。**

实施者**不得**根据用户手册或现库「总结」官方关系；内容以你确认的材料为准。

---

## 2. 数据存哪里（三层）

| 层 | 路径 | 用途 |
|---|---|---|
| 原始材料 | `产品关系材料/原始材料/` | 原件归档（Excel / MD / PDF / 说明），**不入运行时** |
| 规范化主干 | `data/product_relation_backbone.json` | Git/SVN 跟踪的真源；给人改、给程序读 |
| 运行时图谱 | `data/rag_relational.db` | sync → 分拆审批 → apply；`created_by = seed:product_backbone` |

与 `data/domain_catalog.json`：

- **catalog**：Product / Tool / Service 等**实体身份**（名称、别名、`doc_categories`）
- **backbone**：你确认过的**关系主干**（及主干所需的实体补充）；可引用 catalog 已有实体名

不把官方关系只写进 LLM prompt，不硬编码进单个 extract 规则。

---

## 3. `product_relation_backbone.json` Schema

```json
{
  "schema_version": 1,
  "source_ref": "product-relation-backbone/source/<你的文件名>",
  "entities": [
    {
      "name": "示例工具名",
      "entity_type": "Tool",
      "aliases": [],
      "doc_category": "StampTools"
    }
  ],
  "relations": [
    {
      "source": "示例工具名",
      "relation_type": "belongs_to",
      "target": "StampTools",
      "note": "来自官方材料第x节/表格"
    }
  ]
}
```

约束：

```text
1. entity_type / relation_type 必须落在 graph_schema 已允许集合内
2. relations[].source / target 必须能在 entities[] 或 domain_catalog / 正式库已有实体中解析
3. note 建议填写材料出处，便于审计
4. 空数组表示「尚未填入」；有内容后禁止实施者擅自增删边
5. schema_version 变更须同步改本 PRD 与加载代码
```

允许的关系类型以 [`rag_knowledge/models/graph_schema.py`](../../../rag_knowledge/models/graph_schema.py) 为准；常见主干边：`belongs_to`、`different_from`、`depends_on`、`requires`、`has_table` 等。若材料含 schema 尚未支持的边，先扩 schema 再入库，禁止 silent 丢弃。

---

## 4. 产品目标

### 4.1 必须达成

```text
1. source/ 中有业务方原件，且本页材料纪要已填写
2. data/product_relation_backbone.json 已按 schema 填入并经业务确认
3. 主干实体若缺席 domain_catalog，已补身份（仅身份，不另造未确认关系）
4. seed sync → 分拆审批（禁止 --approve-all）→ apply 成功
5. 正式库可查到主干关系；created_by 含 seed:product_backbone（或约定等价 seed 标记）
6. Task 8.1 Gate 仍为 PASS；与 profile 事实无未解决 type_conflict
```

### 4.2 本轮不做

```text
1. 不启用 LLM 抽取
2. 不做 rebuild-safe 全库替换
3. 不调 GraphRAG
4. 不由实施者发明产品线关系
```

---

## 5. 任务清单

| 编号 | 任务 | 优先级 | 验收 |
|:---:|------|:---:|------|
| R2.5-0 | 业务方交原件到 `source/`，填写本页纪要 | P0 | 文件存在 + 纪要完整 |
| R2.5-1 | 规范化写入 `data/product_relation_backbone.json` | P0 | schema 合法；业务确认 |
| R2.5-2 | 对齐 `domain_catalog.json` 缺失实体身份 | P0 | 主干实体可 resolve |
| R2.5-3 | 实现/接通 seed sync（mode=`product_backbone_seed`） | P0 | dry-run 候选数 = 主干边+实体 |
| R2.5-4 | 分拆 review + apply（confirm 三件套） | P0 | batch status=applied |
| R2.5-5 | audit / quality；记录 seed 事实增量 | P0 | 主干关系 100% 可查 |
| R2.5-6 | 更新总览状态为本轮「已完成」 | P1 | 总览与本 PRD 顶部状态同步 |

---

## 6. 实施步骤（材料齐后）

```text
Step 1  归档原件 → source/，填写「材料交接纪要」
Step 2  按 schema 填写 data/product_relation_backbone.json（内容只来自材料）
Step 3  缺席实体补进 domain_catalog.json（仅 name/type/aliases/doc_categories）
Step 4  停止占用 rag_relational.db 的进程
Step 5  seed sync（CLI 名以实施时为准，例如 sync_product_backbone_to_graph.py
        或 run_graph_build 子命令）→ 得到 batch_id
Step 6  review --summary；分拆批准 entity / relation（禁止 --approve-all）
Step 7  apply --confirm-db-path --confirm-batch --confirm-backup
Step 8  audit + 抽检主干边；Task 8.1 Gate
Step 9  本 PRD 与总览标记「已完成」，解锁第 3 轮
```

备份建议路径：`data/backups/rag_relational_pre_round2_5.db`

---

## 7. 入库后对后续轮次的边界规则

写入第 3 轮 / 总览，执行时强制遵守：

```text
1. rebuild-safe 保留集合必须包含 seed:product_backbone（及既有 manual/admin/seed/rule:special/profile 保护策略）
2. 规则或 LLM 候选若与主干边矛盾（改归属、否定 different_from、同名异型冲突）→ 不得进入 apply
3. LLM 优先在主干实体邻域补 Procedure / Step / Command / ConfigItem / EnvironmentComponent 等叶子
4. 不得用 --approve-all 覆盖主干相关 batch
```

---

## 8. 验收标准

```text
✅ source_ref 指向真实原件
✅ product_relation_backbone.json 通过人工确认
✅ seed batch applied
✅ 主干 relations 在正式库逐条可查
✅ stale_link_count = 0（本轮引入范围内）
✅ Task 8.1 Gate PASS
✅ 总览下一动作改为第 3 轮（不再写「等待材料」）
```

---

## 9. 交付物

```text
1. 产品关系材料/原始材料/<原件>
2. data/product_relation_backbone.json
3. （如有）domain_catalog.json 身份补丁
4. seed batch export / apply 审计
5. data/graph_audit_post_round2_5.json（或等价命名）
6. 本 PRD 顶部状态改为「已完成」并填日期与 batch id
```

---

## 10. 给 Codex 的执行提示

```text
材料未放入 source/ 前：只维护文档与空 schema，不要臆造 relations。
材料齐后：严格按本 PRD 规范化 JSON → seed sync → 分拆审批 → apply；
不要跳过本轮去跑第 3 轮全库 --include-llm。
```
