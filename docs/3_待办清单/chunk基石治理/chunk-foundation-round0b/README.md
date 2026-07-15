# Chunk 基石治理 Round 0B

## 状态

**已完成（母 PRD v1.2）**。代码实现、自动化回归、人工候选审核与标题 Precision 核验均已关闭；证据见 `../chunk-foundation-round0b-final/`。下一步为 Round 0C（Tree Lite + 合并生产接线），正式库重建仍在 Round 0G。

## 本轮范围

1. 重写低信息过滤，停止把中英文混合技术文本直接判为垃圾。
2. 收紧 DOCX 无样式标题兜底，防止正文、命令、配置和端口生成 Section。
3. 保持审计规则与生产规则一致，并支持在报告中显示执行轮次。

## 实现结果

### 低信息过滤

- 删除 `mixed_script_token` 和 `mixed_script_line` 作为直接拒绝条件。
- 保留目录、纯链接、极短噪声、低可读率和连续不可读字符检测。
- 新增非预期文字脚本检测，用于拦截混入 Arabic/Indic/Thai/Tibetan/Myanmar/Georgian 等字符区段的损坏文本。
- 正常中文技术文本中的路径、命令、端口、`WebGL2`、`Turnserver`、`≥` 等不再因混排被拒绝。

### 标题兜底

- 编号标题继续保留。
- 无编号标题改为同时满足“全段加粗 + 至少 14pt”。
- `vim /etc/...`、`enabled=1`、`5349：TLS/TCP，TLS服务` 等命令、配置和端口不再进入 fallback heading。
- 审计命令规则改为要求命令后有空格或结束符，`CD/DVD设置` 不再被误判为 `cd` 命令。

## 实测对比

| 指标 | Round 0A | Round 0B |
|---|---:|---:|
| 重解析过滤前块数 | 1100 | 706 |
| 重解析过滤后块数 | 658 | 685 |
| 重解析过滤率 | 40.18% | 2.97% |
| `mixed_script_token` 拒绝 | 256 | 0 |
| `mixed_script_line` 拒绝 | 2 | 0 |
| 剩余拒绝 | 305 | 21 |
| 剩余拒绝原因 | 混排为主 | 13 条 `single_short_line`、8 条 `compact_too_short` |

原始 DOCX 使用新标题规则重新解析后：

| 文档 | Canonical Element 数 | 命令/配置/端口型 Section 标题 |
|---|---:|---:|
| StampServer | 172 | 0 |
| StampTools | 89 | 0 |
| StampWebRTC | 96 | 0 |

## 验证

```text
63 passed, 1 warning, 4 subtests passed
```

新增回归覆盖：

1. Round 0A 的 50 条误删样本全部保留。
2. 长乱码与非预期文字脚本仍被拒绝。
3. 12pt 正文不升格为标题。
4. 命令、配置、端口不升格为标题。
5. 编号标题保持有效。
6. `CD/DVD设置` 不再被误判为命令。

## 本轮未改变的内容

1. 正式 Chroma 尚未重建，仍为 767 个旧 Chunk；长度中位数 101 字、`<100` 为 49.28% 的基线不会在本轮变化。
2. 旧库中已有的 34 个疑似错误标题仍会出现在旧快照审计中，只有重建后才会消失。
3. 短 Section 合并、稳定顺序/邻接、表格治理、图片 OCR 和 PDF 结构恢复均不在本轮范围内。

## 人工审核结果

- 34 条旧库疑似标题均为有效正文（命令、路径、端口或配置行），不应成为 Section 标题。
- 105 条短 Chunk 中，101 条为有效正文；4 条为 PDF 页眉、页码或封面信息。
- 原审计中的 21 条过滤拒绝，按原始资料判断有 20 条应保留；复审确认它们在正式 Chunk 流水线中均未丢失，原审计是在结构前缀生成前进行过滤判断而产生的假阳性。
- `chunk-foundation-round0b-recheck` 复审结果为 `pre=731`、`post=730`、过滤率 `0.14%`；唯一过滤项为 `StampWebRTC用户手册.docx` 的目录页签，reason code 为 `toc_marker`，有效正文误删为 0。

## Round 0B 关闭说明

母 PRD 记录：新解析器标题 Precision `100%`（361/361）；有效正文误删为 0。未达后续轮次门槛前仍不得重建正式库或全量图谱推广。

## 产物

- `chunk_health_audit.json`：Round 0B 完整审计数据。
- `chunk_health_audit.md`：可读审计报告。
- `heading_body_garbage_candidates.json`：已完成标签的人工审核候选集。
- `filter_reject_samples.json`：21 条拒绝样本的只读查看副本。
