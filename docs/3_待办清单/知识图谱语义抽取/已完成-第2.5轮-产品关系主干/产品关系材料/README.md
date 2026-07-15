# 产品关系主干 — 交接说明

本目录配合 [第 2.5 轮执行 PRD](../执行PRD.md) 与 [阶段完成纪要](../阶段完成纪要.md)。

## 你需要做什么

1. 把**公司已确认**的产品/工具/服务关系原件放入本目录下的 `原始材料/`（Excel、Markdown、PDF、导出表均可）。
2. 在执行 PRD 顶部「材料交接纪要」填写：交付日期、文件名、确认人。
3. **不要**要求实施者根据用户手册自行「总结」关系；以你确认的材料为准。

## 数据会落到哪里

| 层 | 路径 |
|---|---|
| 原件 | `原始材料/`（本目录） |
| 规范化真源 | 仓库根下 `data/product_relation_backbone.json` |
| 运行时 | `data/rag_relational.db`（`created_by=seed:product_backbone`） |

实体身份仍主要维护在 `data/domain_catalog.json`；主干关系写在 `product_relation_backbone.json`。

## 规范化 JSON 最小例子

```json
{
  "schema_version": 1,
  "source_ref": "product-relation-backbone/source/你的文件名.xlsx",
  "entities": [
    {"name": "PipelineBuilder", "entity_type": "Tool", "aliases": [], "doc_category": "StampTools"}
  ],
  "relations": [
    {
      "source": "PipelineBuilder",
      "relation_type": "belongs_to",
      "target": "StampTools",
      "note": "官方材料-产品归属表"
    }
  ]
}
```

> `source_ref` 为运行时 JSON 字段，可与历史路径保持一致；文档原件现存放于 `原始材料/`。

当前仓库中的 `data/product_relation_backbone.json` 已填入 40 entities / 40 relations（2026-07-14 apply）。

## 材料齐之后的顺序

```text
规范化 JSON → 对齐 domain_catalog 身份 → seed sync → 分拆审批 → apply → 再开第 3 轮
```

第 2 轮 LLM 试点 GO **不能**跳过本步直接全库 LLM 重建。
