# Chunk 基石治理 Round 0A

目标：建立可重复的真实语料基线，**不修改正式库**。

## 交付物

| 文件 | 说明 |
|---|---|
| `chunk_health_audit.json` / `.md` | 正式库只读审计报告 |
| `heading_body_garbage_candidates.json` | 标题/正文/垃圾标注候选（待人工 label） |
| `filter_false_positive_regression.json` | 被误删中文技术段落回归集 |
| `multi_chunk_qa_gold.json` | 多 Chunk 问答黄金样本 |
| `filter_reject_samples.json` | 重解析过滤拒绝样本 |

## 运行

```powershell
# 只读审计（含源文件重解析对比；不写 Chroma）
.\venv\Scripts\python.exe scripts/audit_chunk_health.py

# 仅读 Chroma，跳过重解析
.\venv\Scripts\python.exe scripts/audit_chunk_health.py --no-reparse
```

默认输出目录即本目录。

## 退出条件

后续 Round 0B～0F 的改动，都能用同一批语料与本目录黄金样本重复比较。
