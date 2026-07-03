"""API 请求与响应数据模型。"""
from typing import Optional

from pydantic import BaseModel, Field


class SourceSummary(BaseModel):
    """上一轮 assistant 来源的轻量摘要（不传完整 chunk 内容）。"""
    file_name: Optional[str] = None
    source: Optional[str] = None
    section_title: Optional[str] = None
    page_label: Optional[str] = None
    chunk_id: Optional[str] = None
    preview: Optional[str] = None  # 最多 200 字符


class HistoryItem(BaseModel):
    role: str
    content: str
    sources: Optional[list[SourceSummary]] = None  # assistant 消息可携带上一轮来源摘要


class QueryRequest(BaseModel):
    question: str
    collection_name: Optional[str] = "rag_knowledge"
    kb_name: Optional[str] = None
    doc_category: Optional[str] = None
    history: Optional[list[HistoryItem]] = None
    llm_model: Optional[str] = None
    vision_model: Optional[str] = None
    thinking: Optional[bool] = None
    web_search: Optional[bool] = None
    allow_general_knowledge: Optional[bool] = None
    agent_prompt: Optional[str] = None


class QueryResponse(BaseModel):
    answer: str
    source_documents: list


class UploadResponse(BaseModel):
    message: str
    chunks_count: int
    file_name: str
    new_files: int = 0
    skipped_files: int = 0
    errors: int = 0


class ScanResponse(BaseModel):
    message: str
    new_files: int
    skipped_files: int
    errors: int


class StatsResponse(BaseModel):
    total_chunks: int
    collection_name: str
    watched_directory: str
    file_types: list
    scan_interval_minutes: int


class ChunkCountItem(BaseModel):
    key: str
    chunk_count: int


class ChunkHitCountItem(BaseModel):
    key: str
    hit_count: int


class FileChunkDistributionItem(BaseModel):
    file_path: str
    file_name: str
    kb_name: str | None = None
    doc_category: str | None = None
    file_type: str
    chunk_count: int


class ChunkHitItem(BaseModel):
    chunk_id: str
    hit_count: int
    file_name: str | None = None
    file_path: str | None = None
    review_status: str | None = None
    file_type: str | None = None


class ChunkStatsOverview(BaseModel):
    total_chunks: int
    avg_chunk_tokens: float
    avg_chunk_length: float
    min_chunk_length: int
    max_chunk_length: int


class ChunkStatsDistributions(BaseModel):
    by_file: list[FileChunkDistributionItem]
    by_file_type: list[ChunkCountItem]
    by_review_status: list[ChunkCountItem]


class ChunkStatsOnlineHitRates(BaseModel):
    total_queries: int
    hit_queries: int
    query_hit_rate: float
    top_chunks: list[ChunkHitItem]
    by_review_status: list[ChunkHitCountItem]
    by_file_type: list[ChunkHitCountItem]
    last_updated_at: str | None = None


class ChunkStatsOfflineHitRates(BaseModel):
    available: bool
    evaluated_at: str | None = None
    sample_count: int = 0
    hit_rate: float = 0.0
    recall_at_k: dict[str, float] = Field(default_factory=dict)


class ChunkStatsHitRates(BaseModel):
    online: ChunkStatsOnlineHitRates
    offline: ChunkStatsOfflineHitRates


class ChunkStatsResponse(BaseModel):
    overview: ChunkStatsOverview
    distributions: ChunkStatsDistributions
    hit_rates: ChunkStatsHitRates


class ReviewRequest(BaseModel):
    file_paths: Optional[list[str]] = None
    chunk_ids: Optional[list[str]] = None
    status: str = "approved"


class ReviewResponse(BaseModel):
    message: str
    updated_chunks: int
    requested_chunks: int
    status: str


class CrawlRequest(BaseModel):
    url: str


class CrawlResponse(BaseModel):
    title: str
    source_url: str
    author: str
    platform: str
    publish_date: str | None = None
    file_path: str
    message: str


class BlogPostItem(BaseModel):
    filename: str
    title: str
    author: str | None = None
    platform: str | None = None
    file_path: str
    file_size: int
    crawled_at: str | None = None


class BlogPostListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    posts: list[BlogPostItem]
    posts_dir: str
