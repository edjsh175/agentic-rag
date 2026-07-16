# 0G 轮受控重建与切换准备清单

本文件为切块合并与二阶段适配上线前的受控迁移指南。  
**治理总账以 [GOV-0G-20260716-001-门禁未过人工豁免上线.md](./GOV-0G-20260716-001-门禁未过人工豁免上线.md) 为准。**

---

## 〇、 当前 live 状态（2026-07-16 结账）

| 项 | 状态 |
|---|---|
| live 向量库 | **新库已启用**：60 文件 / 3678 Chunk / 全部 approved / 一致性 3678/3678 |
| BM25 | 已按新库重建 |
| 自动 FR-10 质量门禁 | **未通过**（evidence_recall 32.50% &lt; 冻结基线 44.17%） |
| 系统曾自动回滚 | 是 |
| 最终上线依据 | **人工豁免**（非门禁全绿） |
| 黄金锚点文件 | **未修改** |
| 旧库备份 | `rag_knowledge__backup__before_user_waiver_20260716` |
| 未结清债务 | 45 条伪 Section 黄金锚点口径迁移；门禁账与 live 分离记账 |

---

## 一、 Go / No-Go 准入清单

### A. 重建前准备（技术就绪）

- [x] **完整测试无回退**：本地 pytest 通过（记录曾报 621/622）。
- [x] **隔离 Chunk 验收通过**：真实语料隔离验证，分块长度门禁通过。
- [x] **FR-10 旧库基线冻结**：pass_rate=**35.00%**、mean_completeness=**47.83%**、evidence_recall=**44.17%**。
- [x] **全目录画像盘点与映射**：`data/document_profile_map.json` 已人工确认。
- [x] **0G 冷备份完成**：`data/backups/0G_before_rebuild_20260716-121204`。
- [x] **停服与进程清空**：重建前已确认。

### B. 自动质量门禁（不得因豁免改写为通过）

- [x] **正式重建已执行**：操作 `20260716-123935-922592` → 60 文件 / 3678 Chunk。
- [x] **一致性**：3678/3678，全部 approved。
- [x] **Hybrid pass_rate / completeness 相对基线提升**：44.17% / 58.94%。
- [ ] **Hybrid evidence_recall ≥ 冻结基线 44.17%**：**未通过（32.50%）** → 触发自动回滚。
- [x] **根因已记录**：45/120 锚点依赖旧伪 Section；正文未丢；黄金集未改。详见治理决定书。

### C. 人工豁免（门禁外放行）

- [x] **操作者明确指示直接启用新库**（2026-07-16）。
- [x] **豁免切换完成**：live=新库；旧库保留 waiver 备份名。
- [x] **治理决定书已落盘**：`GOV-0G-20260716-001-门禁未过人工豁免上线.md`。
- [ ] **黄金锚点人工迁移并重新冻结**：未做（债务）。
- [ ] **豁免后重跑 FR-10 并达到门禁或书面关闭该项**：未做（债务）。

---

## 二、 0G 冷备份方案 (Chroma & Sqlite Backups)

正式受控重建前，必须停止所有读写，对已有库进行完全物理冷备份。

### 1. 备份数据路径
- **Chroma 向量数据库**：项目根目录的 `chroma_db` 文件夹（以 `Config.chroma_dir` 为准）。
- **关系数据库 (知识图谱/元数据)**：`data/rag_relational.db` 文件。
- **文件索引记录**：`data/file_index.json` 与决策索引 `data/ingestion_decisions.json`。

### 2. 备份命令
在 PowerShell 中执行以下命令（冷备份）：
```powershell
# 创建备份归档目录
New-Item -ItemType Directory -Force -Path "data/backups/0G_before_rebuild"

# 复制文件
Copy-Item -Recurse -Force "chroma_db" "data/backups/0G_before_rebuild/chroma_db"
Copy-Item -Force "data/rag_relational.db" "data/backups/0G_before_rebuild/rag_relational.db"
Copy-Item -Force "data/file_index.json" "data/backups/0G_before_rebuild/file_index.json"
Copy-Item -Force "data/ingestion_decisions.json" "data/backups/0G_before_rebuild/ingestion_decisions.json"
```

---

## 三、 停止后端与评估进程

在重建过程中，Chroma 数据库的写入具有独占性。多进程并发访问将导致 SQLite 锁死或 Chroma 数据损坏。

### 1. 查看并停止活动服务进程
- 停止运行中的后端 web 进程：
  ```powershell
  # 查找占用 8000 端口（或其他后端端口）的 pid
  Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }
  ```
- 停止所有处于活动状态的评估或扫描脚本任务。

---

## 四、 0G 回滚与恢复预案 (Rollback Plan)

若重建中途抛出异常、崩溃或升级后性能指标不达标，必须立即执行回滚以恢复线上服务。

### 1. 物理回滚步骤
1. 确保后端与并发写进程已完全停止。
2. 彻底删除损坏的运行时库文件：
   ```powershell
   Remove-Item -Recurse -Force "chroma_db"
   Remove-Item -Force "data/rag_relational.db"
   Remove-Item -Force "data/file_index.json"
   Remove-Item -Force "data/ingestion_decisions.json"
   ```
3. 从备份副本中完整还原数据：
   ```powershell
   Copy-Item -Recurse -Force "data/backups/0G_before_rebuild/chroma_db" "chroma_db"
   Copy-Item -Force "data/backups/0G_before_rebuild/rag_relational.db" "data/rag_relational.db"
   Copy-Item -Force "data/backups/0G_before_rebuild/file_index.json" "data/file_index.json"
   Copy-Item -Force "data/backups/0G_before_rebuild/ingestion_decisions.json" "data/ingestion_decisions.json"
   ```
4. 重新启动后端服务，验证线上查询接口是否恢复。

### 2. 本次已发生的回滚与再切换
1. **自动回滚（门禁）**：evidence_recall 未达标 → live 曾恢复旧库。  
2. **人工再切换（豁免）**：操作者决定启用新库 → live=3678 新库；旧库改名为 waiver 备份。  
3. 若豁免上线后需再退回：优先使用 `rag_knowledge__backup__before_user_waiver_20260716` 与冷备份目录，并另开治理记录。

---

## 五、 正式受控重建流程

不允许通过手动删除 `chroma_db` 或清空表的方式做非受控的重建。必须遵循事务级受控接口或重建协调器运行。

### 1. 重建触发入口
- **后端 API 重建端点**：通过发送 HTTP POST 请求到 `/api/rebuild` 进行事务级 staging 重建。
  - 请求载荷（本轮已获得全量 Chunk 审核批准）：
    ```json
    { "confirmation": "REBUILD_KNOWLEDGE_BASE", "approve_all_chunks": true }
    ```
- **命令行重建脚本**（基于 `RebuildCoordinator`）：
  在命令行中启动受控重建程序，使其在隔离的临时 staging collection 中完成解析、向量化及一致性校验，确认 100% 成功后原子化 swap 进 live 库：
  ```powershell
  .\venv\Scripts\python.exe -c "from rag_knowledge.services.rebuild_coordinator import RebuildCoordinator; ... coordinator.run()"
  ```

### 2. 已知修复（待提交）
重建协调器曾存在「切换后仍读旧句柄、误清理新库」问题，已在本轮修复并跑通相关测试；**代码合入 SVN/Git 前需单独提交审查**。
