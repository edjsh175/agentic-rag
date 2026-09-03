import type { Message, MessageClarification, SourceDoc } from '../types'

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

export type MessageClarificationSummary = {
  question: string
  selected?: string
  option_id?: string
  snapshot_id?: string
  selection_kind?: string
  free_text?: string
  published_trace_id?: string | null
  response_trace_id?: string | null
}

function currentClarificationSummary(m: Message): MessageClarificationSummary | undefined {
  if (!m.clarification) return undefined
  const selectedOpt = m.clarification.options?.find((o) => o.id === m.clarification?.selectedId)
  const selectedLabel = selectedOpt?.label || (m.clarification.selectedId === 'other' ? m.clarification.otherText : undefined)
  return {
    question: m.clarification.ask_question,
    selected: selectedLabel || undefined,
    option_id: m.clarification.selectedId || undefined,
    snapshot_id: m.clarification.clarification_snapshot_id || undefined,
    selection_kind: m.clarification.selection_kind || (m.clarification.selectedId === 'other' ? 'other' : (m.clarification.selectedId ? 'option' : undefined)),
    free_text: m.clarification.otherText || undefined,
    published_trace_id: m.clarification.published_trace_id || undefined,
    response_trace_id: m.clarification.response_trace_id || undefined,
  }
}

export type ChatHistoryItem = {
  role: string
  content: string
  trace_id?: string | null
  sources?: HistorySourceSummary[]
  clarification?: MessageClarificationSummary
  clarification_history?: MessageClarificationSummary[]
}

/** 将即将被新卡片替换的澄清交互冻结到消息历史，避免 Clarify→Clarify 覆盖事实。 */
export function archiveClarificationInteraction(clarification: MessageClarification): MessageClarification['history'] {
  const selectedOpt = clarification.options?.find((o) => o.id === clarification.selectedId)
  const selected = selectedOpt?.label || (clarification.selectedId === 'other' ? clarification.otherText : undefined)
  return [
    ...(clarification.history || []),
    {
      question: clarification.ask_question,
      selected,
      option_id: clarification.selectedId,
      snapshot_id: clarification.clarification_snapshot_id,
      selection_kind: clarification.selection_kind,
      free_text: clarification.otherText,
      published_trace_id: clarification.published_trace_id,
      response_trace_id: clarification.response_trace_id,
    },
  ]
}

/** 把同一 HTTP 请求最终返回的 trace 精确绑定到“响应旧澄清”和/或“发布新澄清”事实。 */
export function bindClarificationTrace(
  msg: Message,
  traceId: string,
  options?: {
    respondingSnapshotId?: string
    clarificationPublishedInThisRequest?: boolean
  },
) {
  const respondingSnapshotId = options?.respondingSnapshotId
  const clarificationPublishedInThisRequest = options?.clarificationPublishedInThisRequest ?? false
  const tid = String(traceId || '').trim()
  msg.trace_id = tid || null
  if (!tid || !msg.clarification) return

  if (respondingSnapshotId) {
    const archived = [...(msg.clarification.history || [])].reverse().find(
      (item) => item.snapshot_id === respondingSnapshotId,
    )
    if (archived) archived.response_trace_id = tid
    else if (msg.clarification.clarification_snapshot_id === respondingSnapshotId) {
      msg.clarification.response_trace_id = tid
    }
  }

  if (clarificationPublishedInThisRequest) {
    msg.clarification.published_trace_id = tid
  }
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

/** 统一组装发给后端的 history（含 assistant sources 与澄清交互，供跨轮事实与 source_anchor） */
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
  return list.slice(-maxMessages).map((m) => {
    const clarificationPayload = currentClarificationSummary(m)
    const clarificationHistory = m.clarification?.history?.map((item) => ({
      question: item.question,
      selected: item.selected,
      option_id: item.option_id,
      snapshot_id: item.snapshot_id,
      selection_kind: item.selection_kind,
      free_text: item.free_text,
      published_trace_id: item.published_trace_id || undefined,
      response_trace_id: item.response_trace_id || undefined,
    }))
    return {
      role: m.role,
      content: m.content,
      trace_id: m.trace_id ? String(m.trace_id).trim() : undefined,
      ...(m.role === 'assistant' && m.sources?.length
        ? { sources: extractHistorySourceSummaries(m.sources) }
        : {}),
      ...(clarificationPayload ? { clarification: clarificationPayload } : {}),
      ...(clarificationHistory?.length ? { clarification_history: clarificationHistory } : {}),
    }
  })
}
