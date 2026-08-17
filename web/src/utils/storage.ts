/**
 * 聊天记录持久化 —— 服务器 JSON + 浏览器 localStorage 双存储
 *
 * 加载策略：服务器优先 → localStorage（兼容旧数据）
 * 保存策略：服务器为主，localStorage 为回退
 * 图片策略：base64 仅存浏览器 IndexedDB，服务器存 hasImage 标记
 */
import type { Message as ChatMessage, SourceDoc, MessageClarification } from '../types'
import { loadServerChat, saveServerChat, deleteServerChat } from '../api'
import { getFingerprint } from './fingerprint'

// ---- localStorage 键名 ----
const STORAGE_KEY = 'rag-knowledge-chat'

/** 最多保留消息条数 */
const MAX_MESSAGES = 60

/** localStorage 存储的轻量结构（不含图片原始数据） */
interface StoredMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
  hasImage: boolean
  sources?: SourceDoc[]
  feedback?: 'useful' | 'unuseful' | null
  trace_id?: string | null
  clarification?: MessageClarification
  thinking?: string
  thinkingDuration?: string
  agentTools?: any[]
  timelineItems?: any[]
}

// ================================================================
//  localStorage — 消息元数据（回退存储）
// ================================================================

function saveMessages(messages: ChatMessage[]): void {
  const stored: StoredMsg[] = messages.map((m) => ({
    id: m.id,
    role: m.role,
    content: m.content,
    hasImage: !!m.imageUrl,
    sources: m.sources,
    feedback: m.feedback,
    trace_id: m.trace_id,
    clarification: m.clarification,
    thinking: m.thinking,
    thinkingDuration: m.thinkingDuration,
    agentTools: m.agentTools,
    timelineItems: m.timelineItems,
  }))
  localStorage.setItem(STORAGE_KEY, JSON.stringify(stored))
}

function loadMessages(): StoredMsg[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function clearMessages(): void {
  localStorage.removeItem(STORAGE_KEY)
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
//  对外接口
// ================================================================

const WELCOME_MSG: ChatMessage = {
  id: 'welcome',
  role: 'assistant',
  content: '你好！我是 RAG 知识库助手。\n\n你可以输入文字提问，也可以**粘贴或上传图片**让我识别描述。',
}

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

/** 将 StoredMsg 还原为 ChatMessage（从 IndexedDB 回补图片） */
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
        thinking: s.thinking,
        thinkingDuration: s.thinkingDuration,
        agentTools: s.agentTools,
        timelineItems: s.timelineItems,
      }
      if (s.hasImage) {
        msg.imageUrl = (await loadImageFromDB(s.id)) ?? undefined
      }
      return msg
    }),
  )
}

/**
 * 全量加载：服务器优先 → localStorage 回退
 * 无数据时返回欢迎消息
 */
export async function loadChatState(): Promise<ChatMessage[]> {
  const fingerprint = getFingerprint()

  // 1. 尝试从服务器加载
  try {
    const serverMessages = await loadServerChat(fingerprint)
    if (serverMessages) {
      const stored: StoredMsg[] = serverMessages.map((m: any) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        hasImage: !!m.hasImage,
        sources: m.sources,
        feedback: m.feedback,
        trace_id: m.trace_id,
        clarification: m.clarification,
        thinking: m.thinking,
        thinkingDuration: m.thinkingDuration,
        agentTools: m.agentTools,
        timelineItems: m.timelineItems,
      }))
      return restoreMessages(stored)
    }
  } catch {
    // 服务器不可用，继续 fallback
  }

  // 2. 回退到 localStorage
  const stored = loadMessages()
  if (stored.length > 0) {
    return restoreMessages(stored)
  }

  // 3. 无数据 → 欢迎消息
  return []
}

/**
 * 全量保存：写服务器 + 写 localStorage（回退）
 * 自动过滤 loading 消息，裁切超过上限的消息
 */
export async function saveChatState(messages: ChatMessage[]): Promise<void> {
  const fingerprint = getFingerprint()

  // 1. 裁切 + 过滤 loading 状态
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
      thinking: m.thinking,
      thinkingDuration: m.thinkingDuration,
      agentTools: m.agentTools,
      timelineItems: m.timelineItems,
    }))

  // 2. 写入服务器
  try {
    await saveServerChat(fingerprint, toSave)
  } catch {
    // 服务器不可用时静默失败，本地存储兜底
  }

  // 3. 图片写入 IndexedDB
  const imageJobs = messages
    .filter((m) => m.imageUrl)
    .map((m) => saveImageToDB(m.id, m.imageUrl!))
  await Promise.all(imageJobs)

  // 4. 写入 localStorage（始终作为回退）
  saveMessages(trimmed)
}

/**
 * 清空：服务器 + localStorage + IndexedDB
 */
export async function clearChatState(): Promise<void> {
  const fingerprint = getFingerprint()

  try {
    await deleteServerChat(fingerprint)
  } catch {
    // 服务器不可用时静默
  }

  clearMessages()
  await clearImagesFromDB()
}
