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


@dataclass
class StructuredRetrievalConfig:
    """结构化与表格检索加权控制配置"""
    enabled: bool = True
    table_boost: float = 0.03
    table_header_boost: float = 0.01
    section_match_boost: float = 0.02


@dataclass
class ContextBudgetConfig:

    """Context 自动裁剪配置（Token 预算控制）"""
    enabled: bool = True
    # 模型上下文窗口大小（tokens），切换模型时同步修改
    context_window: int = 32768
    # 生成预留 tokens（对应 num_predict）
    generation_reserve: int = 2048
    # 系统提示词预留 tokens
    system_reserve: int = 1000
    # 当前问题预留 tokens
    question_reserve: int = 500
    # context 在可用预算中的占比（剩余给 history）
    context_ratio: float = 0.7
    # 字符数 / token 的估算系数（中文约 1.5，偏保守取 1.3）
    chars_per_token: float = 1.3


@dataclass
class HistoryCompressionConfig:
    """历史消息压缩与摘要配置"""
    enabled: bool = True
    # 触发压缩时的保留最小原始对话轮数（1轮=1个user+1个assistant消息，即最近10个message）
    min_raw_rounds: int = 8
    # 触发压缩的最大原始对话轮数（超过此轮数则对历史进行摘要压缩）
    max_raw_rounds: int = 20
    failure_cooldown_seconds: int = 300



@dataclass
class CacheConfig:
    """Stage 6 performance cache settings."""
    embedding_cache_enabled: bool = True
    embedding_cache_capacity: int = 10000
    query_cache_enabled: bool = False
    query_cache_ttl_seconds: int = 300
    query_cache_capacity: int = 256
    retrieval_executor_workers: int = 4


@dataclass
class QueryPlannerConfig:
    """意图驱动检索计划配置。"""
    enabled: bool = True
    llm_timeout: int = 15
    procedure_top_k: int = 8
    procedure_candidate_k: int = 24
    troubleshooting_top_k: int = 6
    troubleshooting_candidate_k: int = 18
    comparison_top_k: int = 6
    comparison_candidate_k: int = 18
    max_expanded_queries: int = 8
    neighbor_window: int = 2
    max_neighbors_per_source: int = 6


@dataclass
class GraphRetrievalConfig:
    """Evidence-backed knowledge graph retrieval settings (Phase C)."""
    enabled: bool = False
    min_link_confidence: float = 0.75
    min_entity_confidence: float = 0.7
    min_relation_confidence: float = 0.7
    max_entities: int = 16
    max_chunks: int = 24
    graph_weight: float = 1.25


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
        self.helper_llm_model = _get("model", "helper_llm", "gemma3:4b")
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
        self.semantic_chunking_enabled = _get("text", "semantic_chunking_enabled", "true").lower() == "true"
        self.semantic_breakpoint_percentile = int(_get("text", "semantic_breakpoint_percentile", "80"))
        self.semantic_min_chunk_size = int(_get("text", "semantic_min_chunk_size", "200"))
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

        # ---- 意图驱动检索计划 ----
        self.query_planner = QueryPlannerConfig(
            enabled=_get("query_planner", "enabled", "true").lower() == "true",
            llm_timeout=int(_get("query_planner", "llm_timeout", "15")),
            procedure_top_k=int(_get("query_planner", "procedure_top_k", "8")),
            procedure_candidate_k=int(_get("query_planner", "procedure_candidate_k", "24")),
            troubleshooting_top_k=int(_get("query_planner", "troubleshooting_top_k", "6")),
            troubleshooting_candidate_k=int(_get("query_planner", "troubleshooting_candidate_k", "18")),
            comparison_top_k=int(_get("query_planner", "comparison_top_k", "6")),
            comparison_candidate_k=int(_get("query_planner", "comparison_candidate_k", "18")),
            max_expanded_queries=int(_get("query_planner", "max_expanded_queries", "8")),
            neighbor_window=int(_get("query_planner", "neighbor_window", "2")),
            max_neighbors_per_source=int(_get("query_planner", "max_neighbors_per_source", "6")),
        )

        self.graph_retrieval = GraphRetrievalConfig(
            enabled=_get("graph_retrieval", "enabled", "false").lower() == "true",
            min_link_confidence=float(_get("graph_retrieval", "min_link_confidence", "0.75")),
            min_entity_confidence=float(_get("graph_retrieval", "min_entity_confidence", "0.7")),
            min_relation_confidence=float(_get("graph_retrieval", "min_relation_confidence", "0.7")),
            max_entities=int(_get("graph_retrieval", "max_entities", "16")),
            max_chunks=int(_get("graph_retrieval", "max_chunks", "24")),
            graph_weight=float(_get("graph_retrieval", "graph_weight", "1.25")),
        )

        self.cache = CacheConfig(
            embedding_cache_enabled=_get("cache", "embedding_cache_enabled", "true").lower() == "true",
            embedding_cache_capacity=int(_get("cache", "embedding_cache_capacity", "10000")),
            query_cache_enabled=_get("cache", "query_cache_enabled", "false").lower() == "true",
            query_cache_ttl_seconds=int(_get("cache", "query_cache_ttl_seconds", "300")),
            query_cache_capacity=int(_get("cache", "query_cache_capacity", "256")),
            retrieval_executor_workers=int(_get("cache", "retrieval_executor_workers", "4")),
        )

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

        # ---- 结构化与表格检索加权配置 ----
        self.structured_retrieval = StructuredRetrievalConfig(
            enabled=_get("structured_retrieval", "enabled", "true").lower() == "true",
            table_boost=float(_get("structured_retrieval", "table_boost", "0.03")),
            table_header_boost=float(_get("structured_retrieval", "table_header_boost", "0.01")),
            section_match_boost=float(_get("structured_retrieval", "section_match_boost", "0.02")),
        )

        # ---- Context 自动裁剪 (Token 预算控制) ----
        self.context_budget = ContextBudgetConfig(
            enabled=_get("context_budget", "enabled", "true").lower() == "true",
            context_window=int(_get("context_budget", "context_window", "32768")),
            generation_reserve=int(_get("context_budget", "generation_reserve", "2048")),
            system_reserve=int(_get("context_budget", "system_reserve", "1000")),
            question_reserve=int(_get("context_budget", "question_reserve", "500")),
            context_ratio=float(_get("context_budget", "context_ratio", "0.7")),
            chars_per_token=float(_get("context_budget", "chars_per_token", "1.3")),
        )

        # ---- 历史消息压缩与摘要 ----
        self.history_compression = HistoryCompressionConfig(
            enabled=_get("history_compression", "enabled", "true").lower() == "true",
            min_raw_rounds=int(_get("history_compression", "min_raw_rounds", "8")),
            max_raw_rounds=int(_get("history_compression", "max_raw_rounds", "20")),
            failure_cooldown_seconds=int(
                _get("history_compression", "failure_cooldown_seconds", "300")
            ),
        )

        # ---- 目录扫描 ----
        self.watch_dir = _dir(_get("scanner", "watch_directory", "./watch_directory"), "./watch_directory")
        self.scan_interval = int(_get("scanner", "interval_minutes", "30"))
        raw_types = _get("scanner", "file_types",
                         "pdf,docx,doc,txt,md,xls,xlsx,jpg,jpeg,png,gif,bmp,webp,mp4,avi,mov,mkv")
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
        self._assert_test_paths_are_isolated(root)

        # ---- 创建必要目录 ----
        for d in [self.chroma_dir, self.data_dir, self.log_dir, self.blog_posts_dir, self.blog_crawl_dir, self.blog_publish_dir, self.crawl_image_dir]:
            d.mkdir(parents=True, exist_ok=True)
        self.watch_dir.mkdir(parents=True, exist_ok=True)

    def _assert_test_paths_are_isolated(self, root: Path) -> None:
        if not os.getenv("PYTEST_CURRENT_TEST"):
            return
        if os.getenv("ALLOW_LIVE_STORAGE_IN_TESTS") == "1":
            return

        live_paths = {
            "chroma_dir": root / "chroma_db",
            "relational_db_path": root / "data" / "rag_relational.db",
            "data_dir": root / "data",
            "log_dir": root / "logs",
            "watch_dir": root / "watch_directory",
            "blog_posts_dir": root / "blog_posts",
            "blog_crawl_dir": root / "scrape_article",
            "crawl_image_dir": root / "scrapingImages",
            "blog_publish_dir": (root / "watch_directory") / "已发布文章",
        }
        actual_paths = {
            "chroma_dir": self.chroma_dir,
            "relational_db_path": self.relational_db_path,
            "data_dir": self.data_dir,
            "log_dir": self.log_dir,
            "watch_dir": self.watch_dir,
            "blog_posts_dir": self.blog_posts_dir,
            "blog_crawl_dir": self.blog_crawl_dir,
            "crawl_image_dir": self.crawl_image_dir,
            "blog_publish_dir": self.blog_publish_dir,
        }

        violations = [
            name
            for name, actual in actual_paths.items()
            if Path(actual).resolve() == live_paths[name].resolve()
        ]
        if violations:
            violation_list = ", ".join(sorted(violations))
            raise RuntimeError(
                "pytest refused to use a live storage path without "
                f"ALLOW_LIVE_STORAGE_IN_TESTS=1: {violation_list}"
            )
