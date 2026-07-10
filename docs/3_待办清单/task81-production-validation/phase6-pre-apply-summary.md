# Task 8.1 Profile Sync — 阶段 6 apply 前检查摘要

生成时间：2026-07-10 15:16 (UTC+8)

## 备份

- 文件：`backups/rag_relational-pre-task81-apply-20260710-151353.db`
- SHA256：`A8D8C0B8C60D36A5131A8EF8399529B3915A9727D1EC2549E8A2950260893F7A`（与正式库一致）

## Batch

- `batch_id`：`e8267357-e5d2-41e8-9848-b37383be7b1f`
- `mode`：`profile_sync`
- staging 统计：entity 4 / alias 3 / relation 14 / diagnostic 7（跨 profile 去重后）
- actionable 待 apply：**13 项已全部 approved**
- actionable pending：**0**
- diagnostic：**7 项均为 rejected**（未误批）

## 已批准待 apply 清单（13 项）

### 实体（2）

| 类型 | 名称 | profile |
|---|---|---|
| DataTable | 管线面表 | pipeline_point_table（sibling_penalty_groups） |
| Field | 管线面表.管面编号 | pipeline_face_table |

### Alias（3）

| 实体 | Alias | profile |
|---|---|---|
| 管线点表 | 点数据结构 | pipeline_point_table |
| 管线线表 | 线表数据结构 | pipeline_line_table |
| 管线面表 | 面表数据结构 | pipeline_face_table |

### 关系（8）

| 类型 | 源 | 目标 | profile |
|---|---|---|---|
| belongs_to | 管线点表 | PipelineBuilder | pipeline_point_table |
| belongs_to | 管线线表 | PipelineBuilder | pipeline_line_table |
| belongs_to | 管线面表 | PipelineBuilder | pipeline_face_table |
| has_table | PipelineBuilder | 管线面表 | pipeline_face_table |
| has_field | 管线面表 | 管线面表.管面编号 | pipeline_face_table |
| different_from | 管线点表 | 管线线表 | pipeline_point_table |
| different_from | 管线点表 | 管线面表 | pipeline_point_table |
| different_from | 管线线表 | 管线面表 | pipeline_point_table |

> `defined_in`：本 batch 无候选（`missing_section_entity` diagnostic，管线面表 Section 尚未入库，符合预期）

## Batch Quality

- `quality --batch`：**ok = true**，errors/warnings 为空

## 环境

- 10605 无监听
- 无 run.py / uvicorn / run_graph_build / sync_profiles_to_graph 进程

## 下一步（需明确授权）

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\venv\Scripts\python.exe run_graph_build.py apply --batch e8267357-e5d2-41e8-9848-b37383be7b1f
```

授权后执行阶段 8–10 复验。
