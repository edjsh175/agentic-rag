import type { Message, SourceDoc } from '../types'

/** 与后端 SourceSummary / extract_source_summaries 对齐的轻量来源字段 */
export type HistorySourceSummary = {
  file_name?: string
  source?: string
  section_title?: string
  page_label?: string
  chunk_id?: string
  citation_id?: string | number
  scope_root?: string
  scope_binding_strength?: string
  preview?: string
}

export type ChatHistoryItem = {
  role: string
  content: string
  sources?: HistorySourceSummary[]
}

/** 从助手消息的 source_documents 提取下一轮 history 用的来源摘要 */
export function extractHistorySourceSummaries(
  sources: SourceDoc[] | undefined,
  limit = 4,
): HistorySourceSummary[] {
  if (!sources?.length) return []
  return sources.slice(0, limit).map((s) => ({
    file_name: s.metadata?.file_name || s.metadata?.source || undefined,
    source: s.metadata?.source || undefined,
    section_title: s.metadata?.section_title || undefined,
    page_label: s.metadata?.page_label || undefined,
    chunk_id: s.metadata?.chunk_id || undefined,
    citation_id: s.metadata?.citation_id,
    scope_root: s.metadata?.scope_root || undefined,
    scope_binding_strength: s.metadata?.scope_binding_strength || undefined,
    preview: s.content?.slice(0, 200) || undefined,
  }))
}

/** 统一组装发给后端的 history（含 assistant sources，供 source_anchor） */
export function buildChatHistoryPayload(
  messages: Message[],
  options?: { maxMessages?: number; dropTrailingLoading?: boolean },
): ChatHistoryItem[] {
  const maxMessages = options?.maxMessages ?? 60
  let list = messages
  if (options?.dropTrailingLoading !== false) {
    list = messages[messages.length - 1]?.loading
      ? messages.slice(0, -1)
      : messages
  }
  return list.slice(-maxMessages).map((m) => ({
    role: m.role,
    content: m.content,
    ...(m.role === 'assistant' && m.sources?.length
      ? { sources: extractHistorySourceSummaries(m.sources) }
      : {}),
  }))
}
