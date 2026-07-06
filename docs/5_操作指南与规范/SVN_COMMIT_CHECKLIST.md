# SVN 提交清单

## 建议提交

- `rag_knowledge/`：检索、重排序、质量控制和评估实现。
- `tests/`：对应回归测试。
- `config.ini`、`config-prod.ini`：本地与生产检索配置。
- `requirements.txt`、`requirements-dev.txt`：运行与测试依赖。
- `run_eval_full.py`、`run_retrieval_ab.py`：评估入口。
- `data/eval_dataset.json`、`data/eval_dataset_hardcases.json`：固定评测集。
- `data/retrieval_ab_results.json`：可复核的 A/B 结果。
- `docs/`、`CLAUDE.md`：项目文档。

## 不应提交

- `venv/`、`.venv/`、`__pycache__/`、`.pytest_cache/`。
- `logs/`、`chroma_db/`、`chroma_db_backup_*/`、`backups/`。
- `models/`。
- `data/*.db` 及 SQLite 的 `-wal`、`-shm` 文件。
- `watch_directory/`、`scrapingImages/` 中的大体积原始文件。
- 临时输出和调试文件，例如 `_debug_out.txt`、错误生成的 `=1.2.0`。

## 提交前检查

1. 逐项选择“建议提交”文件，不要直接提交整个工作区。
2. 确认配置中没有密码、令牌或不应公开的内部地址。
3. 运行 `venv\Scripts\python.exe -m pytest -q` 和 `venv\Scripts\python.exe -m pip check`。
4. 检查新增文件体积，模型、向量库和原始附件必须留在仓库外。
