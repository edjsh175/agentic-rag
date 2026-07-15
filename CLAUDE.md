# RAG 本地知识库问答系统：开发与运维速查

## 协作原则

- 先确认事实与边界，再实施；存在歧义时明确说明假设或提问。
- 只做与请求直接相关的最小改动，保留用户已有的无关工作区变更。
- 功能、接口和默认值以代码与配置为准；本文件只记录稳定的使用约束和入口。

## 系统概览

后端使用 FastAPI、LangChain、ChromaDB 和 Ollama，前端使用 Vue 3、TypeScript 和 Vite。系统包含文档入库、知识库问答、博客同步、Chunk 审核和知识图谱能力。

| 区域 | 主要位置 | 职责 |
|---|---|---|
| 配置与启动 | `config.py`、`config.ini`、`run.py` | 配置加载、服务启动与组件初始化 |
| API 与模型 | `rag_knowledge/api/`、`rag_knowledge/models/` | HTTP 路由与请求/响应模型 |
| 数据存储 | `rag_knowledge/repository/`、`data/`、`chroma_db/` | Chroma、文件索引与 SQLite 图谱数据 |
| 业务服务 | `rag_knowledge/services/` | 入库、检索、缓存、重建、图谱与博客服务 |
| 前端 | `web/src/` | 问答、博客和管理端页面 |
| 测试与交付 | `tests/`、`scripts/`、`deploy/` | 测试隔离、仓库检查和 Docker 部署 |

## 配置与运行时约束

- `Config` 的优先级为：环境变量 > `config.ini` > 代码默认值；环境变量名使用 `{SECTION}_{KEY}` 的大写形式。
- 开发与生产分别使用 `config.ini`、`config-prod.ini`。模型、检索、缓存、图谱与路径配置都应从这些文件读取，不在文档中复制环境专属地址或模型快照。
- 当前检索方法为 Hybrid（向量召回与 BM25 的 RRF 融合）。MMR、Similarity 和 BM25 仍可通过 `[retrieval_strategy]` 配置或评估参数使用。
- Reranker、检索质量控制、上下文压缩和 LLM 图谱抽取是可选能力，开关与参数以对应配置段为准；关闭时必须保持安全降级，不得由请求参数绕过全局开关。
- 图谱检索、Query Planner 与对话上下文化用于补全检索意图和融合结构化证据；知识库事实仍必须由原始检索上下文支撑。

## 核心流程

### 文档入库

扫描器对监视目录和上传文件做哈希去重，加载器解析文档、图片或视频后生成结构化 chunk，并写入 Chroma 与文件索引。文本优先保留标题、表格和代码块结构；语义切块不可用时降级到固定长度切块。新 chunk 的审核状态由元数据控制，检索默认仅返回已审核内容。

### 问答与检索

请求先经过闲聊/安全分流，再由 Query Planner、上下文化和多查询构建检索意图。`RetrievalStrategy` 负责 Hybrid 等召回方式；后续可按配置使用图谱融合、Reranker、去重、动态 TopK、上下文压缩和联网搜索。同步与 SSE 流式接口共享相同的回答与来源规则。

回答只能陈述检索上下文支持的知识库事实，并使用来源编号。未命中时先明确说明；通用知识补充、联网搜索和智能体 Prompt 都不能伪造知识库来源。

### 知识图谱

关系数据库保存实体、关系和实体与 chunk 的关联。规则图谱提取生成候选，经过审核后才应用到正式图谱；LLM 提取为可选补充。图谱重建、候选应用和生产同步前都必须遵守一致性检查与写入确认门禁。

## 接口与前端入口

路由定义的唯一来源是 [`rag_knowledge/api/routes.py`](rag_knowledge/api/routes.py)。接口按以下稳定分类使用：

- 问答：健康检查、模型与知识库列表、文本/流式/图片问答。
- 知识库：上传、扫描、统计、索引查看、审核、一致性审计、嵌入模型切换和受控重建。
- 博客与会话：博客爬取、发布、同步和文章管理；聊天记录读写。
- 管理端：Chunk 列表与批量审核，图谱实体/关系管理，以及图谱候选批次的查看、审核、应用和质量检查。

前端路由定义在 [`web/src/router/index.ts`](web/src/router/index.ts)：知识库问答、博客管理、审核工作台、图谱候选审批和图谱管理。API 调用封装位于 `web/src/api/index.ts`；流式接口使用 `fetch` 和 `ReadableStream`。

## 数据与安全边界

- Chroma 持久化目录、`data/` 文件索引和 SQLite 关系库属于运行时数据。执行诊断、迁移或重建前先停止会访问这些数据的进程。
- 必须使用项目根目录的 `venv`，并严格遵循 `requirements-base.txt` 中的 Chroma 相关版本。不要用系统 Python、`.venv` 或其他版本的 Chroma 打开同一个库。
- 受控重建应使用 `/rebuild` 或 `RebuildCoordinator`，由其负责锁、备份、扫描、一致性断言和索引重建；不能手动清空正式库后直接扫描。
- 嵌入模型切换后必须重建知识库；新的 chunk ID 会使旧评测标注失效，应重新生成评测数据。
- `review_status`、`doc_category`、`kb_name` 和章节元数据直接影响检索与审核。变更审核状态后应通过既有 API 保持 BM25 和查询缓存同步。

## 开发、测试与部署

### 本地开发

```powershell
# 仅使用项目根目录的 venv
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe run.py
.\venv\Scripts\python.exe -m pytest

# 检索评估
.\venv\Scripts\python.exe run_eval_full.py
```

```bash
cd web
npm install
npm run dev
npm run check
```

`pytest.ini` 默认排除 `integration` 标记。`isolated_storage` fixture 和配置熔断器会将常规测试与正式运行时路径隔离；确需接触正式库时，显式运行 `pytest -m integration`，并确认没有其他进程占用数据目录。

### Docker 部署

`docker-compose.yml` 定义 `rag-service`（FastAPI）和 `rag-web`（Nginx + Vue dist）。生产配置通过卷挂载提供，`data/` 卷会完整覆盖容器内目录；初始化或升级前必须确认宿主机目录包含必需的运行时数据，不能用空目录覆盖正式数据。

```bash
docker compose build
docker compose up -d
```

完整部署前置条件、卷挂载、Ollama 连通性和验收步骤见 [`deploy/README.md`](deploy/README.md)。

## 交付检查

```powershell
.\venv\Scripts\python.exe scripts/check_repo_hygiene.py
```

提交前检查工作树、禁止的临时文件和必需的跟踪文件。SVN 的唯一提交目标为主干；执行 `svn update .` 或 `svn commit` 前，先运行 `svn info .` 并确认 URL 不包含 `branches/`。
