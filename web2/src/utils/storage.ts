/**
 * 聊天记录与多会话持久化 —— 服务器 JSON + 浏览器 localStorage + IndexedDB 图片存储
 *
 * 支持：
 * 1. 历史会话列表（Sessions List）
 * 2. 多会话切换、新建会话、删除会话、重命名会话
 * 3. 旧单会话数据无缝平滑迁移
 */
import type { Message as ChatMessage, SourceDoc, MessageClarification, ChatSession } from '../types'
import { loadServerChat, saveServerChat, deleteServerChat } from '../api'
import { getFingerprint } from './fingerprint'

// ---- localStorage 键名 ----
const STORAGE_LEGACY_KEY = 'rag-knowledge-chat'
const STORAGE_SESSIONS_KEY = 'rag-knowledge-sessions'
const STORAGE_ACTIVE_SESSION_KEY = 'rag-knowledge-active-session-id'

/** 每个会话最多保留消息条数 */
const MAX_MESSAGES = 60

/** localStorage 存储的轻量消息结构（不含图片原始 base64） */
interface StoredMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
  hasImage: boolean
  sources?: SourceDoc[]
  feedback?: 'useful' | 'unuseful' | null
  trace_id?: string | null
  clarification?: MessageClarification
}

interface StoredSession {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  pinned?: boolean
  messages: StoredMsg[]
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
//  消息转换与裁切
// ================================================================

function trimMessages(messages: ChatMessage[]): ChatMessage[] {
  if (messages.length > MAX_MESSAGES) {
    const removed = messages.slice(0, messages.length - MAX_MESSAGES)
    removed.filter((m) => m.imageUrl).forEach((m) => {
      openDB().then((db) => {
        db.transaction(DB_STORE, 'readwrite').objectStore(DB_STORE).delete(m.id)
      })
    })
    return messages.slice(-MAX_MESSAGES)
  }
  return messages
}

function toStoredMsgs(messages: ChatMessage[]): StoredMsg[] {
  return messages.map((m) => ({
    id: m.id,
    role: m.role,
    content: m.content,
    hasImage: !!m.imageUrl,
    sources: m.sources,
    feedback: m.feedback,
    trace_id: m.trace_id,
    clarification: m.clarification,
  }))
}

async function restoreMessages(stored: StoredMsg[]): Promise<ChatMessage[]> {
  return Promise.all(
    stored.map(async (s) => {
      const msg: ChatMessage = {
        id: s.id,
        role: s.role,
        content: s.content,
        sources: s.sources,
        feedback: s.feedback,
        trace_id: s.trace_id,
        clarification: s.clarification,
      }
      if (s.hasImage) {
        msg.imageUrl = (await loadImageFromDB(s.id)) ?? undefined
      }
      return msg
    }),
  )
}

// ================================================================
//  多会话（Sessions）持久化接口
// ================================================================

export function getActiveSessionId(): string | null {
  return localStorage.getItem(STORAGE_ACTIVE_SESSION_KEY)
}

export function setActiveSessionId(id: string): void {
  localStorage.setItem(STORAGE_ACTIVE_SESSION_KEY, id)
}

/**
 * 加载所有历史会话列表（优先 localStorage，自动做老数据兼容迁移）
 */
export async function loadAllSessions(): Promise<ChatSession[]> {
  let rawSessions: StoredSession[] = []

  try {
    const raw = localStorage.getItem(STORAGE_SESSIONS_KEY)
    if (raw) {
      rawSessions = JSON.parse(raw)
    }
  } catch {
    rawSessions = []
  }

  // 检查是否需要从旧单会话数据做迁移
  if (rawSessions.length === 0) {
    const legacyRaw = localStorage.getItem(STORAGE_LEGACY_KEY)
    let legacyStored: StoredMsg[] = []
    if (legacyRaw) {
      try {
        legacyStored = JSON.parse(legacyRaw)
      } catch {}
    }

    if (legacyStored.length > 0) {
      const firstUserMsg = legacyStored.find((m) => m.role === 'user')
      const title = firstUserMsg ? (firstUserMsg.content.slice(0, 20) || '新对话') : '历史对话'
      const legacySession: StoredSession = {
        id: Date.now().toString(),
        title,
        createdAt: Date.now(),
        updatedAt: Date.now(),
        messages: legacyStored,
      }
      rawSessions = [legacySession]
      localStorage.setItem(STORAGE_SESSIONS_KEY, JSON.stringify(rawSessions))
      setActiveSessionId(legacySession.id)
    }
  }

  // 将 StoredSession 还原为完整 ChatSession（含图片还原）
  const sessions: ChatSession[] = await Promise.all(
    rawSessions.map(async (s) => ({
      id: s.id,
      title: s.title,
      createdAt: s.createdAt,
      updatedAt: s.updatedAt,
      pinned: !!s.pinned,
      messages: await restoreMessages(s.messages || []),
    })),
  )

  return sessions.sort((a, b) => {
    if (!!a.pinned !== !!b.pinned) {
      return a.pinned ? -1 : 1
    }
    return b.updatedAt - a.updatedAt
  })
}

/**
 * 全量保存所有会话列表
 */
export async function saveAllSessions(sessions: ChatSession[]): Promise<void> {
  const storedSessions: StoredSession[] = sessions.map((s) => ({
    id: s.id,
    title: s.title,
    createdAt: s.createdAt,
    updatedAt: s.updatedAt,
    pinned: !!s.pinned,
    messages: toStoredMsgs(trimMessages(s.messages.filter((m) => !m.loading))),
  }))

  localStorage.setItem(STORAGE_SESSIONS_KEY, JSON.stringify(storedSessions))

  // 将所有图片并行写入 IndexedDB
  const imageJobs: Promise<void>[] = []
  for (const s of sessions) {
    for (const m of s.messages) {
      if (m.imageUrl) {
        imageJobs.push(saveImageToDB(m.id, m.imageUrl))
      }
    }
  }
  if (imageJobs.length > 0) {
    await Promise.all(imageJobs)
  }
}

/**
 * 兼容旧接口：加载指定会话的消息，如未传 sessionId 则加载当前激活会话或第一个会话
 */
export async function loadChatState(sessionId?: string): Promise<ChatMessage[]> {
  const sessions = await loadAllSessions()
  if (sessions.length === 0) return []

  const targetId = sessionId || getActiveSessionId() || sessions[0]?.id
  const target = sessions.find((s) => s.id === targetId) || sessions[0]
  if (target) {
    setActiveSessionId(target.id)
    return target.messages
  }
  return []
}

/**
 * 兼容旧接口：保存当前会话的消息状态
 */
export async function saveChatState(
  messages: ChatMessage[],
  sessionId?: string,
  sessionTitle?: string,
): Promise<void> {
  const sessions = await loadAllSessions()
  const targetId = sessionId || getActiveSessionId() || Date.now().toString()

  let found = sessions.find((s) => s.id === targetId)
  if (!found) {
    const firstUserMsg = messages.find((m) => m.role === 'user')
    const title = sessionTitle || (firstUserMsg ? firstUserMsg.content.slice(0, 24) : '新对话')
    found = {
      id: targetId,
      title,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      messages: [],
    }
    sessions.unshift(found)
  }

  // 若标题还是默认“新对话”，自动根据第一条用户消息提取标题
  if (found.title === '新对话' || !found.title) {
    const firstUserMsg = messages.find((m) => m.role === 'user')
    if (firstUserMsg && firstUserMsg.content.trim()) {
      found.title = firstUserMsg.content.trim().slice(0, 24)
    }
  }

  found.messages = messages
  found.updatedAt = Date.now()
  setActiveSessionId(targetId)

  await saveAllSessions(sessions)

  // 同时也写入服务器做双重备份
  const fingerprint = getFingerprint()
  try {
    const trimmed = trimMessages(messages)
    const toSave = trimmed
      .filter((m) => !m.loading)
      .map((m) => ({
        id: m.id,
        role: m.role as 'user' | 'assistant',
        content: m.content,
        hasImage: !!m.imageUrl,
        sources: m.sources,
        feedback: m.feedback,
        trace_id: m.trace_id,
        clarification: m.clarification,
      }))
    await saveServerChat(fingerprint, toSave)
  } catch {}
}

/**
 * 清空指定会话或全部会话
 */
export async function clearChatState(sessionId?: string): Promise<void> {
  if (sessionId) {
    const sessions = await loadAllSessions()
    const remaining = sessions.filter((s) => s.id !== sessionId)
    await saveAllSessions(remaining)
    if (getActiveSessionId() === sessionId) {
      if (remaining.length > 0) {
        setActiveSessionId(remaining[0].id)
      } else {
        localStorage.removeItem(STORAGE_ACTIVE_SESSION_KEY)
      }
    }
  } else {
    // 全量清空
    const fingerprint = getFingerprint()
    try {
      await deleteServerChat(fingerprint)
    } catch {}
    localStorage.removeItem(STORAGE_SESSIONS_KEY)
    localStorage.removeItem(STORAGE_ACTIVE_SESSION_KEY)
    localStorage.removeItem(STORAGE_LEGACY_KEY)
    await clearImagesFromDB()
  }
}
