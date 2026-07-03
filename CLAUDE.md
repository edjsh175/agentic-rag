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

### 当前阶段：检索能力优化 + 交付前查漏补缺

正在基于 [rag知识库任务.md](docs/试岗/rag知识库任务.md) 进行「四、检索能力优化」。当前同步口径截至 2026-07-03：检索优化主线已基本完成，交付前查漏补缺正在收口；已新增 Chunk 统计接口、chunk 命中统计、对话式查询上下文化/多查询召回、审核状态更新后的 BM25 同步。剩余重点是 Git/SVN 交付清理、Excel 复杂真实文件测试、同步/异步检索路径完全统一和交付文档口径一致。
- ✅ 阶段一：评估框架 — 已完成，Baseline 指标已测得（Recall@3=85.7%, MRR=0.79）
- ✅ 阶段二：BM25 关键词检索 — 已完成（Recall@3=92.9%, MRR=0.85，+7pp）
- ✅ 阶段三：混合检索（Hybrid Search）— 已完成（Recall@3=92.9%, MRR=0.88）
- ✅ 阶段四：语料治理 + 新资料规范入库 — 已完成（文本清洗、难例评测集生成、批量审核入口）
- ✅ 阶段五：Cross-Encoder Reranker — 代码、降级流程和本地 A/B 验证已完成；当前最佳策略为 `Hybrid+Rerank`，生产默认是否启用取决于部署机模型与 CUDA 条件
- ✅ 阶段五附加：检索质量控制 — 已实现分数归一化、Jaccard 去重、动态 TopK；但 `Hybrid+Rerank+Quality` 指标略低于 `Hybrid+Rerank`，暂不建议默认开启
- ✅ 阶段六：性能优化 — Embedding 缓存、查询缓存、Hybrid 两路并发召回、异步检索与并发检索已落地，并已补齐缓存失效与测试验证
- ✅ Token 预算与历史摘要管理 — 已完成（基于 Token 预算的 context 自动裁剪、阶梯窗口及缓存式增量历史摘要）
- ✅ 表格/代码块完整保留 — 已完成结构保护切块：Markdown/Excel 表格按完整行切分并重复表头，fenced code block 按完整代码块或完整行切分
- ✅ 文档能力补齐：真正的语义切块已实现，普通文本默认按段落/句子做 embedding 语义边界切分，标题章节不跨段合并；Ollama embedding 超时、模型不可用或返回异常时自动降级到现有固定长度滑窗切分，不影响入库
- ✅ Chunk 统计接口：`/stats/chunks` 已提供总量、长度、token 估算、文件/类型/审核状态/知识库分布，以及线上 chunk 命中统计与离线评估命中率入口
- ✅ 对话式查询上下文化：`query_contextualizer.py` 已支持基于历史消息和上一轮来源的独立问题改写、多查询召回、来源锚点查询，LLM 失败时使用启发式降级
- ✅ Chunk 命中统计：`chunk_hit_telemetry.py` 已记录问答返回来源的 chunk 命中次数，为 `/stats/chunks` 提供线上命中分布
- ✅ 审核状态同步：`/review/status` 更新 Chroma metadata 后会重建 BM25 索引并清空查询缓存，避免 pending/approved/rejected 状态在关键词检索中滞后
- 审核工作台、图谱画布、分类过滤前端、反问 Prompt 暂缓

### 核心功能
- **章节切片**：`.md`/`.docx`/`.txt` 使用 `unstructured` 按标题结构切片，保留 `section_title`；`.pdf`/`.doc` 回退到固定字数切片
- **结构保护切块**：Markdown/Excel 表格不切断行和单元格，代码块不切断 fence；超长结构块按完整行拆分。
- **语料清洗**：入库前自动移除 `HYPERLINK` / `PAGEREF` / `TOC` 等 Word 域代码，过滤纯目录块、纯链接块和极短噪声块
- **文档分类过滤**：上传时选择 `doc_category`（运维管理/前端开发/后端开发/二次开发/开源生态/其他），检索时可按分类筛选
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
├── config.ini                      # 开发配置文件
├── config-prod.ini                 # 生产配置文件
├── Dockerfile                      # Docker 部署
├── requirements.txt                # Python 依赖
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
│       └── blog_syncer.py          # 博客发布系统同步（API → 本地文件）
│
├── web/                            # 前端（Vue 3 + TypeScript + Vite）
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
├── data/                           # 数据目录（file_index.json 等）
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

> 结论：当前推荐的高质量检索方案是 `Hybrid+Rerank`。检索质量控制策略已经实现，但不建议作为默认叠加项。生产默认策略仍需结合部署机模型文件、显存/CUDA 条件和延迟要求决定。

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
    → Query 上下文化（结合历史消息/上一轮来源，将追问改写成独立问题；LLM 不可用时启发式降级）
    → 多查询构建（原问题、上下文化问题、历史关键词补强、上一轮来源锚点 query）
    → RetrievalStrategy 调度检索（按 config.ini [retrieval_strategy] method 选择）
      → mmr: ChromaDB MMR 检索（top_k=4, fetch_k=12, lambda_mult=0.7）
      → similarity: ChromaDB 余弦相似度检索
      → bm25: BM25Okapi 关键词检索（jieba 中文分词）
      → hybrid: Similarity + BM25，RRF 融合（rrf_k=60，每路 candidate_k=12）
      → 检索默认过滤 review_status='approved'，可选 doc_category 过滤
      → `_retrieve()` 支持 `review_status=None` 跳过审核过滤（评估用）
      → `_retrieve()` 支持 `method` 参数覆盖配置（评估用）
    → [可选] Reranker 精排（粗召回 candidate_k → reranker top_n；失败时回退原始排序）
    → 检索质量控制（分数归一化、Jaccard 去重、动态 TopK）
    → [可选] 上下文压缩（从 chunk 中提取与问题相关的连续原文片段）
    → [可选] 联网搜索增强（DuckDuckGo）
    → 组装 prompt（system + context + history + question）
    → 调用 Ollama LLM 生成回答（同步/SSE 流式）
    → 记录本次返回来源的 chunk 命中次数（供 `/stats/chunks` 线上命中统计使用）
    → 返回回答 + 来源文档
```

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
- MVP 新增 metadata 字段：`doc_category`（文档分类）、`section_title`（章节标题）、`review_status`（审核状态，默认 pending，检索只返回 approved）、`geo_wkt`（空间预留字段，始终为 None）
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

### 持久化策略（web/src/utils/storage.ts）
- **localStorage**：消息文本、角色、来源文档（轻量数据）
- **IndexedDB**：用户消息中的图片 base64（大体积数据）
- 每次 AI 回复完成后自动全量保存，最多保留 30 条消息

## 开发和运行

### 本地开发

**后端：**
```powershell
# 唯一标准本地环境：项目根目录下的 venv（不要使用裸 python 或 .venv）
.\venv\Scripts\python.exe -m pip install -r requirements.txt

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

### Docker 部署
```bash
docker build -t rag-knowledge .
docker run -p 10605:10605 rag-knowledge
```

## 常见操作

- **添加文档**：放入 watch_directory/已发布文章/ 或 watch_directory/upload/，等待定时扫描，或手动调用 POST /scan
- **重建知识库**：替换向量模型后，调用 `GET /rebuild`
- **切换模型**：前端下拉框选择，嵌入模型切换后需重建知识库
- **清空对话**：前端垃圾桶按钮，只清除前端缓存，不影响向量库
- **查看日志**：`logs/rag.log`（全部）、`logs/rag_error.log`（WARNING+，保留 30 天）
- **评估命令**：始终使用 `.\venv\Scripts\python.exe run_eval_full.py`，不要使用 `python run_eval_full.py`
- **重建后评估**：重建会生成全新的 chunk ID；旧的 `eval_dataset.json` 和难例集随即失效，必须基于新向量库重新生成
- **数据库维护**：执行离线重建、迁移或诊断前先停止后端和其他评估进程，确认没有进程占用 `chroma_db`

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

## Prompt 与回答规范

- 知识库事实必须由检索 context 明确支持，并在正文使用 `[1]`、`[2]` 等编号引用；禁止推测、补全隐含逻辑或伪造来源。
- context 无法明确回答时必须先原样输出“当前知识库中未查询到相关内容。”；仅有部分依据时，只回答有依据的部分并明确指出缺失项。
- `[answer] allow_general_knowledge = true` 默认允许在未命中提示后增加独立的“通用知识补充”；设为 `false` 或使用环境变量 `ANSWER_ALLOW_GENERAL_KNOWLEDGE=false` 时完全禁用补充。请求字段 `allow_general_knowledge` 可逐次覆盖配置。
- 历史消息只用于理解追问和指代，不作为事实来源。智能体 Prompt 只作为附加角色要求，不能替换基础系统规则。
- 每个来源由后端确定性生成引用编号、文件名、页码和原文片段；没有真实页码的 TXT、Markdown、图片、视频等明确显示“无页码”，不生成虚假页码。
- 联网搜索必须由请求显式开启，结果标记为“外部来源”，与知识库来源分开显示。非流式与 SSE 流式问答共用同一套 Prompt 和来源结构。
