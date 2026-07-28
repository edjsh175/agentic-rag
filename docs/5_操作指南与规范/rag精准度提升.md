RAG 检索优化 & Rerank 重排序 — 实施计划

> **历史快照（2026-07-28）**：早期六阶段实施计划；所列能力多数已落地。现行进度见 [docs/README.md](../README.md) 与 [待办清单](../3_待办清单/待办清单.md)，勿把下文「待实施」当成现状。

Context
当前项目已具备基础 RAG 能力（MMR 向量检索 + Ollama LLM 生成），但检索方式单一（仅 MMR）、无重排序、无缓存、无评估体系。需要逐步完成文档中「四、检索能力优化」所列的全部任务，并建立可量化的准确率测试方法。

核心问题：在优化前必须先建立评估基准，否则无法判断优化是否有效。

准确率如何测试？
方法：三轮递进式评估
第一轮：自动构建测试集（LLM 合成）

从已入库的 approved chunks 中，每条 chunk 让 LLM 生成 2-3 个相关问题
该 chunk 的 chunk_id 即为「相关文档」金标准
可快速生成 100-500 条测试用例，覆盖两个知识库
第二轮：检索指标测量

对每条测试问题，用不同检索配置执行检索
计算指标：Recall@K（召回率）、MRR（平均倒数排名）、Hit Rate（命中率）、NDCG@K
横向对比：MMR vs Similarity vs BM25 vs Hybrid，有/无 Reranker
第二轮半：生成质量评估（RAGAS）

使用 ragas 库评估端到端质量
指标：Context Recall（上下文召回）、Context Precision（上下文精确率）、Faithfulness（答案忠实度）、Answer Relevancy（答案相关性）
第三轮：人工抽检

从自动测试集中随机抽 30-50 条，人工判断答案是否正确、是否幻觉
计算人工评估的准确率作为最终金标准
评估代码位置
新建 rag_knowledge/evaluation/ 包
test_dataset.py — 数据集管理（合成 + 加载）
metrics.py — 指标计算
runner.py — 评估运行器，支持 ablations（不同策略组合对比）
实施路线（6 个阶段，按依赖顺序）
阶段 1：评估框架（建立基准） ⭐ 最优先
目标：先有尺子，再量长短。在所有优化前测得当前 baseline。

新建文件：

rag_knowledge/evaluation/__init__.py
rag_knowledge/evaluation/test_dataset.py — 从 approved chunks 用 LLM 合成测试集
rag_knowledge/evaluation/metrics.py — Recall@K, MRR, Hit Rate, NDCG
rag_knowledge/evaluation/runner.py — 跑评估、输出对比表
依赖：pip install ragas pandas

验证：运行 runner.py，得到当前 MMR 配置下的 Recall@4、MRR 等指标

阶段 2：BM25 关键词检索
目标：补充关键词匹配能力，为混合检索打基础。

新建文件：

rag_knowledge/services/bm25_store.py — BM25Okapi 索引管理
从 ChromaDB 拉全量文档构建索引
支持 kb_name 和 review_status 过滤
中文分词用 jieba
修改文件：

requirements.txt — 添加 rank_bm25, jieba
rag_knowledge/services/rag.py — _retrieve() 增加 BM25 分支
验证：评估对比 MMR vs BM25 的 Recall@K

阶段 3：混合检索（Hybrid Search）
目标：向量 + BM25 融合，取长补短。

新建文件：

rag_knowledge/services/retrieval_strategy.py — 策略调度器
similarity / mmr / bm25 / hybrid 四种模式
Hybrid 使用 LangChain EnsembleRetriever + RRF 融合
修改文件：

rag_knowledge/config.py — 添加 [retrieval_strategy] 配置项
config.ini — 新增配置节
rag_knowledge/services/rag.py — _retrieve() 改为调用 RetrievalStrategy
验证：评估对比四种模式的 Recall@K，确认 Hybrid 优于单一方法

阶段 4：Rerank 重排序
目标：Top50 召回 → Rerank 精排 → Top5 返回，最大化最终结果相关性。

新建文件：

rag_knowledge/services/reranker.py — 重排序器抽象 + 多后端实现
BGEReranker（FlagEmbedding, 本地运行，推荐）
CrossEncoderReranker（sentence-transformers, 本地运行）
工厂函数 create_reranker(type, model_name)
修改文件：

rag_knowledge/config.py — 添加 [reranker] 配置项
config.ini — 新增配置节（默认 bge, recall_top_n=50, rerank_top_n=5）
rag_knowledge/services/rag.py — 检索后插入 Rerank 步骤
requirements.txt — 添加 sentence-transformers, FlagEmbedding
验证：评估对比有/无 Reranker 的 Recall@5、MRR

阶段 5：检索质量优化
目标：精细化控制检索结果质量。

修改文件：

rag_knowledge/config.py — 添加 [retrieval_quality] 配置项
config.ini — 新增配置节
rag_knowledge/services/retrieval_strategy.py 或 rag.py — 实现以下逻辑：
相似度阈值过滤：score < threshold 的 chunk 丢弃
Chunk 去重：Jaccard 相似度 > 0.85 的 chunk 只保留得分最高的
动态 TopK：分数断崖（drop > 50%）处截断
上下文压缩：可选使用轻量 LLM 从 chunk 中提取相关片段（ContextualCompressionRetriever）
验证：每一步开启/关闭的 A/B 评估对比

阶段 6：性能优化
目标：减少重复计算和 Ollama 调用开销。

新建文件：

rag_knowledge/services/embedding_cache.py — Embedding 缓存（LRU, 默认 10000 条）
rag_knowledge/services/query_cache.py — 查询结果缓存（TTL, 默认关闭）
修改文件：

rag_knowledge/config.py — 添加 [cache] 配置项
config.ini — 新增配置节
rag_knowledge/repository/vector_store.py — 集成 embedding cache
rag_knowledge/services/rag.py — 集成 query cache（query() 方法顶部）
rag_knowledge/services/rag.py — 多 KB 检索改为 ThreadPoolExecutor 并发
验证：对比缓存命中率、端到端延迟

关键文件清单
文件	操作	所属阶段
rag_knowledge/evaluation/ (4 个文件)	新建	阶段 1
rag_knowledge/services/bm25_store.py	新建	阶段 2
rag_knowledge/services/retrieval_strategy.py	新建	阶段 3
rag_knowledge/services/reranker.py	新建	阶段 4
rag_knowledge/services/embedding_cache.py	新建	阶段 6
rag_knowledge/services/query_cache.py	新建	阶段 6
rag_knowledge/config.py	修改（4 次）	阶段 3/4/5/6
rag_knowledge/services/rag.py	修改（4 次）	阶段 2/3/4/6
rag_knowledge/repository/vector_store.py	修改	阶段 6
config.ini + config-prod.ini	修改	阶段 3/4/5/6
requirements.txt	修改	阶段 1/2/4
依赖关系图
阶段 1 (评估框架)
  │
  └──► 阶段 2 (BM25) ──► 阶段 3 (Hybrid) ──► 阶段 4 (Rerank)
                                                    │
                                                    ▼
                                              阶段 5 (质量优化)
                                                    │
                                                    ▼
                                              阶段 6 (性能优化)
阶段 1 必须最先做（建立 baseline）
阶段 2 是阶段 3 的前提（Hybrid 依赖 BM25）
阶段 3 是阶段 4 的前提（Rerank 依赖统一的检索入口）
阶段 5 可在阶段 3 之后任意时间做
阶段 6 相对独立，但建议最后做（等检索逻辑稳定后加缓存才有意义）
风险 & 注意事项
BM25 中文分词：rank_bm25 默认按空格分词，对中文无效 → 必须用 jieba 分词预处理
Reranker 模型大小：BGE-reranker-v2-m3 约 1.2GB，首次加载慢 → 懒加载 + 可配置使用更小的 base 版（278MB）
Chromium 同步：BM25 索引需在文档入库/删除后重建 → 挂载到 /rebuild API 和 scanner.scan()
查询缓存风险：知识库更新后缓存可能返回过期结果 → 默认关闭，手动开启，入库时自动清空
评估数据质量：LLM 合成的问题可能与真实用户提问分布不同 → 后续补充真实用户日志标注