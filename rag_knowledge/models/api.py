"""API 请求与响应数据模型。"""
from typing import Literal, Optional
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
    entity_name: Optional[str] = None
    history: Optional[list[HistoryItem]] = None
    llm_model: Optional[str] = None
    vision_model: Optional[str] = None
    thinking: Optional[bool] = None
    web_search: Optional[bool] = None
    allow_general_knowledge: Optional[bool] = None
    agent_prompt: Optional[str] = None
    pipeline_events: Optional[bool] = True
    pinned_chunk_ids: Optional[list[str]] = None
    excluded_chunk_ids: Optional[list[str]] = None
    clarification_question: Optional[str] = None
    clarification_selected: Optional[str] = None



class ClarificationOptionFilter(BaseModel):
    doc_category: Optional[str] = None
    entity_name: Optional[str] = None
    kb_name: Optional[str] = None


class ClarificationOption(BaseModel):
    id: str
    label: str
    filter: ClarificationOptionFilter


class ClarifyRequest(BaseModel):
    question: str
    kb_name: Optional[str] = None
    doc_category: Optional[str] = None
    entity_name: Optional[str] = None


class ClarifyResponse(BaseModel):
    needs_clarification: bool
    ask_question: Optional[str] = None
    trigger: Optional[str] = None
    reason: Optional[str] = None
    options: list[ClarificationOption] = Field(default_factory=list)


class QueryResponse(BaseModel):
    answer: str
    source_documents: list


class AdminQaDebugResponse(QueryResponse):
    evidence_chain: dict
    trace_id: Optional[str] = None


class QaTraceListResponse(BaseModel):
    total: int
    items: list[dict]
    limit: int
    offset: int


class QaTraceFeedbackRequest(BaseModel):
    feedback: Optional[str] = None


class UploadResponse(BaseModel):
    message: str
    chunks_count: int
    file_name: str
    new_files: int = 0
    skipped_files: int = 0
    errors: int = 0
    decisions: Optional[list[dict]] = None


class ScanResponse(BaseModel):
    message: str
    new_files: int
    skipped_files: int
    errors: int
    decisions: Optional[list[dict]] = None


class RebuildRequest(BaseModel):
    confirmation: Literal["REBUILD_KNOWLEDGE_BASE"]
    approve_all_chunks: bool = False


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
    """实体类型 — 覆盖三层图谱。

    旧值（功能模块/数据文件/配置项/API接口）保留为别名以兼容已有 API。
    """
    # --- 第一层：文档结构 ---
    document = "Document"
    section = "Section"
    # --- 第二层：领域概念 ---
    product = "Product"
    tool = "Tool"
    service = "Service"
    module = "Module"
    data_table = "DataTable"
    field = "Field"
    config_item = "ConfigItem"
    format = "Format"
    # --- 第三层：业务能力 ---
    procedure = "Procedure"
    step = "Step"
    error = "Error"
    solution = "Solution"
    # --- 旧版兼容别名 ---
    legacy_module = "功能模块"
    legacy_data_file = "数据文件"
    legacy_config = "配置项"
    legacy_api = "API接口"


class RelationTypeEnum(str, Enum):
    """关系类型 — 覆盖三层图谱。"""
    # 文档结构
    has_section = "has_section"
    has_chunk = "has_chunk"
    defined_in = "defined_in"
    # 领域概念
    alias_of = "alias_of"
    different_from = "different_from"
    belongs_to = "belongs_to"
    has_table = "has_table"
    has_field = "has_field"
    uses_config = "uses_config"
    supports_format = "supports_format"
    produces = "produces"
    consumes = "consumes"
    requires = "requires"
    # 业务能力
    has_step = "has_step"
    causes = "causes"
    solved_by = "solved_by"
    # --- 旧版兼容别名 ---
    legacy_dependency = "依赖"
    legacy_used_in = "被使用于"
    legacy_contains = "包含"
    legacy_peer = "平级"


class LinkTypeEnum(str, Enum):
    """实体-知识块关联类型。"""
    primary = "primary"
    mention = "mention"
    evidence = "evidence"
    table_source = "table_source"
    # --- 旧版兼容别名 ---
    legacy_primary = "主要描述"
    legacy_indirect = "间接提及"


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
    canonical_name: Optional[str] = Field(None, description="归一化名称")
    description: Optional[str] = Field(None, description="实体描述")
    properties_json: Optional[str] = Field(None, description="扩展属性JSON")
    confidence: Optional[float] = Field(None, description="置信度")
    review_status: Optional[str] = Field(None, description="审核状态")


class EntityCreateResponse(BaseModel):
    id: str
    name: str
    entity_type: EntityTypeEnum
    doc_category: Optional[DocCategoryEnum] = None
    canonical_name: Optional[str] = None
    description: Optional[str] = None
    properties_json: Optional[str] = None
    confidence: Optional[float] = None
    review_status: Optional[str] = None
    created_by: str
    created_at: str
    created: bool = Field(..., description="是否为新创建的实体")


class EntityUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, description="实体名称")
    entity_type: Optional[EntityTypeEnum] = Field(None, description="实体类型")
    doc_category: Optional[DocCategoryEnum] = Field(None, description="所属文档分类")
    canonical_name: Optional[str] = Field(None, description="归一化名称")
    description: Optional[str] = Field(None, description="实体描述")
    properties_json: Optional[str] = Field(None, description="扩展属性JSON")
    confidence: Optional[float] = Field(None, description="置信度")
    review_status: Optional[str] = Field(None, description="审核状态")


class EntityResponse(BaseModel):
    id: str
    name: str
    entity_type: EntityTypeEnum
    doc_category: Optional[DocCategoryEnum] = None
    canonical_name: Optional[str] = None
    description: Optional[str] = None
    properties_json: Optional[str] = None
    confidence: Optional[float] = None
    review_status: Optional[str] = None
    created_by: str
    created_at: str


class RelationCreateRequest(BaseModel):
    source_id: str = Field(..., description="源实体 ID")
    target_id: str = Field(..., description="目标实体 ID")
    relation_type: RelationTypeEnum = Field(..., description="关系类型")
    properties_json: Optional[str] = Field("{}", description="扩展属性JSON")
    confidence: Optional[float] = Field(None, description="置信度")
    evidence_text: Optional[str] = Field("", description="证据链文本")
    source_chunk_id: Optional[str] = Field("", description="关联chunk ID")
    review_status: Optional[str] = Field(None, description="审核状态")


class RelationResponse(BaseModel):
    id: str
    source_id: str
    target_id: str
    relation_type: RelationTypeEnum
    properties_json: Optional[str] = None
    confidence: Optional[float] = None
    evidence_text: Optional[str] = None
    source_chunk_id: Optional[str] = None
    review_status: Optional[str] = None
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
    canonical_name: Optional[str] = None
    description: Optional[str] = None
    properties_json: Optional[str] = None
    confidence: Optional[float] = None
    review_status: Optional[str] = None
    created_by: Optional[str] = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str
    confidence: Optional[float] = None
    review_status: Optional[str] = None
    source_chunk_id: Optional[str] = None
    evidence_text: Optional[str] = None


class GraphDataResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class ProductBackboneEntityRequest(BaseModel):
    name: str = Field(..., min_length=1)
    graph_type: str = Field("Module", min_length=1)
    layer: Optional[str] = None
    subtype: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    alias_candidates: Optional[list[str] | str] = None


class ProductBackboneEntityUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    graph_type: Optional[str] = Field(None, min_length=1)
    layer: Optional[str] = None
    subtype: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    alias_candidates: Optional[list[str] | str] = None


class ProductBackboneRelationRequest(BaseModel):
    source_id: str = Field(..., min_length=1)
    target_id: str = Field(..., min_length=1)
    relation_type: str = Field(..., min_length=1)
    evidence_text: Optional[str] = None


class EntityChunkDetailResponse(BaseModel):
    chunk_id: str
    file_name: str
    section_title: str
    link_type: str
    content_preview: str
    content: str


class GraphAliasCreateRequest(BaseModel):
    alias: str = Field(..., min_length=1, description="Alias value")
    confidence: Optional[float] = Field(None, description="Alias confidence")
    evidence_text: Optional[str] = Field(None, description="Evidence text")
    source_chunk_id: Optional[str] = Field(None, description="Source chunk id")
    review_status: Optional[str] = Field(None, description="Review status")


class GraphAliasItem(BaseModel):
    id: str
    entity_id: str
    alias: str
    confidence: Optional[float] = None
    source_chunk_id: Optional[str] = None
    evidence_text: Optional[str] = None
    review_status: Optional[str] = None
    created_at: str
    created: Optional[bool] = None


class GraphCandidateBatch(BaseModel):
    id: str
    mode: str
    status: str
    created_at: str
    reviewed_at: Optional[str] = None
    applied_at: Optional[str] = None
    error_text: Optional[str] = None
    filters: dict = Field(default_factory=dict)
    stats: dict = Field(default_factory=dict)


class GraphCandidateItem(BaseModel):
    id: str
    batch_id: str
    candidate_kind: str
    status: str
    payload: dict = Field(default_factory=dict)
    evidence_text: Optional[str] = None
    source_chunk_id: Optional[str] = None
    rejection_reason: Optional[str] = None
    reviewed_at: Optional[str] = None
    applied_at: Optional[str] = None
    applied_target_id: Optional[str] = None
    created_at: str


class GraphCandidateReviewRequest(BaseModel):
    approve_all: bool = False
    approve_ids: list[str] = Field(default_factory=list)
    reject_ids: list[str] = Field(default_factory=list)
    approve_kind: Optional[str] = None
    reason: Optional[str] = None


class GraphCandidateReviewResponse(BaseModel):
    batch_id: str
    updated_candidates: int
    batch_status: str


class GraphCandidateApplyResponse(BaseModel):
    batch_id: str
    status: str
    applied_candidates: int


class GraphQualityResponse(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)


class UserFeedbackRequest(BaseModel):
    user_id: str = "anonymous_user"
    query_text: str = ""
    answer_text: str = ""
    referenced_chunk_ids: list[str] = Field(default_factory=list)
    rating: str
    reason: str = ""
    trace_id: str = ""
    feedback_scope: str = "answer"  # "answer" or "chunk"
    target_chunk_id: str = ""


class UserFeedbackResponse(BaseModel):
    feedback_id: str
    rating: str
    triggered_chunks: list[dict] = Field(default_factory=list)
    message: str = "Feedback recorded"


class QualityMetrics(BaseModel):
    total_chunks: int
    approved_ratio: float
    pending_chunks: int
    isolated_entities: int
    isolated_chunks: int
    duplicate_ratio: float
    no_result_ratio_7d: float
    satisfaction_ratio_7d: float


class QualityAlert(BaseModel):
    type: str
    chunk_id: str
    source_file: str
    down_count: int
    reason: str


class QualityDashboardResponse(BaseModel):
    metrics: QualityMetrics
    alerts: list[QualityAlert] = Field(default_factory=list)
