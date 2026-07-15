# Chunk 基石治理 — Round 0B 并行准备包

在 Round 0B 收尾期间并行完成的 **评测准备** 与 **0C/0E/留痕设计 + 离线 Spike**。

> **编号权威**：以母 PRD **v1.2.2**（`../2026-07-14-RAG文档解析与Chunk基石治理PRD.md`）§7 / §13 / §14 为准。  
> 本目录文件名中的历史称呼对照如下，避免与旧笔记混淆。
> 
> **门禁提醒（v1.2.2）**：短块临时阈值与 FR-10 整体/`procedure` 下限均须隔离库实测后写入 Go 清单冻结；禁止预写 15%/35%、50%/60% 等未实测数字。`ocr` 与纳入范围绑定；`conflict` 在 Round 0F 接线后强制考核。

## 与 PRD v1.2.2 轮次对照

| 本目录产物 / 旧称呼 | PRD v1.2 归属 | 状态 |
|---|---|---|
| `round0c_merge_*`（合并/邻接） | **Round 0C** 生产接线输入 | 设计 + Spike ✅；生产 ❌ |
| `lineage_manifest_contract.md` | **Round 0C** 留痕 | 契约冻结 + Spike ✅ |
| `round0d_media_pdf_*`（旧名 0D 调研） | **Round 0E** 媒体/PDF | 调研 Spike ✅；生产 ❌ |
| `answer_governance_draft.md`（旧称正式 0E） | **Round 0F** FR-08 | 草案 ✅ |
| `admin_evidence_chain_spec.md` | **Round 0F** FR-09 | 规格 ✅ |
| `multi_chunk_qa_gold_v2` + `fr10_baseline_*` | **Round 0F** 评测基线 | ✅ |
| 受控重建 | **Round 0G** | 未开始 |

## 硬边界

- 不写正式 `chroma_db`，不调用 `/rebuild`
- 不改生产 `FileLoader` / `UnstructuredChapterLoader` 主路径行为（0C 正式实现前）
- 不把 Hit@K 当作 FR-10 主结论
- Round 0B 已由母 PRD 关闭（误删 0、标题 Precision 100%）

## 产物清单

| 文件 | 说明 |
|---|---|
| `multi_chunk_qa_gold_v2.json` | FR-10 分层 120 题（30/30/20/10/10/10/10） |
| `fr10_baseline_report.json` / `.md` | 当前正式库 retrieval 代理答案基线 |
| `answer_governance_draft.md` | FR-08 回答完整性/冲突策略草案 → 正式接线在 **0F** |
| `admin_evidence_chain_spec.md` | FR-09 管理证据链 UX 规格 → 正式接线在 **0F** |
| `round0c_merge_adjacency_design.md` | 0C 合并与邻接冻结设计 |
| `round0c_merge_spike_report.json` / `.md` | 三手册离线合并仿真 |
| `lineage_manifest_contract.md` | FR-01.1 留痕契约 |
| `fixtures/lineage_spike.docx` | 留痕 Spike 用小 DOCX（脚本可生成） |
| `round0d_media_pdf_design.md` | 媒体/PDF 调研说明（对应 PRD **0E**） |
| `round0d_media_pdf_spike.json` / `.md` | 媒体全量枚举 + PDF 对比 |

留痕运行产物目录：`data/chunk_audit/_spike_*`（已 gitignore，含原文）。

## 命令

```powershell
# 重建黄金集（一般不必重复）
.\venv\Scripts\python.exe scripts\build_multi_chunk_qa_gold_v2.py

# FR-10 离线打分（只读 Chroma；默认 retrieval 代理答案）
.\venv\Scripts\python.exe scripts\eval_multi_evidence_offline.py --mode retrieval
.\venv\Scripts\python.exe scripts\eval_multi_evidence_offline.py --mode rules --empty-baseline

# 0C 合并仿真
.\venv\Scripts\python.exe scripts\spike_short_section_merge.py

# 留痕 Spike
.\venv\Scripts\python.exe scripts\spike_parse_lineage.py

# 媒体/PDF Spike（PRD 0E 调研）
.\venv\Scripts\python.exe scripts\spike_media_pdf.py
```

相关代码：

- `rag_knowledge/evaluation/multi_evidence_metrics.py`
- `rag_knowledge/services/chunk_merge_spike.py`（禁止被 loader import，直至 0C 正式并入）
- `tests/test_multi_evidence_metrics.py`
- `tests/test_chunk_merge_spike.py`
- `tests/test_lineage_spike.py`

## 交接（v1.2）

| 事项 | 归属 |
|---|---|
| 本目录设计与基线 | 并行包已交付 |
| 下一步 | **Round 0C**：Tree Lite + 合并/邻接/血缘生产接线（隔离库，不重建正式库） |
| 0C 之后 | 0D 表格/策略（可缩）→ 0E 媒体/PDF 接线 → 0F EvidencePack + FR-08 → 0G 重建 |

## Spike 结果摘要（执行时点）

- FR-10 retrieval 基线（120 题）：pass_rate **35%**，mean_completeness **47.8%**，conflict/none/ocr 均为 0%（旧库 + 无冲突 Prompt 的预期）
- 合并：三手册 elements `366 → 276`，lt200 `59.0% → 50.4%`（仍远高于正式 0C/§8.2 门槛）
- 媒体：Server 191 / Tools 123 / WebRTC 201（生产仍截断为每文档 5 张）
- 留痕：fixture 双向追溯与无静默删除校验通过
- FR-10 详情见 `fr10_baseline_report.md`
