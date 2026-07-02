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
    file_name?: string
    page_label?: string
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
