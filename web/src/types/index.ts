/** 消息角色 */
export type Role = 'user' | 'assistant'

/** 单条聊天消息 */
export interface Message {
  id: string
  role: Role
  content: string
  /** 用户消息携带的图片（data URL） */
  imageUrl?: string
  /** assistant 消息携带的来源文档 */
  sources?: SourceDoc[]
  /** 是否正在生成（打字动画） */
  loading?: boolean
  /** 仅用于当前流式请求，不持久化 */
  status?: string
  /** 深度思考过程（assistant 消息） */
  thinking?: string
  /** 歧义反问卡片 */
  clarification?: MessageClarification
  /** 用户反馈（useful / unuseful） */
  feedback?: 'useful' | 'unuseful' | null
  /** 对应后端追踪 ID */
  trace_id?: string | null
  /** 问答过程与证据流水线数据 */
  pipelineSteps?: PipelineStep[]
  evidencePack?: EvidencePack
}

export interface EvidencePack {
  cited?: EvidenceItem[]
  retrieved_uncited?: EvidenceItem[]
  gaps?: any[]
  conflicts?: any[]
}

export interface PipelineStep {
  stage: string
  plan?: any
  retrieval?: any
  evidence?: EvidencePack
  stages_ms?: Record<string, number>
}

/** 反问选项过滤器 */
export interface ClarifyOptionFilter {
  doc_category?: string
  entity_name?: string
  kb_name?: string
}

/** 反问卡片单个选项 */
export interface ClarificationOption {
  id: string
  label: string
  filter: ClarifyOptionFilter
}

/** 反问预检响应结构 */
export interface ClarifyResult {
  needs_clarification: boolean
  ask_question?: string
  trigger?: string
  reason?: string
  options: ClarificationOption[]
}

/** 消息所携带的反问卡片数据与交互状态 */
export interface MessageClarification {
  ask_question: string
  trigger?: string
  reason?: string
  options: ClarificationOption[]
  selectedId?: string
}

/** 来源文档片段 */
export interface SourceDoc {
  content: string
  metadata: {
    source: string
    category?: string
    file_path?: string
    citation_id?: number
    chunk_id?: string
    file_name?: string
    page_label?: string
    title?: string
    section_title?: string
    section_path?: string
    source_type?: 'knowledge_base' | 'external'
    url?: string
  }
}

/** 知识库统计数据 */
export interface Stats {
  total_chunks: number
  collection_name: string
  watched_directory: string
  file_types: string[]
  scan_interval_minutes: number
}

export interface ChunkCountItem {
  key: string
  chunk_count: number
}

export interface ChunkHitCountItem {
  key: string
  hit_count: number
}

export interface FileChunkDistributionItem {
  file_path: string
  file_name: string
  kb_name?: string
  doc_category?: string
  file_type: string
  chunk_count: number
}

export interface ChunkHitItem {
  chunk_id: string
  hit_count: number
  file_name?: string
  file_path?: string
  review_status?: string
  file_type?: string
}

export interface ChunkStatsOverview {
  total_chunks: number
  avg_chunk_tokens: number
  avg_chunk_length: number
  min_chunk_length: number
  max_chunk_length: number
}

export interface ChunkStatsDistributions {
  by_file: FileChunkDistributionItem[]
  by_file_type: ChunkCountItem[]
  by_review_status: ChunkCountItem[]
}

export interface ChunkStatsOnlineHitRates {
  total_queries: number
  hit_queries: number
  query_hit_rate: number
  top_chunks: ChunkHitItem[]
  by_review_status: ChunkHitCountItem[]
  by_file_type: ChunkHitCountItem[]
  last_updated_at?: string | null
}

export interface ChunkStatsOfflineHitRates {
  available: boolean
  evaluated_at?: string | null
  sample_count: number
  hit_rate: number
  recall_at_k: Record<string, number>
}

export interface ChunkStats {
  overview: ChunkStatsOverview
  distributions: ChunkStatsDistributions
  hit_rates: {
    online: ChunkStatsOnlineHitRates
    offline: ChunkStatsOfflineHitRates
  }
}

export const DOC_CATEGORIES = [
  'StampServer', 'StampTools', 'StampWebRTC', '实景三维', '耕地保护',
  '矢量瓦片', '基础环境', '博客', '其他',
] as const

export type DocCategory = typeof DOC_CATEGORIES[number]
export type ReviewStatus = 'pending' | 'approved' | 'rejected'

export interface AdminChunk {
  chunk_id: string
  file_name: string
  source: string
  section_title: string
  doc_category: DocCategory
  review_status: ReviewStatus
  content_preview: string
  content: string
  kb_name?: string | null
  page_label: string
  indexed_at?: string | null
  file_path?: string | null
  kb_path?: string | null
  title?: string | null
  source_url?: string | null
  author?: string | null
  platform?: string | null
  publish_date?: string | null
  last_modified?: string | null
  crawled_at?: string | null
}

export interface AdminChunkListResponse {
  items: AdminChunk[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface AdminChunkUpdate {
  review_status?: ReviewStatus
  doc_category?: DocCategory
  section_title?: string
}

export interface ReviewMutationResponse {
  message: string
  updated_chunks: number
  requested_chunks: number
  status: string
}

/** 扫描结果 */
export interface ScanResult {
  new_files: number
  skipped_files: number
  errors: number
  decisions?: IngestionDecision[]
}

/** 博客爬取响应 */
export interface CrawlResult {
  title: string
  source_url: string
  author: string
  platform: string
  publish_date: string | null
  file_path: string
  message: string
}

/** 博客文章列表项 */
export interface BlogPostItem {
  filename: string
  title: string
  author: string | null
  platform: string | null
  file_path: string
  file_size: number
  crawled_at: string | null
}

/** 博客文章列表响应（含分页） */
export interface BlogPostList {
  total: number
  page: number
  page_size: number
  total_pages: number
  posts: BlogPostItem[]
  posts_dir: string
}

export interface GraphNode {
  id: string
  label: string
  type: string
  doc_category?: string | null
  canonical_name?: string | null
  description?: string | null
  properties_json?: string | null
  confidence?: number | null
  review_status?: string | null
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  label: string
  confidence?: number | null
  review_status?: string | null
  source_chunk_id?: string | null
  evidence_text?: string | null
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface GraphEntityUpsert {
  name: string
  entity_type: string
  doc_category?: string | null
  canonical_name?: string | null
  description?: string | null
  properties_json?: string | null
  confidence?: number | null
  review_status?: string | null
}

export interface GraphEntityUpdate {
  name?: string
  entity_type?: string
  doc_category?: string | null
  canonical_name?: string | null
  description?: string | null
  properties_json?: string | null
  confidence?: number | null
  review_status?: string | null
}

export interface GraphRelationCreate {
  source_id: string
  target_id: string
  relation_type: string
  properties_json?: string | null
  confidence?: number | null
  evidence_text?: string | null
  source_chunk_id?: string | null
  review_status?: string | null
}

export interface EvidenceItem {
  index?: number
  document?: string
  source?: string
  section_id?: string
  section_path?: string
  chunk_id?: string
  snippet?: string
  drop_reason?: string
}

export interface EvidenceChain {
  cited: EvidenceItem[]
  retrieved_uncited: EvidenceItem[]
  gaps: Record<string, string>[]
  conflicts: { key: string; values: EvidenceItem[] }[]
}

export interface QaDebugResult {
  answer: string
  source_documents: SourceDoc[]
  evidence_chain: EvidenceChain
  trace_id?: string | null
}

export interface QaTraceSummary {
  trace_id: string
  request_id?: string | null
  created_at?: string
  path?: string
  elapsed_ms?: number
  error?: string | null
  question?: string
  answer_preview?: string
  candidate_count?: number
  cited_count?: number
  runtime?: Record<string, unknown>
  file?: string
  feedback?: 'useful' | 'unuseful' | string | null
}

export interface QaTraceListResult {
  total: number
  items: QaTraceSummary[]
  limit: number
  offset: number
}

export interface QaTraceDetail {
  meta: {
    trace_id: string
    request_id?: string | null
    created_at?: string
    path?: string
    elapsed_ms?: number
    error?: string | null
  }
  feedback?: 'useful' | 'unuseful' | string | null
  request: Record<string, unknown>
  runtime: Record<string, unknown>
  stages: Record<string, number>
  plan: Record<string, unknown>
  retrieval: {
    query_hits?: unknown[]
    candidates?: Array<Record<string, unknown>>
    candidate_count?: number
  }
  evidence: EvidenceChain
  answer: {
    text?: string
    source_documents?: SourceDoc[]
  }
}

export interface ProductBackboneEntityPayload {
  name: string
  graph_type: string
  layer?: string | null
  subtype?: string | null
  description?: string | null
  source?: string | null
  status?: string | null
  alias_candidates?: string[] | string | null
}

export interface ProductBackboneEntityUpdatePayload {
  name?: string
  graph_type?: string
  layer?: string | null
  subtype?: string | null
  description?: string | null
  source?: string | null
  status?: string | null
  alias_candidates?: string[] | string | null
}

export interface ProductBackboneRelationPayload {
  source_id: string
  target_id: string
  relation_type: string
  evidence_text?: string | null
}

export interface EntityChunkDetail {
  chunk_id: string
  file_name: string
  section_title: string
  link_type: string
  content_preview: string
  content: string
}

export interface GraphAliasItem {
  id: string
  entity_id: string
  alias: string
  confidence?: number | null
  source_chunk_id?: string | null
  evidence_text?: string | null
  review_status?: string | null
  created_at: string
  created?: boolean
}

export interface GraphAliasCreateRequest {
  alias: string
  confidence?: number | null
  evidence_text?: string | null
  source_chunk_id?: string | null
  review_status?: string | null
}

export interface GraphCandidateBatch {
  id: string
  mode: string
  status: string
  created_at: string
  reviewed_at?: string | null
  applied_at?: string | null
  error_text?: string | null
  filters: Record<string, any>
  stats: Record<string, any>
}

export interface GraphCandidateItem {
  id: string
  batch_id: string
  candidate_kind: string
  status: 'pending' | 'approved' | 'rejected' | 'applied' | string
  payload: Record<string, any>
  evidence_text?: string | null
  source_chunk_id?: string | null
  rejection_reason?: string | null
  reviewed_at?: string | null
  applied_at?: string | null
  applied_target_id?: string | null
  created_at: string
}

export interface GraphCandidateReviewRequest {
  approve_all?: boolean
  approve_ids?: string[]
  reject_ids?: string[]
  reason?: string | null
}

export interface GraphCandidateReviewResponse {
  batch_id: string
  updated_candidates: number
  batch_status: string
}

export interface GraphCandidateApplyResponse {
  batch_id: string
  status: string
  applied_candidates: number
}

export interface GraphQualityReport {
  ok: boolean
  errors: string[]
  warnings: string[]
  stats: Record<string, any>
}

export interface IngestionDecision {
  file_name: string
  file_path: string
  file_hash: string
  status: 'queued' | 'excluded'
  reason_code: string
  locator?: string | null
  message: string
  created_at: string
}

export interface QualityMetrics {
  total_chunks: number
  approved_ratio: number
  pending_chunks: number
  isolated_entities: number
  isolated_chunks: number
  duplicate_ratio: number
  no_result_ratio_7d: number
  satisfaction_ratio_7d: number
}

export interface QualityAlert {
  type: 'negative_feedback' | 'duplicate' | string
  chunk_id: string
  source_file: string
  down_count: number
  reason: string
}

export interface QualityDashboardData {
  metrics: QualityMetrics
  alerts: QualityAlert[]
}

export interface UserFeedbackPayload {
  user_id?: string
  query_text?: string
  answer_text?: string
  referenced_chunk_ids?: string[]
  rating: 'up' | 'down'
  reason?: string
  trace_id?: string
  feedback_scope?: 'answer' | 'chunk'
  target_chunk_id?: string
}

export interface UserFeedbackResult {
  feedback_id: string
  rating: string
  triggered_chunks: Array<{
    chunk_id: string
    down_count: number
    reason: string
  }>
  message: string
}
