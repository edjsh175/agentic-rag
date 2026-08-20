# Task 8.1 / 8.2 正式库验收开发总结

> **历史快照（2026-07-28）**：Task 8.1 结论有效；其后图谱第 1–4 轮与 missing_evidence 清债见 docs/3_待办清单/知识图谱语义抽取/。


- **日期**：2026-07-10
- **详细报告**：[../3_待办清单/task81-production-validation/2026-07-10-正式库验收报告.md](../3_待办清单/task81-production-validation/2026-07-10-正式库验收报告.md)

## 结论

Profile 四表（点/线/面/DOMBuilder）Graph 事实迁移完成。正式库 batch `e8267357-e5d2-41e8-9848-b37383be7b1f` 已 apply，专项 Gate **PASS**。

## 写入摘要

| 类型 | 内容 |
|---|---|
| 实体 | `管线面表`、`管线面表.管面编号` |
| alias | 点数据结构、线表数据结构、面表数据结构 |
| 关系 | 三表 `different_from`；`belongs_to` / `has_table` / `has_field` |

正式库规模：704 entities、1623 relations、4 aliases。

## 测试

- 专项：17 passed
- 全量：471 passed，2 failed（CLI review 断言，与 apply 无关）

## 残留

- 全图 104 条历史 `missing_evidence`（Phase B 治理）
- 管线面表无 Phase B `defined_in` Section
- legacy migration 文件待瘦身
