/**
 * API 调用层 —— 基于 axios 封装
 *
 * 统一处理：
 *   - 基础路径 / 超时 / 请求头
 *   - 响应自动解包
 *   - 错误统一格式化
 *
 * 流式接口 /query/stream 仍使用原生 fetch（axios 不适合 SSE）
 */
import axios from 'axios'
import { withDataCache, invalidateDataCache } from './cache'
export { withDataCache, invalidateDataCache }
import type {
  AdminChunkListResponse,
  AdminChunkUpdate,
  ChunkStats,
  DocCategory,
  ReviewMutationResponse,
  ReviewStatus,
  Stats,
  ScanResult,
  IngestionDecision,
  GraphData,
  EntityChunkDetail,
  GraphAliasItem,
  GraphAliasCreateRequest,
  GraphCandidateBatch,
  GraphCandidateItem,
  GraphCandidateReviewRequest,
  GraphCandidateReviewResponse,
  GraphCandidateApplyResponse,
  GraphQualityReport,
  GraphEntityUpsert,
  GraphEntityUpdate,
  GraphRelationCreate,
  ProductBackboneEntityPayload,
  ProductBackboneEntityUpdatePayload,
  ProductBackboneRelationPayload,
  QaDebugResult,
  QaTraceDetail,
  QaTraceListResult,
  ClarifyResult,
  MessageClarification,
} from '../types'

// ---- axios 实例 ----
const http = axios.create({
  baseURL: '/api',
  timeout: 120_000,           // 单次请求最大等待
})

// 响应拦截：统一解包 data，提取后端错误信息
http.interceptors.response.use(
  (res) => res,
  (err) => {
    const msg =
      err.response?.data?.detail     // FastAPI 错误格式
      || err.response?.data?.message
      || err.message
      || '请求失败'
    return Promise.reject(new Error(msg))
  },
)

// ---- 类型 ----

interface QueryResult {
  answer: string
  source_documents: any[]
}

interface UploadResult {
  message: string
  chunks_count: number
  file_name: string
  new_files: number
  skipped_files: number
  errors: number
  decisions?: IngestionDecision[]
}

export type DocumentProfile = 'section_based' | 'technical_manual' | 'procedure' | 'api_doc' | 'table_doc' | 'record_list'

export const DOCUMENT_PROFILE_OPTIONS: ReadonlyArray<{ value: DocumentProfile; label: string }> = [
  { value: 'section_based', label: '通用章节' },
  { value: 'technical_manual', label: '分层用户手册' },
  { value: 'procedure', label: '部署/操作步骤' },
  { value: 'api_doc', label: 'API/接口文档' },
  { value: 'table_doc', label: '表结构/配置字典' },
  { value: 'record_list', label: '问题/功能清单' },
]

interface HealthResult {
  status: string
  models: Record<string, string>
}

// ---- 通用 JSON 请求 ----
async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const { data } = await http.get<T>(url, { signal })
  return data
}

async function postJSON<T>(url: string, body?: any, signal?: AbortSignal): Promise<T> {
  const { data } = await http.post<T>(url, body, { signal })
  return data
}

export async function listAdminChunks(params: {
  review_status?: ReviewStatus | 'all'
  doc_category?: DocCategory | 'all'
  filename?: string
  page?: number
  page_size?: number
}, signal?: AbortSignal, forceRefresh = false) {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') query.set(key, String(value))
  })
  const url = `/admin/chunks?${query.toString()}`
  return withDataCache(url, () => getJSON<AdminChunkListResponse>(url, signal), 300000, forceRefresh)
}

export async function updateAdminChunk(chunkId: string, changes: AdminChunkUpdate) {
  const { data } = await http.patch<ReviewMutationResponse>(
    `/admin/chunks/${encodeURIComponent(chunkId)}`,
    changes,
  )
  invalidateDataCache('/admin/chunks')
  return data
}

export async function batchReviewChunks(chunkIds: string[], status: 'approved' | 'rejected') {
  const res = await postJSON<ReviewMutationResponse>('/admin/chunks/batch-review', {
    chunk_ids: chunkIds,
    status,
  })
  invalidateDataCache('/admin/chunks')
  return res
}

// ---- 接口实现 ----

/** 健康检查 */
export async function healthCheck(signal?: AbortSignal) {
  return getJSON<HealthResult>('/health', signal)
}

/** 问题歧义预检（反问卡片） */
export async function queryClarify(
  question: string,
  docCategory?: string,
  kbName?: string,
  signal?: AbortSignal,
) {
  const docCat = docCategory && docCategory !== '全部' ? docCategory : undefined
  const kb = kbName && kbName !== '全部知识库' ? kbName : undefined
  const { data } = await http.post<ClarifyResult>(
    '/query/clarify',
    {
      question,
      doc_category: docCat,
      kb_name: kb,
    },
    { signal },
  )
  return data
}

/** 文字问答（带历史 + 可选模型选择） */
export async function queryKnowledge(
  question: string,
  history?: { role: string; content: string }[],
  llmModel?: string,
  kbName?: string,
  thinking?: boolean,
  webSearch?: boolean,
  signal?: AbortSignal,
  agentPrompt?: string,
  allowGeneralKnowledge?: boolean,
  docCategory?: string,
  entityName?: string,
) {
  const { data } = await http.post<QueryResult>(
    '/query',
    {
      question,
      history,
      llm_model: llmModel,
      kb_name: kbName,
      doc_category: docCategory,
      entity_name: entityName,
      thinking,
      web_search: webSearch,
      agent_prompt: agentPrompt,
      allow_general_knowledge: allowGeneralKnowledge,
    },
    { signal, timeout: 600_000 },
  )
  return data
}

/**
 * 流式问答 — 通过回调接收 token / sources / done
 * 使用原生 fetch（SSE 不适合 axios）
 */
export async function queryKnowledgeStream(
  question: string,
  history: { role: string; content: string }[],
  callbacks: {
    onToken: (token: string) => void
    onStatus?: (status: string) => void
    onThinking?: (thought: string) => void
    onFinalAnswer?: (answer: string) => void
    onSources: (sources: any[]) => void
    onTrace?: (traceId: string) => void
    onPipeline?: (pipelineData: any) => void
    onDone: () => void
    onError: (err: Error) => void
  },
  llmModel?: string,
  kbName?: string,
  thinking?: boolean,
  webSearch?: boolean,
  signal?: AbortSignal,
  agentPrompt?: string,
  allowGeneralKnowledge?: boolean,
  docCategory?: string,
  entityName?: string,
  pinnedChunkIds?: string[],
  excludedChunkIds?: string[],
) {
  const res = await fetch('/api/query/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      history,
      llm_model: llmModel,
      kb_name: kbName,
      doc_category: docCategory,
      entity_name: entityName,
      thinking,
      web_search: webSearch,
      agent_prompt: agentPrompt,
      allow_general_knowledge: allowGeneralKnowledge,
      pipeline_events: true,
      pinned_chunk_ids: pinnedChunkIds,
      excluded_chunk_ids: excludedChunkIds,
    }),
    signal,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `请求失败 (${res.status})`)
  }

  const reader = res.body?.getReader()
  if (!reader) throw new Error('浏览器不支持 ReadableStream')

  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const raw = line.slice(6).trim()
        if (!raw) continue
        if (raw === '[DONE]') { callbacks.onDone(); continue }

        try {
          const event = JSON.parse(raw)
          if (event.type === 'token') {
            callbacks.onToken(event.data)
          } else if (event.type === 'status') {
            callbacks.onStatus?.(event.data)
          } else if (event.type === 'thinking') {
            callbacks.onThinking?.(event.data)
          } else if (event.type === 'final_answer') {
            callbacks.onFinalAnswer?.(event.data)
          } else if (event.type === 'sources') {
            callbacks.onSources(event.data)
          } else if (event.type === 'trace') {
            callbacks.onTrace?.(event.data)
          } else if (event.type === 'pipeline') {
            callbacks.onPipeline?.(event.data)
          } else if (event.type === 'done') {
            callbacks.onDone()
          }
        } catch { /* skip malformed lines */ }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/** 图片问答（流式 SSE） */
export async function queryImageStream(
  question: string,
  imageFile: Blob,
  callbacks: {
    onToken: (token: string) => void
    onDone: () => void
    onError: (err: Error) => void
  },
  visionModel?: string,
  signal?: AbortSignal,
) {
  const form = new FormData()
  form.append('image', imageFile, 'image.png')
  form.append('question', question)
  if (visionModel) form.append('vision_model', visionModel)

  const res = await fetch('/api/query/image', {
    method: 'POST',
    body: form,
    signal,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `请求失败 (${res.status})`)
  }

  const reader = res.body?.getReader()
  if (!reader) throw new Error('浏览器不支持 ReadableStream')

  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const raw = line.slice(6).trim()
        if (!raw) continue
        if (raw === '[DONE]') { callbacks.onDone(); continue }
        try {
          const event = JSON.parse(raw)
          if (event.type === 'token') callbacks.onToken(event.data)
          else if (event.type === 'done') callbacks.onDone()
        } catch { /* skip */ }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/** 知识库统计 */
export async function getStats(signal?: AbortSignal) {
  return getJSON<Stats>('/stats', signal)
}

/** Chunk 级深度统计 */
export async function getChunkStats(signal?: AbortSignal) {
  return getJSON<ChunkStats>('/stats/chunks', signal)
}

/** 手动扫描 */
export async function triggerScan(signal?: AbortSignal) {
  return postJSON<ScanResult>('/scan', {}, signal)
}

/** 文件索引 */
export async function getIndex(signal?: AbortSignal) {
  return getJSON<{ total_files: number; files: any[] }>('/scan/index', signal)
}

/** 模型列表 */
export interface ModelInfo {
  name: string
  type: 'llm' | 'vision' | 'embedding'
}

export interface ModelsResponse {
  models: ModelInfo[]
  current: {
    llm: string
    embedding: string
    vision: string
  }
}

export async function getModels(signal?: AbortSignal) {
  return getJSON<ModelsResponse>('/models', signal)
}

/** 切换向量模型（需要随后重建知识库） */
export async function setEmbeddingModel(model: string) {
  const form = new FormData()
  form.append('model', model)
  const res = await fetch('/api/config/embedding-model', { method: 'POST', body: form })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || '设置失败')
  }
  return res.json() as Promise<{ message: string }>
}

/** 上传文档到知识库 */
export async function uploadDocument(
  file: File,
  kbName?: string,
  documentProfile: DocumentProfile = 'section_based',
  signal?: AbortSignal,
) {
  const form = new FormData()
  form.append('file', file)
  if (kbName) form.append('kb_name', kbName)
  form.append('document_profile', documentProfile)
  const { data } = await http.post<UploadResult>('/upload', form, { signal })
  return data
}

// ============================================================
// 博客爬取
// ============================================================

/** 获取知识库列表 */
export async function getKnowledgeBases(signal?: AbortSignal) {
  return getJSON<{ bases: string[] }>('/knowledge-bases', signal)
}

/** 智能体预设列表 */
export interface AgentInfo {
  id: string
  name: string
  icon: string
  description: string
  system_prompt: string
}

export async function getAgents(signal?: AbortSignal) {
  return getJSON<{ agents: AgentInfo[] }>('/agents', signal)
}

import type { CrawlResult, BlogPostList } from '../types'

/** 统一爬取入口，自动识别平台 */
export async function crawl(url: string) {
  return postJSON<CrawlResult>('/crawl', { url })
}

/** 列出已保存的博客文章（支持搜索、分页、平台筛选） */
export async function listBlogPosts(params?: {
  page?: number
  page_size?: number
  q?: string
  platform?: string
}) {
  const query = new URLSearchParams()
  if (params) {
    if (params.page) query.set('page', String(params.page))
    if (params.page_size) query.set('page_size', String(params.page_size))
    if (params.q) query.set('q', params.q)
    if (params.platform) query.set('platform', params.platform)
  }
  const qs = query.toString()
  return getJSON<BlogPostList>(`/blog/posts${qs ? '?' + qs : ''}`)
}

/** 获取单篇博客文章内容 */
export async function getBlogPost(filename: string) {
  return getJSON<{ filename: string; content: string; file_path: string }>(`/blog/posts/${encodeURIComponent(filename)}`)
}

/** 删除博客文章（同时清理向量数据） */
export async function deleteBlogPost(filename: string) {
  const { data } = await http.delete(`/blog/posts/${encodeURIComponent(filename)}`)
  return data
}

/** 发布博客文章：调用 addRag API → 移入监视目录 → 触发扫描入库 */
export async function publishBlogPost(filename: string) {
  const { data } = await http.post(`/blog/publish/${encodeURIComponent(filename)}`)
  return data as { message: string; new_files: number; skipped_files: number; errors: number }
}

/** 同步博客发布系统的已发布文章 */
export async function syncPublishedPosts() {
  return postJSON<{
    new: number; updated: number; skipped: number; deleted: number; message: string
  }>('/blog/sync')
}

// ============================================================
// 聊天记录（服务端持久化）
// ============================================================

interface StoredMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  hasImage?: boolean
  sources?: any[]
  clarification?: MessageClarification
}

/** 从服务器加载聊天记录；无记录时返回 null，便于回退 localStorage */
export async function loadServerChat(fingerprint: string) {
  try {
    const { data } = await http.get<{ messages: StoredMessage[] }>('/chat/history', {
      headers: { 'X-Device-Fingerprint': fingerprint },
    })
    if (!data.messages?.length) return null
    return data.messages
  } catch (e: any) {
    if (e.response?.status === 404) return null
    throw e
  }
}

/** 保存聊天记录到服务器 */
export async function saveServerChat(fingerprint: string, messages: StoredMessage[]) {
  await http.put('/chat/history', { messages }, {
    headers: { 'X-Device-Fingerprint': fingerprint },
  })
}

/** 删除服务端聊天记录 */
export async function deleteServerChat(fingerprint: string) {
  await http.delete('/chat/history', {
    headers: { 'X-Device-Fingerprint': fingerprint },
  })
}

// ============================================================
// 知识图谱管理 API
// ============================================================

/** 获取知识图谱数据 */
export async function getGraphData(docCategory?: string, forceRefresh = false) {
  const query = docCategory && docCategory !== 'all'
    ? `?doc_category=${encodeURIComponent(docCategory)}`
    : ''
  const url = `/admin/knowledge_graph/data${query}`
  return withDataCache(url, () => getJSON<GraphData>(url), 300000, forceRefresh)
}

export async function queryAdminDebug(question: string, signal?: AbortSignal) {
  // 非流式全链路（检索+生成）在 CPU/大模型下常超过 2 分钟
  const { data } = await http.post<QaDebugResult>(
    '/admin/qa-debug',
    { question },
    { signal, timeout: 600_000 },
  )
  return data
}

/** 问答调试流式：逐步推送 plan / 检索 / 证据等 pipeline 事件 */
export async function queryAdminDebugStream(
  question: string,
  callbacks: {
    onStatus?: (status: string) => void
    onPipeline?: (data: any) => void
    onToken?: (token: string) => void
    onFinalAnswer?: (answer: string) => void
    onSources?: (sources: any[]) => void
    onTrace?: (traceId: string) => void
    onDone?: () => void
    onError?: (err: Error) => void
  },
  signal?: AbortSignal,
  docCategory?: string,
  entityName?: string,
  clarificationQuestion?: string,
  clarificationSelected?: string,
) {
  const res = await fetch('/api/admin/qa-debug/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      doc_category: docCategory,
      entity_name: entityName,
      clarification_question: clarificationQuestion,
      clarification_selected: clarificationSelected,
    }),
    signal,
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `请求失败 (${res.status})`)
  }
  const reader = res.body?.getReader()
  if (!reader) throw new Error('浏览器不支持 ReadableStream')
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const raw = line.slice(6).trim()
        if (!raw || raw === '[DONE]') continue
        try {
          const event = JSON.parse(raw)
          if (event.type === 'status') callbacks.onStatus?.(event.data)
          else if (event.type === 'pipeline') callbacks.onPipeline?.(event.data)
          else if (event.type === 'token') callbacks.onToken?.(event.data)
          else if (event.type === 'final_answer') callbacks.onFinalAnswer?.(event.data)
          else if (event.type === 'sources') callbacks.onSources?.(event.data)
          else if (event.type === 'trace') callbacks.onTrace?.(event.data?.trace_id || event.data)
          else if (event.type === 'done') callbacks.onDone?.()
        } catch {
          /* skip */
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export async function listQaTraces(params: {
  limit?: number
  offset?: number
  q?: string
  errors_only?: boolean
} = {}, signal?: AbortSignal) {
  const query = new URLSearchParams()
  if (params.limit != null) query.set('limit', String(params.limit))
  if (params.offset != null) query.set('offset', String(params.offset))
  if (params.q) query.set('q', params.q)
  if (params.errors_only) query.set('errors_only', 'true')
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return getJSON<QaTraceListResult>(`/admin/qa-traces${suffix}`, signal)
}

export async function getQaTrace(traceId: string, signal?: AbortSignal) {
  return getJSON<QaTraceDetail>(`/admin/qa-traces/${encodeURIComponent(traceId)}`, signal)
}

export async function deleteQaTrace(traceId: string) {
  const { data } = await http.delete<{ ok: boolean; trace_id: string }>(
    `/admin/qa-traces/${encodeURIComponent(traceId)}`,
  )
  return data
}

export async function updateQaTraceFeedback(traceId: string, feedback: 'useful' | 'unuseful' | null) {
  const { data } = await http.post<{ ok: boolean; trace_id: string; feedback: string | null }>(
    `/admin/qa-traces/${encodeURIComponent(traceId)}/feedback`,
    { feedback },
  )
  return data
}

export async function getProductBackbonePreview(forceRefresh = false) {
  const url = '/admin/knowledge_graph/product_backbone_preview'
  return withDataCache(url, () => getJSON<GraphData>(url), 300000, forceRefresh)
}

export async function getProductBackboneComplexPreview(forceRefresh = false) {
  const url = '/admin/knowledge_graph/product_backbone_preview_complex'
  return withDataCache(url, () => getJSON<GraphData>(url), 300000, forceRefresh)
}

export async function createProductBackboneEntity(payload: ProductBackboneEntityPayload) {
  const res = await postJSON<any>('/admin/knowledge_graph/product_backbone_preview/entities', payload)
  invalidateDataCache('/admin/knowledge_graph')
  return res
}

export async function updateProductBackboneEntity(entityId: string, payload: ProductBackboneEntityUpdatePayload) {
  const { data } = await http.patch(
    `/admin/knowledge_graph/product_backbone_preview/entities/${encodeURIComponent(entityId)}`,
    payload
  )
  invalidateDataCache('/admin/knowledge_graph')
  return data
}

export async function deleteProductBackboneEntity(entityId: string) {
  const { data } = await http.delete(`/admin/knowledge_graph/product_backbone_preview/entities/${encodeURIComponent(entityId)}`)
  invalidateDataCache('/admin/knowledge_graph')
  return data
}

export async function createProductBackboneRelation(payload: ProductBackboneRelationPayload) {
  const res = await postJSON<any>('/admin/knowledge_graph/product_backbone_preview/relations', payload)
  invalidateDataCache('/admin/knowledge_graph')
  return res
}

export async function deleteProductBackboneRelation(relationId: string) {
  const { data } = await http.delete(`/admin/knowledge_graph/product_backbone_preview/relations/${encodeURIComponent(relationId)}`)
  invalidateDataCache('/admin/knowledge_graph')
  return data
}

/** 获取实体关联的证据 Chunk */
export async function getEntityChunks(entityId: string) {
  return getJSON<EntityChunkDetail[]>(`/admin/knowledge_graph/entities/${encodeURIComponent(entityId)}/chunks`)
}

/** 删除实体（级联删除关系和链接） */
export async function deleteEntity(entityId: string) {
  const { data } = await http.delete(`/admin/knowledge_graph/entities/${encodeURIComponent(entityId)}`)
  return data
}

/** 删除特定关系边 */
export async function deleteRelation(relationId: string) {
  const { data } = await http.delete(`/admin/knowledge_graph/relations/${encodeURIComponent(relationId)}`)
  return data
}

/** 解除实体与 Chunks 的证据关联 */
export async function deleteEntityChunkLink(entityId: string, chunkId: string) {
  const { data } = await http.delete(`/admin/knowledge_graph/entities/${encodeURIComponent(entityId)}/chunks/${encodeURIComponent(chunkId)}`)
  return data
}

export async function createGraphEntity(payload: GraphEntityUpsert) {
  return postJSON<any>('/admin/knowledge_graph/entities', payload)
}

export async function updateGraphEntity(entityId: string, payload: GraphEntityUpdate) {
  const { data } = await http.patch(`/admin/knowledge_graph/entities/${encodeURIComponent(entityId)}`, payload)
  return data
}

export async function createGraphRelation(payload: GraphRelationCreate) {
  return postJSON<any>('/admin/knowledge_graph/relations', payload)
}

export async function linkEntityChunk(entityId: string, chunkId: string, linkType: string) {
  return postJSON<any>(`/admin/knowledge_graph/entities/${encodeURIComponent(entityId)}/chunks`, {
    chunk_id: chunkId,
    link_type: linkType,
  })
}

export async function listEntityAliases(entityId: string) {
  return getJSON<GraphAliasItem[]>(`/admin/knowledge_graph/entities/${encodeURIComponent(entityId)}/aliases`)
}

export async function createEntityAlias(entityId: string, payload: GraphAliasCreateRequest) {
  return postJSON<GraphAliasItem>(`/admin/knowledge_graph/entities/${encodeURIComponent(entityId)}/aliases`, payload)
}

export async function deleteEntityAlias(aliasId: string) {
  const { data } = await http.delete(`/admin/knowledge_graph/aliases/${encodeURIComponent(aliasId)}`)
  return data
}

export async function listGraphCandidateBatches(status?: string, forceRefresh = false) {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  const url = `/admin/graph-candidates/batches${query}`
  return withDataCache(url, () => getJSON<GraphCandidateBatch[]>(url), 300000, forceRefresh)
}

export async function listGraphCandidateItems(batchId: string, status?: string, forceRefresh = false) {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  const url = `/admin/graph-candidates/batches/${encodeURIComponent(batchId)}/candidates${query}`
  return withDataCache(url, () => getJSON<GraphCandidateItem[]>(url), 300000, forceRefresh)
}

export async function reviewGraphCandidates(batchId: string, payload: GraphCandidateReviewRequest) {
  const res = await postJSON<GraphCandidateReviewResponse>(`/admin/graph-candidates/batches/${encodeURIComponent(batchId)}/review`, payload)
  invalidateDataCache('/admin/graph-candidates')
  return res
}

export async function applyGraphCandidateBatch(batchId: string) {
  const res = await postJSON<GraphCandidateApplyResponse>(`/admin/graph-candidates/batches/${encodeURIComponent(batchId)}/apply`)
  invalidateDataCache('/admin/graph-candidates')
  return res
}

export async function getGraphCandidateQuality(batchId: string, forceRefresh = false) {
  const url = `/admin/graph-candidates/batches/${encodeURIComponent(batchId)}/quality`
  return withDataCache(url, () => getJSON<GraphQualityReport>(url), 300000, forceRefresh)
}

import type { QualityDashboardData, UserFeedbackPayload, UserFeedbackResult } from '../types'

/** 获取质量控制仪表盘数据与预警列表 */
export async function getQualityDashboard(forceRefresh = false) {
  const url = '/quality/dashboard'
  return withDataCache(url, () => getJSON<QualityDashboardData>(url), 300000, forceRefresh)
}

/** 提交用户反馈并自动触发差评重审闭环 */
export async function submitUserFeedback(payload: UserFeedbackPayload) {
  const res = await postJSON<UserFeedbackResult>('/feedback', payload)
  invalidateDataCache('/quality/dashboard')
  return res
}

/** 手动触发 SimHash 文本重复块检测 */
export async function triggerDuplicateCheck() {
  const res = await postJSON<{ duplicate_count: number; duplicates: any[] }>('/quality/detect-duplicates')
  invalidateDataCache('/quality/dashboard')
  return res
}
