import type {
  AgentTimelineItem,
  AgentToolCall,
  AssistantBlock,
  LLMReasoningEventData,
  MarkdownBlock,
  ReasoningBlock,
  ReviewStatusEventData,
  SystemEventBlock,
  ToolBlock,
  ToolResultEventData,
  ToolStartEventData,
} from '../types'

/** 工具标题本地化映射 */
export function getToolLabel(toolName: string): string {
  switch (toolName) {
    case 'retrieve_kb':
      return '知识库检索'
    case 'web_search':
      return '网页搜索'
    case 'understand':
      return '意图理解'
    case 'rewrite':
      return '查询改写'
    case 'link_entities':
      return '图谱实体检索'
    case 'reuse_evidence':
      return '复用已有证据'
    case 'clarify':
      return '反问澄清'
    case 'environment.read_status':
      return '系统状态读取'
    default:
      if (toolName.startsWith('environment.')) {
        return `环境工具: ${toolName.replace('environment.', '')}`
      }
      return toolName
  }
}

/** 格式化 Reasoning 阶段标题 */
export function getReasoningStageTitle(stage: string): string {
  switch (stage) {
    case 'agent_controller':
      return 'Main · Controller'
    case 'answer_generation':
      return 'Main · Answer'
    case 'grounded_retry':
      return 'Main · Rewrite'
    case 'grounding_reviewer':
      return 'Reviewer'
    default:
      return `Main · ${stage || 'Reasoning'}`
  }
}

/** 首版用户可见 notice 白名单：当前后端仅允许显存自动降级提示进入主聊天。 */
export function isUserVisibleAgentNotice(content: string): boolean {
  return content.startsWith('当前显存不足以加载所选模型，已自动降级为 ')
}

/**
 * AgentBlockProjector: 管理单条 Agent 消息的 SSE -> AssistantBlock[] 白名单投影状态机
 */
export class AgentBlockProjector {
  private blocks: AssistantBlock[] = []
  private sequenceCounter = 0
  private toolKeyIndexMap = new Map<string, number>()
  private reasoningCallIndexMap = new Map<string, number>()
  private activeSystemEventMap = new Map<string, number>()

  constructor(initialBlocks: AssistantBlock[] = []) {
    // 直接持有调用方提供的数组。ChatView 传入 Vue reactive blocks，
    // 这样 reasoning delta / tool lifecycle 的原位更新才能逐事件触发界面刷新。
    this.blocks = initialBlocks
    this.sequenceCounter = this.blocks.reduce((max, block) => Math.max(max, block.sequence || 0), 0)
    // 重建索引
    this.blocks.forEach((b, idx) => {
      if (b.kind === 'reasoning') {
        this.reasoningCallIndexMap.set(b.callId, idx)
      } else if (b.kind === 'tool') {
        this.toolKeyIndexMap.set(b.toolCallKey, idx)
      } else if (b.kind === 'system_event' && b.correlationId) {
        this.activeSystemEventMap.set(b.correlationId, idx)
      }
    })
  }

  public getBlocks(): AssistantBlock[] {
    return this.blocks
  }

  private nextSequence(): number {
    return ++this.sequenceCounter
  }

  /** 处理 Main Reasoning 开始 */
  public handleReasoningStart(data: LLMReasoningEventData): void {
    if (data.role !== 'main') return
    const callId = data.call_id
    if (!callId) return

    if (this.reasoningCallIndexMap.has(callId)) {
      const idx = this.reasoningCallIndexMap.get(callId)!
      const existing = this.blocks[idx] as ReasoningBlock
      existing.status = 'running'
      existing.isStreaming = true
      if (data.stage) existing.stage = data.stage
      if (data.model) existing.model = data.model
      if (data.provider) existing.provider = data.provider
      return
    }

    const block: ReasoningBlock = {
      id: `reasoning-${callId}`,
      kind: 'reasoning',
      type: 'reasoning',
      sequence: this.nextSequence(),
      callId,
      stage: data.stage || 'agent_controller',
      role: 'main',
      model: data.model,
      provider: data.provider,
      text: '',
      content: '',
      status: 'running',
      isStreaming: true,
    }

    const newIdx = this.blocks.length
    this.blocks.push(block)
    this.reasoningCallIndexMap.set(callId, newIdx)
  }

  /** 处理 Main Reasoning 增量 */
  public handleReasoningDelta(data: LLMReasoningEventData): void {
    if (data.role !== 'main') return
    const callId = data.call_id
    if (!callId) return

    if (!this.reasoningCallIndexMap.has(callId)) {
      this.handleReasoningStart(data)
    }

    const idx = this.reasoningCallIndexMap.get(callId)
    if (idx === undefined) return

    const existing = this.blocks[idx] as ReasoningBlock
    const text = (existing.text || '') + (data.delta || '')
    existing.text = text
    existing.content = text
    existing.status = 'running'
    existing.isStreaming = true
  }

  /** 处理 Main Reasoning 结束 */
  public handleReasoningEnd(data: LLMReasoningEventData): void {
    if (data.role !== 'main') return
    const callId = data.call_id
    if (!callId) return

    if (!this.reasoningCallIndexMap.has(callId)) {
      this.handleReasoningStart(data)
    }

    const idx = this.reasoningCallIndexMap.get(callId)
    if (idx === undefined) return

    const existing = this.blocks[idx] as ReasoningBlock
    existing.status = data.error
      ? 'error'
      : data.reasoning_available === false
        ? 'unavailable'
        : 'completed'
    existing.isStreaming = false
    if (data.elapsed_ms !== undefined) {
      existing.elapsedMs = data.elapsed_ms
      existing.duration = `${(data.elapsed_ms / 1000).toFixed(1)}s`
    }
  }

  /** 处理真实 Tool 开始。ToolBlock 只能在这里创建。 */
  public handleToolStart(data: ToolStartEventData): void {
    const toolName = data.name || 'retrieve_kb'
    const sequence = this.nextSequence()
    const step = data.step !== undefined ? data.step : sequence
    const toolKey = `tool:${step}:${toolName}`

    if (this.toolKeyIndexMap.has(toolKey)) {
      const idx = this.toolKeyIndexMap.get(toolKey)!
      const existing = this.blocks[idx] as ToolBlock
      existing.status = 'running'
      existing.isStreaming = true
      if (data.arguments) {
        existing.input = data.arguments
        existing.in = data.arguments
      }
      return
    }

    const block: ToolBlock = {
      id: toolKey,
      kind: 'tool',
      type: 'tool',
      sequence,
      toolCallKey: toolKey,
      tool: toolName,
      toolName,
      label: getToolLabel(toolName),
      description: data.arguments?.query ? String(data.arguments.query) : toolName,
      input: data.arguments,
      in: data.arguments,
      status: 'running',
      isStreaming: true,
      gap: data.gap,
      expectedGain: data.expected_gain,
    }

    const newIdx = this.blocks.length
    this.blocks.push(block)
    this.toolKeyIndexMap.set(toolKey, newIdx)
  }

  /** 处理真实 Tool 结果 */
  public handleToolResult(data: ToolResultEventData): void {
    const toolName = data.name || 'retrieve_kb'
    const step = data.step
    let toolKey = step !== undefined ? `tool:${step}:${toolName}` : ''

    let idx: number | undefined
    if (toolKey && this.toolKeyIndexMap.has(toolKey)) {
      idx = this.toolKeyIndexMap.get(toolKey)
    } else {
      // 找不到精确 key 时从后往前寻找相同 tool 且处于 running 的 block
      for (let i = this.blocks.length - 1; i >= 0; i--) {
        const b = this.blocks[i]
        if (b.kind === 'tool' && b.tool === toolName && b.status === 'running') {
          idx = i
          toolKey = b.toolCallKey
          break
        }
      }
    }

    const isDenied = data.progress === 'DENIED' || data.status === 'DENIED'
    const isError = data.ok === false || !!data.error
    const status = isDenied ? 'denied' : isError ? 'failed' : 'completed'

    const output = data.summary
      ? { summary: data.summary, ok: data.ok, progress: data.progress }
      : data.data || data.error

    // 严格 fail-safe：缺少匹配的 tool_start 时不允许凭 tool_result 伪造用户可见 ToolBlock。
    if (idx === undefined || !this.blocks[idx]) return

    const existing = this.blocks[idx] as ToolBlock
    existing.status = status
    existing.isStreaming = false
    existing.output = output
    existing.out = output
    existing.elapsedMs = data.elapsed_ms
    existing.error = data.error || null
    if (data.arguments) {
      existing.input = data.arguments
      existing.in = data.arguments
    }
    if (data.gap) existing.gap = data.gap
    if (data.expected_gain) existing.expectedGain = data.expected_gain
  }

  /** 处理 Reviewer 审查结果 (仅 REVISE 产生 SystemEvent，PASS 默认静默) */
  public handleReviewStatus(data: ReviewStatusEventData): void {
    if (data.verdict === 'REVISE') {
      const corrId = `review-revise-${data.review_count || 1}`
      if (this.activeSystemEventMap.has(corrId)) {
        return
      }
      const block: SystemEventBlock = {
        id: `sys-${corrId}`,
        kind: 'system_event',
        type: 'system_event',
        sequence: this.nextSequence(),
        event: 'review_revise',
        level: 'warning',
        text: '候选回答未通过证据审查，正在重新组织…',
        status: 'active',
        correlationId: corrId,
      }
      const newIdx = this.blocks.length
      this.blocks.push(block)
      this.activeSystemEventMap.set(corrId, newIdx)
    }
  }

  /** 处理明确白名单的致命执行错误；同一 correlationId 原位去重。 */
  public handleSystemError(message: string, correlationId?: string): void {
    if (correlationId && this.activeSystemEventMap.has(correlationId)) {
      const idx = this.activeSystemEventMap.get(correlationId)!
      const existing = this.blocks[idx] as SystemEventBlock
      existing.text = message
      existing.level = 'error'
      existing.status = 'failed'
      return
    }

    const sequence = this.nextSequence()
    const block: SystemEventBlock = {
      id: `sys-err-${sequence}`,
      kind: 'system_event',
      type: 'system_event',
      sequence,
      event: 'execution_error',
      level: 'error',
      text: message,
      status: 'failed',
      correlationId,
    }
    const newIdx = this.blocks.length
    this.blocks.push(block)
    if (correlationId) this.activeSystemEventMap.set(correlationId, newIdx)
  }

  /** 处理明确白名单的系统通知。未知 notice 直接忽略。 */
  public handleNotice(content: string, level: 'info' | 'warning' | 'error' = 'warning'): void {
    if (!isUserVisibleAgentNotice(content)) return
    const sequence = this.nextSequence()
    const block: SystemEventBlock = {
      id: `notice-${sequence}`,
      kind: 'system_event',
      type: 'system_event',
      sequence,
      event: 'model_downshift',
      level,
      text: content,
      status: 'completed',
    }
    this.blocks.push(block)
  }

  /** 处理 Final Answer -> MarkdownBlock */
  public handleFinalAnswer(answerText: string): void {
    // 寻找是否已有 MarkdownBlock，原位更新或追加
    const existingIdx = this.blocks.findIndex(b => b.kind === 'markdown')
    if (existingIdx >= 0) {
      const existing = this.blocks[existingIdx] as MarkdownBlock
      existing.text = answerText
      existing.markdown = answerText
      existing.status = 'final'
    } else {
      const sequence = this.nextSequence()
      const block: MarkdownBlock = {
        id: `markdown-${sequence}`,
        kind: 'markdown',
        type: 'markdown',
        sequence,
        text: answerText,
        markdown: answerText,
        status: 'final',
      }
      this.blocks.push(block)
    }

    // 标记所有 active 的 SystemEvent 为 completed
    for (const b of this.blocks) {
      if (b.kind === 'system_event' && b.status === 'active') {
        b.status = 'completed'
      }
    }
  }
}

/** 仅用于读取旧持久化格式；这些字段不得重新进入 Message 生产模型。 */
export interface LegacyAgentMessageSnapshot {
  blocks?: AssistantBlock[]
  content?: string
  thinking?: string
  thinkingDuration?: string
  agentTools?: AgentToolCall[]
  timelineItems?: AgentTimelineItem[]
}

/**
 * 历史消息加载时一次性规范化为统一的 AssistantBlock[] 数组。
 */
export function normalizeLegacyMessageToBlocks(msg: LegacyAgentMessageSnapshot): AssistantBlock[] {
  if (msg.blocks && msg.blocks.length > 0) {
    return [...msg.blocks]
  }

  const result: AssistantBlock[] = []
  let seq = 0

  // 1. 从 timelineItems 转换
  if (msg.timelineItems && msg.timelineItems.length > 0) {
    for (const item of msg.timelineItems) {
      if (item.type === 'think' && item.content) {
        result.push({
          id: item.eventKey || `reasoning-legacy-${++seq}`,
          kind: 'reasoning',
          type: 'reasoning',
          sequence: ++seq,
          callId: item.callId || `legacy-${seq}`,
          stage: item.stage || 'agent_controller',
          role: 'main',
          model: item.model,
          provider: item.provider,
          text: item.content,
          content: item.content,
          status: 'completed',
          isStreaming: false,
          duration: item.duration,
        })
      } else if (item.type === 'tool_call') {
        result.push({
          id: item.eventKey || `tool-legacy-${++seq}`,
          kind: 'tool',
          type: 'tool',
          sequence: ++seq,
          toolCallKey: item.eventKey || `tool-legacy-${seq}`,
          tool: item.tool,
          toolName: item.tool,
          label: item.label || getToolLabel(item.tool),
          description: item.description,
          input: item.in,
          in: item.in,
          output: item.out,
          out: item.out,
          status: item.status === 'denied' ? 'denied' : (item.status === 'failed' || item.error) ? 'failed' : 'completed',
          isStreaming: false,
          elapsedMs: item.elapsed_ms,
          error: item.error,
          gap: item.gap,
          expectedGain: item.expected_gain,
        })
      } else if (item.type === 'review_status' && item.verdict === 'REVISE') {
        result.push({
          id: `sys-legacy-${++seq}`,
          kind: 'system_event',
          type: 'system_event',
          sequence: ++seq,
          event: 'review_revise',
          level: 'warning',
          text: '候选回答未通过证据审查，正在重新组织…',
          status: 'completed',
        })
      } else if (item.type === 'notice' && item.content) {
        result.push({
          id: `notice-legacy-${++seq}`,
          kind: 'system_event',
          type: 'system_event',
          sequence: ++seq,
          event: 'system_notice',
          level: item.level || 'warning',
          text: item.content,
          status: 'completed',
        })
      }
      // 内部 events (understanding, decision, guard, evidence, publication, pass) 全部跳过
    }
  } else {
    // 2. 从旧版独立 thinking / agentTools 转换
    if (msg.thinking) {
      result.push({
        id: `reasoning-legacy-${++seq}`,
        kind: 'reasoning',
        type: 'reasoning',
        sequence: ++seq,
        callId: `legacy-${seq}`,
        stage: 'agent_controller',
        role: 'main',
        text: msg.thinking,
        content: msg.thinking,
        status: 'completed',
        isStreaming: false,
        duration: msg.thinkingDuration,
      })
    }
    if (msg.agentTools && msg.agentTools.length > 0) {
      for (const t of msg.agentTools) {
        result.push({
          id: `tool-legacy-${t.step || ++seq}`,
          kind: 'tool',
          type: 'tool',
          sequence: ++seq,
          toolCallKey: `tool-legacy-${t.step || seq}`,
          tool: t.name,
          toolName: t.name,
          label: getToolLabel(t.name),
          description: t.arguments?.query ? String(t.arguments.query) : t.name,
          input: t.arguments,
          in: t.arguments,
          output: t.observation || t.summary,
          out: t.observation || t.summary,
          status: t.status === 'denied' ? 'denied' : (t.ok === false || t.status === 'error') ? 'failed' : 'completed',
          isStreaming: false,
          elapsedMs: t.elapsed_ms,
          error: t.error,
          gap: t.gap,
          expectedGain: t.expected_gain,
        })
      }
    }
  }

  // 3. 最终 content 补充 MarkdownBlock（如果尚未包含）
  if (msg.content && !result.some(b => b.kind === 'markdown')) {
    result.push({
      id: `markdown-${++seq}`,
      kind: 'markdown',
      type: 'markdown',
      sequence: ++seq,
      text: msg.content,
      markdown: msg.content,
      status: 'final',
    })
  }

  return result
}
