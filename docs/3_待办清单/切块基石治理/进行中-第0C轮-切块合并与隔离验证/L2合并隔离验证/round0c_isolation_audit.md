# Round 0C Isolation Audit

- generated_at: `2026-07-15T10:16:19.519941+00:00`
- watch_directory: `E:\申浩霖实习文件夹\rag_cy\rag\watch_directory`
- chunk_foundation_gate_passed: **True**
- enter_0g: **False**

## PRD ordinary-text length

- scope: `content_type=text; heading/table/code/embedded_image and explicit same-section table_context excluded`
- all chunks before/after: `640 -> 299`

| | count | lt100 | lt200 | gt1200 |
|---|---:|---:|---:|---:|
| before | 615 | 62.9% | 79.8% | 1.8% |
| after | 260 | 2.7% | 7.3% | 0.0% |
| PRD gate | - | <=5% | <=15% | <=5% |

## Identity

- within-doc section_id sharing: **OK** (not a go/no-go failure)
- cross-document section_id collisions: **0**
- section_id raw duplicates (includes within-doc): 53 (occurrences 182)
- chunk_uid duplicates: **0** (occurrences 0)
- source_document_id unique values: 3 / 299 chunks
- broken prev/next: **0**

## Per document

### StampServer用户手册_Rocky9 .docx

- elements -> final: `399 -> 171`
- ordinary-text lt200: `80.3% -> 9.8%`
- median chars: `42 -> 411`
- flush_reason_counts: `{"merge_under_target_min": 227, "already_at_target_min": 48, "merge_same_l1_short_leaf": 28, "different_l1_hard_boundary": 28, "merge_command_follow": 1, "next_leaf_not_short": 25, "l2_bucket_target_reached": 15, "projected_over_soft_max": 7, "soft_max_exceeded": 8, "flush_before_atomic": 5, "atomic_keep": 5, "different_parent": 1, "flush_eof": 1}`
- skip_reason_counts: `{"already_at_target_min": 48, "different_l1_hard_boundary": 28, "next_leaf_not_short": 25, "l2_bucket_target_reached": 15, "projected_over_soft_max": 7, "soft_max_exceeded": 8, "different_parent": 1}`
- merge opportunities (flush replay): `{"merge_under_target_min": 227, "merge_same_l1_short_leaf": 28, "merge_command_follow": 1}`
- lineage missing element/raw (non-heading): `0` / `0`
- source section lineage errors: paths=`0`, ids=`0`, pairs=`0`, invalid_ids=`0`, titles=`0`

### StampTools用户手册.docx

- elements -> final: `109 -> 67`
- ordinary-text lt200: `71.7% -> 4.2%`
- median chars: `114 -> 424.5`
- flush_reason_counts: `{"merge_under_target_min": 33, "merge_same_l1_short_leaf": 14, "already_at_target_min": 20, "flush_before_atomic": 10, "atomic_keep": 10, "next_leaf_not_short": 10, "different_l1_hard_boundary": 7, "l2_bucket_target_reached": 3, "different_parent": 1, "flush_eof": 1}`
- skip_reason_counts: `{"already_at_target_min": 20, "next_leaf_not_short": 10, "different_l1_hard_boundary": 7, "l2_bucket_target_reached": 3, "different_parent": 1}`
- merge opportunities (flush replay): `{"merge_under_target_min": 33, "merge_same_l1_short_leaf": 14}`
- lineage missing element/raw (non-heading): `0` / `0`
- source section lineage errors: paths=`0`, ids=`0`, pairs=`0`, invalid_ids=`0`, titles=`0`

### StampWebRTC用户手册.docx

- elements -> final: `132 -> 61`
- ordinary-text lt200: `84.7% -> 3.4%`
- median chars: `81 -> 371`
- flush_reason_counts: `{"different_l1_hard_boundary": 8, "merge_under_target_min": 30, "merge_same_l1_short_leaf": 52, "already_at_target_min": 7, "l2_bucket_target_reached": 28, "flush_before_atomic": 1, "atomic_keep": 1, "next_leaf_not_short": 3, "projected_over_soft_max": 1, "flush_eof": 1}`
- skip_reason_counts: `{"different_l1_hard_boundary": 8, "already_at_target_min": 7, "l2_bucket_target_reached": 28, "next_leaf_not_short": 3, "projected_over_soft_max": 1}`
- merge opportunities (flush replay): `{"merge_under_target_min": 30, "merge_same_l1_short_leaf": 52}`
- lineage missing element/raw (non-heading): `0` / `0`
- source section lineage errors: paths=`0`, ids=`0`, pairs=`0`, invalid_ids=`0`, titles=`0`

## WebRTC diagnosis

- elements -> final: `132 -> 61`
- lt200: `84.7% -> 3.4%`
- flush_reason_counts: `{"different_l1_hard_boundary": 8, "merge_under_target_min": 30, "merge_same_l1_short_leaf": 52, "already_at_target_min": 7, "l2_bucket_target_reached": 28, "flush_before_atomic": 1, "atomic_keep": 1, "next_leaf_not_short": 3, "projected_over_soft_max": 1, "flush_eof": 1}`
- skip_reason_counts: `{"different_l1_hard_boundary": 8, "already_at_target_min": 7, "l2_bucket_target_reached": 28, "next_leaf_not_short": 3, "projected_over_soft_max": 1}`
- merge_opportunity_counts: `{"merge_under_target_min": 30, "merge_same_l1_short_leaf": 52}`

Sample skip flush events:

```json
[
  {
    "reason": "different_l1_hard_boundary",
    "prev_path": "",
    "next_path": "概述 > 运行环境",
    "prev_order": 1,
    "next_order": 2,
    "bucket_len": 2,
    "next_len": 26
  },
  {
    "reason": "already_at_target_min",
    "prev_path": "概述 > 显卡设置 > 独立显卡设置",
    "next_path": "概述 > 显卡设置 > 独立显卡设置",
    "prev_order": 2,
    "next_order": 6,
    "bucket_len": 320,
    "next_len": 119
  },
  {
    "reason": "l2_bucket_target_reached",
    "prev_path": "概述 > 推流启动",
    "next_path": "概述 > 基本操作 > 鼠标",
    "prev_order": 6,
    "next_order": 8,
    "bucket_len": 338,
    "next_len": 53
  },
  {
    "reason": "already_at_target_min",
    "prev_path": "概述 > 基本操作 > 鼠标",
    "next_path": "概述 > 基本操作 > 键盘",
    "prev_order": 8,
    "next_order": 10,
    "bucket_len": 400,
    "next_len": 51
  },
  {
    "reason": "l2_bucket_target_reached",
    "prev_path": "快捷菜单 > 飞行路径",
    "next_path": "快捷菜单 > 视点管理",
    "prev_order": 12,
    "next_order": 16,
    "bucket_len": 1150,
    "next_len": 36
  },
  {
    "reason": "already_at_target_min",
    "prev_path": "快捷菜单 > 光影控制",
    "next_path": "快捷菜单 > 光影控制",
    "prev_order": 16,
    "next_order": 19,
    "bucket_len": 350,
    "next_len": 134
  },
  {
    "reason": "l2_bucket_target_reached",
    "prev_path": "快捷菜单 > 天气特效",
    "next_path": "快捷菜单 > 地形透明",
    "prev_order": 19,
    "next_order": 22,
    "bucket_len": 431,
    "next_len": 89
  },
  {
    "reason": "l2_bucket_target_reached",
    "prev_path": "快捷菜单 > 地下浏览",
    "next_path": "快捷菜单 > 相机碰撞",
    "prev_order": 22,
    "next_order": 26,
    "bucket_len": 325,
    "next_len": 44
  },
  {
    "reason": "different_l1_hard_boundary",
    "prev_path": "快捷菜单 > 屏幕录制",
    "next_path": "浏览 > 场景漫游",
    "prev_order": 26,
    "next_order": 30,
    "bucket_len": 271,
    "next_len": 51
  },
  {
    "reason": "l2_bucket_target_reached",
    "prev_path": "浏览 > 场景漫游",
    "next_path": "浏览 > GPSTrack",
    "prev_order": 30,
    "next_order": 32,
    "bucket_len": 332,
    "next_len": 53
  }
]
```

## Go / No-Go

- all measured chunk-foundation gates passed

## Remaining Round 0G requirements

- FR-10 overall/category gates and thresholds are not frozen or verified
- 0E/OCR inclusion scope is not reviewed
- Go checklist is not frozen

