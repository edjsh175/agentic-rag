<script setup lang="ts">
defineOptions({ name: 'ChatView' })
import { ref, onMounted, onUnmounted, nextTick, computed } from 'vue'
import type { Message, SourceDoc, Stats, ClarificationCallbackRequest, ClarificationSelection, ClarifyResult, EvidenceItem, GpuStatus, ChatSessionSummary, WorkMode } from '../types'
import { queryKnowledgeStream, queryKnowledge, queryImageStream, queryClarify, getStats, triggerScan, uploadDocument, getModels, getGpuStatus, getKnowledgeBases, getAgents, updateQaTraceFeedback, submitUserFeedback, DOCUMENT_PROFILE_OPTIONS } from '../api'
import type { DocumentProfile, KnowledgeStreamCallbacks } from '../api'
import type { ModelsResponse, AgentInfo } from '../api'
import {
  saveSessionState,
  saveSessionStateLocalSync,
  loadChatSessions,
  loadSessionMessages,
  createChatSession,
  renameChatSession,
  setActiveChatSession,
  deleteChatSession,
  generateSessionTitle,
} from '../utils/storage'
import { buildChatHistoryPayload } from '../utils/chatHistory'
import { AgentBlockProjector } from '../utils/agentBlockProjector'
import { isNearScrollBottom } from '../utils/scrollFollow'
import ChatMessage from '../components/ChatMessage.vue'
import ChatInput from '../components/ChatInput.vue'
import SourcePanel from '../components/SourcePanel.vue'

const sessions = ref<ChatSessionSummary[]>([])
const activeSessionId = ref<string>('')
const showSidebar = ref(localStorage.getItem('rag-chat-sidebar') !== 'false')
const editingSessionId = ref<string | null>(null)
const editingTitle = ref('')
const renameInput = ref<HTMLInputElement[] | null>(null)

const currentSession = computed(() => sessions.value.find((s) => s.id === activeSessionId.value))
const currentSessionTitle = computed(() => currentSession.value?.title || '新建对话')

function toggleSidebar() {
  showSidebar.value = !showSidebar.value
  localStorage.setItem('rag-chat-sidebar', String(showSidebar.value))
}

const messages = ref<Message[]>([])
const currentSources = ref<SourceDoc[]>([])
const loading = ref(false)
const stats = ref<Stats | null>(null)
const gpuStatus = ref<GpuStatus | null>(null)
const gpuNotice = ref('')          // 显存不足自动降级提示
let gpuNoticeTimer = 0
let gpuPollTimer = 0

// 自定义模型下拉菜单与 GPU 悬浮框的交互状态
const showGpuPopover = ref(false)
const showModelDropdown = ref(false)
const modelSelectTrigger = ref<HTMLElement | null>(null)
const gpuPillTrigger = ref<HTMLElement | null>(null)

const modelSelectStyle = ref({ top: '0px', left: '0px' })
const gpuPopoverStyle = ref({ top: '0px', right: '0px' })
let gpuPopoverTimer = 0

function gpuFits(name: string): boolean | null {
  const fit = gpuStatus.value?.models?.find(m => m.name === name)
  return fit ? (fit.fits ?? null) : null
}

async function refreshGpu() {
  try {
    gpuStatus.value = await getGpuStatus()
  } catch { /* gpu-agent 不可用时静默 */ }
}

function showGpuNotice(msg: string) {
  gpuNotice.value = msg
  clearTimeout(gpuNoticeTimer)
  gpuNoticeTimer = window.setTimeout(() => { gpuNotice.value = '' }, 8000)
}

const vramPct = computed(() => {
  const g = gpuStatus.value?.gpu
  if (!g || !g.total_mib) return 0
  return Math.min(100, Math.round((g.used_mib / g.total_mib) * 100))
})
const vramText = computed(() => {
  const g = gpuStatus.value?.gpu
  if (!g) return ''
  return `${(g.used_mib / 1024).toFixed(1)}/${(g.total_mib / 1024).toFixed(1)} GB`
})
const recommendedModel = computed(() => gpuStatus.value?.recommended_model || '')
const gpuTitle = computed(() => {
  const g = gpuStatus.value?.gpu
  if (!g) return 'GPU 离线（gpu-agent 未启用或不可达）'
  return `${g.name} | 利用率 ${g.utilization ?? '-'}% | 温度 ${g.temperature ?? '-'}℃`
})

const gpuVramClass = computed(() => {
  const pct = vramPct.value
  if (pct < 60) return 'safe'
  if (pct < 85) return 'warning'
  return 'danger'
})

const gpuLoadClass = computed(() => {
  const util = gpuStatus.value?.gpu?.utilization
  if (util === undefined || util === null) return 'unknown'
  if (util < 50) return 'low'
  if (util < 85) return 'medium'
  return 'high'
})

const tempClass = computed(() => {
  const temp = gpuStatus.value?.gpu?.temperature
  if (temp === undefined || temp === null) return 'unknown'
  if (temp < 65) return 'cool'
  if (temp < 80) return 'warm'
  return 'hot'
})

// 切换问答模型下拉列表并定位
function toggleModelDropdown() {
  showModelDropdown.value = !showModelDropdown.value
  if (showModelDropdown.value && modelSelectTrigger.value) {
    const rect = modelSelectTrigger.value.getBoundingClientRect()
    modelSelectStyle.value = {
      top: `${rect.bottom + window.scrollY + 6}px`,
      left: `${rect.left + window.scrollX}px`
    }
  }
}

// 展开 GPU 卡片面板并定位（右对齐）
function showGpuPopoverPanel() {
  clearTimeout(gpuPopoverTimer)
  if (!gpuPillTrigger.value) return

  const rect = gpuPillTrigger.value.getBoundingClientRect()
  gpuPopoverStyle.value = {
    top: `${rect.bottom + window.scrollY + 6}px`,
    right: `${window.innerWidth - rect.right - window.scrollX}px`
  }
  showGpuPopover.value = true
}

// 延时收起 GPU 卡片
function hideGpuPopoverPanel() {
  clearTimeout(gpuPopoverTimer)
  gpuPopoverTimer = window.setTimeout(() => {
    showGpuPopover.value = false
  }, 200)
}

// 清除隐藏定时器（供 hover 悬浮框内部时使用）
function clearGpuPopoverTimer() {
  clearTimeout(gpuPopoverTimer)
}

// 选择模型时的统一代理
function onSelectModel(name: string) {
  selectModel(name)
  showModelDropdown.value = false
  showGpuPopover.value = false
}

// 获取模型显存开销文本
function getModelFootprint(name: string): string {
  const m = gpuStatus.value?.models?.find(mod => mod.name === name)
  return m?.footprint_gib ? `${m.footprint_gib.toFixed(1)} GB` : ''
}
const showSources = ref(false)
const sourcePanel = ref<InstanceType<typeof SourcePanel> | null>(null)
const msgContainer = ref<HTMLElement | null>()
const autoFollowBottom = ref(true)
const initialized = ref(false)

const pinnedChunks = ref<{ id: string; doc: string }[]>([])
const excludedChunks = ref<{ id: string; doc: string }[]>([])

function handlePinChunk(chunkId: string, item: EvidenceItem) {
  if (!pinnedChunks.value.some(c => c.id === chunkId)) {
    pinnedChunks.value.push({ id: chunkId, doc: item.document || '未知文档' })
    showToast('已锁定该 Chunk，将在下一轮提问中强行引入上下文')
  }
}

function handleExcludeChunk(chunkId: string, item: EvidenceItem) {
  if (!excludedChunks.value.some(c => c.id === chunkId)) {
    excludedChunks.value.push({ id: chunkId, doc: item.document || '未知文档' })
    showToast('已锁定排除该 Chunk，将在下一轮提问中忽略该段')
  }
}

function removePinnedChunk(index: number) {
  pinnedChunks.value.splice(index, 1)
}

function removeExcludedChunk(index: number) {
  excludedChunks.value.splice(index, 1)
}
const welcomeHint = `你好！我是 RAG 知识库助手。

我主要用于回答项目文档、配置、接口和业务资料相关问题。
你也可以直接上传文档或图片，让我帮你分析。`

const toast = ref('')          // 操作反馈
let toastTimer = 0

function showToast(msg: string) {
  toast.value = msg
  clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { toast.value = '' }, 3000)
}

const availableModels = ref<{ name: string; type?: string }[]>([])
const agentOrchestrationEnabled = ref(false)
const workMode = ref<WorkMode>(
  (localStorage.getItem('rag-work-mode') as WorkMode) || 'agent'
)

function setWorkMode(mode: WorkMode) {
  workMode.value = mode
  localStorage.setItem('rag-work-mode', mode)
}

function modelType(name: string, type?: string): string {
  if (type) return type
  // 后端未返回 type 时的降级分类
  const n = name.toLowerCase()
  if (n.includes('embedding')) return 'embedding'
  if (n.includes('vl') || n.includes('vision')) return 'vision'
  return 'llm'
}

const llmModels = computed(() => {
  const list = availableModels.value.filter(m => modelType(m.name, m.type) === 'llm').map(m => m.name)
  if (currentModel.value && !list.includes(currentModel.value)) {
    list.unshift(currentModel.value)
  }
  return list
})
const visionModels = computed(() => {
  const list = availableModels.value.filter(m => modelType(m.name, m.type) === 'vision').map(m => m.name)
  if (visionModel.value && !list.includes(visionModel.value)) {
    list.unshift(visionModel.value)
  }
  return list
})
const currentModel = ref(localStorage.getItem('rag-llm-model') || '')
const visionModel = ref(localStorage.getItem('rag-vision-model') || '')
const embeddingModel = ref(localStorage.getItem('rag-embedding-model') || '')
const thinkingEnabled = ref(localStorage.getItem('rag-thinking') === 'true')
const webSearchEnabled = ref(localStorage.getItem('rag-web-search') === 'true')
const agents = ref<AgentInfo[]>([])
const activeAgentId = ref(localStorage.getItem('rag-active-agent') || 'general')
const showAgentPicker = ref(false)
const activeAgent = computed(() => agents.value.find(a => a.id === activeAgentId.value))

function applyAgent(agent: AgentInfo) {
  activeAgentId.value = agent.id
  localStorage.setItem('rag-active-agent', agent.id)
  showAgentPicker.value = false
}

function toggleWebSearch() {
  webSearchEnabled.value = !webSearchEnabled.value
  localStorage.setItem('rag-web-search', String(webSearchEnabled.value))
}

const supportsThinking = computed(() => {
  const model = currentModel.value.toLowerCase()
  return model.includes('deepseek') || model.includes('qwen3') || model.includes('qwq')
})

const deepModeTitle = computed(() => (
  supportsThinking.value
    ? '开启后会启用重排序检索；支持的模型还会启用深度思考。'
    : '开启后会启用重排序检索；当前模型仅增强检索。'
))

function toggleThinking() {
  thinkingEnabled.value = !thinkingEnabled.value
  localStorage.setItem('rag-thinking', String(thinkingEnabled.value))
}

const kbList = ref<string[]>([])
const currentKb = ref(localStorage.getItem('rag-kb-name') || '全部知识库')
const docCategory = ref(localStorage.getItem('rag-doc-category') || '')
const entityName = ref(localStorage.getItem('rag-entity-name') || '')
const allowGeneralKnowledge = ref(localStorage.getItem('rag-allow-general') === 'true')
const showParamsPopover = ref(false)

function setDocCategory(val: string) {
  docCategory.value = val
  localStorage.setItem('rag-doc-category', val)
}

function setEntityName(val: string) {
  entityName.value = val
  localStorage.setItem('rag-entity-name', val)
}

function toggleAllowGeneralKnowledge() {
  allowGeneralKnowledge.value = !allowGeneralKnowledge.value
  localStorage.setItem('rag-allow-general', String(allowGeneralKnowledge.value))
}

function selectKb(name: string) {
  currentKb.value = name
  localStorage.setItem('rag-kb-name', name)
}

function onLayoutClick(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (showUploadPicker.value && !target.closest('.upload-wrap')) {
    showUploadPicker.value = false
  }
  // 点击空白处关闭自定义下拉菜单与 GPU 悬浮框
  if (showModelDropdown.value && !target.closest('.model-pill') && !target.closest('.model-dropdown-menu')) {
    showModelDropdown.value = false
  }
  if (showGpuPopover.value && !target.closest('.gpu-pill') && !target.closest('.gpu-popover')) {
    showGpuPopover.value = false
  }
}

function selectModel(name: string) {
  currentModel.value = name
  localStorage.setItem('rag-llm-model', name)
}
function selectVision(name: string) {
  visionModel.value = name
  localStorage.setItem('rag-vision-model', name)
}
/** 切换向量模型由 config.ini 配置，前端不允许用户选择 */

const chatHistory = computed(() => buildChatHistoryPayload(messages.value))
const showWelcomeHint = computed(() => messages.value.length === 0)

async function handleNewChat() {
  if (abortController.value) {
    abortController.value.abort()
    abortController.value = null
  }
  loading.value = false

  // 如果当前已经是空会话，且没有任何消息，直接跳过新建
  if (messages.value.length === 0 && currentSession.value && currentSession.value.title === '新建对话') {
    return
  }

  try {
    const created = await createChatSession('新建对话')
    activeSessionId.value = created.id
    sessions.value = [
      {
        id: created.id,
        title: created.title,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        message_count: 0,
      },
      ...sessions.value.filter((s) => s.id !== created.id),
    ]
    messages.value = []
    currentSources.value = []
    showSources.value = false
    pinnedChunks.value = []
    excludedChunks.value = []
  } catch (e: any) {
    showToast('创建新对话失败: ' + (e.message || '网络异常'))
  }
}

async function handleSwitchSession(sessionId: string) {
  if (activeSessionId.value === sessionId) return
  if (abortController.value) {
    abortController.value.abort()
    abortController.value = null
  }
  loading.value = false

  if (activeSessionId.value && messages.value.length > 0) {
    try {
      await persist()
    } catch (e: any) {
      showToast('保存当前对话失败: ' + (e.message || '网络异常'))
      return
    }
  }

  try {
    await setActiveChatSession(sessionId)
  } catch (e: any) {
    if (e.response?.status === 404) {
      const meta = await loadChatSessions()
      sessions.value = meta.sessions
      const nextSessionId = meta.activeSessionId || meta.sessions[0]?.id
      if (nextSessionId) {
        activeSessionId.value = nextSessionId
        messages.value = await loadSessionMessages(nextSessionId)
      } else {
        activeSessionId.value = ''
        messages.value = []
        currentSources.value = []
        pinnedChunks.value = []
        excludedChunks.value = []
      }
      showToast('该对话已不存在，已刷新会话列表')
      return
    }
    showToast('切换对话失败: ' + (e.message || '网络异常'))
    return
  }
  activeSessionId.value = sessionId
  messages.value = await loadSessionMessages(sessionId)
  const withSources = messages.value.filter((m) => m.role === 'assistant' && m.sources?.length)
  currentSources.value = withSources.length ? withSources[withSources.length - 1].sources! : []
  pinnedChunks.value = []
  excludedChunks.value = []
  scrollDown(true)
}

function startRenameSession(session: ChatSessionSummary, event?: Event) {
  event?.stopPropagation()
  editingSessionId.value = session.id
  editingTitle.value = session.title
  nextTick(() => {
    const inputs = renameInput.value
    if (inputs && inputs.length > 0) {
      inputs[0]?.focus()
      inputs[0]?.select()
    }
  })
}

async function commitRenameSession(session: ChatSessionSummary) {
  if (editingSessionId.value !== session.id) return
  const newTitle = editingTitle.value.trim()
  if (newTitle && newTitle !== session.title) {
    const oldTitle = session.title
    session.title = newTitle
    try {
      await renameChatSession(session.id, newTitle)
    } catch (e: any) {
      session.title = oldTitle
      showToast('重命名失败: ' + (e.message || '网络异常'))
    }
  }
  editingSessionId.value = null
}

function cancelRenameSession() {
  editingSessionId.value = null
}

async function handleDeleteSession(sessionId: string) {
  const ok = await showConfirm('确定删除该对话记录？')
  if (!ok) return

  const isCurrent = activeSessionId.value === sessionId

  if (isCurrent) {
    if (abortController.value) {
      abortController.value.abort()
      abortController.value = null
    }
    loading.value = false
    messages.value = []
    currentSources.value = []
    pinnedChunks.value = []
    excludedChunks.value = []
  }

  try {
    await deleteChatSession(sessionId)
  } catch (e: any) {
    showToast('删除对话失败: ' + (e.message || '网络异常'))
    return
  }

  sessions.value = sessions.value.filter((s) => s.id !== sessionId)

  if (isCurrent) {
    if (sessions.value.length > 0) {
      const nextId = sessions.value[0].id
      activeSessionId.value = nextId
      messages.value = await loadSessionMessages(nextId)
      const withSources = messages.value.filter((m) => m.role === 'assistant' && m.sources?.length)
      currentSources.value = withSources.length ? withSources[withSources.length - 1].sources! : []
      scrollDown(true)
    } else {
      await handleNewChat()
    }
  }
  showToast('对话已删除')
}

onMounted(async () => {
  let meta: Awaited<ReturnType<typeof loadChatSessions>>
  try {
    meta = await loadChatSessions()
  } catch (e: any) {
    showToast('加载会话失败: ' + (e.message || '服务端异常'))
    return
  }
  sessions.value = meta.sessions
  if (meta.sessions.length > 0) {
    activeSessionId.value = meta.activeSessionId || meta.sessions[0].id
    messages.value = await loadSessionMessages(activeSessionId.value)
  } else {
    try {
      const created = await createChatSession('新建对话')
      activeSessionId.value = created.id
      sessions.value = [
        {
          id: created.id,
          title: created.title,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          message_count: 0,
        },
      ]
      messages.value = []
    } catch (e) {
      console.warn('初始化新会话失败:', e)
    }
  }

  const withSources = messages.value.filter((m) => m.role === 'assistant' && m.sources?.length)
  currentSources.value = withSources.length ? withSources[withSources.length - 1].sources! : []
  initialized.value = true
  stats.value = await getStats().catch(() => null)
  try {
    const modelsResp = await getModels()
    availableModels.value = modelsResp.models
    embeddingModel.value = modelsResp.current.embedding
    agentOrchestrationEnabled.value = Boolean(modelsResp.current.agent_orchestration_enabled)
    if (!localStorage.getItem('rag-work-mode')) {
      workMode.value = agentOrchestrationEnabled.value ? 'agent' : 'linear'
    }

    const lastDefaultLlm = localStorage.getItem('rag-llm-default-model')
    if (lastDefaultLlm !== modelsResp.current.llm) {
      currentModel.value = modelsResp.current.llm || ''
      localStorage.setItem('rag-llm-default-model', modelsResp.current.llm || '')
      localStorage.setItem('rag-llm-model', modelsResp.current.llm || '')
    } else {
      currentModel.value = localStorage.getItem('rag-llm-model') || modelsResp.current.llm || ''
    }

    const lastDefaultVision = localStorage.getItem('rag-vision-default-model')
    if (lastDefaultVision !== modelsResp.current.vision) {
      visionModel.value = modelsResp.current.vision || ''
      localStorage.setItem('rag-vision-default-model', modelsResp.current.vision || '')
      localStorage.setItem('rag-vision-model', modelsResp.current.vision || '')
    } else {
      visionModel.value = localStorage.getItem('rag-vision-model') || modelsResp.current.vision || ''
    }
  } catch { /* 静默 */ }
  try {
    const kbResp = await getKnowledgeBases()
    kbList.value = kbResp.bases
    if (!kbResp.bases.includes(currentKb.value)) {
      currentKb.value = '全部知识库'
    }
  } catch { /* 静默 */ }
  try {
    const agentsResp = await getAgents()
    agents.value = agentsResp.agents
    const saved = agents.value.find(a => a.id === activeAgentId.value)
    if (saved) applyAgent(saved)
  } catch { /* 静默 */ }
  refreshGpu()
  gpuPollTimer = window.setInterval(refreshGpu, 5000)
  window.addEventListener('beforeunload', handleBeforeUnload)
})

function handleBeforeUnload() {
  if (initialized.value && activeSessionId.value && messages.value.length > 0) {
    saveSessionStateLocalSync(activeSessionId.value, messages.value, currentSession.value?.title)
  }
}

onUnmounted(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
  clearInterval(gpuPollTimer)
  clearTimeout(gpuNoticeTimer)
  clearTimeout(gpuPopoverTimer)
})

async function persist(titleOverride?: string) {
  if (!initialized.value || !activeSessionId.value) return
  const s = currentSession.value
  let title = titleOverride || s?.title
  if ((!title || title === '新建对话') && messages.value.length > 0) {
    const firstUserMsg = messages.value.find((m) => m.role === 'user' && m.content)
    if (firstUserMsg) {
      title = generateSessionTitle(firstUserMsg.content)
    }
  }
  const effectiveTitle = title || '新建对话'
  const idx = sessions.value.findIndex((item) => item.id === activeSessionId.value)
  if (idx >= 0) {
    sessions.value[idx].title = effectiveTitle
    sessions.value[idx].message_count = messages.value.filter((m) => !m.loading).length
    sessions.value[idx].updated_at = new Date().toISOString()
    sessions.value = [...sessions.value]
  }
  await saveSessionState(activeSessionId.value, messages.value, effectiveTitle)
}

async function handleCitationClick(message: Message, citationId: number) {
  if (!message.sources?.length) return
  currentSources.value = message.sources
  showSources.value = true
  await nextTick()
  sourcePanel.value?.focusCitation(citationId)
}

const abortController = ref<AbortController | null>(null)

function handleStop() {
  abortController.value?.abort()
  loading.value = false
  const last = messages.value.filter((m) => m.role === 'assistant').slice(-1)[0]
  if (last && last.loading) {
    last.status = undefined
    last.loading = false
    if (last.content) last.content += '\n\n*（已停止）*'
    else last.content = '*（已停止）*'
  }
  persist()
}

function applyClarification(msg: Message, data: ClarifyResult | undefined) {
  if (!data?.needs_clarification || !data.options || data.options.length < 1) return false
  msg.loading = false
  msg.status = undefined
  msg.clarification = {
    ask_question: data.ask_question || '请选择您要查询的具体模块或方向：',
    trigger: data.trigger,
    reason: data.reason,
    clarification_snapshot_id: data.clarification_snapshot_id || (data as any)?.snapshot_id,
    options: data.options,
  }
  loading.value = false
  return true
}

function createStreamHandler(
  targetMsg: Message,
  requestedMode: WorkMode = targetMsg.mode || workMode.value,
): KnowledgeStreamCallbacks {
  const streamMode = requestedMode
  targetMsg.mode = streamMode
  let inThinkTag = false
  let finalAnswerReceived = false

  if (streamMode === 'agent' && !targetMsg.blocks) {
    // 先把空数组挂到 reactive Message 上，再交给 projector 原位更新。
    // 否则 projector 若持有独立普通数组，SSE delta 虽到达但 Vue 不会逐段重渲染。
    targetMsg.blocks = []
  }
  const projector = streamMode === 'agent' ? new AgentBlockProjector(targetMsg.blocks!) : null

  return {
    onStatus: (status: string) => {
      if (streamMode !== 'linear') return
      targetMsg.status = status
      scrollDown()
    },
    onUnderstanding: (_data) => {
      // 内部状态仅记录在 trace，主界面不渲染
    },
    onLLMReasoningStart: (data) => {
      if (streamMode !== 'agent' || !projector) return
      targetMsg.status = undefined
      projector.handleReasoningStart(data)
      scrollDown()
    },
    onLLMReasoningDelta: (data) => {
      if (streamMode !== 'agent' || !projector) return
      projector.handleReasoningDelta(data)
      scrollDown()
    },
    onLLMReasoningEnd: (data) => {
      if (streamMode !== 'agent' || !projector) return
      projector.handleReasoningEnd(data)
      scrollDown()
    },
    onPublicExplanation: (data) => {
      if (streamMode !== 'agent' || !projector) return
      projector.handlePublicExplanation(data)
      scrollDown()
    },
    onDecision: (_data) => {
      // 内部调度决策仅记录在 trace，主界面不渲染
    },
    onGuard: (_data) => {
      // 内部安全防护仅记录在 trace，主界面不渲染
    },
    onEvidenceUpdate: (_data) => {
      // 内部证据库更新仅记录在 trace，主界面不渲染
    },
    onEvidenceGap: (_data) => {
      // 内部证据缺口仅记录在 trace，主界面不渲染
    },
    onFinalizationCheck: (_data) => {
      // 内部完备性检查仅记录在 trace，主界面不渲染
    },
    onCandidateStatus: (_data) => {
      // 内部候选草稿状态仅记录在 trace，主界面不渲染
    },
    onGroundingReviewStarted: (data) => {
      if (streamMode !== 'agent' || !projector) return
      projector.handleGroundingReviewStarted(data)
      scrollDown()
    },
    onReviewStatus: (data) => {
      if (streamMode !== 'agent' || !projector) return
      projector.handleReviewStatus(data)
      scrollDown()
    },
    onRewriteStatus: (data) => {
      if (streamMode !== 'agent' || !projector) return
      if (data.status === 'failed') {
        projector.handleSystemError('回答修正失败，本次未发布未经证据支持的结论。', 'rewrite-failed')
        scrollDown()
      }
    },
    onPublication: (_data) => {
      // 内部发布审计仅记录在 trace，主界面不渲染
    },
    onExecutionError: (data) => {
      if (streamMode !== 'agent' || !projector) return
      projector.handleSystemError(data.message || 'Agent 执行异常', `exec-err:${data.code || ''}`)
      scrollDown()
    },
    onToken: (token: string) => {
      // Agent strict-grounding 下 token 永远不是正式答案来源；即使后端协议退化也 fail-closed。
      if (streamMode === 'agent' || finalAnswerReceived) return
      targetMsg.status = undefined
      targetMsg.loading = false
      let text = token
      // 检查 <think> 标签流
      if (!inThinkTag && text.includes('<think>')) {
        const parts = text.split('<think>')
        if (parts[0]) {
          targetMsg.content += parts[0]
        }
        inThinkTag = true
        text = parts.slice(1).join('<think>')
      }

      if (inThinkTag) {
        if (text.includes('</think>')) {
          const parts = text.split('</think>')
          inThinkTag = false
          text = parts.slice(1).join('</think>')
          if (text) {
            targetMsg.content += text
          }
        }
      } else {
        targetMsg.content += text
      }
      scrollDown()
    },
    onThinking: (thought: string) => {
      if (streamMode !== 'linear' || !thought) return
      targetMsg.thinking = (targetMsg.thinking || '') + thought
      scrollDown()
    },
    onToolStart: (data) => {
      if (streamMode !== 'agent' || !projector) return
      projector.handleToolStart(data)
      scrollDown()
    },
    onToolResult: (data) => {
      if (streamMode !== 'agent' || !projector) return
      projector.handleToolResult(data)
      scrollDown()
    },
    onToolEnd: (data) => {
      if (streamMode !== 'agent' || !projector) return
      projector.handleToolResult(data)
      scrollDown()
    },
    onFinalAnswer: (answer: string) => {
      finalAnswerReceived = true
      targetMsg.status = undefined
      let cleanAnswer = answer || ''
      if (cleanAnswer.includes('<think>')) {
        const parts = cleanAnswer.split('</think>')
        cleanAnswer = parts.length > 1 ? parts.slice(1).join('</think>').trim() : parts[0].split('<think>')[0].trim()
      }
      targetMsg.content = cleanAnswer
      targetMsg.loading = false
      if (projector) {
        projector.handleFinalAnswer(cleanAnswer)
      }
      scrollDown()
    },
    onSources: (sources) => {
      currentSources.value = sources
      targetMsg.sources = sources
    },
    onTrace: (traceId: string) => {
      targetMsg.trace_id = traceId ? String(traceId).trim() : null
    },
    onPipeline: (pipelineData) => {
      if (streamMode !== 'linear') return
      if (!targetMsg.pipelineSteps) targetMsg.pipelineSteps = []
      const stageIndex = targetMsg.pipelineSteps.findIndex(step => step.stage === pipelineData.stage)
      if (stageIndex >= 0) targetMsg.pipelineSteps[stageIndex] = pipelineData
      else targetMsg.pipelineSteps.push(pipelineData)
      if (pipelineData.evidence) {
        targetMsg.evidencePack = pipelineData.evidence
      }
    },
    onNotice: (notice: string) => {
      showGpuNotice(notice)
      if (streamMode === 'agent' && projector) {
        projector.handleNotice(notice, 'warning')
      }
      scrollDown()
    },
    onClarify: (data: ClarifyResult) => {
      applyClarification(targetMsg, data)
    },
    onDone: async () => {
      targetMsg.status = undefined
      targetMsg.loading = false
      if (targetMsg.content.includes('<think>')) {
        const parts = targetMsg.content.split('</think>')
        targetMsg.content = parts.length > 1 ? parts.slice(1).join('</think>').trim() : parts[0].split('<think>')[0].trim()
      }
      loading.value = false
      abortController.value = null
      pinnedChunks.value = []
      excludedChunks.value = []
      await persist()
      scrollDown()
    },
    onError: (error) => { throw error },
  }
}

async function handleSend(text: string, image?: File) {
  const requestMode: WorkMode = image ? 'linear' : workMode.value
  let imageUrl: string | undefined
  if (image) {
    imageUrl = await fileToDataUrl(image)
  }
  const userMsg: Message = {
    id: Date.now().toString(),
    role: 'user',
    content: text,
    imageUrl,
  }
  messages.value.push(userMsg)
  await persist()
  scrollDown(true)

  const aiId = (Date.now() + 1).toString()
  messages.value.push({
    id: aiId,
    role: 'assistant',
    content: '',
    mode: requestMode,
    loading: true,
    status: image
      ? '正在分析图片...'
      : requestMode === 'linear'
        ? '正在理解问题...'
        : undefined,
  })
  loading.value = true
  scrollDown(true)

  function lastAiMsg() {
    return messages.value[messages.value.findIndex((m) => m.id === aiId)]
  }

  if (image) {
    abortController.value = new AbortController()
    let streamOk = false
    try {
      await queryImageStream(text, image, {
        onToken: (token) => {
          const msg = lastAiMsg()
          msg.status = undefined
          msg.content += token
          msg.loading = false
          scrollDown()
        },
        onDone: () => {
          streamOk = true
          lastAiMsg().status = undefined
          lastAiMsg().loading = false
        },
        onError: (err) => { throw err },
      }, visionModel.value || undefined, abortController.value?.signal)
      if (streamOk) {
        await persist()
      }
    } catch (e: any) {
      if ((e as DOMException)?.name !== 'AbortError') {
        lastAiMsg().status = undefined
        lastAiMsg().content = `**出错了**\n\n${e.message}`
      }
      lastAiMsg().loading = false
      await persist()
    } finally {
      loading.value = false
      abortController.value = null
      scrollDown()
    }
    return
  }

  try {
    abortController.value = new AbortController()

    const history = chatHistory.value.slice(0, -1)
    let streamOk = false
    try {
      const llmModel = currentModel.value || undefined
      await queryKnowledgeStream(
        text,
        history,
        createStreamHandler(lastAiMsg(), requestMode),
        llmModel,
        currentKb.value,
        thinkingEnabled.value || undefined,
        webSearchEnabled.value || undefined,
        abortController.value?.signal,
        activeAgent.value?.system_prompt,
        allowGeneralKnowledge.value,
        docCategory.value || undefined,
        entityName.value || undefined,
        pinnedChunks.value.map(c => c.id),
        excludedChunks.value.map(c => c.id),
        undefined,
        undefined,
        requestMode,
      )
      streamOk = true
    } catch {
      lastAiMsg().status = undefined
      if (!streamOk && !abortController.value) {
        try {
          const result = await queryKnowledge(
            text,
            history,
            currentModel.value || undefined,
            currentKb.value,
            thinkingEnabled.value || undefined,
            webSearchEnabled.value || undefined,
            undefined,
            activeAgent.value?.system_prompt,
            allowGeneralKnowledge.value,
            docCategory.value || undefined,
            entityName.value || undefined,
            requestMode,
          )
          const msg = lastAiMsg()
          msg.content = result.answer
          msg.loading = false
          currentSources.value = result.source_documents
          msg.sources = result.source_documents
          applyClarification(msg, result.clarification)
          if (result.downshift_notice) showGpuNotice(result.downshift_notice)
        } catch (err: any) {
          const msg = lastAiMsg()
          msg.content = `**生成异常**：${err.message || '服务未响应'}`
          msg.loading = false
        } finally {
          await persist()
          loading.value = false
          scrollDown()
        }
      }
    }
  } catch (e: any) {
    if ((e as DOMException)?.name === 'AbortError') {
      // 手动中止
    } else {
      lastAiMsg().status = undefined
      lastAiMsg().content = `**出错了**\n\n${e.message || '请求失败'}`
      lastAiMsg().loading = false
    }
    await persist()
    loading.value = false
    abortController.value = null
  }
}

async function handleFeedbackChange(msg: Message, feedback: 'useful' | 'unuseful') {
  if (msg.feedback === feedback) {
    msg.feedback = null
  } else {
    msg.feedback = feedback
  }
  await persist()

  if (msg.feedback) {
    const chunkIds = (msg.sources || [])
      .map((s) => s.metadata?.chunk_id)
      .filter((id): id is string => !!id)

    const msgIndex = messages.value.findIndex((m) => m.id === msg.id)
    const userQuery = msgIndex > 0 && messages.value[msgIndex - 1].role === 'user'
      ? messages.value[msgIndex - 1].content
      : ''

    submitUserFeedback({
      user_id: 'chat_user',
      query_text: userQuery,
      answer_text: msg.content,
      referenced_chunk_ids: chunkIds,
      rating: msg.feedback === 'useful' ? 'up' : 'down',
      trace_id: msg.trace_id || undefined,
    }).catch(() => {})
  }

  if (msg.trace_id) {
    updateQaTraceFeedback(msg.trace_id, msg.feedback).catch(() => {})
  }
}

async function handleCurrentChunkFeedback(chunkId: string, rating: 'down', reason?: string) {
  const activeMsg = messages.value.find((m) => m.sources === currentSources.value) || messages.value[messages.value.length - 1]
  const msgIndex = messages.value.findIndex((m) => m.id === activeMsg?.id)
  const userQuery = msgIndex > 0 && messages.value[msgIndex - 1].role === 'user'
    ? messages.value[msgIndex - 1].content
    : ''

  try {
    await submitUserFeedback({
      user_id: 'chat_user',
      query_text: userQuery,
      answer_text: activeMsg?.content || '',
      referenced_chunk_ids: [chunkId],
      rating: rating,
      reason: reason || '单 Chunk 差评',
      trace_id: activeMsg?.trace_id || undefined,
      feedback_scope: 'chunk',
      target_chunk_id: chunkId,
    })
  } catch (err) {
    console.error('提交单 Chunk 反馈失败:', err)
  }
}

/** 用户点击反问卡片的选项后触发 */
async function handleSelectClarificationOption(aiMsg: Message, selection: ClarificationSelection) {
  if (!aiMsg.clarification || aiMsg.clarification.selectedId || loading.value) return

  const { option, kind, freeText } = selection
  const requestMode = aiMsg.mode || workMode.value
  aiMsg.mode = requestMode

  aiMsg.clarification.selectedId = option.id
  aiMsg.loading = true
  const selectedText = kind === 'free_text' ? (freeText || '').trim() : option.label
  aiMsg.status = requestMode === 'linear'
    ? `已选择「${selectedText}」，正在检索回答...`
    : undefined
  loading.value = true
  scrollDown()

  const aiIndex = messages.value.findIndex((m) => m.id === aiMsg.id)
  let userText = ''
  let history: ReturnType<typeof buildChatHistoryPayload> = []
  if (aiIndex > 0 && messages.value[aiIndex - 1].role === 'user') {
    userText = messages.value[aiIndex - 1].content
    // 保留此前 assistant.sources，供追问 source_anchor；不要只传 role+content
    history = buildChatHistoryPayload(messages.value.slice(0, aiIndex - 1), {
      dropTrailingLoading: false,
    })
  } else {
    userText = messages.value.filter((m) => m.role === 'user').slice(-1)[0]?.content || ''
    history = chatHistory.value.slice(0, -1)
  }

  const clarificationQuestion = aiMsg.clarification.ask_question
  const clarificationSelected = selectedText
  const clarificationCallback: ClarificationCallbackRequest = {
    optionId: option.id,
    snapshotId: aiMsg.clarification.clarification_snapshot_id || '',
    selectionKind: kind,
    freeText: kind === 'free_text' ? selectedText : undefined,
  }

  if (kind === 'other' || kind === 'free_text') {
    aiMsg.clarification.otherText = selectedText
  }

  // Candidate metadata is returned for callback resolution and traceability, not
  // trusted as a direct entity filter. The backend resolves the selected option id.
  const docCategoryVal = docCategory.value || undefined
  const entityNameVal = entityName.value || undefined

  try {
    abortController.value = new AbortController()
    const llmModel = currentModel.value || undefined
    await queryKnowledgeStream(
      userText,
      history,
      createStreamHandler(aiMsg, requestMode),
      llmModel,
      currentKb.value,
      thinkingEnabled.value || undefined,
      webSearchEnabled.value || undefined,
      abortController.value?.signal,
      activeAgent.value?.system_prompt,
      allowGeneralKnowledge.value,
      docCategoryVal,
      entityNameVal,
      undefined,
      undefined,
      clarificationQuestion,
      clarificationSelected,
      requestMode,
      clarificationCallback,
    )
  } catch (e: any) {
    if ((e as DOMException)?.name === 'AbortError') {
      // 手动中止
    } else {
      aiMsg.status = undefined
      aiMsg.content = `**出错了**\n\n${e.message || '请求失败'}`
      aiMsg.loading = false
    }
    loading.value = false
    abortController.value = null
  }
}

/** 取消反问并终止回答 */
function handleCancelClarification(aiMsg: Message) {
  if (aiMsg.clarification) {
    aiMsg.clarification = undefined
    aiMsg.content = "已终止思考和回答。"
    aiMsg.loading = false
    aiMsg.status = undefined
    persist()
  }
}

const docInput = ref<HTMLInputElement>()
const uploading = ref(false)
const showUploadPicker = ref(false)
const uploadTargetKb = ref('')
const scanningOverlay = ref(false)
const uploadDocumentProfile = ref<DocumentProfile>('section_based')

function clickUpload() {
  const realKbs = kbList.value.filter(b => b !== '全部知识库')
  if (realKbs.length <= 1) {
    // 只有一个知识库或不指定，直接选文件
    if (realKbs.length === 1) uploadTargetKb.value = realKbs[0]
    docInput.value?.click()
    return
  }
  showUploadPicker.value = !showUploadPicker.value
}

function pickUploadKb(kb: string) {
  uploadTargetKb.value = kb
  showUploadPicker.value = false
  docInput.value?.click()
}

async function handleUpload(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const targetKb = uploadTargetKb.value || '未分类'
  uploading.value = true
  showToast('正在入库…')
  try {
    const result = await uploadDocument(file, targetKb, uploadDocumentProfile.value)
    let decMsg = ''
    if (result.decisions && result.decisions.length > 0) {
      decMsg = '\n\n**决策及状态明细：**\n'
      result.decisions.forEach(d => {
        const statusText = d.status === 'queued' ? '⏳ 排队中' : '🚫 已排除'
        const locatorStr = d.locator ? ` (位置: \`${d.locator}\`)` : ''
        decMsg += `- **${d.file_name}** [${statusText}]：${d.message}${locatorStr}\n`
      })
    }
    messages.value.push({
      id: Date.now().toString(), role: 'assistant',
      content: `已上传 **${result.file_name}** → 知识库「${targetKb}」\n- 新增分块数: ${result.chunks_count} | 扫描新增: ${result.new_files} | 跳过: ${result.skipped_files} | 错误: ${result.errors}${decMsg}`,
    })
    stats.value = await getStats()
    await persist()
    showToast('上传成功')
  } catch (e: any) {
    messages.value.push({
      id: Date.now().toString(), role: 'assistant',
      content: `上传失败：${e.message}`,
    })
    showToast('上传失败')
  } finally {
    uploading.value = false
    uploadTargetKb.value = ''
  }
  input.value = ''
  scrollDown()
}

async function handleScan() {
  scanningOverlay.value = true
  try {
    const r = await triggerScan()
    let decMsg = ''
    if (r.decisions && r.decisions.length > 0) {
      decMsg = '\n\n**决策及状态明细：**\n'
      r.decisions.forEach(d => {
        const statusText = d.status === 'queued' ? '⏳ 排队中' : '🚫 已排除'
        const locatorStr = d.locator ? ` (位置: \`${d.locator}\`)` : ''
        decMsg += `- **${d.file_name}** [${statusText}]：${d.message}${locatorStr}\n`
      })
    }
    messages.value.push({
      id: Date.now().toString(), role: 'assistant',
      content: `扫描完成：新增 ${r.new_files} 个，跳过 ${r.skipped_files} 个，失败 ${r.errors} 个${decMsg}`,
    })
    stats.value = await getStats()
    await persist()
    scrollDown()
    showToast('扫描完成')
  } catch (e: any) {
    messages.value.push({
      id: Date.now().toString(), role: 'assistant',
      content: `扫描失败：${e.message}`,
    })
    showToast('扫描失败')
  } finally {
    scanningOverlay.value = false
  }
}

async function handleClear() {
  const ok = await showConfirm('确定清空当前对话记录？')
  if (!ok) return

  // 1. 中止可能正在进行的流式/分析请求，避免旧回调二次污染
  if (abortController.value) {
    abortController.value.abort()
    abortController.value = null
  }
  loading.value = false

  // 2. 清空人工干预上下文与来源面板
  pinnedChunks.value = []
  excludedChunks.value = []
  currentSources.value = []
  showSources.value = false

  // 3. 清空页面消息并更新当前会话状态
  messages.value = []
  if (activeSessionId.value) {
    await saveSessionState(activeSessionId.value, [], currentSession.value?.title || '新建对话')
    const s = sessions.value.find((item) => item.id === activeSessionId.value)
    if (s) s.message_count = 0
  }

  showToast('当前对话已清空')
}

// ---- 确认弹窗 ----
const confirmVisible = ref(false)
const confirmMessage = ref('')
let confirmResolve: ((v: boolean) => void) | null = null

function showConfirm(msg: string): Promise<boolean> {
  confirmMessage.value = msg
  confirmVisible.value = true
  return new Promise((resolve) => { confirmResolve = resolve })
}
function confirmOk() {
  confirmVisible.value = false
  confirmResolve?.(true)
}
function confirmCancel() {
  confirmVisible.value = false
  confirmResolve?.(false)
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.readAsDataURL(file)
  })
}

function handleMessageScroll() {
  const container = msgContainer.value
  if (!container) return
  autoFollowBottom.value = isNearScrollBottom(container)
}

/**
 * Streaming follows the bottom only while the user is already near it.
 * User-initiated actions such as sending a message or switching sessions may
 * explicitly force one jump to the latest message.
 */
function scrollDown(force = false) {
  if (force) autoFollowBottom.value = true
  if (!autoFollowBottom.value) return

  nextTick(() => {
    const container = msgContainer.value
    if (!container) return
    // The user may have scrolled upward between scheduling and this tick.
    if (!force && !autoFollowBottom.value) return
    container.scrollTop = container.scrollHeight
  })
}
</script>

<template>
  <div class="chat-layout" @click="onLayoutClick">
    <!-- 左侧历史会话侧边栏 -->
    <aside class="chat-sidebar" :class="{ 'chat-sidebar--collapsed': !showSidebar }">
      <div class="sidebar-header">
        <button class="new-chat-btn" @click="handleNewChat" title="开启新对话">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          <span class="btn-text">新建对话</span>
        </button>
        <button class="toggle-sidebar-btn" @click="toggleSidebar" title="收起会话列表">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
            <line x1="9" y1="3" x2="9" y2="21"></line>
          </svg>
        </button>
      </div>

      <div class="session-list-container">
        <div v-if="sessions.length === 0" class="session-list-empty">
          暂无历史对话
        </div>
        <div
          v-for="sess in sessions"
          :key="sess.id"
          class="session-item"
          :class="{ active: sess.id === activeSessionId }"
          @click="handleSwitchSession(sess.id)"
        >
          <svg class="session-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
          </svg>

          <!-- 标题展示与行内编辑 -->
          <div v-if="editingSessionId === sess.id" class="session-title-edit" @click.stop>
            <input
              ref="renameInput"
              v-model="editingTitle"
              class="session-rename-input"
              @keydown.enter="commitRenameSession(sess)"
              @keydown.esc="cancelRenameSession"
              @blur="commitRenameSession(sess)"
            />
          </div>
          <div v-else class="session-title" :title="sess.title" @dblclick="startRenameSession(sess, $event)">
            {{ sess.title }}
          </div>

          <!-- 操作按钮（重命名/删除） -->
          <div class="session-actions" @click.stop>
            <button
              class="session-action-btn"
              title="重命名"
              @click="startRenameSession(sess, $event)"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 20h9"></path>
                <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>
              </svg>
            </button>
            <button
              class="session-action-btn delete-btn"
              title="删除对话"
              @click="handleDeleteSession(sess.id)"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
              </svg>
            </button>
          </div>
        </div>
      </div>
    </aside>

    <div class="chat-main">
      <header class="header">
        <div class="header-left">
          <button v-if="!showSidebar" class="sidebar-expand-btn" @click="toggleSidebar" title="展开会话列表">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
              <line x1="9" y1="3" x2="9" y2="21"></line>
            </svg>
          </button>
          <h1 class="title">RAG 知识库</h1>
          <span class="session-badge" :title="currentSessionTitle">{{ currentSessionTitle }}</span>
          <span v-if="stats" class="stat">{{ stats.total_chunks }} chunks</span>
        </div>
        <div class="header-right">
          <div class="model-group">
            <div class="agent-pill" @click="showAgentPicker = !showAgentPicker">
              <span class="agent-icon" v-html="activeAgent?.icon || '🤖'"></span>
              <span class="agent-name">{{ activeAgent?.name || '通用助手' }}</span>
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="6 9 12 15 18 9"/>
              </svg>
            </div>
            <Teleport to="body">
              <div v-if="showAgentPicker" class="agent-popover-overlay" @click.self="showAgentPicker = false">
                <div class="agent-popover" @click.stop>
                  <div v-if="agents.length === 0" class="agent-empty">正在加载...</div>
                  <div
                    v-for="a in agents" :key="a.id"
                    class="agent-opt"
                    :class="{ active: a.id === activeAgentId }"
                    @click="applyAgent(a)"
                  >
                    <span class="agent-opt-icon" v-html="a.icon"></span>
                    <div class="agent-opt-info">
                      <div class="agent-opt-name">{{ a.name }}</div>
                      <div class="agent-opt-desc">{{ a.description }}</div>
                    </div>
                  </div>
                </div>
              </div>
            </Teleport>
            <span class="model-pill">
              知识库
              <select v-if="kbList.length > 0" class="model-select" :value="currentKb" @change="selectKb(($event.target as HTMLSelectElement).value)">
                <option v-for="kb in kbList" :key="kb" :value="kb">{{ kb }}</option>
              </select>
              <span v-else class="model-tag">{{ currentKb }}</span>
            </span>
            <span class="model-pill" ref="modelSelectTrigger" :class="{ 'has-dropdown': llmModels.length > 0 }">
              问答
              <span v-if="llmModels.length > 0" class="model-custom-select" @click.stop="toggleModelDropdown">
                <span class="selected-val">{{ currentModel.replace(':latest', '') }}</span>
                <span v-if="gpuFits(currentModel) === false" class="badge-mini badge-error">⚠️</span>
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="select-chevron" :class="{ open: showModelDropdown }">
                  <polyline points="6 9 12 15 18 9"/>
                </svg>
              </span>
              <span v-else class="model-tag">{{ currentModel.replace(':latest','') || '…' }}</span>
            </span>
            <Teleport to="body">
              <Transition name="fade-slide">
                <div v-if="showModelDropdown" class="model-dropdown-menu" :style="modelSelectStyle">
                  <div
                    v-for="m in llmModels"
                    :key="m"
                    class="model-dropdown-item"
                    :class="{ active: m === currentModel, 'gpu-insufficient': gpuFits(m) === false }"
                    @click="onSelectModel(m)"
                  >
                    <div class="model-item-left">
                      <span class="model-item-name">{{ m.replace(':latest', '') }}</span>
                      <span class="model-item-footprint" v-if="getModelFootprint(m)">{{ getModelFootprint(m) }}</span>
                    </div>
                    <div class="model-item-right">
                      <span v-if="m === currentModel" class="badge-status current">当前</span>
                      <span v-else-if="m === recommendedModel" class="badge-status recommended">推荐</span>
                      <span v-else-if="gpuFits(m) === false" class="badge-status insufficient">显存不足</span>
                      <span v-else class="badge-status fit">可用</span>
                    </div>
                  </div>
                </div>
              </Transition>
            </Teleport>
            <span class="model-pill">
              视觉
              <select v-if="visionModels.length > 0" class="model-select" v-model="visionModel" @change="selectVision(visionModel)">
                <option v-for="m in visionModels" :key="m" :value="m">
                  {{ m.replace(':latest', '') }}
                </option>
              </select>
              <span v-else class="model-tag">{{ visionModel.replace(':latest','') || '…' }}</span>
            </span>
            <span class="model-pill" title="嵌入模型需通过配置文件修改">嵌入 {{ embeddingModel.replace(':latest','') || '…' }}</span>
            <span
              v-if="gpuStatus"
              ref="gpuPillTrigger"
              class="gpu-pill"
              :class="{ offline: !gpuStatus.gpu, active: showGpuPopover }"
              @mouseenter="showGpuPopoverPanel"
              @mouseleave="hideGpuPopoverPanel"
              @click.stop="showGpuPopover = !showGpuPopover"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="2" y="4" width="20" height="14" rx="2"/><path d="M8 21h8M12 18v3"/>
              </svg>
              <span v-if="gpuStatus.gpu" class="gpu-vram">
                <span class="gpu-bar"><span :class="gpuVramClass" :style="{ width: vramPct + '%' }"></span></span>
                {{ vramText }}
              </span>
              <span v-else>GPU 离线</span>
              <span
                v-if="gpuStatus.gpu && recommendedModel && recommendedModel !== currentModel"
                class="gpu-reco"
                title="当前显存下的推荐模型，点击选用"
                @click.stop="onSelectModel(recommendedModel)"
              >
                推荐 {{ recommendedModel.replace(':latest','') }}
              </span>
            </span>
            <Teleport to="body">
              <Transition name="fade-slide">
                <div
                  v-if="showGpuPopover && gpuStatus"
                  class="gpu-popover"
                  :class="{ offline: !gpuStatus.gpu }"
                  :style="gpuPopoverStyle"
                  @mouseenter="clearGpuPopoverTimer"
                  @mouseleave="hideGpuPopoverPanel"
                  @click.stop
                >
                  <template v-if="gpuStatus.gpu">
                    <div class="gpu-popover-header">
                      <div class="gpu-title-group">
                        <svg class="gpu-popover-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <rect x="2" y="4" width="20" height="14" rx="2"/><path d="M8 21h8M12 18v3"/>
                        </svg>
                        <span class="gpu-card-name">{{ gpuStatus.gpu.name }}</span>
                      </div>
                      <span class="gpu-status-dot" :class="gpuLoadClass" title="GPU 状态"></span>
                    </div>

                    <div class="gpu-popover-section">
                      <div class="vram-label-row">
                        <span class="section-label">显存占用 (VRAM)</span>
                        <span class="vram-values">{{ vramText }} ({{ vramPct }}%)</span>
                      </div>
                      <div class="gpu-popover-bar">
                        <div class="gpu-popover-bar-fill" :class="gpuVramClass" :style="{ width: vramPct + '%' }"></div>
                      </div>
                    </div>

                    <div class="gpu-popover-grid">
                      <div class="grid-item">
                        <span class="grid-label">利用率</span>
                        <span class="grid-val">{{ gpuStatus.gpu.utilization ?? '-' }}%</span>
                      </div>
                      <div class="grid-item">
                        <span class="grid-label">核心温度</span>
                        <span class="grid-val" :class="tempClass">{{ gpuStatus.gpu.temperature ?? '-' }}℃</span>
                      </div>
                      <div class="grid-item" v-if="gpuStatus.gpu.power_draw !== undefined && gpuStatus.gpu.power_draw !== null">
                        <span class="grid-label">实时功耗</span>
                        <span class="grid-val">{{ gpuStatus.gpu.power_draw }}W</span>
                      </div>
                    </div>

                    <div class="gpu-popover-section">
                      <div class="section-subtitle">可用模型与显存要求</div>
                      <div class="gpu-models-list">
                        <div
                          v-for="m in gpuStatus.models"
                          :key="m.name"
                          class="gpu-model-row"
                          :class="{ active: m.name === currentModel, disabled: m.fits === false }"
                          @click="onSelectModel(m.name)"
                        >
                          <div class="model-row-left">
                            <span class="model-row-name">{{ m.name.replace(':latest', '') }}</span>
                            <span class="model-row-footprint" v-if="m.footprint_gib">约 {{ m.footprint_gib.toFixed(1) }} GB</span>
                          </div>
                          <div class="model-row-right">
                            <span v-if="m.name === currentModel" class="tag-status tag-current">当前</span>
                            <span v-else-if="m.name === recommendedModel" class="tag-status tag-reco">推荐</span>
                            <span v-else-if="m.fits === false" class="tag-status tag-insufficient">显存不足</span>
                            <span v-else class="tag-status tag-fit">可用</span>
                          </div>
                        </div>
                      </div>
                    </div>

                    <div v-if="recommendedModel && recommendedModel !== currentModel" class="gpu-popover-footer">
                      <div class="footer-notice">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
                        </svg>
                        <span>推荐选用 <strong>{{ recommendedModel.replace(':latest','') }}</strong></span>
                      </div>
                      <button class="btn-switch-reco" @click="onSelectModel(recommendedModel)">立即切换</button>
                    </div>
                  </template>
                  <template v-else>
                    <div class="gpu-offline-title">GPU 监控服务离线</div>
                    <p class="gpu-offline-desc">未检测到本地 GPU 运行数据。这通常是因为后端未启用显存监控，或 <code>gpu-agent</code> 服务未启动（默认 11435 端口）。</p>
                    <div class="gpu-offline-help">
                      <strong>启用方式：</strong>
                      <ol>
                        <li>在服务器或本机启动 <code>gpu-agent</code>。</li>
                        <li>检查本项目的 <code>config.ini</code> 配置文件中 <code>[gpu_agent]</code> 的 <code>enabled</code> 与 <code>base_url</code> 配置。</li>
                      </ol>
                    </div>
                  </template>
                </div>
              </Transition>
            </Teleport>
            <button class="think-btn" :class="{ active: thinkingEnabled }" @click="toggleThinking" :title="deepModeTitle">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><circle cx="12" cy="8" r="0.5" fill="currentColor"/>
              </svg>
              深度模式
            </button>
            <button class="think-btn" :class="{ active: webSearchEnabled }" @click="toggleWebSearch" title="联网搜索">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
              </svg>
              联网
            </button>
            <button class="think-btn" :class="{ active: showParamsPopover || !!docCategory || !!entityName }" @click="showParamsPopover = !showParamsPopover" title="设置分类领域、实体锚定与通用知识">
              高级参数
            </button>
            <Teleport to="body">
              <div v-if="showParamsPopover" class="agent-popover-overlay" @click.self="showParamsPopover = false">
                <div class="agent-popover params-popover" @click.stop>
                  <div class="popover-title">高级问答控制参数</div>
                  <div class="pop-field">
                    <label>分类领域 (doc_category)</label>
                    <input class="pop-input" :value="docCategory" placeholder="如: 技术文档 / 论坛" @input="setDocCategory(($event.target as HTMLInputElement).value)" />
                  </div>
                  <div class="pop-field">
                    <label>产品/实体锚定 (entity_name)</label>
                    <input class="pop-input" :value="entityName" placeholder="如: StampServer / UE" @input="setEntityName(($event.target as HTMLInputElement).value)" />
                  </div>
                  <div class="pop-field check-field">
                    <label class="pop-check-label">
                      <input type="checkbox" :checked="allowGeneralKnowledge" @change="toggleAllowGeneralKnowledge()" />
                      允许无上下文时使用通用知识回答
                    </label>
                  </div>
                </div>
              </div>
            </Teleport>
          </div>

          <label class="profile-picker" title="选择文档结构，系统不会根据文件名自动猜测">
            <span>文档类型</span>
            <select v-model="uploadDocumentProfile">
              <option v-for="option in DOCUMENT_PROFILE_OPTIONS" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </label>
          <input ref="docInput" type="file" accept=".pdf,.docx,.doc,.txt,.md,.xls,.xlsx" hidden @change="handleUpload" />
          <div class="upload-wrap">
            <button class="icon-btn" @click="clickUpload" :disabled="uploading" title="上传文档">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
            </button>
            <Transition name="pop">
              <div v-if="showUploadPicker" class="upload-popover">
                <div class="popover-title">上传到哪个知识库？</div>
                <button
                  v-for="kb in kbList.filter(b => b !== '全部知识库')"
                  :key="kb"
                  class="popover-item"
                  @click="pickUploadKb(kb)"
                >{{ kb }}</button>
              </div>
            </Transition>
          </div>
          <button class="icon-btn" :class="{ active: showSources }" @click="showSources = !showSources" title="参考来源">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
            </svg>
            <span v-if="currentSources.length > 0" class="badge-dot">{{ currentSources.length }}</span>
          </button>
          <button class="icon-btn" @click="handleScan" :disabled="loading" title="重新扫描">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="23 4 23 10 17 10"/>
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
            </svg>
          </button>
          <button class="icon-btn icon-btn--danger" @click="handleClear" title="清空对话">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
          </button>
        </div>
      </header>

      <Transition name="slide-fade">
        <div v-if="gpuNotice" class="gpu-notice">
          <span class="gpu-notice-text">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="gpu-notice-icon">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
            </svg>
            {{ gpuNotice }}
          </span>
          <button class="gpu-notice-close" @click="gpuNotice = ''" title="关闭提示">✕</button>
        </div>
      </Transition>

      <div ref="msgContainer" class="msg-list" @scroll.passive="handleMessageScroll">
        <div class="msg-wrap">
          <div v-if="showWelcomeHint" class="welcome-panel">
            <div class="welcome-badge">助手说明</div>
            <div class="welcome-title">优先提问项目资料，回复会更准确</div>
            <p class="welcome-text">{{ welcomeHint }}</p>
          </div>
          <ChatMessage
            v-for="msg in messages" :key="msg.id"
            :role="msg.role" :content="msg.content"
            :mode="msg.mode"
            :image-url="msg.imageUrl" :loading="msg.loading"
            :status="msg.status"
            :thinking="msg.thinking"
            :blocks="msg.blocks"
            :sources="msg.sources"
            :clarification="msg.clarification"
            :feedback="msg.feedback"
            :trace-id="msg.trace_id"
            :pipeline-steps="msg.pipelineSteps"
            :evidence-pack="msg.evidencePack"
            @citation-click="handleCitationClick(msg, $event)"
            @select-clarification-option="handleSelectClarificationOption(msg, $event)"
            @cancel-clarification="handleCancelClarification(msg)"
            @feedback-change="handleFeedbackChange(msg, $event)"
            @pin-chunk="handlePinChunk"
            @exclude-chunk="handleExcludeChunk"
          />
        </div>
      </div>

      <!-- 人工纠偏与锁定 Chunk 栏 -->
      <div v-if="pinnedChunks.length || excludedChunks.length" class="intervention-bar">
        <span v-for="(p, i) in pinnedChunks" :key="'p-' + i" class="interact-tag tag-pin">
          [锁定引用] {{ p.doc }}
          <button type="button" title="移除该锁定" @click="removePinnedChunk(i)">✕</button>
        </span>
        <span v-for="(e, i) in excludedChunks" :key="'e-' + i" class="interact-tag tag-exclude">
          [锁定排除] {{ e.doc }}
          <button type="button" title="移除该排除" @click="removeExcludedChunk(i)">✕</button>
        </span>
      </div>

      <!-- 回到最新悬浮按钮 -->
      <Transition name="fade-slide">
        <button
          v-if="!autoFollowBottom && messages.length > 0"
          type="button"
          class="jump-to-bottom-btn"
          title="回到最新消息"
          @click="scrollDown(true)"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
            <polyline points="6 9 12 15 18 9"></polyline>
          </svg>
          <span>回到最新</span>
        </button>
      </Transition>

      <ChatInput v-model:mode="workMode" :disabled="loading" @send="handleSend" @stop="handleStop" />
    </div>

    <!-- Toast 反馈 -->
    <Transition name="toast">
      <div v-if="toast" class="toast">{{ toast }}</div>
    </Transition>

    <aside class="sidebar" :class="{ 'sidebar--open': showSources }">
      <div class="sidebar-hd">
        <h2>参考来源</h2>
        <button class="close-btn" @click="showSources = false">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>
      <div class="sidebar-bd">
        <SourcePanel ref="sourcePanel" :sources="currentSources" @chunk-feedback="handleCurrentChunkFeedback" />
      </div>
    </aside>

    <!-- 确认弹窗 -->
    <Teleport to="body">
      <div v-if="confirmVisible" class="confirm-overlay" @click.self="confirmCancel">
        <div class="confirm-box">
          <p class="confirm-msg">{{ confirmMessage }}</p>
          <div class="confirm-actions">
            <button class="btn btn-cancel" @click="confirmCancel">取消</button>
            <button class="btn btn-danger" @click="confirmOk">确定</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 扫描遮罩 -->
    <Teleport to="body">
      <div v-if="scanningOverlay" class="scan-overlay">
        <div class="scan-spinner"></div>
        <div class="scan-text">正在重新扫描...</div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.chat-layout { display: flex; height: 100%; background: #fff; overflow: hidden; }

/* 左侧会话侧边栏 */
.chat-sidebar {
  width: 250px;
  height: 100%;
  background: #f8fafc;
  border-right: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  transition: width 0.2s cubic-bezier(0.4, 0, 0.2, 1), min-width 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  position: relative;
  z-index: 10;
}

.chat-sidebar--collapsed {
  width: 0;
  min-width: 0;
  border-right: none;
}

.sidebar-header {
  padding: 10px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.new-chat-btn {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 7px 12px;
  background: #2563eb;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease, transform 0.1s ease;
  box-shadow: 0 1px 2px rgba(37, 99, 235, 0.15);
  white-space: nowrap;
}

.new-chat-btn:hover {
  background: #1d4ed8;
}

.new-chat-btn:active {
  transform: scale(0.98);
}

.toggle-sidebar-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 6px;
  border: 1px solid #cbd5e1;
  background: #fff;
  color: #64748b;
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.toggle-sidebar-btn:hover {
  background: #f1f5f9;
  color: #1e293b;
}

.sidebar-expand-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  color: #64748b;
  cursor: pointer;
  margin-right: 4px;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.sidebar-expand-btn:hover {
  background: #edf2f7;
  color: #1e293b;
}

.session-list-container {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.session-list-empty {
  padding: 24px 12px;
  text-align: center;
  color: #94a3b8;
  font-size: 12px;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  color: #475569;
  font-size: 13px;
  transition: background 0.15s ease, color 0.15s ease;
  position: relative;
  user-select: none;
}

.session-item:hover {
  background: #f1f5f9;
  color: #0f172a;
}

.session-item.active {
  background: #e2e8f0;
  color: #0f172a;
  font-weight: 500;
}

.session-icon {
  flex-shrink: 0;
  color: #64748b;
}

.session-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-title-edit {
  flex: 1;
  min-width: 0;
}

.session-rename-input {
  width: 100%;
  padding: 2px 6px;
  font-size: 12px;
  border: 1px solid #2563eb;
  border-radius: 4px;
  outline: none;
  background: #fff;
  color: #0f172a;
}

.session-actions {
  display: none;
  align-items: center;
  gap: 2px;
  margin-left: auto;
  flex-shrink: 0;
}

.session-item:hover .session-actions,
.session-item.active .session-actions {
  display: flex;
}

.session-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 4px;
  border: none;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  transition: all 0.15s ease;
}

.session-action-btn:hover {
  background: #cbd5e1;
  color: #1e293b;
}

.session-action-btn.delete-btn:hover {
  background: #fee2e2;
  color: #dc2626;
}

.session-badge {
  font-size: 12px;
  color: #475569;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  padding: 2px 8px;
  border-radius: 6px;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }

/* 顶部栏 */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 52px;
  border-bottom: 1px solid #e8eaed;
  background: #fff;
  flex-shrink: 0;
}
.header-left { display: flex; align-items: center; gap: 10px; min-width: 0; }
.header-right {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.title {
  font-size: 16px;
  font-weight: 600;
  color: #1e2a41;
  margin: 0;
}
.stat {
  font-size: 12px;
  color: #8a8f99;
  background: #f7f8fa;
  padding: 2px 8px;
  border-radius: 4px;
}

.model-group {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow-x: auto;
  flex-shrink: 1;
  min-width: 0;
  -ms-overflow-style: none;
  scrollbar-width: none;
}
.model-group::-webkit-scrollbar { display: none; }

.agent-pill {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px 2px 6px;
  border-radius: 6px;
  background: #eef2ff;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s;
  flex-shrink: 0;
}
.agent-pill:hover { background: #e0e7ff; }
.agent-icon { display: flex; align-items: center; font-size: 14px; line-height: 1; }
.agent-name { font-size: 12px; font-weight: 600; color: #3370ff; }

.agent-popover-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 60px;
}
.agent-popover {
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.12);
  padding: 6px;
  min-width: 260px;
}
.agent-opt {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.1s;
}
.agent-opt:hover { background: #f7f8fa; }
.agent-opt.active { background: #eef2ff; }
.agent-opt-icon { display: flex; align-items: center; font-size: 20px; flex-shrink: 0; }
.agent-opt-info { min-width: 0; }
.agent-opt-name { font-size: 13px; font-weight: 600; color: #1e2a41; }
.agent-opt-desc { font-size: 11px; color: #8a8f99; margin-top: 1px; }
.agent-empty { padding: 12px 16px; color: #8a8f99; font-size: 13px; text-align: center; }

.think-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 6px;
  border: 1px solid #e8eaed;
  background: #f7f8fa;
  color: #8a8f99;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s;
}
.think-btn:hover { border-color: #3370ff; color: #3370ff; }
.think-btn.active {
  background: #eef2ff;
  border-color: #3370ff;
  color: #3370ff;
}
.think-btn.active svg { stroke: #3370ff; }

.model-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #8a8f99;
  background: #f7f8fa;
  padding: 2px 8px;
  border-radius: 6px;
  white-space: nowrap;
}
.model-pill.has-dropdown {
  cursor: pointer;
}
.model-custom-select {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #1e2a41;
  font-weight: 500;
  cursor: pointer;
  user-select: none;
}
.select-chevron {
  transition: transform 0.2s ease;
  color: #8a8f99;
}
.select-chevron.open {
  transform: rotate(180deg);
}
.badge-mini {
  font-size: 10px;
  margin-left: 2px;
}
.model-select {
  font-size: 11px;
  padding: 1px 4px;
  border: none;
  border-radius: 3px;
  background: transparent;
  color: #1e2a41;
  cursor: pointer;
  outline: none;
  font-weight: 500;
  max-width: 100px;
}
.model-select:focus { background: #eef0f4; }
.model-tag {
  font-size: 11px;
  color: #1e2a41;
  font-weight: 500;
}

/* 自定义下拉菜单 Teleport 样式 */
.model-dropdown-menu {
  position: absolute;
  z-index: 10000;
  background: rgba(255, 255, 255, 0.98);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(226, 232, 240, 0.9);
  border-radius: 8px;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.08), 0 8px 10px -6px rgba(0, 0, 0, 0.05);
  padding: 4px;
  min-width: 240px;
  max-height: 300px;
  overflow-y: auto;
}
.model-dropdown-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
  gap: 16px;
}
.model-dropdown-item:hover {
  background: #f1f5f9;
}
.model-dropdown-item.active {
  background: #eef2ff;
}
.model-dropdown-item.gpu-insufficient {
  opacity: 0.85;
}
.model-item-left {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.model-item-name {
  font-size: 12px;
  font-weight: 500;
  color: #1e293b;
}
.model-dropdown-item.active .model-item-name {
  color: #3b82f6;
  font-weight: 600;
}
.model-item-footprint {
  font-size: 10px;
  color: #94a3b8;
}
.badge-status {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 4px;
}
.badge-status.current {
  background: #dbeafe;
  color: #1e40af;
}
.badge-status.recommended {
  background: #fef3c7;
  color: #92400e;
}
.badge-status.insufficient {
  background: #fee2e2;
  color: #991b1b;
}
.badge-status.fit {
  background: #d1fae5;
  color: #065f46;
}

/* GPU 显存小面板优化 */
.gpu-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #475569;
  background: #f1f5f9;
  padding: 2px 8px;
  border-radius: 6px;
  white-space: nowrap;
  cursor: pointer;
  transition: all 0.2s ease;
  user-select: none;
}
.gpu-pill:hover, .gpu-pill.active {
  background: #e2e8f0;
  color: #0f172a;
}
.gpu-pill.offline {
  color: #94a3b8;
  background: #f8fafc;
  cursor: default;
}
.gpu-vram {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.gpu-bar {
  position: relative;
  width: 40px;
  height: 6px;
  border-radius: 3px;
  background: #cbd5e1;
  overflow: hidden;
  display: inline-block;
}
.gpu-bar span {
  position: absolute;
  left: 0; top: 0; bottom: 0;
  border-radius: 3px;
  transition: width 0.3s ease, background-color 0.3s ease;
}
.gpu-bar span.safe, .gpu-popover-bar-fill.safe {
  background-color: #10b981;
}
.gpu-bar span.warning, .gpu-popover-bar-fill.warning {
  background-color: #f59e0b;
}
.gpu-bar span.danger, .gpu-popover-bar-fill.danger {
  background-color: #ef4444;
  animation: pulse-warn 1.5s infinite ease-in-out;
}

@keyframes pulse-warn {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.gpu-reco {
  color: #b45309;
  background: #fef3c7;
  border-radius: 4px;
  padding: 0 5px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s;
}
.gpu-reco:hover { background: #fde68a; }

/* GPU 悬浮框 popover 样式 */
.gpu-popover {
  position: absolute;
  z-index: 10000;
  background: rgba(255, 255, 255, 0.98);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(226, 232, 240, 0.9);
  border-radius: 12px;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
  padding: 16px;
  width: 320px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.gpu-popover.offline {
  width: 280px;
  padding: 14px;
  gap: 8px;
}
.gpu-popover-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid #f1f5f9;
  padding-bottom: 8px;
}
.gpu-title-group {
  display: flex;
  align-items: center;
  gap: 6px;
}
.gpu-popover-icon {
  color: #64748b;
}
.gpu-card-name {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
}
.gpu-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.gpu-status-dot.low { background-color: #10b981; }
.gpu-status-dot.medium { background-color: #f59e0b; }
.gpu-status-dot.high { background-color: #ef4444; }
.gpu-status-dot.unknown { background-color: #94a3b8; }

.gpu-popover-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.vram-label-row {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
}
.section-label {
  color: #64748b;
}
.vram-values {
  font-weight: 600;
  color: #1e293b;
}
.gpu-popover-bar {
  height: 8px;
  border-radius: 4px;
  background: #e2e8f0;
  overflow: hidden;
}
.gpu-popover-bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.3s ease, background-color 0.3s ease;
}

.gpu-popover-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  background: #f8fafc;
  padding: 8px;
  border-radius: 8px;
  border: 1px solid #f1f5f9;
}
.grid-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}
.grid-label {
  font-size: 10px;
  color: #64748b;
}
.grid-val {
  font-size: 12px;
  font-weight: 600;
  color: #1e293b;
}
.grid-val.cool { color: #10b981; }
.grid-val.warm { color: #d97706; }
.grid-val.hot { color: #ef4444; }

.section-subtitle {
  font-size: 11px;
  font-weight: 600;
  color: #64748b;
  border-bottom: 1px solid #f1f5f9;
  padding-bottom: 4px;
}
.gpu-models-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 150px;
  overflow-y: auto;
}
.gpu-model-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.gpu-model-row:hover {
  background: #f1f5f9;
}
.gpu-model-row.active {
  background: #eef2ff;
}
.gpu-model-row.disabled {
  opacity: 0.85;
}
.model-row-left {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.model-row-name {
  font-size: 11px;
  font-weight: 500;
  color: #334155;
}
.gpu-model-row.active .model-row-name {
  color: #3b82f6;
  font-weight: 600;
}
.model-row-footprint {
  font-size: 9px;
  color: #94a3b8;
}
.tag-status {
  font-size: 9px;
  font-weight: 600;
  padding: 1px 4px;
  border-radius: 3px;
}
.tag-status.tag-current { background: #dbeafe; color: #1e40af; }
.tag-status.tag-reco { background: #fef3c7; color: #92400e; }
.tag-status.tag-insufficient { background: #fee2e2; color: #991b1b; }
.tag-status.tag-fit { background: #d1fae5; color: #065f46; }

.gpu-popover-footer {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-top: 1px solid #f1f5f9;
  padding-top: 10px;
}
.footer-notice {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #92400e;
}
.footer-notice svg {
  flex-shrink: 0;
  color: #d97706;
}
.btn-switch-reco {
  width: 100%;
  border: none;
  background: #3b82f6;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 6px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}
.btn-switch-reco:hover {
  background: #2563eb;
}

.gpu-offline-title {
  font-size: 13px;
  font-weight: 600;
  color: #64748b;
}
.gpu-offline-desc {
  font-size: 11px;
  color: #94a3b8;
  line-height: 1.4;
}
.gpu-offline-help {
  font-size: 10px;
  color: #94a3b8;
  border-top: 1px solid #f1f5f9;
  padding-top: 6px;
}
.gpu-offline-help ol {
  margin-top: 4px;
  padding-left: 14px;
}

/* 降级提示 Notice 样式优化 */
.gpu-notice {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 16px;
  font-size: 12px;
  color: #92400e;
  background: #fffbeb;
  border-bottom: 1px solid #fde68a;
  box-shadow: inset 0 -1px 0 rgba(0,0,0,0.02);
}
.gpu-notice-text {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
}
.gpu-notice-icon {
  color: #d97706;
}
.gpu-notice-close {
  border: none;
  background: transparent;
  color: #b45309;
  cursor: pointer;
  font-size: 13px;
  padding: 2px 6px;
  border-radius: 4px;
  transition: background 0.15s;
}
.gpu-notice-close:hover {
  background: #fde68a;
}

/* Transition 动画 */
.fade-slide-enter-active, .fade-slide-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.fade-slide-enter-from, .fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.slide-fade-enter-active, .slide-fade-leave-active {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  max-height: 60px;
  opacity: 1;
  overflow: hidden;
}
.slide-fade-enter-from, .slide-fade-leave-to {
  max-height: 0;
  opacity: 0;
  padding-top: 0;
  padding-bottom: 0;
  border-bottom-width: 0;
}

.icon-btn {
  position: relative;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #6b7280;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.icon-btn:hover { background: #f7f8fa; color: #3370ff; }
.icon-btn.active { background: #eef2ff; color: #3370ff; }
.icon-btn:disabled { opacity: 0.35; cursor: not-allowed; }
.icon-btn--danger:hover { color: #f25d5d; background: #fef0f0; }

.upload-wrap { position: relative; }

.profile-picker {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #667085;
  font-size: 12px;
}

.profile-picker select {
  max-width: 150px;
  padding: 5px 24px 5px 8px;
  border: 1px solid #dfe3ea;
  border-radius: 8px;
  background: #fff;
  color: #344054;
  font-size: 12px;
}

.upload-popover {
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  margin-top: 6px;
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.12);
  padding: 6px;
  min-width: 140px;
  z-index: 100;
}
.popover-title {
  font-size: 12px;
  color: #8a8f99;
  padding: 4px 8px 6px;
  border-bottom: 1px solid #f3f4f6;
  margin-bottom: 4px;
  white-space: nowrap;
}
.popover-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 6px 8px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: #1e2a41;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
}
.popover-item:hover { background: #f7f8fa; color: #3370ff; }

.pop-enter-active, .pop-leave-active { transition: all 0.15s ease; }
.pop-enter-from, .pop-leave-to { opacity: 0; transform: translateX(-50%) translateY(-4px); }

.badge-dot {
  position: absolute;
  top: 2px;
  right: 2px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  font-size: 10px;
  font-weight: 600;
  line-height: 16px;
  text-align: center;
  background: #3370ff;
  color: #fff;
  border-radius: 8px;
}

/* 消息列表 */
.msg-list {
  flex: 1;
  overflow-y: auto;
  padding: 24px 24px 8px;
}
.msg-wrap {
  max-width: 820px;
  margin: 0 auto;
}
.welcome-panel {
  margin-bottom: 24px;
  padding: 18px 20px;
  border: 1px solid #e8eaed;
  border-radius: 12px;
  background: linear-gradient(180deg, #f8fbff 0%, #ffffff 100%);
  color: #1e2a41;
}
.welcome-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  background: #e8f0ff;
  color: #3370ff;
  font-size: 11px;
  font-weight: 600;
}
.welcome-title {
  margin-top: 10px;
  font-size: 16px;
  font-weight: 600;
}
.welcome-text {
  margin: 10px 0 0;
  color: #5e6673;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-line;
}

/* 侧栏 */
.sidebar {
  width: 0;
  overflow: hidden;
  border-left: 1px solid #e8eaed;
  background: #fafbfc;
  transition: width 0.25s ease;
  display: flex;
  flex-direction: column;
}
.sidebar--open { width: 360px; }
.sidebar-hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  height: 52px;
  border-bottom: 1px solid #e8eaed;
  flex-shrink: 0;
}
.sidebar-hd h2 {
  font-size: 14px;
  font-weight: 600;
  color: #1e2a41;
  margin: 0;
}
.close-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: #8a8f99;
  padding: 4px;
  border-radius: 4px;
  display: flex;
}
.close-btn:hover { color: #1e2a41; background: #eef0f4; }
.sidebar-bd { flex: 1; overflow-y: auto; padding: 16px 20px; }

/* Toast */
.toast {
  position: fixed;
  bottom: 80px;
  left: 50%;
  transform: translateX(-50%);
  background: #1e2a41;
  color: #fff;
  padding: 8px 20px;
  border-radius: 8px;
  font-size: 13px;
  z-index: 2000;
  pointer-events: none;
  box-shadow: 0 4px 16px rgba(0,0,0,0.15);
}
.toast-enter-active { transition: all 0.25s ease-out; }
.toast-leave-active { transition: all 0.2s ease-in; }
.toast-enter-from { opacity: 0; transform: translateX(-50%) translateY(12px); }
.toast-leave-to { opacity: 0; transform: translateX(-50%) translateY(12px); }

/* ---- 确认弹窗 ---- */
.confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
}
.confirm-box {
  background: #fff;
  border-radius: 10px;
  padding: 24px 28px 20px;
  min-width: 320px;
  box-shadow: 0 12px 40px rgba(0,0,0,0.15);
}
.confirm-msg {
  margin: 0 0 20px;
  font-size: 14px;
  color: #1e2a41;
  line-height: 1.6;
}
.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.confirm-actions .btn {
  padding: 7px 18px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid #e8eaed;
  background: #fff;
  color: #4b5563;
  transition: all 0.15s;
}
.confirm-actions .btn:hover { background: #f7f8fa; }
.confirm-actions .btn-danger {
  background: #f25d5d;
  color: #fff;
  border-color: #f25d5d;
}
.confirm-actions .btn-danger:hover { background: #e04848; }

/* ---- 扫描遮罩 ---- */
.scan-overlay {
  position: fixed;
  inset: 0;
  background: rgba(255,255,255,0.88);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  gap: 16px;
}
.scan-spinner {
  width: 40px;
  height: 40px;
  border: 4px solid #e8eaed;
  border-top-color: #3370ff;
  border-radius: 50%;
  animation: scan-spin 0.8s linear infinite;
}
@keyframes scan-spin {
  to { transform: rotate(360deg); }
}
.scan-text {
  font-size: 15px;
  color: #1e2a41;
  font-weight: 500;
}

/* ===== 移动端响应式 ===== */
@media (max-width: 768px) {
  .header {
    flex-direction: column;
    align-items: stretch;
    height: auto;
    padding: 8px 12px;
    gap: 6px;
  }
  .header-left {
    justify-content: space-between;
  }
  .header-right {
    flex-wrap: wrap;
    gap: 4px;
  }
  .model-group {
    flex-wrap: wrap;
    gap: 4px;
    order: 1;
    width: 100%;
  }
  .model-pill {
    font-size: 11px;
    padding: 2px 6px;
  }
  .model-select {
    max-width: 80px;
    font-size: 11px;
  }
  .gpu-pill { display: none; }
  .agent-pill {
    padding: 2px 8px 2px 4px;
  }
  .agent-name {
    font-size: 11px;
  }
  .think-btn {
    font-size: 10px;
    padding: 2px 6px;
  }
  .icon-btn {
    width: 28px;
    height: 28px;
  }
  .icon-btn svg {
    width: 14px;
    height: 14px;
  }
  .upload-popover {
    left: auto;
    right: 0;
    transform: none;
  }
  .pop-enter-from, .pop-leave-to {
    transform: translateY(-4px);
  }

  /* 消息列表 */
  .msg-list {
    padding: 12px 12px 8px;
  }
  .msg-wrap {
    max-width: 100%;
  }
  .welcome-panel {
    margin-bottom: 16px;
    padding: 14px 16px;
  }
  .welcome-title {
    font-size: 14px;
  }
  .welcome-text {
    font-size: 13px;
  }

  /* 参考来源侧栏 → 底部全屏抽屉 */
  .sidebar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    width: 100% !important;
    height: 0;
    border-left: none;
    border-top: 1px solid #e8eaed;
    z-index: 999;
    transition: height 0.3s ease;
    border-radius: 16px 16px 0 0;
    box-shadow: 0 -4px 20px rgba(0,0,0,0.1);
  }
  .sidebar--open {
    height: 60vh;
  }
  .sidebar-hd {
    padding: 0 16px;
    height: 48px;
  }
  .sidebar-bd {
    padding: 12px 16px;
  }

  /* Toast 位置调整 */
  .toast {
    bottom: 100px;
    max-width: 80vw;
    font-size: 12px;
  }

  /* 确认弹窗 */
  .confirm-box {
    min-width: unset;
    width: calc(100vw - 40px);
    max-width: 360px;
  }
}

@media (max-width: 480px) {
  .title {
    font-size: 14px;
  }
  .stat {
    font-size: 11px;
  }
  .model-group {
    gap: 3px;
  }
  .model-pill {
    font-size: 10px;
    padding: 1px 5px;
  }
  .model-select {
    font-size: 10px;
  }
}

.intervention-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 16px;
  background: var(--color-bg-secondary, #f8fafc);
  border-top: 1px solid var(--color-border, #e2e8f0);
}

.interact-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  font-size: 12px;
}

.params-popover {
  width: 280px;
  padding: 16px;
  background: #ffffff;
  border-radius: 8px;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.popover-title {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  margin-bottom: 4px;
}

.pop-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pop-field label {
  font-size: 11px;
  font-weight: 600;
  color: #64748b;
}

.pop-input {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  font-size: 12px;
  color: #1e293b;
}

.pop-check-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #334155;
  cursor: pointer;
}

.tag-pin {
  background: #eff6ff;
  color: #1d4ed8;
  border: 1px solid #bfdbfe;
}

.tag-exclude {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
}

.interact-tag button {
  background: transparent;
  border: none;
  cursor: pointer;
  font-size: 11px;
  color: inherit;
  opacity: 0.7;
}

.interact-tag button:hover {
  opacity: 1;
}

.jump-to-bottom-btn {
  position: absolute;
  bottom: 84px;
  right: 24px;
  z-index: 20;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 500;
  color: #1e293b;
  background-color: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 20px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  cursor: pointer;
  user-select: none;
  transition: all 150ms ease;
}

.jump-to-bottom-btn:hover {
  background-color: #f8fafc;
  border-color: #94a3b8;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.12);
  transform: translateY(-1px);
}

</style>
