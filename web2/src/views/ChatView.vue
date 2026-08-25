<script setup lang="ts">
defineOptions({ name: 'ChatView' })
import { ref, onMounted, onUnmounted, nextTick, computed } from 'vue'
import type { Message, SourceDoc, Stats, ClarificationOption, EvidenceItem, GpuStatus, ChatSession } from '../types'
import { queryKnowledgeStream, queryKnowledge, queryImageStream, queryClarify, getStats, triggerScan, uploadDocument, getModels, getGpuStatus, getKnowledgeBases, getAgents, updateQaTraceFeedback, submitUserFeedback, DOCUMENT_PROFILE_OPTIONS } from '../api'
import type { DocumentProfile } from '../api'
import type { ModelsResponse, AgentInfo } from '../api'
import {
  saveChatState,
  loadChatState,
  clearChatState,
  loadAllSessions,
  saveAllSessions,
  getActiveSessionId,
  setActiveSessionId,
} from '../utils/storage'
import { buildChatHistoryPayload } from '../utils/chatHistory'
import { copyToClipboard } from '../utils/clipboard'
import ChatMessage from '../components/ChatMessage.vue'
import ChatInput from '../components/ChatInput.vue'
import SourcePanel from '../components/SourcePanel.vue'

const sessions = ref<ChatSession[]>([])
const currentSessionId = ref<string>('')
const showHistorySidebar = ref(localStorage.getItem('rag-sidebar-open') !== 'false')
const editingSessionId = ref<string | null>(null)
const editingTitleDraft = ref('')

// 多选管理状态
const isBatchMode = ref(false)
const selectedSessionIds = ref<Set<string>>(new Set())

// 三个点（···）更多操作浮层状态
const activeMenuSessionId = ref<string | null>(null)
const menuPosition = ref({ top: '0px', left: '0px' })
const menuSession = computed(() =>
  sessions.value.find((s) => s.id === activeMenuSessionId.value) || null,
)

function toggleSessionMenu(session: ChatSession, e: MouseEvent) {
  e.stopPropagation()
  if (activeMenuSessionId.value === session.id) {
    activeMenuSessionId.value = null
    return
  }
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  menuPosition.value = {
    top: `${rect.bottom + 4}px`,
    left: `${Math.max(10, rect.right - 120)}px`,
  }
  activeMenuSessionId.value = session.id
}

function handleMenuPin(s: ChatSession) {
  activeMenuSessionId.value = null
  togglePinSession(s)
}

function handleMenuShare(s: ChatSession) {
  activeMenuSessionId.value = null
  openShareModal(s)
}

function startRenameSession(s: ChatSession) {
  editingSessionId.value = s.id
  editingTitleDraft.value = s.title || ''
}

function cancelRenameSession() {
  editingSessionId.value = null
  editingTitleDraft.value = ''
}

async function saveRenameSession(s: ChatSession) {
  if (editingSessionId.value === s.id) {
    const trimmed = editingTitleDraft.value.trim()
    if (trimmed && trimmed !== s.title) {
      s.title = trimmed
      await saveAllSessions(sessions.value)
    }
    editingSessionId.value = null
    editingTitleDraft.value = ''
  }
}

function handleMenuRename(s: ChatSession) {
  activeMenuSessionId.value = null
  startRenameSession(s)
}

function handleMenuDelete(s: ChatSession) {
  activeMenuSessionId.value = null
  handleDeleteSession(s.id)
}

// 分享导出模态框状态
const shareModalVisible = ref(false)
const shareTargetSession = ref<ChatSession | null>(null)

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

// 切换问答模型下拉列表并定位（加入视口边界防御）
function toggleModelDropdown() {
  showModelDropdown.value = !showModelDropdown.value
  if (showModelDropdown.value && modelSelectTrigger.value) {
    updateModelDropdownPos()
  }
}

function updateModelDropdownPos() {
  if (!modelSelectTrigger.value) return
  const rect = modelSelectTrigger.value.getBoundingClientRect()
  const menuWidth = 240
  let left = rect.left + window.scrollX
  if (rect.left + menuWidth > window.innerWidth - 10) {
    left = Math.max(10, rect.right + window.scrollX - menuWidth)
  }
  modelSelectStyle.value = {
    top: `${rect.bottom + window.scrollY + 6}px`,
    left: `${left}px`
  }
}

// 展开 GPU 卡片面板并定位（右对齐与视口边界防御）
function showGpuPopoverPanel() {
  clearTimeout(gpuPopoverTimer)
  if (!gpuPillTrigger.value) return

  const rect = gpuPillTrigger.value.getBoundingClientRect()
  const popoverWidth = gpuStatus.value?.gpu ? 320 : 280
  let right = window.innerWidth - rect.right - window.scrollX
  if (right + popoverWidth > window.innerWidth - 10) {
    right = Math.max(10, window.innerWidth - 10 - popoverWidth)
  }
  gpuPopoverStyle.value = {
    top: `${rect.bottom + window.scrollY + 6}px`,
    right: `${Math.max(8, right)}px`
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
const chatInputRef = ref<InstanceType<typeof ChatInput> | null>(null)
const msgContainer = ref<HTMLElement | null>()
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
const allowGeneralKnowledge = ref(localStorage.getItem('rag-allow-general') !== 'false')
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
  activeMenuSessionId.value = null
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

function toggleHistorySidebar() {
  showHistorySidebar.value = !showHistorySidebar.value
  localStorage.setItem('rag-sidebar-open', String(showHistorySidebar.value))
}

async function handleNewChat() {
  if (loading.value && abortController.value) {
    abortController.value.abort()
    loading.value = false
  }
  // 如果当前已经是空的且无消息，直接聚焦输入框
  const cur = sessions.value.find((s) => s.id === currentSessionId.value)
  if (cur && cur.messages.length === 0) {
    chatInputRef.value?.focus()
    return
  }
  const newSession: ChatSession = {
    id: Date.now().toString(),
    title: '新对话',
    createdAt: Date.now(),
    updatedAt: Date.now(),
    messages: [],
  }
  sessions.value.unshift(newSession)
  await saveAllSessions(sessions.value)
  await switchSession(newSession.id)
  chatInputRef.value?.focus()
}

async function switchSession(id: string) {
  if (loading.value && abortController.value) {
    abortController.value.abort()
    loading.value = false
  }
  currentSessionId.value = id
  setActiveSessionId(id)
  const target = sessions.value.find((s) => s.id === id)
  if (target) {
    messages.value = target.messages || []
    const withSources = messages.value.filter((m) => m.role === 'assistant' && m.sources?.length)
    currentSources.value = withSources.length ? withSources[withSources.length - 1].sources! : []
  } else {
    messages.value = []
    currentSources.value = []
  }
  scrollDown()
}

async function handleDeleteSession(id: string, e?: Event) {
  e?.stopPropagation()
  const ok = await showConfirm('确定删除此条历史对话？')
  if (!ok) return
  sessions.value = sessions.value.filter((s) => s.id !== id)
  await saveAllSessions(sessions.value)
  if (currentSessionId.value === id) {
    if (sessions.value.length > 0) {
      await switchSession(sessions.value[0].id)
    } else {
      await handleNewChat()
    }
  }
  showToast('已删除对话')
}

function sortSessions() {
  sessions.value.sort((a, b) => {
    if (!!a.pinned !== !!b.pinned) {
      return a.pinned ? -1 : 1
    }
    return b.updatedAt - a.updatedAt
  })
}

async function togglePinSession(session: ChatSession, e?: Event) {
  e?.stopPropagation()
  session.pinned = !session.pinned
  sortSessions()
  await saveAllSessions(sessions.value)
  showToast(session.pinned ? '已置顶对话' : '已取消置顶')
}

// ---- 多选批量管理 ----
function toggleBatchMode() {
  isBatchMode.value = !isBatchMode.value
  selectedSessionIds.value.clear()
}

function toggleSelectSession(id: string, e?: Event) {
  e?.stopPropagation()
  if (selectedSessionIds.value.has(id)) {
    selectedSessionIds.value.delete(id)
  } else {
    selectedSessionIds.value.add(id)
  }
}

function selectAllSessions() {
  if (selectedSessionIds.value.size === sessions.value.length) {
    selectedSessionIds.value.clear()
  } else {
    selectedSessionIds.value = new Set(sessions.value.map((s) => s.id))
  }
}

async function handleBatchPin(pin: boolean) {
  if (selectedSessionIds.value.size === 0) return
  sessions.value.forEach((s) => {
    if (selectedSessionIds.value.has(s.id)) {
      s.pinned = pin
    }
  })
  sortSessions()
  await saveAllSessions(sessions.value)
  showToast(pin ? `已置顶 ${selectedSessionIds.value.size} 个对话` : `已取消置顶 ${selectedSessionIds.value.size} 个对话`)
}

async function handleBatchDelete() {
  if (selectedSessionIds.value.size === 0) return
  const count = selectedSessionIds.value.size
  const ok = await showConfirm(`确定批量删除选中的 ${count} 个对话？`)
  if (!ok) return

  const toDelete = new Set(selectedSessionIds.value)
  sessions.value = sessions.value.filter((s) => !toDelete.has(s.id))
  await saveAllSessions(sessions.value)

  if (toDelete.has(currentSessionId.value)) {
    if (sessions.value.length > 0) {
      await switchSession(sessions.value[0].id)
    } else {
      await handleNewChat()
    }
  }
  selectedSessionIds.value.clear()
  isBatchMode.value = false
  showToast(`已删除 ${count} 个对话`)
}

// ---- 分享对话公开链接 ----
function openShareModal(session?: ChatSession, e?: Event) {
  e?.stopPropagation()
  if (session) {
    shareTargetSession.value = session
  } else {
    shareTargetSession.value = sessions.value.find((s) => s.id === currentSessionId.value) || null
  }
  if (!shareTargetSession.value) {
    showToast('暂无对话可分享')
    return
  }
  // 打开分享弹窗时自动将快照存入 localStorage 以便分享读取
  try {
    localStorage.setItem(`rag-shared-session-${shareTargetSession.value.id}`, JSON.stringify(shareTargetSession.value))
  } catch (e) {
    console.error('Failed to pre-save share snapshot', e)
  }
  shareModalVisible.value = true
}

const shareLinkUrl = computed(() => {
  if (!shareTargetSession.value) return ''
  const base = window.location.origin + window.location.pathname
  return `${base}?shareId=${shareTargetSession.value.id}`
})

async function copyShareLink() {
  if (!shareTargetSession.value) return
  const s = shareTargetSession.value
  try {
    localStorage.setItem(`rag-shared-session-${s.id}`, JSON.stringify(s))
  } catch (e) {
    console.error('Failed to save share snapshot', e)
  }
  const ok = await copyToClipboard(shareLinkUrl.value)
  if (ok) {
    showToast('已复制分享链接到剪贴板')
  } else {
    showToast('复制链接失败，请手动复制输入框内容')
  }
}

// 历史对话搜索模态框状态
const isSearchModalOpen = ref(false)
const searchModalQuery = ref('')
const searchModalInputRef = ref<HTMLInputElement | null>(null)
const searchSelectedIdx = ref(0)

function openSearchModal() {
  isSearchModalOpen.value = true
  searchModalQuery.value = ''
  searchSelectedIdx.value = 0
  nextTick(() => {
    searchModalInputRef.value?.focus()
  })
}

function closeSearchModal() {
  isSearchModalOpen.value = false
  searchModalQuery.value = ''
  searchSelectedIdx.value = 0
}

interface SearchResultItem {
  session: ChatSession
  matchedContentSnippet?: string
  timeLabel: string
}

const searchModalResults = computed<SearchResultItem[]>(() => {
  const q = searchModalQuery.value.trim().toLowerCase()
  const list = [...sessions.value].sort(
    (a, b) => (b.updatedAt || b.createdAt) - (a.updatedAt || a.createdAt),
  )

  const formatTime = (ts: number) => {
    const d = new Date(ts)
    const now = new Date()
    const isSameYear = d.getFullYear() === now.getFullYear()
    const month = String(d.getMonth() + 1).padStart(2, '0')
    const date = String(d.getDate()).padStart(2, '0')
    const hour = String(d.getHours()).padStart(2, '0')
    const minute = String(d.getMinutes()).padStart(2, '0')
    return isSameYear ? `${month}-${date} ${hour}:${minute}` : `${d.getFullYear()}-${month}-${date}`
  }

  if (!q) {
    return []
  }

  const results: SearchResultItem[] = []
  for (const s of list) {
    let matchedSnippet = ''

    if (s.messages && s.messages.length > 0) {
      for (const m of s.messages) {
        const text = m.content || ''
        const idx = text.toLowerCase().indexOf(q)
        if (idx !== -1) {
          const start = Math.max(0, idx - 20)
          const end = Math.min(text.length, idx + q.length + 30)
          matchedSnippet = (start > 0 ? '...' : '') + text.slice(start, end).replace(/\n+/g, ' ') + (end < text.length ? '...' : '')
          break
        }
      }
    }

    if (matchedSnippet) {
      results.push({
        session: s,
        matchedContentSnippet: matchedSnippet,
        timeLabel: formatTime(s.updatedAt || s.createdAt),
      })
    }
  }

  return results
})

function handleSearchModalKeydown(e: KeyboardEvent) {
  const len = searchModalResults.value.length
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    if (len > 0) {
      searchSelectedIdx.value = (searchSelectedIdx.value + 1) % len
    }
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    if (len > 0) {
      searchSelectedIdx.value = (searchSelectedIdx.value - 1 + len) % len
    }
  } else if (e.key === 'Enter') {
    e.preventDefault()
    if (len > 0 && searchModalResults.value[searchSelectedIdx.value]) {
      handleSelectSearchResult(searchModalResults.value[searchSelectedIdx.value].session)
    }
  } else if (e.key === 'Escape') {
    e.preventDefault()
    closeSearchModal()
  }
}

function handleSelectSearchResult(s: ChatSession) {
  closeSearchModal()
  switchSession(s.id)
}

interface SessionGroup {
  key: string
  label: string
  sessions: ChatSession[]
}

const groupedSessions = computed<SessionGroup[]>(() => {
  const now = new Date()
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const yesterdayStart = todayStart - 24 * 60 * 60 * 1000
  const last7DaysStart = todayStart - 6 * 24 * 60 * 60 * 1000

  const pinnedList: ChatSession[] = []
  const todayList: ChatSession[] = []
  const yesterdayList: ChatSession[] = []
  const last7DaysList: ChatSession[] = []
  const earlierList: ChatSession[] = []

  // 按更新时间/创建时间倒序
  const sorted = [...sessions.value].sort(
    (a, b) => (b.updatedAt || b.createdAt) - (a.updatedAt || a.createdAt),
  )

  sorted.forEach((s) => {
    if (s.pinned) {
      pinnedList.push(s)
      return
    }
    const time = s.updatedAt || s.createdAt
    if (time >= todayStart) {
      todayList.push(s)
    } else if (time >= yesterdayStart) {
      yesterdayList.push(s)
    } else if (time >= last7DaysStart) {
      last7DaysList.push(s)
    } else {
      earlierList.push(s)
    }
  })

  const groups: SessionGroup[] = []
  if (pinnedList.length > 0) {
    groups.push({ key: 'pinned', label: '📌 置顶对话', sessions: pinnedList })
  }
  if (todayList.length > 0) {
    groups.push({ key: 'today', label: '今天', sessions: todayList })
  }
  if (yesterdayList.length > 0) {
    groups.push({ key: 'yesterday', label: '昨天', sessions: yesterdayList })
  }
  if (last7DaysList.length > 0) {
    groups.push({ key: 'last7', label: '近 7 天', sessions: last7DaysList })
  }
  if (earlierList.length > 0) {
    groups.push({ key: 'earlier', label: '更早', sessions: earlierList })
  }

  return groups
})

onMounted(async () => {
  sessions.value = await loadAllSessions()
  if (sessions.value.length === 0) {
    const defaultSession: ChatSession = {
      id: Date.now().toString(),
      title: '新对话',
      createdAt: Date.now(),
      updatedAt: Date.now(),
      messages: [],
    }
    sessions.value = [defaultSession]
    currentSessionId.value = defaultSession.id
    messages.value = []
  } else {
    const savedActiveId = getActiveSessionId()
    const targetSession = sessions.value.find((s) => s.id === savedActiveId) || sessions.value[0]
    currentSessionId.value = targetSession.id
    setActiveSessionId(targetSession.id)
    messages.value = targetSession.messages || []
  }

  // 检测 URL 是否包含分享 shareId
  const urlParams = new URLSearchParams(window.location.search)
  const sharedId = urlParams.get('shareId')
  if (sharedId) {
    const sharedDataStr = localStorage.getItem(`rag-shared-session-${sharedId}`)
    if (sharedDataStr) {
      try {
        const sharedSession: ChatSession = JSON.parse(sharedDataStr)
        const exist = sessions.value.find((s) => s.id === sharedSession.id)
        if (!exist) {
          sessions.value.unshift(sharedSession)
        }
        currentSessionId.value = sharedSession.id
        setActiveSessionId(sharedSession.id)
        messages.value = sharedSession.messages || []
        showToast(`已载入分享对话：${sharedSession.title || '对话'}`)
      } catch (e) {
        console.error('Parse shared session error', e)
      }
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

  window.addEventListener('keydown', handleGlobalKeydown)
  window.addEventListener('resize', handleWindowCloseOrReposition)
  window.addEventListener('scroll', handleWindowCloseOrReposition, true)
  document.addEventListener('click', handleGlobalDocumentClick)
})

function handleWindowCloseOrReposition() {
  if (showModelDropdown.value) {
    updateModelDropdownPos()
  }
  if (showGpuPopover.value) {
    showGpuPopover.value = false
  }
}

function handleGlobalDocumentClick(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (!target) return
  if (activeMenuSessionId.value && !target.closest('.btn-session-more') && !target.closest('.session-menu-popover')) {
    activeMenuSessionId.value = null
  }
  if (showUploadPicker.value && !target.closest('.upload-wrap')) {
    showUploadPicker.value = false
  }
  if (showModelDropdown.value && !target.closest('.model-pill') && !target.closest('.model-dropdown-menu')) {
    showModelDropdown.value = false
  }
  if (showGpuPopover.value && !target.closest('.gpu-pill') && !target.closest('.gpu-popover')) {
    showGpuPopover.value = false
  }
}

function handleGlobalKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
    e.preventDefault()
    if (isSearchModalOpen.value) {
      closeSearchModal()
    } else {
      openSearchModal()
    }
  }
}

onUnmounted(() => {
  clearInterval(gpuPollTimer)
  clearTimeout(gpuNoticeTimer)
  clearTimeout(gpuPopoverTimer)
  window.removeEventListener('keydown', handleGlobalKeydown)
  window.removeEventListener('resize', handleWindowCloseOrReposition)
  window.removeEventListener('scroll', handleWindowCloseOrReposition, true)
  document.removeEventListener('click', handleGlobalDocumentClick)
})

async function persist() {
  if (!initialized.value) return
  const cur = sessions.value.find((s) => s.id === currentSessionId.value)
  if (cur) {
    cur.messages = messages.value
    cur.updatedAt = Date.now()
    if (cur.title === '新对话' || !cur.title) {
      const firstUserMsg = messages.value.find((m) => m.role === 'user')
      if (firstUserMsg && firstUserMsg.content.trim()) {
        cur.title = firstUserMsg.content.trim().slice(0, 24)
      }
    }
  }
  await saveChatState(messages.value, currentSessionId.value, cur?.title)
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

async function handleSend(text: string, image?: File) {
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
  scrollDown()

  const aiId = (Date.now() + 1).toString()
  messages.value.push({
    id: aiId,
    role: 'assistant',
    content: '',
    loading: true,
    status: image ? '正在分析图片...' : '正在理解问题...',
  })
  loading.value = true
  scrollDown()

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
    } finally {
      loading.value = false
      abortController.value = null
      scrollDown()
    }
    return
  }

  try {
    abortController.value = new AbortController()

    // 1. 提问前预检：检测是否有歧义需要反问 (Query Clarification Pre-check)
    try {
      const clarifyRes = await queryClarify(
        text,
        undefined,
        currentKb.value && currentKb.value !== '全部知识库' ? currentKb.value : undefined,
        abortController.value.signal,
      )
      if (clarifyRes && clarifyRes.needs_clarification && clarifyRes.options.length >= 2) {
        const msg = lastAiMsg()
        msg.loading = false
        msg.status = undefined
        msg.clarification = {
          ask_question: clarifyRes.ask_question || '请选择您要查询的具体模块或方向：',
          trigger: clarifyRes.trigger,
          reason: clarifyRes.reason,
          options: clarifyRes.options,
        }
        loading.value = false
        abortController.value = null
        await persist()
        scrollDown()
        return
      }
    } catch (err: any) {
      if ((err as DOMException)?.name === 'AbortError') throw err
      // 若预检服务异常，优雅降级为正常检索问答
    }

    // 2. 无歧义或预检跳过，执行常规流式检索问答
    const history = chatHistory.value.slice(0, -1)
    let streamOk = false
    try {
      const llmModel = currentModel.value || undefined
      await queryKnowledgeStream(text, history, {
        onStatus: (status) => {
          lastAiMsg().status = status
          scrollDown()
        },
        onToken: (token) => {
          const msg = lastAiMsg()
          msg.status = undefined
          msg.content += token
          msg.loading = false
          scrollDown()
        },
        onThinking: (thought) => {
          const msg = lastAiMsg()
          msg.thinking = (msg.thinking || '') + thought
          scrollDown()
        },
        onFinalAnswer: (answer) => {
          const msg = lastAiMsg()
          msg.status = undefined
          msg.content = answer
          msg.loading = false
          scrollDown()
        },
        onSources: (sources) => {
          currentSources.value = sources
          lastAiMsg().sources = sources
        },
        onTrace: (traceId) => {
          lastAiMsg().trace_id = traceId
        },
        onPipeline: (pipelineData) => {
          const msg = lastAiMsg()
          if (!msg.pipelineSteps) msg.pipelineSteps = []
          msg.pipelineSteps.push(pipelineData)
          if (pipelineData.evidence) {
            msg.evidencePack = pipelineData.evidence
          }
        },
        onNotice: (notice) => {
          showGpuNotice(notice)
        },
        onDone: () => {
          streamOk = true
          abortController.value = null
          lastAiMsg().status = undefined
          lastAiMsg().loading = false
          loading.value = false
          pinnedChunks.value = []
          excludedChunks.value = []
          persist()
          scrollDown()
        },
        onError: () => { throw new Error('stream failed') },
      }, llmModel, currentKb.value, thinkingEnabled.value || undefined, webSearchEnabled.value || undefined, abortController.value?.signal, activeAgent.value?.system_prompt, allowGeneralKnowledge.value, docCategory.value || undefined, entityName.value || undefined, pinnedChunks.value.map(c => c.id), excludedChunks.value.map(c => c.id))
    } catch {
      lastAiMsg().status = undefined
      if (!streamOk && !abortController.value) {
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
        )
        const msg = lastAiMsg()
        msg.content = result.answer
        msg.loading = false
        currentSources.value = result.source_documents
        msg.sources = result.source_documents
        if (result.downshift_notice) showGpuNotice(result.downshift_notice)
        await persist()
        loading.value = false
        scrollDown()
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
async function handleSelectClarificationOption(aiMsg: Message, option: ClarificationOption) {
  if (!aiMsg.clarification || aiMsg.clarification.selectedId || loading.value) return

  aiMsg.clarification.selectedId = option.id
  aiMsg.loading = true
  aiMsg.status = `已选择「${option.label}」，正在检索回答...`
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
  const clarificationSelected = option.label

  if (option.id === 'other') {
    aiMsg.clarification.otherText = option.label
    userText = `${userText} (${option.label})`
  }

  const docCategoryVal = option.filter.doc_category || docCategory.value || undefined
  const entityNameVal = option.filter.entity_name || entityName.value || undefined

  try {
    abortController.value = new AbortController()
    let streamOk = false
    try {
      const llmModel = currentModel.value || undefined
      await queryKnowledgeStream(
        userText,
        history,
        {
          onStatus: (status) => {
            aiMsg.status = status
            scrollDown()
          },
          onToken: (token) => {
            aiMsg.status = undefined
            aiMsg.content += token
            aiMsg.loading = false
            scrollDown()
          },
          onThinking: (thought) => {
            aiMsg.thinking = (aiMsg.thinking || '') + thought
            scrollDown()
          },
          onFinalAnswer: (answer) => {
            aiMsg.status = undefined
            aiMsg.content = answer
            aiMsg.loading = false
            scrollDown()
          },
          onSources: (sources) => {
            currentSources.value = sources
            aiMsg.sources = sources
          },
          onTrace: (traceId) => {
            aiMsg.trace_id = traceId
          },
          onNotice: (notice) => {
            showGpuNotice(notice)
          },
          onDone: () => {
            streamOk = true
            abortController.value = null
            aiMsg.status = undefined
            aiMsg.loading = false
            loading.value = false
            persist()
            scrollDown()
          },
          onError: () => { throw new Error('stream failed') },
        },
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
      )
    } catch {
      aiMsg.status = undefined
      if (!streamOk && !abortController.value) {
        const result = await queryKnowledge(
          userText,
          history,
          currentModel.value || undefined,
          currentKb.value,
          thinkingEnabled.value || undefined,
          webSearchEnabled.value || undefined,
          undefined,
          activeAgent.value?.system_prompt,
          allowGeneralKnowledge.value,
          docCategoryVal,
          entityNameVal,
        )
        aiMsg.content = result.answer
        aiMsg.loading = false
        currentSources.value = result.source_documents
        aiMsg.sources = result.source_documents
        if (result.downshift_notice) showGpuNotice(result.downshift_notice)
        await persist()
        loading.value = false
        scrollDown()
      }
    }
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

async function dataUrlToFile(dataUrl: string, filename: string): Promise<File> {
  const res = await fetch(dataUrl)
  const blob = await res.blob()
  return new File([blob], filename, { type: blob.type || 'image/png' })
}

/** 重新编辑消息并触发重新提问 */
async function handleResend(msg: Message, payload: { content: string; imageFile?: File; imageUrl?: string } | string) {
  const index = messages.value.findIndex((m) => m.id === msg.id)
  if (index === -1) return

  // 若当前正在流式回答，先中止
  if (loading.value && abortController.value) {
    abortController.value.abort()
    loading.value = false
  }

  // 截断此消息及其之后的所有消息
  messages.value = messages.value.slice(0, index)

  // 以新内容和图片触发提问流程
  const content = typeof payload === 'string' ? payload : payload.content
  const imageFile = typeof payload === 'object' ? payload.imageFile : undefined
  const existingImageUrl = typeof payload === 'object' ? payload.imageUrl : undefined

  if (imageFile) {
    await handleSend(content, imageFile)
  } else if (existingImageUrl) {
    try {
      const file = await dataUrlToFile(existingImageUrl, 're-edit-image.png')
      await handleSend(content, file)
    } catch {
      await handleSend(content)
    }
  } else {
    await handleSend(content)
  }
}

/** 将指定消息内容填入底部输入框 */
function handleEditInInput(text: string) {
  chatInputRef.value?.setText(text)
  showToast('已填入输入框')
}

/** 复制成功反馈 */
function handleCopyText(_text: string) {
  showToast('已复制到剪贴板')
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
  const ok = await showConfirm('确定清空所有对话记录？')
  if (!ok) return
  await clearChatState()
  messages.value = [{
    id: 'welcome', role: 'assistant',
    content: '你好！我是 RAG 知识库助手。\n\n你可以输入文字提问，也可以**粘贴或上传图片**让我识别描述。',
  }]
  messages.value = []
  currentSources.value = []
  showSources.value = false
  showToast('对话已清空')
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

function scrollDown() {
  nextTick(() => {
    if (msgContainer.value) {
      msgContainer.value.scrollTop = msgContainer.value.scrollHeight
    }
  })
}
</script>

<template>
  <div class="chat-layout" @click="onLayoutClick">
    <!-- 左侧历史会话边栏 -->
    <aside class="history-sidebar" :class="{ 'history-sidebar--collapsed': !showHistorySidebar }">
      <!-- 边栏顶部 Header：两行布局 -->
      <div class="history-sidebar-top">
        <!-- 第 1 行：RAG 知识库 标题 + 搜索按钮 + 收起边栏按钮 -->
        <div class="sidebar-brand-row">
          <div class="sidebar-brand">
            <svg class="brand-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
              <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
            </svg>
            <span class="brand-title">RAG 知识库</span>
          </div>
          <div class="sidebar-top-btns">
            <!-- 🔍 搜索历史对话按钮 -->
            <button
              class="btn-sidebar-icon"
              :class="{ active: isSearchModalOpen }"
              title="搜索对话 (Ctrl+K)"
              @click="openSearchModal"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="11" cy="11" r="8"/>
                <line x1="21" y1="21" x2="16.65" y2="16.65"/>
              </svg>
            </button>
            <!-- 收起边栏按钮 -->
            <button class="btn-sidebar-collapse" title="收起边栏" @click="toggleHistorySidebar">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="15 18 9 12 15 6"/>
              </svg>
            </button>
          </div>
        </div>

        <!-- 第 2 行：常规开启新对话 / 多选时显示对话与关闭符号 -->
        <div class="sidebar-action-row">
          <!-- 常规模式：开启新对话 -->
          <button v-if="!isBatchMode" class="btn-new-chat" title="开启新对话" @click="handleNewChat">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <line x1="12" y1="5" x2="12" y2="19"/>
              <line x1="5" y1="12" x2="19" y2="12"/>
            </svg>
            <span>开启新对话</span>
          </button>

          <!-- 多选模式：左侧显示对话状态，右侧显示关闭符号 ✕ -->
          <div v-else class="batch-status-panel">
            <div class="batch-status-text">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="9 11 12 14 22 4"/>
                <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
              </svg>
              <span>{{ selectedSessionIds.size === 0 ? '选择对话' : `已选择 ${selectedSessionIds.size} 条对话` }}</span>
            </div>
            <button class="btn-batch-close" title="退出多选" @click="toggleBatchMode">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4">
                <line x1="18" y1="6" x2="6" y2="18"/>
                <line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>
        </div>
      </div>

      <div class="history-sidebar-list">
        <div v-if="sessions.length === 0" class="history-empty">
          暂无历史对话
        </div>

        <div v-for="(group, gIdx) in groupedSessions" :key="group.key" class="session-group">
          <div class="session-group-header">
            <span class="session-group-title">{{ group.label }}</span>
            <button
              v-if="gIdx === 0 && !isBatchMode"
              class="btn-group-batch-toggle"
              title="多选"
              @click="toggleBatchMode"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="9 11 12 14 22 4"/>
                <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
              </svg>
            </button>
          </div>
          <div
            v-for="s in group.sessions"
            :key="s.id"
            class="session-nav-item"
            :class="{
              active: !isBatchMode && s.id === currentSessionId,
              'is-pinned': s.pinned,
            }"
            @click="isBatchMode ? toggleSelectSession(s.id, $event) : switchSession(s.id)"
          >
            <!-- 多选圆框选择器 -->
            <label v-if="isBatchMode" class="session-check-wrap" @click.stop>
              <input
                type="checkbox"
                class="session-checkbox-input"
                :checked="selectedSessionIds.has(s.id)"
                @change="toggleSelectSession(s.id)"
              />
              <span class="session-check-circle" :class="{ checked: selectedSessionIds.has(s.id) }">
                <svg v-if="selectedSessionIds.has(s.id)" width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
              </span>
            </label>

            <!-- 编辑名称模式 -->
            <input
              v-if="editingSessionId === s.id"
              v-model="editingTitleDraft"
              class="session-edit-input"
              autofocus
              @click.stop
              @keydown.enter.prevent="saveRenameSession(s)"
              @keydown.esc="cancelRenameSession"
              @blur="saveRenameSession(s)"
            />
            <!-- 常规标题显示 -->
            <span v-else class="session-nav-title" :title="s.title">{{ s.title || '新对话' }}</span>

            <!-- 三个点更多操作按钮（悬浮或激活时显示） -->
            <div v-if="editingSessionId !== s.id && !isBatchMode" class="session-nav-actions" @click.stop>
              <button
                class="session-more-btn"
                :class="{ active: activeMenuSessionId === s.id }"
                @click="toggleSessionMenu(s, $event)"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
                  <circle cx="5" cy="12" r="2"/>
                  <circle cx="12" cy="12" r="2"/>
                  <circle cx="19" cy="12" r="2"/>
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 批量管理底部操作栏：仅保留置顶与删除 -->
      <Transition name="batch-bar">
        <div v-if="isBatchMode" class="history-batch-bar">
          <div class="batch-actions-grid">
            <button
              class="batch-op-btn"
              :disabled="selectedSessionIds.size === 0"
              title="批量置顶"
              @click="handleBatchPin(true)"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5v6l1 1 1-1v-6h5v-2l-2-2z"/>
              </svg>
              <span>置顶</span>
            </button>
            <button
              class="batch-op-btn batch-op-btn--danger"
              :disabled="selectedSessionIds.size === 0"
              title="批量删除"
              @click="handleBatchDelete"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="3 6 5 6 21 6"/>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
              </svg>
              <span>删除</span>
            </button>
          </div>
        </div>
      </Transition>
    </aside>

    <div class="chat-main">
      <header class="header">
        <div class="header-left">
          <!-- 边栏展开按钮（边栏收起时显示） -->
          <button
            v-if="!showHistorySidebar"
            class="sidebar-expand-btn"
            title="展开历史对话边栏"
            @click="toggleHistorySidebar"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
              <line x1="9" y1="3" x2="9" y2="21"/>
            </svg>
          </button>
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

          <div class="header-action-tools">
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

      <div ref="msgContainer" class="msg-list">
        <div class="msg-wrap">
          <div v-if="showWelcomeHint" class="welcome-panel">
            <div class="welcome-badge">助手说明</div>
            <div class="welcome-title">优先提问项目资料，回复会更准确</div>
            <p class="welcome-text">{{ welcomeHint }}</p>
          </div>
          <ChatMessage
            v-for="msg in messages" :key="msg.id"
            :role="msg.role" :content="msg.content"
            :image-url="msg.imageUrl" :loading="msg.loading"
            :status="msg.status"
            :thinking="msg.thinking"
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
            @resend="handleResend(msg, $event)"
            @edit-in-input="handleEditInInput"
            @copy="handleCopyText"
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

      <ChatInput ref="chatInputRef" :disabled="loading" @send="handleSend" @stop="handleStop" />
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

    <!-- 三个点更多操作浮层菜单 -->
    <Teleport to="body">
      <div
        v-if="activeMenuSessionId && menuSession"
        class="session-popover-menu"
        :style="menuPosition"
        @click.stop
      >
        <button class="session-popover-item" @click="handleMenuPin(menuSession)">
          <svg width="13" height="13" viewBox="0 0 24 24" :fill="menuSession.pinned ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2">
            <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5v6l1 1 1-1v-6h5v-2l-2-2z"/>
          </svg>
          <span>{{ menuSession.pinned ? '取消置顶' : '置顶对话' }}</span>
        </button>
        <button class="session-popover-item" @click="handleMenuShare(menuSession)">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="18" cy="5" r="3"/>
            <circle cx="6" cy="12" r="3"/>
            <circle cx="18" cy="19" r="3"/>
            <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/>
            <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>
          </svg>
          <span>分享</span>
        </button>
        <button class="session-popover-item" @click="handleMenuRename(menuSession)">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 20h9M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
          </svg>
          <span>重命名</span>
        </button>
        <div class="session-popover-divider"></div>
        <button class="session-popover-item session-popover-item--danger" @click="handleMenuDelete(menuSession)">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
          </svg>
          <span>删除对话</span>
        </button>
      </div>
    </Teleport>

    <!-- 分享对话公开链接弹窗 -->
    <Teleport to="body">
      <div v-if="shareModalVisible && shareTargetSession" class="confirm-overlay" @click.self="shareModalVisible = false">
        <div class="share-modal-box">
          <div class="share-modal-header">
            <div class="share-modal-title-group">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
                <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
              </svg>
              <h3>创建分享链接</h3>
            </div>
            <button class="share-modal-close" @click="shareModalVisible = false">✕</button>
          </div>

          <div class="share-modal-body">
            <div class="share-session-info">
              <div class="share-session-title">{{ shareTargetSession.title || '新对话' }}</div>
              <div class="share-session-meta">
                共 {{ shareTargetSession.messages.length }} 条消息 · {{ new Date(shareTargetSession.createdAt).toLocaleString('zh-CN') }}
              </div>
            </div>

            <!-- 分享链接生成与复制区域 -->
            <div class="share-link-section">
              <label class="share-link-label">公开分享链接</label>
              <div class="share-link-input-group">
                <input
                  type="text"
                  readonly
                  :value="shareLinkUrl"
                  class="share-link-input"
                  @click="($event.target as HTMLInputElement).select()"
                />
                <button class="btn-copy-link" @click="copyShareLink">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                  </svg>
                  <span>复制链接</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 🔍 历史对话全局搜索模态框 -->
    <Teleport to="body">
      <div
        v-if="isSearchModalOpen"
        class="search-modal-backdrop"
        @click.self="closeSearchModal"
      >
        <div class="search-modal-box" @keydown="handleSearchModalKeydown">
          <!-- 顶部搜索输入头 -->
          <div class="search-modal-input-wrap">
            <svg class="search-modal-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="11" cy="11" r="8"/>
              <line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <input
              ref="searchModalInputRef"
              v-model="searchModalQuery"
              type="text"
              class="search-modal-input"
              placeholder="搜索对话内容..."
              @input="searchSelectedIdx = 0"
            />
            <!-- 关闭按钮 -->
            <button
              type="button"
              class="search-modal-close-btn"
              @click="closeSearchModal"
            >
              ✕
            </button>
          </div>

          <!-- 搜索结果列表（仅在输入关键词后展示） -->
          <div v-if="searchModalQuery.trim()" class="search-modal-body">
            <!-- 1. 已输入搜索词但无匹配结果 -->
            <div v-if="searchModalResults.length === 0" class="search-modal-empty">
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="empty-icon">
                <circle cx="11" cy="11" r="8"/>
                <line x1="21" y1="21" x2="16.65" y2="16.65"/>
              </svg>
              <p class="empty-title">未找到相关对话</p>
              <p class="empty-sub">尝试使用更简短或不同的关键词搜索</p>
            </div>

            <!-- 2. 已输入搜索词且有匹配结果列表 -->
            <div v-else class="search-modal-results-list">
              <div
                v-for="(item, idx) in searchModalResults"
                :key="item.session.id"
                class="search-result-row"
                :class="{
                  active: idx === searchSelectedIdx,
                  'is-current': item.session.id === currentSessionId
                }"
                @mouseenter="searchSelectedIdx = idx"
                @click="handleSelectSearchResult(item.session)"
              >
                <div class="result-icon-col">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                  </svg>
                </div>
                <div class="result-info-col">
                  <div class="result-title-row">
                    <span class="result-title">{{ item.session.title || '新对话' }}</span>
                    <span v-if="item.session.pinned" class="pinned-tag">置顶</span>
                  </div>
                  <div v-if="item.matchedContentSnippet" class="result-snippet">
                    {{ item.matchedContentSnippet }}
                  </div>
                </div>
                <div class="result-meta-col">
                  <span class="result-time">{{ item.timeLabel }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

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
.chat-layout { display: flex; height: 100%; background: #fff; position: relative; }
.chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; height: 100%; }

/* 左侧历史边栏 (History Sidebar) */
.history-sidebar {
  width: 260px;
  background: #f8fafc;
  border-right: 1px solid #e8eaed;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  transition: width 0.25s cubic-bezier(0.4, 0, 0.2, 1), transform 0.25s ease;
  z-index: 10;
  height: 100%;
}
.history-sidebar--collapsed {
  width: 0;
  overflow: hidden;
  border-right: none;
}

.history-sidebar-top {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid #eef2f6;
  flex-shrink: 0;
}

.sidebar-brand-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sidebar-top-btns {
  display: flex;
  align-items: center;
  gap: 2px;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #1e293b;
  font-weight: 600;
  font-size: 14px;
  user-select: none;
}

.brand-icon {
  color: #3370ff;
}

.btn-sidebar-icon {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: #64748b;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-sidebar-icon:hover,
.btn-sidebar-icon.active {
  background: #e2e8f0;
  color: #3370ff;
}

.sidebar-search-box {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  transition: all 0.15s ease;
}
.sidebar-search-box:focus-within {
  border-color: #3370ff;
  box-shadow: 0 0 0 2px rgba(51, 112, 255, 0.12);
}

.search-box-icon {
  color: #94a3b8;
  flex-shrink: 0;
}

.sidebar-search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  font-size: 12px;
  color: #1e293b;
  background: transparent;
}

.search-clear-btn {
  border: none;
  background: transparent;
  color: #94a3b8;
  font-size: 11px;
  cursor: pointer;
  padding: 0 2px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.search-clear-btn:hover {
  color: #ef4444;
}

.sidebar-action-row {
  width: 100%;
  height: 32px;
  display: flex;
  align-items: center;
  padding: 0 2px;
  box-sizing: border-box;
}

.btn-new-chat {
  width: 100%;
  height: 32px;
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 12px;
  background: #3370ff;
  color: #ffffff;
  border: none;
  border-radius: 30px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  box-shadow: 0 1px 3px rgba(51, 112, 255, 0.15);
  box-sizing: border-box;
}
.btn-new-chat:hover {
  background: #1a56db;
}

.batch-status-panel {
  width: 100%;
  height: 32px;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 2px 0 6px;
  background: transparent;
  color: #3370ff;
  border: none;
  font-size: 13px;
  font-weight: 500;
  user-select: none;
  box-sizing: border-box;
}

.batch-status-text {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.btn-batch-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  background: transparent;
  color: #64748b;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-batch-close:hover {
  background: #fee2e2;
  color: #ef4444;
}

.btn-sidebar-collapse {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: #64748b;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-sidebar-collapse:hover {
  background: #e2e8f0;
  color: #1e293b;
}

.history-sidebar-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.history-empty {
  padding: 36px 12px;
  text-align: center;
  color: #94a3b8;
  font-size: 12px;
}

.session-group {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-bottom: 8px;
}
.session-group:last-child {
  margin-bottom: 0;
}

.session-group-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 6px 2px 8px;
}

.session-group-title {
  font-size: 11px;
  font-weight: 600;
  color: #94a3b8;
  user-select: none;
  letter-spacing: 0.3px;
}

.btn-group-batch-toggle {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid transparent;
  background: transparent;
  color: #94a3b8;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-group-batch-toggle:hover {
  color: #3370ff;
  background: #edf2f7;
}
.btn-group-batch-toggle.active {
  color: #64748b;
  background: #e2e8f0;
  border-radius: 4px;
  padding: 2px 4px;
  border-color: transparent;
}
.btn-group-batch-toggle.active:hover {
  color: #ef4444;
  background: #fee2e2;
}

.session-nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 10px;
  border-radius: 10px;
  color: #475569;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
  position: relative;
  min-width: 0;
  border: 1px solid transparent;
  box-sizing: border-box;
}
.session-nav-item:hover {
  background: #edf2f7;
  color: #1e293b;
}
.session-nav-item.active {
  background: #e0e7ff;
  color: #1d4ed8;
  font-weight: 500;
}

.session-nav-item.is-pinned {
  background: #fffdf5;
  border-color: #fef3c7;
}
.session-nav-item.is-pinned:hover {
  background: #fef9c3;
}
.session-nav-item.is-pinned.active {
  background: #e0e7ff;
  border-color: #c7d2fe;
}

.session-check-wrap {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  cursor: pointer;
  flex-shrink: 0;
  position: relative;
  margin-right: 4px;
  animation: check-pop 0.22s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes check-pop {
  0% {
    opacity: 0;
    transform: scale(0.4) translateX(-8px);
  }
  100% {
    opacity: 1;
    transform: scale(1) translateX(0);
  }
}

.session-checkbox-input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
  margin: 0;
  pointer-events: none;
}

.session-check-circle {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 1.5px solid #cbd5e1;
  background: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.18s cubic-bezier(0.34, 1.56, 0.64, 1);
  box-sizing: border-box;
}

.session-check-wrap:hover .session-check-circle {
  border-color: #3370ff;
  transform: scale(1.08);
}

.session-check-circle.checked {
  background: #3370ff;
  border-color: #3370ff;
  transform: scale(1.05);
  box-shadow: 0 1px 4px rgba(51, 112, 255, 0.3);
}

.session-icon {
  flex-shrink: 0;
  color: #94a3b8;
}
.session-nav-item.active .session-icon {
  color: #3370ff;
}

.pin-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #f59e0b;
  flex-shrink: 0;
}

.session-nav-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  line-height: 36px;
}

.session-nav-actions {
  display: none;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  margin-left: auto;
}
.session-nav-item:hover .session-nav-actions,
.session-nav-item.active .session-nav-actions {
  display: flex;
}
.session-more-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  margin: 0;
  border: none !important;
  outline: none !important;
  background: transparent !important;
  border-radius: 5px;
  color: #64748b;
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
  box-shadow: none !important;
}
.session-more-btn:hover,
.session-more-btn.active {
  background: rgba(0, 0, 0, 0.08) !important;
  color: #1e293b;
}

/* 三个点 Popover 浮层菜单 */
.session-popover-menu {
  position: fixed;
  z-index: 10001;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.12), 0 8px 10px -6px rgba(0, 0, 0, 0.04);
  padding: 4px;
  min-width: 130px;
  display: flex;
  flex-direction: column;
  gap: 1px;
  animation: pop-in 0.15s cubic-bezier(0.16, 1, 0.3, 1);
}

.session-popover-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #334155;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.12s ease;
  text-align: left;
  width: 100%;
}
.session-popover-item:hover {
  background: #f1f5f9;
  color: #3370ff;
}
.session-popover-item svg {
  flex-shrink: 0;
}

.session-popover-divider {
  height: 1px;
  background: #f1f5f9;
  margin: 3px 0;
}

.session-popover-item--danger {
  color: #ef4444;
}
.session-popover-item--danger:hover {
  background: #fef2f2;
  color: #dc2626;
}

.session-edit-input {
  flex: 1;
  min-width: 0;
  height: 26px;
  padding: 0 6px;
  font-size: 12px;
  border: 1px solid #3370ff;
  border-radius: 4px;
  outline: none;
  background: #ffffff;
  color: #1e293b;
}

.btn-batch-toggle {
  width: 32px;
  height: 32px;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
  background: #ffffff;
  color: #64748b;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
}
.btn-batch-toggle:hover {
  background: #f1f5f9;
  color: #3370ff;
}
.btn-batch-toggle.active {
  background: #e0e7ff;
  color: #1d4ed8;
  border-color: #c7d2fe;
}

.sidebar-expand-btn {
  width: 32px;
  height: 32px;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
  background: #ffffff;
  color: #64748b;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
}
.sidebar-expand-btn:hover {
  background: #f1f5f9;
  color: #3370ff;
  border-color: #cbd5e1;
}

/* 历史会话批量管理操作栏 */
.history-batch-bar {
  padding: 10px 12px;
  background: #ffffff;
  border-top: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  box-shadow: 0 -4px 12px rgba(0, 0, 0, 0.05);
}

.batch-bar-enter-active,
.batch-bar-leave-active {
  transition: all 0.22s cubic-bezier(0.16, 1, 0.3, 1);
}
.batch-bar-enter-from,
.batch-bar-leave-to {
  opacity: 0;
  transform: translateY(100%);
}

.batch-bar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  font-weight: 500;
  color: #475569;
}

.batch-count {
  font-weight: 600;
  color: #1e293b;
}

.batch-text-btn {
  background: transparent;
  border: none;
  color: #3370ff;
  font-size: 12px;
  cursor: pointer;
  padding: 0 4px;
}
.batch-text-btn:hover {
  text-decoration: underline;
}

.batch-actions-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}

.batch-op-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 7px 10px;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  color: #334155;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  text-align: center;
}
.batch-op-btn:hover:not(:disabled) {
  background: #eef2ff;
  color: #3370ff;
  border-color: #c7d2fe;
}
.batch-op-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.batch-op-btn--danger:hover:not(:disabled) {
  background: #fef2f2;
  color: #dc2626;
  border-color: #fecaca;
}

.batch-op-btn--cancel {
  grid-column: span 2;
  background: #f1f5f9;
  color: #64748b;
  border-color: transparent;
}
.batch-op-btn--cancel:hover {
  background: #e2e8f0;
  color: #1e293b;
}

/* 分享与导出模态弹窗 */
.share-modal-box {
  background: #ffffff;
  border-radius: 14px;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.15), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
  width: 440px;
  max-width: 92vw;
  overflow: hidden;
  border: 1px solid #e2e8f0;
  animation: pop-in 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes pop-in {
  from { opacity: 0; transform: scale(0.95); }
  to { opacity: 1; transform: scale(1); }
}

.share-modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid #f1f5f9;
}

.share-modal-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #1e293b;
}
.share-modal-title-group h3 {
  font-size: 15px;
  font-weight: 600;
  margin: 0;
}

.share-modal-close {
  border: none;
  background: transparent;
  color: #94a3b8;
  font-size: 16px;
  cursor: pointer;
  border-radius: 4px;
  padding: 2px 6px;
  transition: all 0.15s;
}
.share-modal-close:hover {
  background: #f1f5f9;
  color: #1e293b;
}

.share-modal-body {
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.share-session-info {
  padding: 10px 12px;
  border-radius: 8px;
  background: #f8fafc;
  border: 1px solid #eef2f6;
}

.share-session-title {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.share-session-meta {
  font-size: 11px;
  color: #64748b;
  margin-top: 3px;
}

.share-link-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.share-link-label {
  font-size: 12px;
  font-weight: 600;
  color: #334155;
}

.share-link-input-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.share-link-input {
  flex: 1;
  min-width: 0;
  height: 38px;
  padding: 0 12px;
  background: #f8fafc;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  font-size: 13px;
  color: #1e293b;
  font-family: inherit;
  outline: none;
  transition: all 0.15s ease;
}
.share-link-input:focus {
  border-color: #3370ff;
  background: #ffffff;
  box-shadow: 0 0 0 3px rgba(51, 112, 255, 0.12);
}

.btn-copy-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 38px;
  padding: 0 16px;
  background: #3370ff;
  color: #ffffff;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  transition: all 0.15s ease;
  box-shadow: 0 1px 3px rgba(51, 112, 255, 0.2);
}
.btn-copy-link:hover {
  background: #1a56db;
}

.share-link-tip {
  margin: 0;
  font-size: 11px;
  color: #64748b;
  line-height: 1.5;
}


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
.header-left { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
  justify-content: flex-end;
}
.header-action-tools {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
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
  right: 0;
  margin-top: 6px;
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.12);
  padding: 6px;
  min-width: 140px;
  z-index: 1000;
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
.pop-enter-from, .pop-leave-to { opacity: 0; transform: translateY(-4px); }

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
  .history-sidebar {
    position: absolute;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: 1000;
    box-shadow: 4px 0 24px rgba(0, 0, 0, 0.15);
  }
  .history-sidebar--collapsed {
    transform: translateX(-100%);
    width: 240px;
  }
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
</style>

<!-- Teleport 全局弹窗与菜单样式 -->
<style>
.share-modal-box {
  background: #ffffff !important;
  border-radius: 14px !important;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.15), 0 10px 10px -5px rgba(0, 0, 0, 0.04) !important;
  width: 460px !important;
  max-width: 92vw !important;
  overflow: hidden !important;
  border: 1px solid #e2e8f0 !important;
  animation: pop-in 0.2s cubic-bezier(0.16, 1, 0.3, 1) !important;
}

.share-modal-header {
  display: flex !important;
  justify-content: space-between !important;
  align-items: center !important;
  padding: 14px 18px !important;
  border-bottom: 1px solid #f1f5f9 !important;
}

.share-modal-title-group {
  display: flex !important;
  align-items: center !important;
  gap: 8px !important;
  color: #1e293b !important;
}
.share-modal-title-group h3 {
  font-size: 15px !important;
  font-weight: 600 !important;
  margin: 0 !important;
  color: #1e293b !important;
}

.share-modal-close {
  border: none !important;
  background: transparent !important;
  color: #94a3b8 !important;
  font-size: 16px !important;
  cursor: pointer !important;
  border-radius: 4px !important;
  padding: 2px 6px !important;
  transition: all 0.15s !important;
}
.share-modal-close:hover {
  background: #f1f5f9 !important;
  color: #1e293b !important;
}

.share-modal-body {
  padding: 18px 20px !important;
  display: flex !important;
  flex-direction: column !important;
  gap: 14px !important;
  background: #ffffff !important;
}

.share-session-info {
  padding: 10px 14px !important;
  border-radius: 8px !important;
  background: #f8fafc !important;
  border: 1px solid #eef2f6 !important;
}

.share-session-title {
  font-size: 13px !important;
  font-weight: 600 !important;
  color: #1e293b !important;
  overflow: hidden !important;
  text-overflow: ellipsis !important;
  white-space: nowrap !important;
}

.share-session-meta {
  font-size: 11px !important;
  color: #64748b !important;
  margin-top: 3px !important;
}

.share-link-section {
  display: flex !important;
  flex-direction: column !important;
  gap: 6px !important;
}

.share-link-label {
  font-size: 12px !important;
  font-weight: 600 !important;
  color: #334155 !important;
}

.share-link-input-group {
  display: flex !important;
  align-items: center !important;
  gap: 8px !important;
}

.share-link-input {
  flex: 1 !important;
  min-width: 0 !important;
  height: 38px !important;
  padding: 0 12px !important;
  background: #f8fafc !important;
  border: 1px solid #cbd5e1 !important;
  border-radius: 8px !important;
  font-size: 12px !important;
  color: #1e293b !important;
  font-family: inherit !important;
  outline: none !important;
  transition: all 0.15s ease !important;
}
.share-link-input:focus {
  border-color: #3370ff !important;
  background: #ffffff !important;
  box-shadow: 0 0 0 3px rgba(51, 112, 255, 0.12) !important;
}

.btn-copy-link {
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  gap: 6px !important;
  height: 38px !important;
  padding: 0 16px !important;
  background: #3370ff !important;
  color: #ffffff !important;
  border: none !important;
  border-radius: 8px !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  cursor: pointer !important;
  white-space: nowrap !important;
  flex-shrink: 0 !important;
  transition: all 0.15s ease !important;
  box-shadow: 0 1px 3px rgba(51, 112, 255, 0.2) !important;
}
.btn-copy-link:hover {
  background: #1a56db !important;
}

/* ===== 全局搜索模态框样式 (Command Palette 风格) ===== */
.search-modal-backdrop {
  position: fixed !important;
  top: 0 !important;
  left: 0 !important;
  right: 0 !important;
  bottom: 0 !important;
  background: rgba(15, 23, 42, 0.45) !important;
  backdrop-filter: blur(5px) !important;
  display: flex !important;
  align-items: flex-start !important;
  justify-content: center !important;
  padding-top: 12vh !important;
  z-index: 99999 !important;
  animation: search-fade-in 0.15s ease !important;
}

@keyframes search-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

.search-modal-box {
  width: 580px !important;
  max-width: 92vw !important;
  background: #ffffff !important;
  border-radius: 14px !important;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25), 0 0 0 1px rgba(0, 0, 0, 0.08) !important;
  overflow: hidden !important;
  display: flex !important;
  flex-direction: column !important;
  animation: search-pop 0.18s cubic-bezier(0.16, 1, 0.3, 1) !important;
}

@keyframes search-pop {
  from {
    opacity: 0;
    transform: scale(0.96) translateY(-8px);
  }
  to {
    opacity: 1;
    transform: scale(1) translateY(0);
  }
}

.search-modal-input-wrap {
  display: flex !important;
  align-items: center !important;
  gap: 10px !important;
  padding: 14px 18px !important;
  background: #ffffff !important;
}

.search-modal-input-wrap:has(+ .search-modal-body) {
  border-bottom: 1px solid #e2e8f0 !important;
}

.search-modal-icon {
  color: #64748b !important;
  flex-shrink: 0 !important;
}

.search-modal-input {
  flex: 1 !important;
  border: none !important;
  outline: none !important;
  font-size: 15px !important;
  color: #1e293b !important;
  background: transparent !important;
  font-family: inherit !important;
}

.search-modal-input::placeholder {
  color: #94a3b8 !important;
}

.search-modal-close-btn {
  border: none !important;
  background: transparent !important;
  color: #94a3b8 !important;
  width: 24px !important;
  height: 24px !important;
  border-radius: 6px !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  font-size: 13px !important;
  cursor: pointer !important;
  padding: 0 !important;
  transition: all 0.15s ease !important;
}
.search-modal-close-btn:hover {
  background: #f1f5f9 !important;
  color: #1e293b !important;
}

.search-modal-body {
  max-height: 420px !important;
  overflow-y: auto !important;
  padding: 8px !important;
  background: #fafbfc !important;
}

.search-modal-empty {
  display: flex !important;
  flex-direction: column !important;
  align-items: center !important;
  justify-content: center !important;
  padding: 36px 16px !important;
  color: #94a3b8 !important;
  text-align: center !important;
}

.search-modal-empty .empty-icon {
  margin-bottom: 12px !important;
  opacity: 0.6 !important;
}

.search-modal-empty .empty-title {
  font-size: 14px !important;
  font-weight: 500 !important;
  color: #475569 !important;
  margin: 0 0 4px 0 !important;
}

.search-modal-empty .empty-sub {
  font-size: 12px !important;
  color: #94a3b8 !important;
  margin: 0 !important;
}

.search-modal-results-list {
  display: flex !important;
  flex-direction: column !important;
  gap: 2px !important;
}

.search-results-section-title {
  font-size: 11px !important;
  font-weight: 600 !important;
  color: #94a3b8 !important;
  padding: 6px 12px !important;
  letter-spacing: 0.5px !important;
  text-transform: uppercase !important;
}

.search-result-row {
  display: flex !important;
  align-items: center !important;
  gap: 12px !important;
  padding: 10px 12px !important;
  border-radius: 8px !important;
  cursor: pointer !important;
  transition: all 0.12s ease !important;
  background: transparent !important;
}

.search-result-row:hover,
.search-result-row.active {
  background: #ffffff !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06), 0 0 0 1px #e2e8f0 !important;
}

.search-result-row.active {
  background: #eff6ff !important;
  box-shadow: 0 2px 8px rgba(51, 112, 255, 0.1), 0 0 0 1px #bfdbfe !important;
}

.result-icon-col {
  width: 28px !important;
  height: 28px !important;
  border-radius: 6px !important;
  background: #f1f5f9 !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  color: #64748b !important;
  flex-shrink: 0 !important;
}

.search-result-row.active .result-icon-col {
  background: #dbeafe !important;
  color: #2563eb !important;
}

.result-info-col {
  flex: 1 !important;
  min-width: 0 !important;
  display: flex !important;
  flex-direction: column !important;
  gap: 3px !important;
}

.result-title-row {
  display: flex !important;
  align-items: center !important;
  gap: 6px !important;
}

.result-title {
  font-size: 13.5px !important;
  font-weight: 500 !important;
  color: #1e293b !important;
  overflow: hidden !important;
  text-overflow: ellipsis !important;
  white-space: nowrap !important;
}

.search-result-row.active .result-title {
  color: #1d4ed8 !important;
  font-weight: 600 !important;
}

.pinned-tag {
  font-size: 10px !important;
  font-weight: 600 !important;
  color: #d97706 !important;
  background: #fef3c7 !important;
  padding: 1px 5px !important;
  border-radius: 4px !important;
  flex-shrink: 0 !important;
}

.current-tag {
  font-size: 10px !important;
  font-weight: 600 !important;
  color: #2563eb !important;
  background: #dbeafe !important;
  padding: 1px 5px !important;
  border-radius: 4px !important;
  flex-shrink: 0 !important;
}

.result-snippet {
  font-size: 12px !important;
  color: #64748b !important;
  overflow: hidden !important;
  text-overflow: ellipsis !important;
  white-space: nowrap !important;
  line-height: 1.4 !important;
}

.result-meta-col {
  flex-shrink: 0 !important;
  text-align: right !important;
}

.result-time {
  font-size: 11px !important;
  color: #94a3b8 !important;
}

.search-modal-footer {
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  padding: 10px 16px !important;
  background: #ffffff !important;
  border-top: 1px solid #e2e8f0 !important;
  font-size: 11.5px !important;
  color: #64748b !important;
}

.footer-tips {
  display: flex !important;
  align-items: center !important;
  gap: 12px !important;
}

.tip-item {
  display: inline-flex !important;
  align-items: center !important;
  gap: 4px !important;
}

.tip-item kbd {
  background: #f1f5f9 !important;
  border: 1px solid #cbd5e1 !important;
  border-radius: 3px !important;
  padding: 1px 4px !important;
  font-size: 10px !important;
  font-weight: 600 !important;
  color: #475569 !important;
}

.tip-count {
  font-size: 11px !important;
  color: #94a3b8 !important;
}
</style>
