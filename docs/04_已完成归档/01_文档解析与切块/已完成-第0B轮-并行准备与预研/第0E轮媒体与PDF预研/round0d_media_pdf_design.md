# Round 0D：图片 / OCR / PDF Spike 说明

状态：离线调研 Spike。**不修改** `FileLoader.max_images=5`，不宣称验收门槛达标。

## 已知生产差距

| 项 | 现状 | Spike 动作 |
|---|---|---|
| DOCX 图片 | 最多 5 张、vision 描述、空 section_path | 全量枚举 media + rId，输出 hash |
| OCR | 无 | 可选 `--ocr-sample`（需 pytesseract）；否则记录不可用 |
| PDF | `PyPDFLoader` 分页文本 | 与 pdfminer 页文本量/预览对比 |
| 版式标题/表格 | 无 | 仅记录差距，不实现 |

## 入口

```powershell
.\venv\Scripts\python.exe scripts\spike_media_pdf.py
.\venv\Scripts\python.exe scripts\spike_media_pdf.py --ocr-sample
```

产物：`round0d_media_pdf_spike.json` / `.md`
