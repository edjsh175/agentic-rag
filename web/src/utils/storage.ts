/**
 * 聊天记录持久化 —— 服务器 JSON + 浏览器 localStorage / IndexedDB 多会话存储
 *
 * 加载策略：服务器优先 → localStorage（兼容旧数据）
 * 保存策略：服务器为主，localStorage 为回退
 * 图片策略：base64 仅存浏览器 IndexedDB，服务器存 hasImage 标记
 */
import type { Message as ChatMessage, SourceDoc, MessageClarification, ChatSessionSummary, AssistantBlock } from '../types'
import { normalizeLegacyMessageToBlocks } from './agentBlockProjector'
import {
  fetchServerSessions,
  createServerSession,
  fetchServerSessionDetail,
  saveServerSession,
  renameServerSession,
  setActiveServerSession,
  deleteServerSession,
  loadServerChat,
  saveServerChat,
  deleteServerChat,
} from '../api'
import { getFingerprint } from './fingerprint'

// ---- localStorage 键名 ----
const SESSIONS_STORAGE_KEY = 'rag-knowledge-sessions'
const ACTIVE_SESSION_KEY = 'rag-knowledge-active-session-id'
const SESSION_MSGS_PREFIX = 'rag-knowledge-msgs:'
const LEGACY_STORAGE_KEY = 'rag-knowledge-chat'

/** 最多保留消息条数 */
const MAX_MESSAGES = 60

/** localStorage 存储的轻量结构（不含图片原始数据） */
interface StoredMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
  mode?: ChatMessage['mode']
  hasImage: boolean
  sources?: SourceDoc[]
  feedback?: 'useful' | 'unuseful' | null
  trace_id?: string | null
  clarification?: MessageClarification
  blocks?: AssistantBlock[]
  thinking?: string
  thinkingDuration?: string
  agentTools?: any[]
  timelineItems?: any[]
}

// ================================================================
//  localStorage — 会话元数据与消息（回退存储）
// ================================================================

function saveLocalSessionsMeta(sessions: ChatSessionSummary[], activeId: string | null): void {
  try {
    localStorage.setItem(SESSIONS_STORAGE_KEY, JSON.stringify(sessions))
    if (activeId) {
      localStorage.setItem(ACTIVE_SESSION_KEY, activeId)
    } else {
      localStorage.removeItem(ACTIVE_SESSION_KEY)
    }
  } catch {}
}

function loadLocalSessionsMeta(): { activeSessionId: string | null; sessions: ChatSessionSummary[] } {
  try {
    const rawList = localStorage.getItem(SESSIONS_STORAGE_KEY)
    const activeId = localStorage.getItem(ACTIVE_SESSION_KEY)
    const sessions = rawList ? JSON.parse(rawList) : []
    return { activeSessionId: activeId || (sessions[0]?.id ?? null), sessions }
  } catch {
    return { activeSessionId: null, sessions: [] }
  }
}

function isNetworkUnavailable(error: unknown): boolean {
  if (!error || typeof error !== 'object') return true
  const requestError = error as { isAxiosError?: boolean; response?: unknown }
  return !requestError.isAxiosError || !requestError.response
}

function isNotFound(error: unknown): boolean {
  return (error as { response?: { status?: number } })?.response?.status === 404
}

export function normalizeTraceId(raw: unknown): string | null {
  if (!raw) return null
  if (typeof raw === 'string') {
    const trimmed = raw.trim()
    return trimmed.length > 0 ? trimmed : null
  }
  if (typeof raw === 'object' && raw !== null) {
    if ('trace_id' in (raw as any)) {
      const tid = (raw as any).trace_id
      if (typeof tid === 'string') {
        const trimmed = tid.trim()
        return trimmed.length > 0 ? trimmed : null
      }
    }
  }
  return null
}

function saveLocalSessionMsgs(sessionId: string, messages: ChatMessage[]): void {
  try {
    const stored: StoredMsg[] = messages.map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      mode: m.mode,
      hasImage: !!m.imageUrl,
      sources: m.sources,
      feedback: m.feedback,
      trace_id: normalizeTraceId(m.trace_id),
      clarification: m.clarification,
      blocks: m.blocks,
    }))
    localStorage.setItem(`${SESSION_MSGS_PREFIX}${sessionId}`, JSON.stringify(stored))
  } catch {}
}

function loadLocalSessionMsgs(sessionId: string): StoredMsg[] {
  try {
    const raw = localStorage.getItem(`${SESSION_MSGS_PREFIX}${sessionId}`)
    if (raw) return JSON.parse(raw)
    // 如果没有分会话消息，检查是否是 legacy 数据
    if (sessionId === 'legacy') {
      const leg = localStorage.getItem(LEGACY_STORAGE_KEY)
      return leg ? JSON.parse(leg) : []
    }
    return []
  } catch {
    return []
  }
}

function removeLocalSession(sessionId: string): void {
  try {
    localStorage.removeItem(`${SESSION_MSGS_PREFIX}${sessionId}`)
    const { sessions, activeSessionId } = loadLocalSessionsMeta()
    const nextSessions = sessions.filter((s) => s.id !== sessionId)
    const nextActive = activeSessionId === sessionId ? (nextSessions[0]?.id ?? null) : activeSessionId
    saveLocalSessionsMeta(nextSessions, nextActive)
  } catch {}
}

// ================================================================
//  IndexedDB — 图片数据
// ================================================================

const DB_NAME = 'rag-knowledge-images'
const DB_STORE = 'images'

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => {
      req.result.createObjectStore(DB_STORE, { keyPath: 'id' })
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function saveImageToDB(id: string, dataUrl: string): Promise<void> {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(DB_STORE, 'readwrite')
    tx.objectStore(DB_STORE).put({ id, dataUrl })
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

async function loadImageFromDB(id: string): Promise<string | null> {
  const db = await openDB()
  return new Promise((resolve) => {
    const tx = db.transaction(DB_STORE, 'readonly')
    const req = tx.objectStore(DB_STORE).get(id)
    req.onsuccess = () => resolve(req.result?.dataUrl ?? null)
    req.onerror = () => resolve(null)
  })
}

async function removeImagesFromDB(ids: string[]): Promise<void> {
  if (!ids.length) return
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(DB_STORE, 'readwrite')
    const store = tx.objectStore(DB_STORE)
    ids.forEach((id) => store.delete(id))
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

async function clearImagesFromDB(): Promise<void> {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(DB_STORE, 'readwrite')
    tx.objectStore(DB_STORE).clear()
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

// ================================================================
//  对外多会话接口
// ================================================================

export function generateSessionTitle(question: string): string {
  const clean = question.replace(/[\r\n]+/g, ' ').trim()
  if (!clean) return '新建对话'
  return clean.slice(0, 24)
}

function trimMessages(messages: ChatMessage[]): ChatMessage[] {
  if (messages.length > MAX_MESSAGES) {
    const removed = messages.slice(0, messages.length - MAX_MESSAGES)
    const imgIds = removed.filter((m) => m.imageUrl).map((m) => m.id)
    if (imgIds.length) {
      removeImagesFromDB(imgIds).catch(() => {})
    }
    return messages.slice(-MAX_MESSAGES)
  }
  return messages
}

async function restoreMessages(stored: StoredMsg[]): Promise<ChatMessage[]> {
  return Promise.all(
    stored.map(async (s) => {
      const restoredBlocks = s.blocks && s.blocks.length > 0
        ? s.blocks
        : normalizeLegacyMessageToBlocks({
            thinking: s.thinking,
            thinkingDuration: s.thinkingDuration,
            agentTools: s.agentTools,
            timelineItems: s.timelineItems,
            content: s.content,
          })

      const msg: ChatMessage = {
        id: s.id,
        role: s.role,
        content: s.content,
        mode: s.mode,
        sources: s.sources,
        feedback: s.feedback,
        trace_id: normalizeTraceId(s.trace_id),
        clarification: s.clarification,
        blocks: restoredBlocks.length > 0 ? restoredBlocks : undefined,
        thinking: s.thinking,
      }
      if (s.hasImage) {
        msg.imageUrl = (await loadImageFromDB(s.id)) ?? undefined
      }
      return msg
    }),
  )
}

/**
 * 加载所有会话列表（服务端为唯一持久化真相源，仅在网络异常时回退本地只读展示）
 */
export async function loadChatSessions(): Promise<{ activeSessionId: string | null; sessions: ChatSessionSummary[] }> {
  const fingerprint = getFingerprint()

  // 1. 尝试从服务端加载
  try {
    const resp = await fetchServerSessions(fingerprint)
    if (resp && Array.isArray(resp.sessions)) {
      saveLocalSessionsMeta(resp.sessions, resp.active_session_id)
      return {
        activeSessionId: resp.active_session_id || (resp.sessions[0]?.id ?? null),
        sessions: resp.sessions,
      }
    }
  } catch (e) {
    if (!isNetworkUnavailable(e)) throw e
    console.warn('服务端会话列表加载失败，回退到本地缓存:', e)
  }

  // 2. 仅在服务端抛出网络/不可达异常时，回退到本地缓存
  const local = loadLocalSessionsMeta()
  if (local.sessions && local.sessions.length > 0) {
    return local
  }

  return { activeSessionId: null, sessions: [] }
}

/**
 * 设置当前活跃会话（同步服务端与本地）
 */
export async function setActiveChatSession(sessionId: string): Promise<void> {
  const fingerprint = getFingerprint()
  try {
    await setActiveServerSession(fingerprint, sessionId)
  } catch (error) {
    if (isNotFound(error)) removeLocalSession(sessionId)
    throw error
  }
  const { sessions } = loadLocalSessionsMeta()
  saveLocalSessionsMeta(sessions, sessionId)
}

/**
 * 加载指定会话的消息
 */
export async function loadSessionMessages(sessionId: string): Promise<ChatMessage[]> {
  const fingerprint = getFingerprint()

  // 1. 服务端优先
  try {
    const detail = await fetchServerSessionDetail(fingerprint, sessionId)
    if (detail === null) return []
    if (detail && detail.messages) {
      const stored: StoredMsg[] = detail.messages.map((m: any) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        mode: m.mode,
        hasImage: !!m.hasImage,
        sources: m.sources,
        feedback: m.feedback,
        trace_id: normalizeTraceId(m.trace_id),
        clarification: m.clarification,
        blocks: m.blocks,
        thinking: m.thinking,
        thinkingDuration: m.thinkingDuration,
        agentTools: m.agentTools,
        timelineItems: m.timelineItems,
      }))
      saveLocalSessionMsgs(sessionId, stored as any)
      return restoreMessages(stored)
    }
  } catch (e) {
    if (!isNetworkUnavailable(e)) throw e
  }

  // 2. 本地回退
  const localStored = loadLocalSessionMsgs(sessionId)
  if (localStored.length > 0) {
    return restoreMessages(localStored)
  }

  return []
}

/**
 * 创建新会话
 */
export async function createChatSession(
  title = '新建对话',
  sessionId?: string,
): Promise<{ id: string; title: string }> {
  const fingerprint = getFingerprint()
  const sid = sessionId || `session_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`

  // 1. 服务端创建
  await createServerSession(fingerprint, title, sid)

  // 2. 本地同步更新
  const { sessions } = loadLocalSessionsMeta()
  const newSummary: ChatSessionSummary = {
    id: sid,
    title,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    message_count: 0,
  }
  const nextSessions = [newSummary, ...sessions.filter((s) => s.id !== sid)]
  saveLocalSessionsMeta(nextSessions, sid)
  saveLocalSessionMsgs(sid, [])

  return { id: sid, title }
}

/**
 * 保存会话消息与标题
 */
export async function saveSessionState(
  sessionId: string,
  messages: ChatMessage[],
  title?: string,
): Promise<void> {
  const fingerprint = getFingerprint()
  const trimmed = trimMessages(messages)

  // 1. 先写本地 localStorage
  saveLocalSessionMsgs(sessionId, trimmed)
  const { sessions } = loadLocalSessionsMeta()
  const existing = sessions.find((s) => s.id === sessionId)
  const updatedTitle = title || (existing ? existing.title : '新建对话')
  const updatedSummary: ChatSessionSummary = {
    id: sessionId,
    title: updatedTitle,
    created_at: existing?.created_at || new Date().toISOString(),
    updated_at: new Date().toISOString(),
    message_count: trimmed.filter((m) => !m.loading).length,
  }
  const nextSessions = [
    updatedSummary,
    ...sessions.filter((s) => s.id !== sessionId),
  ]
  saveLocalSessionsMeta(nextSessions, sessionId)

  // 2. 写入服务端（不吞掉异常）
  const toSave = trimmed
    .filter((m) => !m.loading)
    .map((m) => ({
      id: m.id,
      role: m.role as 'user' | 'assistant',
      content: m.content,
      mode: m.mode,
      hasImage: !!m.imageUrl,
      sources: m.sources,
      feedback: m.feedback,
      trace_id: m.trace_id,
      clarification: m.clarification,
      blocks: m.blocks,
    }))

  await saveServerSession(fingerprint, sessionId, toSave, title)

  // 3. 图片写入 IndexedDB
  const imageJobs = messages
    .filter((m) => m.imageUrl)
    .map((m) => saveImageToDB(m.id, m.imageUrl!))
  await Promise.all(imageJobs)
}

/**
 * 同步写 localStorage（用于 beforeunload 等关窗事件）
 */
export function saveSessionStateLocalSync(
  sessionId: string,
  messages: ChatMessage[],
  title?: string,
): void {
  const trimmed = trimMessages(messages)
  saveLocalSessionMsgs(sessionId, trimmed)
  const { sessions } = loadLocalSessionsMeta()
  const existing = sessions.find((s) => s.id === sessionId)
  if (existing) {
    existing.message_count = trimmed.filter((m) => !m.loading).length
    existing.updated_at = new Date().toISOString()
    if (title) existing.title = title
    saveLocalSessionsMeta(sessions, sessionId)
  }
}

/**
 * 重命名会话
 */
export async function renameChatSession(sessionId: string, title: string): Promise<void> {
  const fingerprint = getFingerprint()
  await renameServerSession(fingerprint, sessionId, title)
  const { sessions, activeSessionId } = loadLocalSessionsMeta()
  const target = sessions.find((s) => s.id === sessionId)
  if (target) {
    target.title = title
    target.updated_at = new Date().toISOString()
    saveLocalSessionsMeta(sessions, activeSessionId)
  }
}

/**
 * 删除会话
 */
export async function deleteChatSession(sessionId: string): Promise<void> {
  const fingerprint = getFingerprint()

  // 1. 服务端删除
  await deleteServerSession(fingerprint, sessionId)

  // 2. 本地删除
  removeLocalSession(sessionId)

  // 3. 清除该会话的消息与图片
  const msgs = loadLocalSessionMsgs(sessionId)
  const imgIds = msgs.filter((m) => m.hasImage).map((m) => m.id)
  if (imgIds.length) {
    removeImagesFromDB(imgIds).catch(() => {})
  }
}

/**
 * 清空所有会话
 */
export async function clearAllChatSessions(): Promise<void> {
  const fingerprint = getFingerprint()
  try {
    localStorage.removeItem(SESSIONS_STORAGE_KEY)
    localStorage.removeItem(ACTIVE_SESSION_KEY)
    localStorage.removeItem(LEGACY_STORAGE_KEY)
    // 移除所有 session msgs
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const key = localStorage.key(i)
      if (key && key.startsWith(SESSION_MSGS_PREFIX)) {
        localStorage.removeItem(key)
      }
    }
  } catch {}

  await clearImagesFromDB()

  try {
    await deleteServerChat(fingerprint)
  } catch {}
}

// ---------------- 兼容旧单会话接口 ----------------

export async function loadChatState(): Promise<ChatMessage[]> {
  const { activeSessionId, sessions } = await loadChatSessions()
  const targetId = activeSessionId || sessions[0]?.id
  if (!targetId) return []
  return loadSessionMessages(targetId)
}

export function saveChatStateLocalSync(messages: ChatMessage[]): void {
  const { activeSessionId } = loadLocalSessionsMeta()
  if (activeSessionId) {
    saveSessionStateLocalSync(activeSessionId, messages)
  }
}

export async function saveChatState(messages: ChatMessage[]): Promise<void> {
  const { activeSessionId } = loadLocalSessionsMeta()
  if (activeSessionId) {
    await saveSessionState(activeSessionId, messages)
  }
}

export async function clearChatState(): Promise<void> {
  const { activeSessionId } = loadLocalSessionsMeta()
  if (activeSessionId) {
    await deleteChatSession(activeSessionId)
  }
}
