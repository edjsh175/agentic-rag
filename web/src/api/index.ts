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
import type { ChunkStats, Stats, ScanResult } from '../types'

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
}

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

// ---- 接口实现 ----

/** 健康检查 */
export async function healthCheck(signal?: AbortSignal) {
  return getJSON<HealthResult>('/health', signal)
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
) {
  return postJSON<QueryResult>('/query', { question, history, llm_model: llmModel, kb_name: kbName, thinking, web_search: webSearch, agent_prompt: agentPrompt, allow_general_knowledge: allowGeneralKnowledge }, signal)
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
    onSources: (sources: any[]) => void
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
) {
  const res = await fetch('/api/query/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, history, llm_model: llmModel, kb_name: kbName, thinking, web_search: webSearch, agent_prompt: agentPrompt, allow_general_knowledge: allowGeneralKnowledge }),
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
          } else if (event.type === 'sources') {
            callbacks.onSources(event.data)
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
export async function uploadDocument(file: File, kbName?: string, signal?: AbortSignal) {
  const form = new FormData()
  form.append('file', file)
  if (kbName) form.append('kb_name', kbName)
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
}

/** 从服务器加载聊天记录 */
export async function loadServerChat(fingerprint: string) {
  try {
    const { data } = await http.get<{ messages: StoredMessage[] }>('/chat/history', {
      headers: { 'X-Device-Fingerprint': fingerprint },
    })
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
