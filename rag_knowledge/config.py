"""
应用配置 —— 集中管理所有可调参数
优先级：环境变量 > config.ini > 代码默认值
"""
import os
from dataclasses import dataclass
from pathlib import Path
from configparser import ConfigParser


@dataclass
class RetrievalQualityConfig:
    """检索后处理质量控制配置（Phase 5）"""
    enabled: bool = True

    score_threshold_enabled: bool = False
    score_threshold: float = 0.35

    jaccard_dedup_enabled: bool = True
    jaccard_threshold: float = 0.85

    dynamic_topk_enabled: bool = True
    score_drop_ratio: float = 0.5
    min_top_k: int = 3
    max_top_k: int = 8

    contextual_compression_enabled: bool = False
    compression_model: str = "qwen2.5:7b"
    max_compressed_chunk_chars: int = 800

    debug_log_enabled: bool = True


class Config:
    """配置管理中心（单例），所有模块通过此对象读取配置"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        root = Path.cwd().resolve()

        # ---- 读取 config.ini ----
        ini = ConfigParser()
        ini_path = root / "config.ini"
        if ini_path.exists():
            ini.read(str(ini_path), encoding="utf-8")

        def _get(section: str, key: str, default: str = "") -> str:
            env_key = f"{section}_{key}".upper()
            return os.getenv(env_key) or ini.get(section, key, fallback=default)

        def _dir(value: str, default: str) -> Path:
            """路径配置：绝对路径直接用，相对路径拼上项目根"""
            p = Path(value)
            return p if p.is_absolute() else (root / p)

        # ---- Ollama ----
        self.ollama_base_url = _get("ollama", "base_url", "http://localhost:11434")

        # ---- 模型 ----
        self.embedding_model = _get("model", "embedding", "qwen3-embedding:4b")
        self.llm_model = _get("model", "llm", "deepseek-r1:7b")
        self.vision_model = _get("model", "vision", "qwen3-vl:8b")

        # ---- 问答策略 ----
        self.allow_general_knowledge = _get(
            "answer", "allow_general_knowledge", "true"
        ).lower() == "true"

        # ---- 向量数据库 ----
        self.chroma_dir = _dir(_get("vector_store", "persist_directory", "./chroma_db"), "./chroma_db")
        self.collection_name = _get("vector_store", "collection_name", "rag_knowledge")

        # ---- 关系数据库 ----
        self.relational_db_path = _dir(_get("relational_db", "db_path", "./data/rag_relational.db"), "./data/rag_relational.db")

        # ---- 文本分块 ----
        self.chunk_size = int(_get("text", "chunk_size", "800"))
        self.chunk_overlap = int(_get("text", "chunk_overlap", "150"))
        self.extract_embedded_images = _get("text", "extract_embedded_images", "true").lower() == "true"
        self.use_unstructured = _get("text", "use_unstructured", "true").lower() == "true"
        self.unstructured_strategy = _get("text", "unstructured_strategy", "fast")

        # ---- 检索策略 ----
        self.retrieval_top_k = int(_get("retrieval", "top_k", "5"))
        self.retrieval_fetch_k = int(_get("retrieval", "fetch_k", "20"))
        self.retrieval_lambda_mult = float(_get("retrieval", "lambda_mult", "0.7"))
        self.retrieval_strategy = _get("retrieval_strategy", "method", "mmr")
        self.retrieval_fusion_method = _get("retrieval_strategy", "fusion_method", "rrf")
        self.retrieval_rrf_k = int(_get("retrieval_strategy", "rrf_k", "60"))
        self.retrieval_candidate_k = int(_get("retrieval_strategy", "candidate_k", "12"))

        # ---- 重排序器 (Phase 4) ----
        self.reranker_enabled = _get("reranker", "enabled", "false").lower() == "true"
        self.reranker_type = _get("reranker", "type", "bge")
        self.reranker_model = _get("reranker", "model", "BAAI/bge-reranker-v2-m3")
        self.reranker_top_n = int(_get("reranker", "top_n", "4"))
        self.reranker_candidate_k = int(_get("reranker", "candidate_k", "20"))

        # ---- 检索质量控制 (Phase 5) ----
        self.retrieval_quality = RetrievalQualityConfig(
            enabled=_get("retrieval_quality", "enabled", "true").lower() == "true",
            score_threshold_enabled=_get("retrieval_quality", "score_threshold_enabled", "false").lower() == "true",
            score_threshold=float(_get("retrieval_quality", "score_threshold", "0.35")),
            jaccard_dedup_enabled=_get("retrieval_quality", "jaccard_dedup_enabled", "true").lower() == "true",
            jaccard_threshold=float(_get("retrieval_quality", "jaccard_threshold", "0.85")),
            dynamic_topk_enabled=_get("retrieval_quality", "dynamic_topk_enabled", "true").lower() == "true",
            score_drop_ratio=float(_get("retrieval_quality", "score_drop_ratio", "0.5")),
            min_top_k=int(_get("retrieval_quality", "min_top_k", "3")),
            max_top_k=int(_get("retrieval_quality", "max_top_k", "8")),
            contextual_compression_enabled=_get("retrieval_quality", "contextual_compression_enabled", "false").lower() == "true",
            compression_model=_get("retrieval_quality", "compression_model", "qwen2.5:7b"),
            max_compressed_chunk_chars=int(_get("retrieval_quality", "max_compressed_chunk_chars", "800")),
            debug_log_enabled=_get("retrieval_quality", "debug_log_enabled", "true").lower() == "true",
        )

        # ---- 目录扫描 ----
        self.watch_dir = _dir(_get("scanner", "watch_directory", "./watch_directory"), "./watch_directory")
        self.scan_interval = int(_get("scanner", "interval_minutes", "30"))
        raw_types = _get("scanner", "file_types",
                         "pdf,docx,doc,txt,jpg,jpeg,png,gif,bmp,webp,mp4,avi,mov,mkv")
        self.watch_file_types = [t.strip().lower() for t in raw_types.split(",")]

        # ---- Web 服务 ----
        self.server_host = _get("server", "host", "0.0.0.0")
        self.server_port = int(_get("server", "port", "8000"))

        # ---- 数据与日志目录 ----
        self.data_dir = _dir(_get("path", "data_dir", "./data"), "./data")
        self.log_dir = _dir(_get("path", "log_dir", "./logs"), "./logs")

        # ---- 博客 ----
        self.blog_posts_dir = _dir(_get("blog", "posts_dir", "./blog_posts"), "./blog_posts")
        self.blog_crawl_dir = _dir(_get("blog", "crawl_dir", "./scrape_article"), "./scrape_article")

        # ---- 爬虫图片下载 ----
        self.crawl_image_dir = _dir(_get("crawl", "image_dir", "./scrapingImages"), "./scrapingImages")

        # ---- 博客发布系统同步 ----
        self.blog_publish_api_url = _get("blog_publish", "api_url", "http://localhost:8080/api/articles/all")
        self.blog_publish_sync_interval = int(_get("blog_publish", "sync_interval", "30"))
        self.blog_publish_dir = self.watch_dir / "已发布文章"

        # ---- 博客发布接口（addRag） ----
        self.blog_add_rag_url = _get("blog_publish", "add_rag_url", "http://127.0.0.1:8080/zslt/system/article/addRag")

        # ---- 创建必要目录 ----
        for d in [self.chroma_dir, self.data_dir, self.log_dir, self.blog_posts_dir, self.blog_crawl_dir, self.blog_publish_dir, self.crawl_image_dir]:
            d.mkdir(parents=True, exist_ok=True)
        self.watch_dir.mkdir(parents=True, exist_ok=True)
