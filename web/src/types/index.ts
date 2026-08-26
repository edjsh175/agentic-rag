/** 消息角色 */
export type Role = 'user' | 'assistant'

export type WorkMode = 'agent' | 'linear'
export type ToolProgress = 'PROGRESS' | 'NO_PROGRESS' | 'DENIED' | 'ERROR'

export interface UnderstandingEventData {
  task_type?: string
  identity_status?: string
  entity?: string
  summary: string
}

export interface LLMReasoningEventData {
  call_id: string
  role: 'main' | 'helper' | string
  stage?: string
  model?: string
  provider?: string
  step?: number
  delta?: string
  reasoning_available?: boolean
  elapsed_ms?: number
  error?: string
}

export interface PublicExplanationEventData {
  call_id: string
  role: 'main' | 'helper' | string
  stage: string
  model?: string | null
  provider?: string | null
  text: string
  source: 'model_protocol' | 'model_generated' | 'system_fallback' | string
  fallback_used?: boolean
}

export interface DecisionEventData {
  step?: number
  action: string
  tool?: string | null
  reason: string
  gap?: string | null
  expected_gain?: string | null
  source?: string
}

export interface GuardEventData {
  allowed: boolean
  reason?: string | null
  message: string
  tool?: string | null
  step?: number
}

export interface ToolStartEventData {
  name: string
  arguments?: Record<string, unknown>
  step?: number
  source?: string
  target?: string | null
  gap?: string | null
  expected_gain?: string | null
}

export interface ToolResultEventData extends ToolStartEventData {
  ok?: boolean
  elapsed_ms?: number
  summary?: string
  error?: string | null
  fallback?: string | null
  status?: ToolProgress
  progress?: ToolProgress
  data?: unknown
  evidence_delta?: EvidenceUpdateEventData
}

export interface EvidenceUpdateEventData {
  new_chunks: number
  new_entities: number
  new_relations: number
  evidence_version_before?: number
  evidence_version_after?: number
  coverage?: string
  status?: ToolProgress
  step?: number
}

export interface EvidenceGapEventData {
  coverage: string
  missing_facts?: string[]
  missing_relations?: string[]
  reason?: string
  step?: number
}

export interface FinalizationCheckEventData {
  coverage: string
  admissibility: string
  message: string
  reason?: string
  gaps?: unknown[]
  step?: number
  forced?: boolean
}

export interface CandidateStatusEventData {
  version: number
  status: string
  message: string
}

export interface GroundingReviewStartedEventData {
  review_count: number
  candidate_version: number
  message: string
}

export interface ClaimReviewEventData {
  claim_id?: string
  claim?: string
  statement?: string
  claim_type?: string
  status: string
  evidence_ids?: number[]
  reason?: string
}

export interface ReviewStatusEventData {
  reviewer_role?: 'helper_llm'
  review_count: number
  verdict: string
  coverage?: string | number
  message: string
  summary?: string
  claim_reviews?: ClaimReviewEventData[]
  rewrite_actions?: RewriteActionEventData[]
  claim_count?: number
  unsupported_count?: number
  contradicted_count?: number
  error?: string | null
}

export interface RewriteActionEventData {
  claim_id: string
  action: string
  instruction?: string
}

export interface RewriteStatusEventData {
  status: 'started' | 'completed' | 'failed'
  mode?: string
  message?: string
  candidate_version?: number
  error?: string
}

export interface PublicationEventData {
  final_mode: string
  review_verdict: string
  coverage: string
  message: string
  published_candidate_attempt?: number | null
}

export interface ExecutionErrorEventData {
  message: string
  code?: string
  stage?: string
  /** 迁移期兼容旧字段。 */
  phase?: string
  recoverable?: boolean
}

export type KnowledgeStreamEvent =
  | { type: 'understanding'; data: UnderstandingEventData }
  | { type: 'llm_reasoning_start' | 'llm_reasoning_delta' | 'llm_reasoning_end'; data: LLMReasoningEventData }
  | { type: 'public_explanation'; data: PublicExplanationEventData }
  | { type: 'decision'; data: DecisionEventData }
  | { type: 'guard'; data: GuardEventData }
  | { type: 'tool_start'; data: ToolStartEventData }
  | { type: 'tool_result' | 'tool_end'; data: ToolResultEventData }
  | { type: 'evidence_update'; data: EvidenceUpdateEventData }
  | { type: 'evidence_gap'; data: EvidenceGapEventData }
  | { type: 'finalization_check'; data: FinalizationCheckEventData }
  | { type: 'candidate_status'; data: CandidateStatusEventData }
  | { type: 'helper_grounding_review_started'; data: GroundingReviewStartedEventData }
  | { type: 'review_status'; data: ReviewStatusEventData }
  | { type: 'rewrite_status'; data: RewriteStatusEventData }
  | { type: 'publication'; data: PublicationEventData }
  | { type: 'error'; data: ExecutionErrorEventData | string }
  | { type: 'token' | 'status' | 'thinking' | 'final_answer' | 'notice'; data: string }
  | { type: 'decision'; data: DecisionEventData }
  | { type: 'guard'; data: GuardEventData }
  | { type: 'tool_start'; data: ToolStartEventData }
  | { type: 'tool_result' | 'tool_end'; data: ToolResultEventData }
  | { type: 'evidence_update'; data: EvidenceUpdateEventData }
  | { type: 'evidence_gap'; data: EvidenceGapEventData }
  | { type: 'finalization_check'; data: FinalizationCheckEventData }
  | { type: 'candidate_status'; data: CandidateStatusEventData }
  | { type: 'helper_grounding_review_started'; data: GroundingReviewStartedEventData }
  | { type: 'review_status'; data: ReviewStatusEventData }
  | { type: 'rewrite_status'; data: RewriteStatusEventData }
  | { type: 'publication'; data: PublicationEventData }
  | { type: 'error'; data: ExecutionErrorEventData | string }
  | { type: 'token' | 'status' | 'thinking' | 'final_answer' | 'notice'; data: string }
  | { type: 'sources'; data: SourceDoc[] }
  | { type: 'trace'; data: string | { trace_id: string } }
  | { type: 'pipeline'; data: PipelineStep }
  | { type: 'clarify'; data: ClarifyResult }
  | { type: 'heartbeat'; phase?: string }
  | { type: 'answer_generation_started'; data?: unknown }
  | { type: 'done'; data?: unknown }

/** Agent 用户可见 Block 流基础模型 */
export interface BaseBlock {
  id: string
  kind: 'reasoning' | 'tool' | 'activity' | 'system_event' | 'markdown'
  type?: 'reasoning' | 'tool' | 'activity' | 'system_event' | 'markdown'
  sequence: number
}

export interface ReasoningBlock extends BaseBlock {
  kind: 'reasoning'
  type?: 'reasoning'
  callId: string
  stage: 'agent_controller' | 'answer_generation' | 'grounded_retry' | string
  role: 'main'
  model?: string
  provider?: string
  contentSource?: 'native_reasoning' | 'public_explanation'
  explanationSource?: string
  text: string
  content?: string
  status: 'running' | 'completed' | 'unavailable' | 'error'
  isStreaming?: boolean
  duration?: string
  elapsedMs?: number
}

export interface ToolBlock extends BaseBlock {
  kind: 'tool'
  type?: 'tool'
  toolCallKey: string
  tool: string
  toolName?: string
  label: string
  description?: string
  input?: unknown
  output?: unknown
  in?: unknown
  out?: unknown
  status: 'running' | 'completed' | 'failed' | 'denied'
  isStreaming?: boolean
  elapsedMs?: number
  error?: string | null
  gap?: string | null
  expectedGain?: string | null
}

export interface ActivityBlock extends BaseBlock {
  kind: 'activity'
  type?: 'activity'
  activity: 'grounding_review'
  reviewCount: number
  candidateVersion?: number
  status: 'running' | 'completed' | 'warning' | 'failed'
  text: string
  startedAt?: number
  elapsedMs?: number
}

export interface SystemEventBlock extends BaseBlock {
  kind: 'system_event'
  type?: 'system_event'
  event: string
  level: 'info' | 'warning' | 'error'
  text: string
  status?: 'active' | 'completed' | 'failed'
  correlationId?: string
}

export interface MarkdownBlock extends BaseBlock {
  kind: 'markdown'
  type?: 'markdown'
  text: string
  markdown?: string
  status: 'final'
}

export type AssistantBlock =
  | ReasoningBlock
  | ToolBlock
  | ActivityBlock
  | SystemEventBlock
  | MarkdownBlock

/** Agent 工具调用记录 (兼容保留) */
export interface AgentToolCall {
  name: string
  step?: number
  ok?: boolean
  elapsed_ms?: number
  summary?: string
  error?: string | null
  fallback?: string | null
  arguments?: Record<string, any>
  observation?: any
  status?: 'running' | 'success' | 'error' | 'denied'
  gap?: string | null
  expected_gain?: string | null
  progress?: ToolProgress
}

/** 时序流单个节点（支持理解、决策、守卫、工具、证据、审核、重写、发布与思考块） */
export type AgentTimelineItem =
  | {
      type: 'think'
      eventKey?: string
      content: string
      duration?: string
      isThinking?: boolean
      _startTime?: number
      callId?: string
      role?: string
      stage?: string
      model?: string
      provider?: string
      reasoningAvailable?: boolean
    }
  | {
      type: 'understanding'
      eventKey?: string
      task_type?: string
      identity_status?: string
      entity?: string
      summary: string
    }
  | {
      type: 'decision'
      eventKey?: string
      step?: number
      action: string
      tool?: string | null
      reason: string
      gap?: string | null
      expected_gain?: string | null
      source?: string
    }
  | {
      type: 'guard'
      eventKey?: string
      allowed: boolean
      reason?: string | null
      message: string
      tool?: string | null
      step?: number
    }
  | {
      type: 'tool_call'
      eventKey?: string
      tool: string
      label?: string
      description?: string
      in?: any
      out?: any
      status?: 'running' | 'completed' | 'failed' | 'denied'
      progress?: ToolProgress
      elapsed_ms?: number
      exitCode?: number
      source?: string
      error?: string | null
      step?: number
      gap?: string | null
      expected_gain?: string | null
      evidence_delta?: any
    }
  | {
      type: 'evidence_update'
      eventKey?: string
      new_chunks: number
      new_entities: number
      new_relations: number
      evidence_version_before?: number
      evidence_version_after?: number
      coverage?: string
      status?: string
    }
  | {
      type: 'evidence_gap'
      eventKey?: string
      coverage: string
      missing_facts?: string[]
      missing_relations?: string[]
      reason?: string
    }

  | {
      type: 'finalization_check'
      eventKey?: string
      coverage: string
      admissibility: string
      message: string
      reason?: string
      gaps?: any[]
      forced?: boolean
    }
  | {
      type: 'candidate_status'
      eventKey?: string
      version: number
      status: string
      message: string
    }
  | {
      type: 'review_status'
      eventKey?: string
      review_count: number
      verdict: string
      coverage: string
      message: string
      summary?: string
      claim_reviews?: ClaimReviewEventData[]
      rewrite_actions?: RewriteActionEventData[]
      error?: string | null
    }
  | {
      type: 'rewrite_status'
      eventKey?: string
      status: 'started' | 'completed' | 'failed'
      mode?: string
      message?: string
      candidate_version?: number
      error?: string
    }
  | {
      type: 'publication'
      eventKey?: string
      final_mode: string
      review_verdict: string
      coverage: string
      message: string
    }
  | {
      type: 'helper_grounding_review_started'
      eventKey?: string
      review_count: number
      candidate_version: number
      message: string
    }
  | {
      type: 'error'
      eventKey?: string
      message: string
      code?: string
      stage?: string
      phase?: string
      recoverable?: boolean
    }
  | {
      type: 'context_inject'
      eventKey?: string
      label: string
      details: string
    }
  | {
      type: 'notice'
      eventKey?: string
      content: string
      level?: 'info' | 'warning' | 'error'
    }


/** 单条聊天消息 */
export interface Message {
  id: string
  role: Role
  content: string
  /** 生成该消息时使用的运行模式；用于历史消息保持原展示语义 */
  mode?: WorkMode
  /** 用户消息携带的图片（data URL） */
  imageUrl?: string
  /** assistant 消息携带的来源文档 */
  sources?: SourceDoc[]
  /** 是否正在生成（打字动画） */
  loading?: boolean
  /** 仅用于当前流式请求，不持久化 */
  status?: string
  /** Linear 模式下由模型返回的推理内容 */
  thinking?: string
  /** Agent 用户可见 Block 流（Reasoning / Tool / System Event / Markdown 唯一来源） */
  blocks?: AssistantBlock[]
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
  agent?: any
  stages_ms?: Record<string, number>
}

/** 会话摘要（列表展示） */
export interface ChatSessionSummary {
  id: string
  title: string
  created_at?: string
  updated_at?: string
  message_count?: number
}

/** 会话详情（含消息） */
export interface ChatSessionDetail {
  id: string
  title: string
  created_at?: string
  updated_at?: string
  messages: Message[]
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
  /** backbone | model_suggested | fixed_other | task_exit | rollback_static */
  source?: string
  canonical_name?: string
  entity_type?: string
  binding_status?: string
  score?: number
}

export type ClarificationSelectionKind = 'option' | 'other' | 'free_text'

export interface ClarificationSelection {
  option: ClarificationOption
  kind: ClarificationSelectionKind
  freeText?: string
}

export interface ClarificationCallbackRequest {
  optionId: string
  options: ClarificationOption[]
  selectionKind: ClarificationSelectionKind
  freeText?: string
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
  otherText?: string
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
    scope_root?: string
    scope_binding_strength?: string
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

/** GPU 显存监控（gpu-agent sidecar） */
export interface GpuMetrics {
  name: string
  total_mib: number
  used_mib: number
  free_mib: number
  utilization?: number
  temperature?: number
  power_draw?: number | null
}

export interface GpuModelFit {
  name: string
  footprint_gib?: number | null
  fits?: boolean | null
}

export interface GpuStatus {
  enabled: boolean
  gpu: GpuMetrics | null
  current_model: string
  recommended_model?: string
  fallback_model?: string
  models: GpuModelFit[]
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
  'StampServer', 'StampTools', 'StampWebRTC', 'StampWebGL', '实景三维', '耕地保护',
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
  created_by?: string | null
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

export interface QaTraceRequest {
  question: string
  collection_name?: string | null
  kb_name?: string | null
  doc_category?: string | null
  entity_name?: string | null
  llm_model?: string | null
  vision_model?: string | null
  thinking?: boolean | null
  web_search?: boolean | null
  allow_general_knowledge?: boolean | null
  agent_prompt?: string | null
  pinned_chunk_ids?: string[]
  excluded_chunk_ids?: string[]
  history_rounds?: number
  clarification_question?: string | null
  clarification_selected?: string | null
  clarification_option_id?: string | null
  clarification_options?: ClarificationOption[] | null
  clarification_selection_kind?: ClarificationSelectionKind | null
  clarification_free_text?: string | null
  [key: string]: unknown
}

export interface RetrievalTraceSnapshot {
  intent?: string
  applied_weights?: {
    bm25?: number
    vector?: number
    [key: string]: number | undefined
  }
  graph_expansion_hops?: number
  top_k?: number
  candidate_k?: number
  effective_mode?: string
  [key: string]: unknown
}

export interface AgentStepRecord {
  step: number
  decision?: {
    action?: string
    tool?: string | null
    arguments?: Record<string, unknown>
    thought?: string
    source?: string
  }
  observation?: {
    name?: string
    ok?: boolean
    elapsed_ms?: number
    summary?: string
    error?: string | null
    fallback?: string | null
    data?: Record<string, unknown>
  }
  terminal?: string
  denied?: string
}

export interface AgentTraceData {
  agent_steps?: AgentStepRecord[]
  tools?: Array<{
    name?: string
    ok?: boolean
    elapsed_ms?: number
    summary?: string
    error?: string | null
    fallback?: string | null
  }>
  route?: string
  conversation_context?: {
    version?: string
    topic_shift?: boolean
    entity_transition?: boolean
    head_entity?: string | null
    selected_entity?: string | null
    resolved_question?: string
    clarification_callback?: boolean
    linked_count?: number
    not_a_fact_source?: boolean
  }
  evidence_groups?: Array<{
    question_id?: string
    kind?: string
    retrieve_index?: number | null
    chunk_ids?: string[]
    status?: string
    head_entity?: string | null
    tool?: string | null
  }>
  budget?: {
    max_steps?: number
    max_retrieve_attempts?: number
    steps_used?: number
    retrieve_attempts?: number
  }
  fallback?: string[]
  retrieve_attempts?: number
  reuse?: boolean
  entity_link?: Record<string, unknown>
  gate?: string
  answer_gate?: {
    allow_knowledge_answer?: boolean
    reason?: string
  }
  evidence_gap?: Array<Record<string, unknown>>
  retrieve_improvement?: number | null
  retrieval_trace?: RetrievalTraceSnapshot
  clarify?: {
    needs_clarification?: boolean
    reason?: string
    option_count?: number
  }
}

export interface ClarifyTraceData {
  needs_clarification?: boolean
  ask_question?: string
  selected?: string | null
  options?: Array<{
    id: string
    label: string
    filter?: {
      doc_category?: string
      entity_name?: string
    }
  }>
}

export interface UnderstandingTraceData {
  mode?: string
  user_utterance?: string
  resolved_question?: string
  retrieval_queries?: Array<{ text: string; kind: string; weight: number }>
  filters?: Record<string, unknown>
  dialogue_focus?: string
  focus?: {
    topic?: string
    confirmed_entity?: string
    open_question?: string
    notes?: string
  }
  is_context_dependent?: boolean
  confidence?: number
  clarify?: unknown
  rationale?: string
}

export interface QaTraceCandidate extends Record<string, unknown> {
  chunk_id?: string
  source?: string
  section_title?: string
  kb_name?: string
  score?: number | string
  citation_id?: string
  matched_query_kinds?: string[]
  retrieval_source?: 'graph_only' | 'hybrid_hit' | 'text_only' | string
  content_preview?: string
}

export interface ExecutionTraceEvent {
  type: string
  data?: unknown
  sequence?: number
  elapsed_ms?: number
  [key: string]: unknown
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
  request: QaTraceRequest
  runtime: Record<string, unknown>
  stages: Record<string, number>
  plan: Record<string, unknown>
  retrieval: {
    query_hits?: unknown[]
    candidates?: QaTraceCandidate[]
    candidate_count?: number
    retrieval_trace?: RetrievalTraceSnapshot
  }
  understanding?: UnderstandingTraceData
  clarify?: ClarifyTraceData
  agent?: AgentTraceData
  execution_events?: ExecutionTraceEvent[]
  pack?: Record<string, unknown>
  evidence: EvidenceChain
  answer: {
    text?: string
    thinking?: string
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
