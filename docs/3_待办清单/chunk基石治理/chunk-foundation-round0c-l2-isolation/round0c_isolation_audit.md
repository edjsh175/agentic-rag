# Round 0C Isolation Audit

- generated_at: `2026-07-15T04:04:10.164580+00:00`
- watch_directory: `E:\申浩霖实习文件夹\rag_cy\rag\watch_directory`
- enter_0g: **False**

## Overall length

| | count | lt100 | lt200 | gt1200 |
|---|---:|---:|---:|---:|
| before | 366 | 33.9% | 59.0% | 4.4% |
| after | 234 | 14.5% | 22.6% | 6.8% |
| PRD gate | - | <=5% | <=15% | <=5% |

## Identity

- within-doc section_id sharing: **OK** (not a go/no-go failure)
- cross-document section_id collisions: **0**
- section_id raw duplicates (includes within-doc): 26 (occurrences 79)
- chunk_uid duplicates: **0** (occurrences 0)
- source_document_id unique values: 3 / 234 chunks
- broken prev/next: **0**

## Per document

### StampServer用户手册_Rocky9 .docx

- elements -> final: `181 -> 122`
- lt200: `53.0% -> 21.3%`
- median chars: `186 -> 367.5`
- flush_reason_counts: `{"merge_under_target_min": 37, "already_at_target_min": 21, "merge_same_l1_short_leaf": 21, "different_l1_hard_boundary": 28, "next_leaf_not_short": 32, "merge_command_follow": 1, "soft_max_exceeded": 11, "l2_bucket_target_reached": 15, "projected_over_soft_max": 3, "flush_before_atomic": 5, "atomic_keep": 5, "different_parent": 1, "flush_eof": 1}`
- skip_reason_counts: `{"already_at_target_min": 21, "different_l1_hard_boundary": 28, "next_leaf_not_short": 32, "soft_max_exceeded": 11, "l2_bucket_target_reached": 15, "projected_over_soft_max": 3, "different_parent": 1}`
- merge opportunities (flush replay): `{"merge_under_target_min": 37, "merge_same_l1_short_leaf": 21, "merge_command_follow": 1}`
- lineage missing element/raw (non-heading): `0` / `0`
- source section lineage errors: paths=`0`, ids=`0`, pairs=`0`, invalid_ids=`0`, titles=`0`

### StampTools用户手册.docx

- elements -> final: `89 -> 61`
- lt200: `56.2% -> 27.9%`
- median chars: `179 -> 383`
- flush_reason_counts: `{"merge_same_l1_short_leaf": 10, "next_leaf_not_short": 12, "already_at_target_min": 15, "merge_under_target_min": 18, "flush_before_atomic": 10, "atomic_keep": 10, "different_l1_hard_boundary": 7, "l2_bucket_target_reached": 5, "different_parent": 1, "flush_eof": 1}`
- skip_reason_counts: `{"next_leaf_not_short": 12, "already_at_target_min": 15, "different_l1_hard_boundary": 7, "l2_bucket_target_reached": 5, "different_parent": 1}`
- merge opportunities (flush replay): `{"merge_same_l1_short_leaf": 10, "merge_under_target_min": 18}`
- lineage missing element/raw (non-heading): `0` / `0`
- source section lineage errors: paths=`0`, ids=`0`, pairs=`0`, invalid_ids=`0`, titles=`0`

### StampWebRTC用户手册.docx

- elements -> final: `96 -> 51`
- lt200: `72.9% -> 19.6%`
- median chars: `126.5 -> 352`
- flush_reason_counts: `{"different_l1_hard_boundary": 8, "merge_same_l1_short_leaf": 44, "merge_under_target_min": 1, "l2_bucket_target_reached": 25, "next_leaf_not_short": 14, "already_at_target_min": 1, "flush_before_atomic": 1, "atomic_keep": 1, "flush_eof": 1}`
- skip_reason_counts: `{"different_l1_hard_boundary": 8, "l2_bucket_target_reached": 25, "next_leaf_not_short": 14, "already_at_target_min": 1}`
- merge opportunities (flush replay): `{"merge_same_l1_short_leaf": 44, "merge_under_target_min": 1}`
- lineage missing element/raw (non-heading): `0` / `0`
- source section lineage errors: paths=`0`, ids=`0`, pairs=`0`, invalid_ids=`0`, titles=`0`

## WebRTC diagnosis

- elements -> final: `96 -> 51`
- lt200: `72.9% -> 19.6%`
- flush_reason_counts: `{"different_l1_hard_boundary": 8, "merge_same_l1_short_leaf": 44, "merge_under_target_min": 1, "l2_bucket_target_reached": 25, "next_leaf_not_short": 14, "already_at_target_min": 1, "flush_before_atomic": 1, "atomic_keep": 1, "flush_eof": 1}`
- skip_reason_counts: `{"different_l1_hard_boundary": 8, "l2_bucket_target_reached": 25, "next_leaf_not_short": 14, "already_at_target_min": 1}`
- merge_opportunity_counts: `{"merge_same_l1_short_leaf": 44, "merge_under_target_min": 1}`

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
    "next_len": 125
  },
  {
    "reason": "l2_bucket_target_reached",
    "prev_path": "概述 > 显卡设置 > 独立显卡设置",
    "next_path": "概述 > 推流启动",
    "prev_order": 2,
    "next_order": 5,
    "bucket_len": 441,
    "next_len": 180
  },
  {
    "reason": "next_leaf_not_short",
    "prev_path": "概述 > 推流启动",
    "next_path": "概述 > 基本操作 > 鼠标",
    "prev_order": 5,
    "next_order": 6,
    "bucket_len": 180,
    "next_len": 400
  },
  {
    "reason": "already_at_target_min",
    "prev_path": "概述 > 基本操作 > 鼠标",
    "next_path": "概述 > 基本操作 > 键盘",
    "prev_order": 6,
    "next_order": 7,
    "bucket_len": 400,
    "next_len": 51
  },
  {
    "reason": "next_leaf_not_short",
    "prev_path": "快捷菜单 > 图层管理",
    "next_path": "快捷菜单 > 飞行路径",
    "prev_order": 9,
    "next_order": 10,
    "bucket_len": 144,
    "next_len": 972
  },
  {
    "reason": "l2_bucket_target_reached",
    "prev_path": "快捷菜单 > 飞行路径",
    "next_path": "快捷菜单 > 视点管理",
    "prev_order": 10,
    "next_order": 11,
    "bucket_len": 972,
    "next_len": 273
  },
  {
    "reason": "l2_bucket_target_reached",
    "prev_path": "快捷菜单 > 光影控制",
    "next_path": "快捷菜单 > 天气特效",
    "prev_order": 11,
    "next_order": 13,
    "bucket_len": 486,
    "next_len": 263
  },
  {
    "reason": "l2_bucket_target_reached",
    "prev_path": "快捷菜单 > 地形透明",
    "next_path": "快捷菜单 > 模型透明",
    "prev_order": 13,
    "next_order": 15,
    "bucket_len": 386,
    "next_len": 44
  },
  {
    "reason": "l2_bucket_target_reached",
    "prev_path": "快捷菜单 > 场景指北",
    "next_path": "快捷菜单 > 场景环绕",
    "prev_order": 15,
    "next_order": 20,
    "bucket_len": 348,
    "next_len": 42
  },
  {
    "reason": "different_l1_hard_boundary",
    "prev_path": "快捷菜单 > 屏幕录制",
    "next_path": "浏览 > 场景漫游",
    "prev_order": 20,
    "next_order": 22,
    "bucket_len": 141,
    "next_len": 332
  }
]
```

## Go / No-Go

- lt100 after=14.5% > 5% PRD gate
- lt200 after=22.6% > 15% PRD gate
- gt1200 after=6.8% > 5% PRD gate

