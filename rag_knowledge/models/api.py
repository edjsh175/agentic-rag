"""API 请求与响应数据模型。"""
from typing import Optional
from enum import Enum

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


class AdminChunkItem(BaseModel):
    chunk_id: str
    file_name: str
    source: str
    section_title: str
    doc_category: str
    review_status: str
    content_preview: str
    content: str
    kb_name: str | None = None
    page_label: str
    indexed_at: str | None = None
    file_path: str | None = None
    kb_path: str | None = None
    title: str | None = None
    source_url: str | None = None
    author: str | None = None
    platform: str | None = None
    publish_date: str | None = None
    last_modified: str | None = None
    crawled_at: str | None = None


class AdminChunkListResponse(BaseModel):
    items: list[AdminChunkItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminChunkUpdateRequest(BaseModel):
    review_status: str | None = None
    doc_category: str | None = None
    section_title: str | None = None


class BatchReviewRequest(BaseModel):
    chunk_ids: list[str]
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


# =====================================================================
# 知识图谱 (Knowledge Graph) 数据模型
# =====================================================================

class EntityTypeEnum(str, Enum):
    module = "功能模块"
    data_file = "数据文件"
    config = "配置项"
    api = "API接口"


class RelationTypeEnum(str, Enum):
    dependency = "依赖"
    used_in = "被使用于"
    contains = "包含"
    peer = "平级"


class LinkTypeEnum(str, Enum):
    primary = "主要描述"
    indirect = "间接提及"


class DocCategoryEnum(str, Enum):
    stamp_server = "StampServer"
    stamp_tools = "StampTools"
    stamp_webrtc = "StampWebRTC"
    real3d = "实景三维"
    farmland = "耕地保护"
    vector_tile = "矢量瓦片"
    infra = "基础环境"
    blog = "博客"
    other = "其他"


class EntityCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, description="实体名称")
    entity_type: EntityTypeEnum = Field(..., description="实体类型")
    doc_category: Optional[DocCategoryEnum] = Field(None, description="所属文档分类")


class EntityCreateResponse(BaseModel):
    id: str
    name: str
    entity_type: EntityTypeEnum
    doc_category: Optional[DocCategoryEnum] = None
    created_by: str
    created_at: str
    created: bool = Field(..., description="是否为新创建的实体")


class EntityUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, description="实体名称")
    entity_type: Optional[EntityTypeEnum] = Field(None, description="实体类型")
    doc_category: Optional[DocCategoryEnum] = Field(None, description="所属文档分类")


class EntityResponse(BaseModel):
    id: str
    name: str
    entity_type: EntityTypeEnum
    doc_category: Optional[DocCategoryEnum] = None
    created_by: str
    created_at: str


class RelationCreateRequest(BaseModel):
    source_id: str = Field(..., description="源实体 ID")
    target_id: str = Field(..., description="目标实体 ID")
    relation_type: RelationTypeEnum = Field(..., description="关系类型")


class RelationResponse(BaseModel):
    id: str
    source_id: str
    target_id: str
    relation_type: RelationTypeEnum
    created_by: str
    created_at: str
    created: Optional[bool] = None


class EntityChunkLinkRequest(BaseModel):
    chunk_id: str = Field(..., description="知识块 ID")
    link_type: Optional[LinkTypeEnum] = Field(LinkTypeEnum.primary, description="关联类型")


class EntityChunkLinkResponse(BaseModel):
    id: str
    entity_id: str
    chunk_id: str
    link_type: LinkTypeEnum
    created_at: str
    created: bool


class GraphNode(BaseModel):
    id: str
    label: str
    type: str
    doc_category: Optional[str] = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str


class GraphDataResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class EntityChunkDetailResponse(BaseModel):
    chunk_id: str
    file_name: str
    section_title: str
    link_type: str
    content_preview: str
    content: str

