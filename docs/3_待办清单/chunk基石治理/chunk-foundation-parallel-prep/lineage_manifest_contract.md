# 全链路解析留痕契约（FR-01.1 冻结稿）

状态：契约冻结 + 单文档 Spike。**不改变**生产拒绝路径。

---

## 1. 运行目录

```text
data/chunk_audit/<run_id>/
  manifest.json
  raw_blocks.jsonl
  canonical_elements.jsonl
  structure_decisions.jsonl
  transformations.jsonl
  content_decisions.jsonl
  final_chunk_lineage.jsonl
  quarantine.jsonl
  summary.json
  summary.md
```

Spike 使用 `data/chunk_audit/_spike_<run_id>/`（gitignore）。

---

## 2. 阶段对象（最小字段）

### Raw Block
`raw_block_id`, `block_index`, `block_type`, `raw_text`, `page_or_part`, `parent_container`, `style_snapshot`, `media_refs`

### Canonical Element
`element_id`, `source_raw_block_ids`, `element_type`, `content_markdown`, `searchable_text`, `element_order`, `candidate_section_path`, `content_type`

### Structure Validation
`validation_id`, `element_id`, `structure_action`, `issue_codes`, `heading_source`, `heading_confidence`, `resolved_section_path`

### Merge / Split
`transformation_id`, `action`, `input_ids`, `output_ids`, `boundary_type`, `reason_code`, `char_range`, `target_size`

### Content Decision
`decision_id`, `target_id`, `action` ∈ {`keep`,`quarantine`,`reject`}, `reason_code`, `confidence`, `quality_metrics`, `quarantine_ref`

### Final Chunk lineage
`chunk_id`, `source_document_id`, `source_snapshot_hash`, `source_raw_block_ids`, `source_element_ids`, `transformation_ids`, `content_decision_id`, `section_id`, `section_path`, `chunk_index_global`, `chunk_index_in_section`, `prev_chunk_id`, `next_chunk_id`, `parser_version`, `config_fingerprint`

---

## 3. 不变量

1. 无静默删除：每个 Raw Block 可查询 `kept|quarantined|rejected|non_content`
2. 双向追溯：Raw ↔ Final
3. 顺序稳定：同输入/配置/版本复跑一致
4. 阶段对账：输入输出合并拆分隔离拒绝可加总
5. 证据不可伪造：引用 → Final → Raw
6. 失败可见：`issue_codes` + summary

---

## 4. Spike 入口

[`scripts/spike_parse_lineage.py`](../../../scripts/spike_parse_lineage.py)

验证：对 fixture DOCX 生成 JSONL，断言每个 raw 有决策去向，Final 能反查 raw ids。
