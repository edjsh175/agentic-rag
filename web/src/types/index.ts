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
