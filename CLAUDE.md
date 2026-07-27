## 1. 在编码前先思考

**不要臆断。不要掩盖困惑。要坦诚地指出利弊权衡之处。**
在实施之前：
- 明确陈述你的假设。如有疑问，请询问。
- 若存在多种解释，予以一一列出——切勿默许选择某一种。
- 若存在更简单的解决方法，请提及。在必要时提出异议。
- 若有不清楚之处，请停止思考。指出令人困惑的地方，并提出疑问。

## 2. "简洁至上"

**解决该问题所需的最简代码。绝无推测成分。**
- 没有超出要求之外的功能。
- 不会为一次性使用的代码进行抽象处理。
- 不会提供未被要求的"灵活性"或"可配置性"。
- 不会为不可能出现的情况进行错误处理。
- 如果你写了 200 行代码，而实际上只需 50 行，那就重写它。
问问自己："资深工程师会说这个设计过于复杂吗？"如果是这样，那就简化一下。

## 3. 手术改变

**只触及你必须触及的事物。只清理你自己的"污迹"。**
在编辑现有代码时：
- 不要修改相邻的代码、注释或格式。
- 不要对没有问题的部分进行重构。
- 保持现有风格，即便你的想法有所不同也应如此。
- 如果发现无关的无用代码，请提及——不要删除它。
当你的修改导致出现"孤变量"时：
- 清除因你的修改而变得无用的导入语句、变量和函数。
- 除非被要求，否则不要删除原有的无效代码。
测试要求：每一处改动的代码都应直接追溯到用户的操作请求。

## 4. 目标导向型执行

**明确成功标准。持续循环直至验证通过。**
将任务转化为可验证的目标：
- "添加验证" → "为无效输入编写测试，然后确保其能够通过"
- "修复错误" → "编写重现该错误的测试，然后确保其能够通过"
- "重构 X" → "在前后两次测试中确保其都能通过"
对于需要分步完成的任务，先概述一个简要的计划：```
1. [步骤] → 验证：[检查]2. [步骤] → 验证：[检查]3. [步骤] → 验证：[检查]```

明确的衡量标准能让您独立进行循环操作。而较模糊的标准（比如"让它运行起来就行"）则需要不断进行解释说明。

---

**这些准则如果能达成以下几点要求则视为有效：** 差异中的不必要的改动减少，因过于复杂而进行的重写减少，以及在实施前先澄清问题而非在犯错之后才去解决。

---

# 项目文档：RAG 本地知识库问答系统 v2.0

## 项目概述

基于 RAG（检索增强生成）的本地知识库问答系统。后端使用 FastAPI + LangChain + ChromaDB + Ollama，前端使用 Vue 3 + TypeScript + Vite。

### 当前阶段：质量治理收尾 + 本地已开图试用（生产模板仍关）

当前同步口径截至 **2026-07-27**：文本 RAG 检索与证据治理已形成可复现基线（FR-10 v4 / 2537 live：39/45 = 86.67%）。知识图谱语义抽取 **第 1–4 轮建设/验收已收口**：第 3 轮规则 + 第 3 轮补实 LLM + R7 类目扩面完成；**第 4 轮 GraphRAG 专项检索 A/B PASS**，并落地 **融合上限**（图块≤1 席 + 保护文本 top1）。**生产模板仍建议 `graph_retrieval.enabled=false`**；本地可开全图体验。阶段总结见 `docs/3_待办清单/知识图谱语义抽取/已完成-第4轮-GraphRAG实效验收/2026-07-27-阶段总结.md`。准入门槛见 `docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集/图谱接入前门槛-2026-07-20.md`。生成物归档约定见 `docs/5_操作指南与规范/data目录约定.md`。
- ✅ 阶段一：评估框架 — 已完成，Baseline 指标已测得（Recall@3=85.7%, MRR=0.79）
- ✅ 阶段二：BM25 关键词检索 — 已完成（Recall@3=92.9%, MRR=0.85，+7pp）
- ✅ 阶段三：混合检索（Hybrid Search）— 已完成（Recall@3=92.9%, MRR=0.88）
- ✅ 阶段四：语料治理 + 新资料规范入库 — 已完成（文本清洗、难例评测集生成、批量审核入口）
- ✅ 阶段五：Cross-Encoder Reranker — 代码、降级流程和本地 A/B 验证已完成；当前最佳策略为 `Hybrid+Rerank`；**生产 CPU 默认关闭**（`config-prod.ini` / `RERANKER_ENABLED=false`），本地开发可启用
- ✅ 阶段五附加：检索质量控制 — 已实现分数归一化、Jaccard 去重、动态 TopK；但 `Hybrid+Rerank+Quality` 指标略低于 `Hybrid+Rerank`，暂不建议默认开启
- ✅ 阶段六：性能优化 — Embedding 缓存、查询缓存、Hybrid 两路并发召回、异步检索与并发检索已落地，并已补齐缓存失效与测试验证
- ✅ 检索路径统一 — 同步与异步检索路径已完全统一，均通过 RetrievalStrategy 进行检索召回与 multi-KB 合并，移除了遗留的直接调用 chroma.as_retriever 分支
- ✅ Token 预算与历史摘要管理 — 已完成（基于 Token 预算的 context 自动裁剪、阶梯窗口及缓存式增量历史摘要）
- ✅ 表格/代码块完整保留 — 已完成结构保护切块：Markdown/Excel 表格按完整行切分并重复表头，fenced code block 按完整代码块或完整行切分
- ✅ 文档能力补齐：真正的语义切块已实现，普通文本默认按段落/句子做 embedding 语义边界切分，标题章节不跨段合并；Ollama embedding 超时、模型不可用或返回异常时自动降级到现有固定长度滑窗切分，不影响入库
- ✅ Chunk 统计接口：`/stats/chunks` 已提供总量、长度、token 估算、文件/类型/审核状态/知识库分布，以及线上 chunk 命中统计与离线评估命中率入口
- ✅ 对话式查询上下文化：`query_contextualizer.py` 已支持基于历史消息和上一轮来源的独立问题改写、多查询召回、来源锚点查询，LLM 失败时使用启发式降级
- ✅ Chunk 命中统计：`chunk_hit_telemetry.py` 已记录问答返回来源的 chunk 命中次数，为 `/stats/chunks` 提供线上命中分布
- ✅ 审核状态同步：`/review/status` 更新 Chroma metadata 后会重建 BM25 索引并清空查询缓存，避免 pending/approved/rejected 状态在关键词检索中滞后
- ✅ 知识图谱语义抽取 MVP — 已于 2026-07-09 完成；支持 Graph Audit、Stale Link Cleanup、Export Manual Facts、LLM Graph Extractor 基础版（提供置信度、证据和 Schema 校验），以及 pipeline 融合与正式库 properties_json 写入
- ✅ Task 8.1 切断 legacy 评分双读 — 运行时 `RetrievalIntentResolver.default()` 只读 `retrieval_intent_policies.json`；意图评分由 `GraphIntentFactProvider` + `score_signals()` 驱动；正式库 profile_sync batch 已 apply，专项 Gate **PASS**（2026-07-10）
- ✅ Task 8.2 Profile Migration 与 Graph Schema 兼容 — scoped Field（`管线点表.管点编号`）、alias / `different_from` / `has_field` 等经分拆审批写入正式 Graph；`scripts/validate_task81_graph_gate.py` 输出 PASS / NEEDS_APPLY / BLOCKED
- ✅ Docker 生产部署骨架 — 双容器（`rag-service` FastAPI + `rag-web` Nginx/dist）；生产 CPU 默认 `INSTALL_RERANKER=false`、不将模型打入镜像；`reranker.enabled=false` 三层门控（QueryPlanner / `_get_reranker` / postprocess 降级）；详见 [`deploy/README.md`](deploy/README.md)
- ✅ 图辅助改写 + 扩召回融合（代码已实现，**默认关闭**）— `graph_query_rewrite.py` 中量图摘要 → helper LLM 改写检索 query；与 `graph_retrieval` 扩召回 chunk 融合共用 `_prepare_graph_plan`；须同时 `enabled=true` 且 `query_rewrite_enabled=true` 才生效
- 审核工作台、图谱画布、分类过滤前端、反问 Prompt 暂缓；legacy migration 文件自动瘦身、管线面表 Phase B Section 治理待办；**第 4 轮 GraphRAG 检索 A/B 已 PASS，生产默认开图仍未批准**

### 核心功能
- **章节切片**：`.md`/`.docx`/`.txt` 使用 `unstructured` 按标题结构切片，保留 `section_title`；`.pdf`/`.doc` 回退到固定字数切片
- **结构保护切块**：Markdown/Excel 表格不切断行和单元格，代码块不切断 fence；超长结构块按完整行拆分。
- **语料清洗**：入库前自动移除 `HYPERLINK` / `PAGEREF` / `TOC` 等 Word 域代码，过滤纯目录块、纯链接块和极短噪声块
- **文档分类过滤**：上传时选择 `doc_category`（StampServer/StampTools/StampWebRTC/实景三维/耕地保护/矢量瓦片/基础环境/博客/其他），检索时可按分类筛选
- **审核状态**：每个 chunk 有 `review_status` 字段（pending/approved/rejected），检索默认只返回 approved
- **批量审核**：支持通过 `/review/status` 按 `file_path` 或 `chunk_id` 批量更新 `review_status`
- **关系数据库**：SQLite 三张表 —— `entities`（实体）、`relations`（关系边）、`entity_chunk_links`（实体-知识块关联）
- **知识库问答**：上传文档（PDF/DOC/DOCX/TXT/MD/XLS/XLSX）后，通过语义检索 + LLM 生成回答；Excel 转 Markdown 表格格式入库
- **对话式查询上下文化**：结合历史消息和上一轮来源，将“它/这个/继续说”等追问改写为独立查询，并生成多路检索 query 和来源锚点 query
- **Chunk 统计与命中率**：`/stats/chunks` 统计 chunk 数量、长度、token 估算、文件/类型/审核状态/知识库分布，并汇总线上 chunk 命中次数与离线评估命中率
- **流式输出**：SSE（Server-Sent Events）实时逐 token 显示回答
- **图片问答**：上传图片，调用视觉模型（qwen3-vl）描述/回答
- **知识库**：固定两个知识库——「已发布文章」和「文章附件」，检索时按 `kb_name` 元数据筛选
- **博客爬取**：支持 CSDN / 博客园 / 掘金 / 微信公众号，爬取后自动入库
- **博客发布同步**：定时从博客发布系统 API 拉取已发布文章，同步到知识库
- **定时扫描**：监视目录下的新/变更文件自动检测、向量化、入库
- **视频处理**：提取关键帧 → 视觉模型描述 → 向量化
- **检索意图策略（policy-only）**：运行时只读 `data/retrieval_intent_policies.json`（`query_hints`、`preferred_doc_categories` 等）；legacy 事实在 `data/migrations/retrieval_intent_profiles_v1.json`，仅供 `sync_profiles_to_graph.py` 迁移
- **Graph 驱动意图评分**：`graph_intent_scoring.py` 从正式 Graph 加载 approved alias、`different_from` sibling、scoped Field、`defined_in` section path，供 `retrieval_quality.py` 做 section boost / sibling penalty（**不依赖** `graph_retrieval.enabled`）
- **图扩召回 + 融合**：实体链接 → 沿关系扩展 → `entity_chunk_links` 取图侧 chunk → 与 Hybrid 结果加权融合（`graph_retrieval.py`）；本地 `config.ini` 已开，`config-prod.ini` 仍默认关
- **图辅助改写 query**：中量图摘要（实体/别名/`different_from`/一跳边类型/`defined_in` 路径）喂给 helper LLM，产出 `kind=graph_rewrite` 的检索 query 再并入 Hybrid；失败启发式降级；本地已开，生产模板仍默认关
- **Profile → Graph 同步**：`sync_profiles_to_graph.py` + `ProfileGraphSyncService` 生成 `profile_sync` 候选；正式库须分拆审批，禁止 `--approve-all`；生产 apply 需 `--confirm-db-path` / `--confirm-batch` / `--confirm-backup`

## 技术栈

| 层 | 技术 |
|---|---|
| **后端框架** | Python 3.11+, FastAPI, Uvicorn |
| **LLM 引擎** | Ollama（本地运行模型） |
| **RAG 框架** | LangChain（检索链）、LangChain-Ollama |
| **向量数据库** | ChromaDB |
| **文本分块** | unstructured 标题硬边界 + Ollama embedding 相邻语义边界；RecursiveCharacterTextSplitter 降级兜底 |
| **文档解析** | PyPDFLoader, Docx2txtLoader, TextLoader, unstructured (partition) |
| **图片/视频** | Pillow（压缩）、OpenCV（视频帧提取） |
| **前端** | Vue 3 + TypeScript + Vite |
| **HTTP 客户端** | httpx（后端调用 Ollama）、axios（前端调用后端） |
| **定时任务** | APScheduler |
| **爬虫** | BeautifulSoup4 + html2text |
| **评估** | ragas 0.4.3（端到端 RAG 评估）、pandas 3.0（数据分析） |
| **关键词检索** | rank-bm25 0.2.2（BM25Okapi）、jieba 0.42.0（中文分词） |

## 目录结构

```
rag_python/
├── run.py                          # 启动入口
├── run_graph_build.py              # 图谱 extract/review/apply/quality CLI
├── sync_profiles_to_graph.py       # Profile → Graph 候选同步 CLI
├── config.ini                      # 开发配置文件
├── config-prod.ini                 # 生产配置模板（部署时复制为宿主机 config.ini 挂载）
├── Dockerfile                      # 后端镜像（ARG INSTALL_RERANKER，默认 false）
├── docker-compose.yml              # 双服务编排（rag-service + rag-web）
├── requirements.txt                # 开发全量依赖（-r base + -r reranker）
├── requirements-base.txt           # 业务依赖（无 Reranker；禁止 unstructured[pdf]，避免另路拉 torch）
├── requirements-reranker.txt         # Reranker 可选依赖（torch/FlagEmbedding；INSTALL_RERANKER 门控）
├── requirements-cuda.txt             # 本地 GPU 开发覆盖（不纳入 Docker CPU 验收）
├── requirements-dev.txt              # 开发依赖（-r requirements.txt + pytest）
├── deploy/README.md                # Docker 生产部署说明
├── CLAUDE.md                       # 项目说明
│
├── rag_knowledge/                  # 后端主包
│   ├── __init__.py                 # 包信息
│   ├── __main__.py                 # 入口（日志 + 组件初始化 + 启动 FastAPI）
│   ├── config.py                   # 配置中心（环境变量 > INI > 默认值）
│   │
│   ├── api/                        # API 层
│   │   ├── routes.py               # 所有路由定义（/query, /upload, /scan, /crawl, /sync ...）
│   │   └── middleware.py           # 请求日志中间件
│   │
│   ├── models/                     # 数据模型
│   │   ├── api.py                  # Pydantic 请求/响应模型
│   │   └── document.py             # 文档数据模型（FileRecord, FileCategory）
│   │
│   ├── repository/                 # 数据持久层
│   │   ├── vector_store.py         # ChromaDB 封装（增删改查、切换模型、重建）
│   │   └── relational_db.py        # SQLite 关系数据库（实体/关系/实体-chunk 关联）
│   │
│   ├── evaluation/                 # 评估框架
│   │   ├── __init__.py              # 包信息
│   │   ├── metrics.py               # 检索指标（Recall@K, MRR, NDCG, Hit Rate）
│   │   ├── test_dataset.py          # 测试数据集构建（LLM 自动合成问题）
│   │   └── runner.py                # 评估运行器（支持 ablations 对比）
│   │
│   └── services/                   # 业务逻辑层
│       ├── rag.py                  # RAG 问答链（检索 + 组装 prompt + LLM 调用）
│       ├── scanner.py              # 目录扫描器（哈希去重 + 自动向量化 + 定时调度）
│       ├── loader.py               # 文件加载器（文本/图片/视频 → 分块）
│       ├── unstructured_loader.py  # 章节感知加载器（unstructured 按标题切片）
│       ├── bm25_store.py           # BM25 关键词索引（jieba 分词，单例，懒加载）
│       ├── retrieval_strategy.py   # 检索策略调度器（mmr/similarity/bm25/hybrid）
│       ├── retrieval_intent.py     # 检索意图 policy 解析（default 只读 policies）
│       ├── graph_intent_scoring.py # Graph 事实加载与意图评分信号（alias/sibling/field/section）
│       ├── query_contextualizer.py # 对话式查询上下文化、多查询生成、来源锚点查询
│       ├── chunk_stats.py          # Chunk 统计服务（长度/分布/命中率）
│       ├── chunk_hit_telemetry.py  # 线上问答来源 chunk 命中次数记录
│       ├── web_search.py           # 联网搜索（DuckDuckGo）
│       ├── agent_service.py        # 智能体预设加载
│       ├── chat_storage.py         # 聊天记录服务端持久化
│       ├── context_budget.py       # Context 自动裁剪（Token 预算控制）
│       ├── history_compressor.py   # 历史消息压缩与增量摘要（LRU 缓存）
│       ├── reranker.py             # 重排序器（Cross-Encoder，BGE/Qwen3）
│       ├── retrieval_quality.py    # 检索后处理质量控制（Phase 5：分数归一化、Jaccard去重、动态TopK）
│       ├── blog_crawler.py         # 多平台博客爬虫（CSDN/博客园/掘金/微信公众号）
│       ├── blog_syncer.py          # 博客发布系统同步（API → 本地文件）
│       ├── knowledge_base_consistency.py # 知识库一致性检测（file_index ↔ Chroma）
│       ├── rebuild_coordinator.py  # 受控重建协调器（单实例锁 + stale lock 检测 + 一致性断言）
│       ├── graph_text_migration.py # 图谱乱码文本迁移（mojibake → 中文修复）
│       ├── query_entity_guard.py   # 查询实体守卫（追问场景实体锚定与过滤）
│       ├── graph_audit.py          # 图谱审计服务（18个指标计算与报告生成）
│       ├── graph_cleanup.py        # Stale 关系/实体链接链接清理服务
│       ├── graph_manual_export.py  # 手工/种子事实导出（映射为 canonical 名字）
│       ├── profile_graph_sync.py   # legacy Profile → Graph 候选（profile_sync batch）
│       ├── graph_governance.py     # 生产写确认、approve-all 禁用、apply 审计
│       ├── task81_graph_gate.py    # Task 8.1 专项 Gate（PASS/NEEDS_APPLY/BLOCKED）
│       ├── graph_extraction/       # 知识图谱提取（Phase B 确定性规则管线）
│       │   ├── pipeline.py         # 提取管线（规则 + LLM 候选提取）
│       │   ├── llm_extractor.py    # LLM 语义抽取器（提供置信度、证据和 Schema 校验）
│       │   ├── prompts/            # LLM 抽取提示词模板
│       │   └── __init__.py
│       ├── graph_retrieval.py      # 图谱检索（实体扩展 + 文档融合 + 守卫过滤）
│       └── graph_query_rewrite.py  # 图辅助检索改写（中量摘要 → helper LLM；默认关）
│
├── web/                            # 前端（Vue 3 + TypeScript + Vite）
│   ├── Dockerfile                  # 多阶段构建（npm build → nginx:alpine）
│   ├── nginx.conf                  # 生产 Nginx（/api 代理、SSE、静态资源）
│   ├── src/
│   │   ├── main.ts                 # 入口
│   │   ├── App.vue                 # 根组件（导航切换：聊天/博客管理）
│   │   ├── api/index.ts            # API 调用层（axios + fetch SSE）
│   │   ├── types/index.ts          # TypeScript 类型定义
│   │   ├── utils/storage.ts        # 本地持久化（localStorage + IndexedDB）
│   │   ├── views/
│   │   │   ├── ChatView.vue        # 知识库问答页面
│   │   │   └── BlogView.vue        # 博客管理页面
│   │   └── components/
│   │       ├── ChatMessage.vue     # 聊天消息气泡
│   │       ├── ChatInput.vue       # 输入框（文字 + 图片粘贴/上传）
│   │       └── SourcePanel.vue     # 参考来源侧边栏
│   ├── vite.config.ts              # Vite 配置（/api 代理到后端）
│   └── package.json
│
├── chroma_db/                      # ChromaDB 向量数据持久化目录
├── watch_directory/                # 文件监视目录（放文档自动入库）
├── data/                           # 数据目录（file_index.json、policies、migrations 等）
├── scripts/                        # 运维/验收脚本（如 validate_task81_graph_gate.py）
├── logs/                           # 日志目录（自动轮转，保留 7 天）
└── uploads/                        # 上传临时目录
```

## 评估框架

**目录：`rag_knowledge/evaluation/`**（阶段一已完成）

### 测试数据集（test_dataset.py）
- `TestDatasetBuilder` 从 ChromaDB 的 approved chunks 中采样，用 LLM 自动生成问题
- 每条数据：`{question, relevant_chunk_ids, kb_name, ...}`
- 数据集保存在 `data/eval_dataset.json`
- 难例评测集可由 `HardCaseDatasetBuilder` 基于现有标注扩展，输出到自定义 JSON 文件
- 历史 Baseline 测试集：14 条；2026-07-01 向量库修复后基于新 chunk ID 重建为 36 条，难例集 144 条

### 检索指标（metrics.py）
- `recall_at_k()` / `precision_at_k()` — 前 K 个结果中的召回率/精确率
- `mrr()` — 第一个相关文档排名的倒数均值
- `hit_rate()` — 是否至少命中一个
- `ndcg_at_k()` — 归一化折损累计增益
- `compute_batch()` — 批量计算平均指标

### 评估运行器（runner.py）
- `EvaluationRunner.run_retrieval_eval()` — 仅评估检索环节
- `EvaluationRunner.run_end_to_end_eval()` — 检索 + 生成全链路
- `EvaluationRunner.run_ablation()` — 多策略对比
- 便捷入口：`build_and_eval()` 一键构建数据集 + 评估

### Baseline 指标（14 条测试数据, k=4）

| 指标 | MMR | Similarity | BM25 | Hybrid |
|---|---|---|---|---|
| Recall@3 | 85.71% | 92.86% | 92.86% | **92.86%** |
| MRR | 0.7857 | 0.8214 | 0.8452 | **0.8810** |
| NDCG@3 | 0.8044 | 0.8495 | 0.8665 | **0.8929** |
| Hit Rate | 85.71% | 92.86% | 92.86% | **92.86%** |
| 均延迟 | 204ms | 191ms | **51ms** | 218ms |

> 阶段三结论：Hybrid（Similarity + BM25，RRF）保持 92.86% Recall@3，并将 MRR 提升至 0.8810、NDCG@3 提升至 0.8929；默认策略暂保留 MMR，待评估集扩充后再决定是否切换。

### 向量库修复后指标（2026-07-01，新生成 36 题）

| 指标 | MMR | Similarity | BM25 | Hybrid |
|---|---:|---:|---:|---:|
| Recall@3 | 61.11% | 72.22% | 80.56% | **83.33%** |
| MRR | 0.5046 | 0.5602 | 0.5579 | **0.6736** |
| NDCG@3 | 0.5321 | 0.5913 | 0.6167 | **0.7097** |
| Hit Rate | 61.11% | 77.78% | 83.33% | **86.11%** |
| 平均延迟 | 215.0ms | 224.3ms | **24.2ms** | 218.4ms |

> 新题集来自 607 个 approved chunks 的重新采样，与历史 14 题不是同一分布，不能把指标下降直接解释为检索退化。四种策略均已恢复正常；Hybrid 在新题集上仍是综合指标最高。

### Rerank A/B 指标（2026-07-02）

| 数据集 | 策略 | Recall@3 | Recall@5 | MRR | 结论 |
|---|---|---:|---:|---:|---|
| 标准集（36 题） | Hybrid | 83.33% | 86.11% | 0.6736 | 对照基线 |
| 标准集（36 题） | Hybrid+Rerank | **88.89%** | **94.44%** | **0.7731** | 当前最优 |
| 标准集（36 题） | Hybrid+Rerank+Quality | 88.89% | 91.67% | 0.7662 | 质量控制略降 |
| 难例集（144 题） | Hybrid | 84.72% | 88.19% | 0.6858 | 对照基线 |
| 难例集（144 题） | Hybrid+Rerank | **90.97%** | **95.83%** | **0.7888** | 当前最优 |
| 难例集（144 题） | Hybrid+Rerank+Quality | 90.28% | 93.06% | 0.7812 | 质量控制略降 |

> 结论：当前推荐的高质量检索方案是 `Hybrid+Rerank`。检索质量控制策略已经实现，但不建议作为默认叠加项。生产 CPU 默认关闭 Reranker（`Hybrid` + Graph）；本地开发或未来 GPU 环境可启用并挂载模型。

## 后端架构详解

### 配置系统（config.py）
- `Config` 单例类，集中管理所有配置
- 优先级：**环境变量 > config.ini > 代码默认值**
- 环境变量命名规则：`{SECTION}_{KEY}` 全大写（如 `OLLAMA_BASE_URL`）

### 核心流程

#### 文档入库流程
```
watch_directory/ 文件变化
  → scanner.scan() 递归遍历
    → 计算 SHA-256 哈希
      → 哈希在索引中已存在？→ 跳过
      → 新文件？→ loader.load()
        → 文本（.md/.docx/.txt）→ unstructured 章节切片（优先）
          失败回退 → PyPDFLoader/Docx2txtLoader/TextLoader + 语义切块（embedding 异常时固定长度降级）
        → 文本（.pdf/.doc）→ PyPDFLoader/旧版解析 → 语义切块（embedding 异常时固定长度降级）
        → 图片（jpg/png/...）→ 视觉模型描述 → 分块
        → 视频（mp4/avi/...）→ OpenCV 提取关键帧 → 视觉模型描述 → 合并 → 分块
      → store.add_chunks() → ChromaDB 向量化存储
      → 更新 file_index.json
```

#### 问答流程
```
用户提问
  → 闲聊检测（问候/感谢/自我介绍等）→ 直接 LLM 回答，跳过检索
  → 敏感内容检测 → 拒绝回答
  → 知识问答 →
    → Query 上下文化（历史对话改写；不读图谱关系；LLM 失败时启发式降级）
    → 多查询构建（原问题、上下文化、来源锚点等）
    → QueryPlanner（意图分类 + 参数：top_k / rerank / 邻居扩展）
    → [_prepare_graph_plan] 仅当 [graph_retrieval] enabled=true：
        → 实体链接 + 关系扩展 + 加载图侧 chunk（entity_chunk_links）
        → 若 query_rewrite_enabled=true：中量图摘要 → helper LLM 图辅助改写
          → 产出 kind=graph_rewrite 的 query 并入 plan.queries（失败启发式降级）
        → 否则跳过图路径（当前本地/生产默认）
    → RetrievalStrategy 多 query Hybrid（Similarity + BM25 / RRF）等
      → 检索默认过滤 review_status='approved'，可选 doc_category 过滤
    → 若图侧有 chunk：与文本召回加权融合（fuse）；否则仅文本路
    → [可选] Reranker 精排（须 [reranker] enabled=true）
    → 检索后处理：结构化加权 + Graph Intent 评分（alias / different_from 加减分；
      不依赖 graph_retrieval.enabled）+ 可选 Quality 过滤
    → [可选] 上下文压缩 / 联网搜索
    → 组装 prompt（context=chunk 原文；若图开且链到实体可附加「实体消歧提示」，
      明确不作为事实来源）
    → 回答治理（无引用降级等）→ Ollama 生成（同步/SSE）
    → 记录 chunk 命中 → 返回回答 + 被引用的来源
```

#### 当前运行时架构（RAG × 图谱）

见下一节「知识图谱架构（代码支持）」——下列模式均已在代码中实现，由配置开关组合启用。

### 知识图谱架构（代码支持）

代码把图谱拆成 **存储 / 建设 / 运行时读路径 / 管理面** 四块。正式库：`data/rag_relational.db`（`RelationalDB`）。

#### A. 存储模型（代码读写的表）

| 表 | 用途 |
|---|---|
| `entities` | 实体节点 |
| `relations` | 关系边（可带 `source_chunk_id`） |
| `aliases` | 别名 |
| `entity_chunk_links` | 实体↔chunk 链接（图扩召回取证据块） |
| `extraction_candidates` | 抽取/同步候选 staging |

另：产品主干预览走 JSON（`product_relation_backbone*.json`），不经过向量库；入库后 `created_by=seed:product_backbone`。

#### B. 建设链路（写图）——代码已支持

```
规则抽取 (graph_extraction/) ± LLM 抽取
  或 Profile sync / 产品主干 sync
  → extraction_candidates
  → CLI/API review（分拆审批）→ apply
  → 正式表 + audit/cleanup/quality/Gate
```

主要入口：`run_graph_build.py`、`sync_profiles_to_graph.py`、产品主干 sync、`graph_governance.py`、`/admin/graph-candidates/*`、前端 `GraphCandidatesView.vue`。

#### C. 运行时读路径——代码已支持的四种介入方式

问答里图谱可按下列 **模式** 介入（可叠加；由开关决定）：

```
Query
  →（历史上下文化，与图无关）
  → QueryPlanner
  → [_prepare_graph_plan] 若 graph_retrieval.enabled
        EntityLinker → GraphExpander
          ├─ 模式3：GraphQueryRewriter（若 query_rewrite_enabled）→ 额外 Hybrid queries
          └─ 模式2：按 entity_chunk_links 加载图侧 Document → 稍后 fuse
  → Hybrid 文本检索
  → 模式2：GraphFusionScorer.fuse（文本路 ∩ 图侧）
  → 模式1：GraphIntentFactProvider 对已召回 chunk 加减分
  → 模式4：_build_messages 附加实体消歧提示（有 linked_entities 时）
  → 回答 LLM（context 仍是 chunk 原文；关系不直接当事实写入答案）
```

| 模式 | 代码位置 | 做什么 | 配置 |
|---|---|---|---|
| **1. Intent 评分** | `graph_intent_scoring.py` ← `retrieval_quality.py` | 用 alias / `different_from` / field / section 给 chunk **排序加减分** | 不依赖 `graph_retrieval.enabled`；随检索后处理调用 |
| **2. 图扩召回 + 融合** | `graph_retrieval.py`（`EntityLinker` / `GraphExpander` / `fuse`） | 问题链实体 → 扩关系 → **`entity_chunk_links` 取 chunk** → 与 Hybrid 结果加权融合 | `[graph_retrieval] enabled`（本地 `config.ini`=true；`config-prod.ini`=false） |
| **3. 图辅助改写 query** | `graph_query_rewrite.py` ← `_prepare_graph_plan` | 中量图摘要（名/别名/兄弟/一跳边类型/`defined_in` 路径，无 evidence 正文）→ helper LLM 产出 `kind=graph_rewrite` 的检索 query 并入 Hybrid | `enabled=true` **且** `query_rewrite_enabled=true`（本地已开；生产模板仍关） |
| **4. 回答侧实体提示** | `rag.py` `_build_messages` | system prompt 加消歧提示（别名、`different_from`）；**声明非事实来源** | 需模式 2 开着且链接到实体 |

环境变量：`GRAPH_RETRIEVAL_ENABLED`、`GRAPH_RETRIEVAL_QUERY_REWRITE_ENABLED`。

**代码明确支持、但未做成「关系当答案」的路径**：没有把 relation 文本/`evidence_text` 作为独立事实通道写入回答契约；回答依据仍是 chunk context + 引用治理。

#### D. 管理面（代码已支持）

| 能力 | 入口 |
|---|---|
| 正式图 CRUD / 别名 / 实体-chunk 链接 | `/admin/knowledge_graph/*` |
| 主干预览编辑（不写正式库） | `/admin/knowledge_graph/product_backbone_preview*` |
| 候选批次 review/apply/quality | `/admin/graph-candidates/*` |
| 图谱可视化 | `KnowledgeGraphView.vue` |
| 候选审批 UI | `GraphCandidatesView.vue` |

#### E. 证据字段在代码里的分工

| 字段 | 谁用 |
|---|---|
| `entity_chunk_links.chunk_id` | 模式 2 加载图侧 Document |
| `relations.source_chunk_id` | 审计/治理/血缘；**当前 GraphExpander 取 chunk 不靠它** |
| `aliases` | 模式 1 评分、模式 2 链接、模式 3 摘要、模式 4 提示 |

### API 路由总览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/models` | Ollama 模型列表 + 当前配置 |
| GET | `/knowledge-bases` | 知识库列表（已发布文章 / 文章附件） |
| POST | `/query` | 知识库问答（非流式） |
| POST | `/query/stream` | 知识库问答（流式 SSE） |
| POST | `/query/image` | 图片问答（流式 SSE） |
| POST | `/upload` | 上传文档 |
| POST | `/review/status` | 按 file_path 或 chunk_id 批量更新审核状态，并同步重建 BM25 |
| POST | `/scan` | 手动触发扫描 |
| GET | `/stats` | 知识库基础统计 |
| GET | `/stats/chunks` | Chunk 数量、长度、token 估算、分布和命中率统计 |
| GET | `/scan/index` | 文件索引详情 |
| POST | `/config/embedding-model` | 切换向量模型 |
| GET/POST | `/rebuild` | 重建知识库（清空 + 全量重扫） |
| POST | `/crawl` | 爬取博客文章（自动识别平台） |
| GET | `/blog/posts` | 博客文章列表（分页/搜索/筛选） |
| GET | `/blog/posts/{filename}` | 博客文章详情 |
| POST | `/blog/publish/{filename}` | 发布文章到博客系统 |
| DELETE | `/blog/posts/{filename}` | 删除博客文章 |
| GET | `/agents` | 智能体预设列表 |
| GET | `/chat/history` | 获取聊天记录 |
| PUT | `/chat/history` | 保存聊天记录 |
| DELETE | `/chat/history` | 删除聊天记录 |
| POST | `/blog/sync` | 同步博客发布系统已发布文章 |

### 知识库概念
- 固定两个知识库：**「已发布文章」** 和 **「文章附件」**
- watch_directory/已发布文章/ 下的文件 → kb_name = "已发布文章"
- 其余所有文件 → kb_name = "文章附件"
- 检索时通过 filter={kb_name: xxx} 筛选
- MVP 新增 metadata 字段：`doc_category`（产品/业务域分类：StampServer/StampTools/StampWebRTC/实景三维/耕地保护/矢量瓦片/基础环境/博客/其他）、`section_title`（章节标题）、`review_status`（审核状态，默认 pending，检索只返回 approved）、`geo_wkt`（空间预留字段，始终为 None）
- **数据迁移**：现有 375 个 chunk 已通过一次性迁移将 `review_status` 设为 `"approved"`。新入库的 chunk 默认仍为 `"pending"`，需通过审核工作台或 API 手动批准

### 关系数据库（SQLite）
- 文件：`data/rag_relational.db`（由 `[relational_db] db_path` 配置，启动时自动创建）
- 三张表：`entities`（实体）、`relations`（关系边）、`entity_chunk_links`（实体-知识块关联）
- 代码：`rag_knowledge/repository/relational_db.py`（单例，完整的 CRUD 方法）

### SVN
- 仓库：`https://192.168.10.251:8443/svn/公司内部开发系统/博客_论坛/rag_python`
- 当前在主干 @ r85（已从 branches/mvp-metadata 合并）
- svn.exe：`C:\Users\Administrator\AppData\Local\Temp\svn-cli\bin\svn.exe`
- 使用：先 cd 到项目目录，用 `.` 作为路径参数避免中文编码问题

### 博客爬虫（blog_crawler.py）
- 自动识别平台：CSDN / 博客园 / 掘金 / 微信公众号
- 统一的 `BaseCrawler` 抽象基类
- 爬取结果保存为 Markdown 文件 + YAML front-matter
- 爬取后自动触发扫描入库

### 博客发布同步（blog_syncer.py）
- 定时从 `api_url` 拉取已发布文章列表
- 增量同步：新增/更新/删除
- 更新/删除时清理对应的向量数据
- 同步后自动触发扫描

## 前端架构

### 组件树
```
App.vue (导航栏: 知识库问答 | 博客管理)
├── ChatView.vue (问答页面)
│   ├── ChatMessage.vue (消息气泡 + Markdown 渲染)
│   ├── ChatInput.vue (输入框 + 图片粘贴/上传)
│   └── SourcePanel.vue (参考来源侧边栏)
└── BlogView.vue (博客管理页面)
    ├── 文章列表 + 搜索/筛选/分页
    ├── 爬取输入框
    ├── 文章预览弹窗 (Markdown 渲染)
    └── 删除确认弹窗
```

### API 层设计（web/src/api/index.ts）
- 基于 axios（自动解包 data + 统一错误格式化）
- 流式接口（`/query/stream`, `/query/image`）使用原生 fetch + ReadableStream
- 生产环境 `baseURL: '/api'`，由 Nginx 反向代理到后端 `10605`（路径前缀 `/api` 在代理时剥离）

### 生产前端托管
- 开发：`vite` dev server + proxy（`web/vite.config.ts`）
- 生产：`npm run build` → `web/dist/` → `rag-web` 容器内 Nginx 静态托管
- SPA 路由：`try_files $uri $uri/ /index.html`
- `/scraping/`、`/articleImg/` 由 Nginx 单独配置（见 `web/nginx.conf`）

### 持久化策略（web/src/utils/storage.ts）
- **localStorage**：消息文本、角色、来源文档（轻量数据）
- **IndexedDB**：用户消息中的图片 base64（大体积数据）
- 每次 AI 回复完成后自动全量保存，最多保留 30 条消息

## 开发和运行

### 本地开发

**后端：**
```powershell
# 唯一标准本地环境：项目根目录下的 venv（不要使用裸 python 或 .venv）
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 修改 config.ini（Ollama 地址等）

# 启动
.\venv\Scripts\python.exe run.py

# 生成新评测集并运行四种检索策略
.\venv\Scripts\python.exe run_eval_full.py
```

> **Chroma 环境强约束**：本项目锁定 `chromadb==0.6.3` 与 `langchain-chroma==0.2.3`。不得用系统 Python、`.venv` 或其他 Chroma 版本打开同一个 `chroma_db`；不得在后端运行时用另一个进程直接操作该目录。VectorStore 初始化时会校验这两个版本，版本不符会拒绝打开数据库。Docker 不要求虚拟环境，但仍必须安装上述锁定版本。

**前端：**
```bash
cd web
npm install
npm run dev  # 默认代理 /api → http://127.0.0.1:10605
```

### Docker 部署（生产推荐）

架构：`rag-web`（Nginx + Vue dist，对外 `:80`）→ `rag-service`（FastAPI，内部 `10605`）。

```bash
# 首次部署前：
# 1. 将 config-prod.ini 复制到 /data/rag_python/config.ini（含 reranker.enabled=false）
# 2. 初始化 /data/rag_python/data/（含 retrieval_intent_policies.json、agents.json、rag_relational.db 等）
# 3. Ollama base_url 使用容器可访问 IP，禁止 localhost

docker compose build
docker compose up -d
```

要点：
- 后端镜像 `INSTALL_RERANKER=false`（默认），不安装 torch/FlagEmbedding，模型目录不进入镜像
- 配置只通过 volume 挂载 `/data/rag_python/config.ini:/app/config.ini:ro`；`config-prod.ini` 不进镜像
- `/data/rag_python/data` volume **完全覆盖**容器内 `data/`，不得用空目录覆盖正式数据
- `rag-service` 带 healthcheck；`rag-web` 在 `service_healthy` 后启动
- SSE：`web/nginx.conf` 须 `proxy_buffering off`

完整步骤与验收清单见 [`deploy/README.md`](deploy/README.md)。

## 常见操作

- **添加文档**：放入 watch_directory/已发布文章/ 或 watch_directory/upload/，等待定时扫描，或手动调用 POST /scan
- **重建知识库**：替换向量模型后，调用 `POST /rebuild`（须 `confirmation=REBUILD_KNOWLEDGE_BASE`）
- **切换模型**：前端下拉框选择，嵌入模型切换后需重建知识库
- **清空对话**：前端垃圾桶按钮，只清除前端缓存，不影响向量库
- **查看日志**：`logs/rag.log`（全部）、`logs/rag_error.log`（WARNING+，保留 30 天）
- **评估命令**：始终使用 `.\venv\Scripts\python.exe run_eval_full.py`，不要使用 `python run_eval_full.py`
- **重建后评估**：重建会生成全新的 chunk ID；旧的 `eval_dataset.json` 和难例集随即失效，必须基于新向量库重新生成
- **数据库维护**：执行离线重建、迁移或诊断前先停止后端和其他评估进程，确认没有进程占用 `chroma_db`
- **受控重建**：`RebuildCoordinator.run()` 提供完整的带锁重建流程（备份 → clear → reset → scan → 一致性断言 → BM25 重建），替代手动逐步操作。也可通过 `POST /rebuild` 触发。重建锁文件 `data/rebuild.lock` 记录 PID，异常退出后下次重建自动检测并清理 stale lock
- **图谱重建**：知识库一致性通过后，按顺序执行：`run_graph_build.py extract --force-rebuild` → `review --batch <id> --approve-all` → `apply --batch <id>` → `quality --graph`。图谱提取前会调用 `assert_consistent()`，不一致时拒绝执行
- **Profile → Graph 同步（Task 8.1）**：`sync_profiles_to_graph.py --dry-run` 预览 → `--apply --review-status pending` 写 staging → `run_graph_build.py review` **分拆审批**（`--approve-kind alias` / `--approve-type` / `--approve-relation-type`，禁止 `profile_sync` 使用 `--approve-all`）→ `apply --batch <id>` 须带 `--confirm-db-path` / `--confirm-batch` / `--confirm-backup`。验收：`$env:PYTHONPATH=(Get-Location).Path; .\venv\Scripts\python.exe scripts\validate_task81_graph_gate.py --json`（目标 PASS）。报告见 `docs/3_待办清单/task81-production-validation/`
- **图谱乱码修复**：`run_graph_build.py repair-text` 修复关系图谱中的 mojibake 中文标签
- **测试隔离**：`pytest` 默认排除 `@pytest.mark.integration` 测试（`addopts = -m "not integration"`）。`isolated_storage` fixture 将全部 8 个运行时路径指向 `tmp_path`。`Config._assert_test_paths_are_isolated()` 在 pytest 下检测到正式路径时直接抛错，除非设置 `ALLOW_LIVE_STORAGE_IN_TESTS=1`。需接触正式库的集成测试显式运行 `pytest -m integration`
- **交付门禁检查**：`.\venv\Scripts\python.exe scripts/check_repo_hygiene.py` — 只读检查工作树清洁度、禁止 `NUL`/`*.tmp`/`_debug` 垃圾、`data/domain_catalog.json` 已跟踪；交付提交前须 exit 0
- **Docker 生产部署**：`docker compose build && docker compose up -d`；详见 [`deploy/README.md`](deploy/README.md)。生产默认 `RERANKER_ENABLED=false`；启用 Reranker 需 `INSTALL_RERANKER=true` 重建镜像并 volume 挂载模型目录

### 2026-07-01 Chroma 环境混用事故

- 事故现象：BM25 正常，但 MMR、Similarity、Hybrid 分别出现 `dict has no attribute dimensionality`、`Error loading hnsw index` 和 compactor 错误。
- 根因：后端使用项目 `venv`（Chroma 0.6.3），评估曾使用系统 Python（Chroma 1.5.9），两套不兼容的持久化格式先后写入同一个 `chroma_db`。
- 纠正：系统 Python 中只卸载 `chromadb`、`langchain-chroma` 两个错误顶层包；项目统一使用 `venv`，并加入启动前版本校验。Windows 下向原生 HNSW 层传递 ASCII 相对路径 `chroma_db`，规避中文绝对路径无法生成二进制索引文件的问题；集合使用 `hnsw:batch_size=50`、`hnsw:sync_threshold=100`，确保当前约 600 chunks 的小型库及时持久化。
- 数据影响：污染库已归档并从 `watch_directory` 全量重建为 607 个 approved chunks；标准评测集 36 题、难例集 144 题，全部标注 ID 均存在于新库，四策略实测指标见“评估框架”章节。

## 关键设计决策

- **单例模式**：Config、VectorStore、BM25Store 均为单例，避免重复初始化
- **检索策略调度**：`RetrievalStrategy` 按 `[retrieval_strategy] method` 配置分发 mmr/similarity/bm25，默认 mmr 向后兼容；`_retrieve()` 的 `method` 参数可覆盖配置（评估用）
- **BM25 关键词检索**：`rank-bm25` + `jieba` 中文分词，全量 ChromaDB 文档懒加载构建 BM25Okapi 索引，入库后 routes.py 自动 rebuild
- **MMR 检索**：使用最大边际相关性（MMR），在相关性和多样性之间平衡
- **哈希去重**：SHA-256 文件哈希确保同一文件不重复入库，移动/重命名可自动追踪
- **Query 上下文化与多查询召回**：检索前用 LLM 或启发式规则补全省略/指代；对依赖历史的问题生成独立问题、历史关键词补强 query 和上一轮来源锚点 query，提升追问场景命中率
- **Chunk 命中统计**：问答返回来源后将 chunk_id/source/page 等写入轻量 JSON telemetry，`/stats/chunks` 汇总线上命中分布；离线 Hit Rate 仍来自 evaluation 结果文件
- **闲聊分流**：正则匹配问候/感谢等，直接 LLM 回答，跳过检索，节省资源
- **联网搜索**：仅在请求显式开启时将 DuckDuckGo 结果作为“外部来源”加入 context，保留标题、URL 和片段并独立编号引用
- **Agent 预设**：可追加角色与输出风格要求，但不能覆盖基础 RAG 的禁止编造、拒答和引用规则
- **SSE 流式**：前后端均使用 SSE（Server-Sent Events），逐 token 展示
- **分块策略**：标题章节作为硬边界；章节内普通文本按段落/句子生成 embedding，使用相邻余弦距离第 80 分位识别语义边界，并受最小 200 字符及 `chunk_size` 最大长度约束。语义路径不添加 overlap；embedding 超时、模型不可用或响应异常时，当前普通文本块整体降级到 RecursiveCharacterTextSplitter，并保留 `chunk_overlap`
- **元数据**：每个 chunk 携带 section_title / section_path / section_index / chunk_in_section / review_status / doc_category / geo_wkt
- **评估体系**：`rag_knowledge/evaluation/` 提供测试集构建（LLM 合成）+ 检索指标计算（Recall@K/MRR/Hit）+ 多策略 ablations 对比
- **数据迁移**：`review_status` 字段为后期添加，现有 chunk 通过一次性脚本设为 `"approved"`；新 chunk 默认为 `"pending"`
- **知识库一致性检测**：`KnowledgeBaseConsistencyService.audit()` 交叉对比 `file_index.json` 与 Chroma collection 的 chunk ID，输出一致的 `summary` 和 `files` 报告；`assert_consistent()` 不一致时抛出 `KnowledgeBaseConsistencyError`
- **受控重建**：`RebuildCoordinator` 使用 `os.O_CREAT | os.O_EXCL` 文件锁防止并发重建，Windows 下通过 `OpenProcess` 检测 stale PID 自动清理遗留锁。流程：备份 → 写入 running 状态 → clear/reset/scan → 一致性断言 → BM25 重建 → 清理状态文件
- **图谱确定性提取**：Phase B 使用规则管线（`SectionPathExtractor` → `TableFieldExtractor` → `ConfigBlockExtractor`），候选按 `[kind, identity_payload]` SHA-256 指纹去重，通过 review/apply 两阶段审批写入关系数据库。`GraphBuilder.build_full()` 启动前调用 `KnowledgeBaseConsistencyService.assert_consistent()`
- **Graph 运行时事实源（Task 8.1）**：alias、`different_from`、`has_field`、`defined_in` 等领域事实以正式 Graph approved 记录为准；`RetrievalIntentResolver.default()` 不读 legacy migration；评分经 `GraphIntentFactProvider.load_one()` / `score_signals()`
- **图检索双开关**：`[graph_retrieval] enabled` 控制扩召回融合；`query_rewrite_enabled` 仅在 enabled=true 时生效，控制图辅助改写。环境变量 `GRAPH_RETRIEVAL_ENABLED` / `GRAPH_RETRIEVAL_QUERY_REWRITE_ENABLED`。本地 `config.ini` 当前均为 true；`config-prod.ini` 仍为 false
- **图辅助改写输入（中量）**：只喂 canonical、别名、`different_from`、一跳关系类型、`defined_in` 路径词；不喂 `evidence_text` / chunk 正文；改写失败不阻断主问答
- **图扩召回证据**：图侧 chunk 来自 `entity_chunk_links`（实体→chunk），不是「向量命中块的父章节兄弟块」；关系级 `source_chunk_id` 用于血缘/准入，与实体链接是两层
- **Field 限定名（Task 8.2）**：canonical Field 为 `{DataTable}.{leaf}`（如 `管线面表.管面编号`）；禁止裸 Field/Section；profile sync 不得创建裸 `PipelineBuilder > …` Section
- **Profile sync 生产写门禁**：`graph_governance.assert_write_confirmation()` 要求显式确认 DB 路径、batch id、备份文件；`profile_sync` / `domain_catalog_seed` 等 mode 禁止 `--approve-all`
- **Task 8.1 专项 Gate**：`Task81GraphGateValidator` 校验四 profile 运行时事实 + migration preview；`global_graph_quality` 中历史 104 条 `missing_evidence` 不改变专项判定
- **Reranker 全局门控**：`[reranker] enabled=false`（或 `RERANKER_ENABLED=false`）时三层防御——(1) `QueryPlanner` / `_plan_retrieval` fallback 不产出 `enable_rerank=True`；(2) `_get_reranker()` 返回 `None`；(3) `_postprocess_docs` / `_postprocess_docs_sync` 对 `None` 或加载失败截断 `top_n`。`force_rerank=True` 不能绕过全局开关
- **Docker 依赖拆分（两条 torch 口子）**：路径 A = `INSTALL_RERANKER` + `requirements-reranker.txt`（Reranker）；路径 B = base 里若写 `unstructured[pdf]` 会经 inference 再拉 torch（与 Reranker 门控无关）。现 base 为 `unstructured==0.18.32` 且禁止 `[pdf]`。`requirements.txt` 聚合两者供本地开发；`requirements-cuda.txt` 仅本地 GPU，不纳入 CPU 生产镜像验收。详见 [`deploy/README.md`](deploy/README.md) §5
- **测试防呆体系**：`isolated_storage` fixture 隔离 8 个运行时路径 → `Config._assert_test_paths_are_isolated()` 作为运行时熔断器 → `pytest.ini addopts = -m "not integration"` 默认排除真实库测试。三层保护确保测试不可能静默写入正式数据

## Prompt 与回答规范

- 知识库事实必须由检索 context 明确支持，并在正文使用 `[1]`、`[2]` 等编号引用；禁止推测、补全隐含逻辑或伪造来源。
- context 无法明确回答时必须先原样输出“当前知识库中未查询到相关内容。”；仅有部分依据时，只回答有依据的部分并明确指出缺失项。
- `[answer] allow_general_knowledge = true` 默认允许在未命中提示后增加独立的“通用知识补充”；设为 `false` 或使用环境变量 `ANSWER_ALLOW_GENERAL_KNOWLEDGE=false` 时完全禁用补充。请求字段 `allow_general_knowledge` 可逐次覆盖配置。
- 历史消息只用于理解追问和指代，不作为事实来源。智能体 Prompt 只作为附加角色要求，不能替换基础系统规则。
- 每个来源由后端确定性生成引用编号、文件名、页码和原文片段；没有真实页码的 TXT、Markdown、图片、视频等明确显示“无页码”，不生成虚假页码。
- 联网搜索必须由请求显式开启，结果标记为“外部来源”，与知识库来源分开显示。非流式与 SSE 流式问答共用同一套 Prompt 和来源结构。
