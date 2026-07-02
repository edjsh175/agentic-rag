"""
应用入口 —— 支持 python -m rag_knowledge 启动
"""
import sys
import logging
import logging.handlers
from pathlib import Path


def _setup_logging(log_dir: Path):
    """配置日志：控制台 + 文件（按天轮转，保留 7 天）"""
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # ---- 控制台（INFO+） ----
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    # ---- 全部日志文件（INFO+，每天轮转，保留 7 天） ----
    all_handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / "rag.log",
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
    )
    all_handler.setLevel(logging.INFO)
    all_handler.setFormatter(fmt)
    root.addHandler(all_handler)

    # ---- 错误日志文件（WARNING+，单独存放方便排查） ----
    err_handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / "rag_error.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    err_handler.setLevel(logging.WARNING)
    err_handler.setFormatter(fmt)
    root.addHandler(err_handler)

    # 压制 ChromaDB 的烦人 telemetry 错误
    logging.getLogger("chromadb.telemetry").setLevel(logging.ERROR)
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.ERROR)

    return logging.getLogger(__name__)


logger = logging.getLogger(__name__)


def main():
    """启动入口"""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn

    from rag_knowledge.config import Config
    from rag_knowledge.services.loader import FileLoader
    from rag_knowledge.services.scanner import DirectoryScanner
    from rag_knowledge.services.rag import RagChain
    from rag_knowledge.repository.vector_store import VectorStore
    from rag_knowledge.repository.relational_db import RelationalDB
    from rag_knowledge.api.routes import router, init_components
    from rag_knowledge.api.middleware import RequestLogMiddleware

    cfg = Config()

    # ---- 日志初始化（必须在所有日志调用之前） ----
    _setup_logging(cfg.log_dir)

    # ---- 启动横幅 ----
    logger.info("")
    logger.info("=" * 54)
    logger.info("  RAG 本地知识库问答系统  v2.0")
    logger.info("=" * 54)
    logger.info("  Ollama:        %s", cfg.ollama_base_url)
    logger.info("  向量模型:      %s", cfg.embedding_model)
    logger.info("  问答模型:      %s", cfg.llm_model)
    logger.info("  视觉模型:      %s", cfg.vision_model)
    logger.info("  监视目录:      %s", cfg.watch_dir)
    logger.info("  向量数据库:    %s", cfg.chroma_dir)
    logger.info("  关系数据库:    %s", cfg.relational_db_path)
    logger.info("  日志目录:      %s", cfg.log_dir)
    logger.info("  扫描间隔:      %d 分钟", cfg.scan_interval)
    logger.info("  检索 top-k:    %d (fetch_k=%d, lambda=%.1f)",
                cfg.retrieval_top_k, cfg.retrieval_fetch_k, cfg.retrieval_lambda_mult)
    logger.info("  分块:          %d / overlap %d", cfg.chunk_size, cfg.chunk_overlap)
    logger.info("  Web 服务:      http://%s:%d", cfg.server_host, cfg.server_port)
    logger.info("  API 文档:      http://localhost:%d/docs", cfg.server_port)
    logger.info("=" * 54)
    logger.info("")

    # ---- 创建组件 ----
    scanner = DirectoryScanner()
    rag = RagChain()
    loader = FileLoader()
    store = VectorStore()
    rel_db = RelationalDB()        # MVP: 初始化关系数据库（自动建表）
    init_components(scanner, rag, loader, store, cfg)

    # ---- FastAPI 应用 ----
    app = FastAPI(title="RAG 本地知识库问答系统", version="2.0.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(RequestLogMiddleware)
    app.include_router(router)

    # ---- 首次同步已发布文章（先同步，让文章落地后再扫描入库） ----
    if cfg.blog_publish_api_url.strip():
        from rag_knowledge.services.blog_syncer import BlogPostSyncer
        syncer = BlogPostSyncer(cfg.blog_publish_dir, cfg.blog_publish_api_url, cfg.data_dir)
        logger.info("首次同步已发布文章...")
        try:
            result = syncer.sync()
            logger.info("首次同步完成: +%d ~%d =%d -%d",
                         result["new"], result["updated"], result["skipped"], result["deleted"])
        except Exception as e:
            logger.warning("首次同步异常: %s", e)
    else:
        syncer = None
        logger.info("未配置博客同步 API，跳过首次同步")

    # ---- 首次扫描（同步下来的文章 + 已有文件一起入库） ----
    logger.info("执行首次目录扫描...")
    try:
        r = scanner.scan()
        logger.info("首次扫描完成: 新增 %d / 跳过 %d / 失败 %d",
                     r["new_files"], r["skipped_files"], r["errors"])
    except Exception as e:
        logger.warning("首次扫描异常: %s", e)

    # ---- 定时扫描 ----
    scanner.start()

    # ---- 定时同步已发布文章 ----
    if cfg.blog_publish_api_url.strip() and syncer is not None:
        from apscheduler.schedulers.background import BackgroundScheduler

        interval = cfg.blog_publish_sync_interval

        def job():
            try:
                result = syncer.sync()
                _ = scanner.scan()
                logger.info("定时同步: +%d ~%d =%d -%d",
                            result["new"], result["updated"], result["skipped"], result["deleted"])
            except Exception as e:
                logger.warning("定时同步异常: %s", e)

        scheduler = BackgroundScheduler()
        scheduler.add_job(job, "interval", minutes=interval, id="blog_sync", replace_existing=True)
        scheduler.start()
        logger.info("定时同步已启动 (间隔: %d 分钟)", interval)
    else:
        logger.info("未配置博客同步 API，跳过定时同步")

    # ---- 启动 Web 服务 ----
    uvicorn.run(app, host=cfg.server_host, port=cfg.server_port, log_config=None)


if __name__ == "__main__":
    main()
