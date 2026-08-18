<script setup lang="ts">
defineOptions({ name: 'ChatView' })
import { ref, onMounted, onUnmounted, nextTick, computed } from 'vue'
import type { Message, SourceDoc, Stats, ClarificationOption, ClarifyResult, EvidenceItem, GpuStatus, AgentToolCall, AgentTimelineItem } from '../types'
import { queryKnowledgeStream, queryKnowledge, queryImageStream, queryClarify, getStats, triggerScan, uploadDocument, getModels, getGpuStatus, getKnowledgeBases, getAgents, updateQaTraceFeedback, submitUserFeedback, DOCUMENT_PROFILE_OPTIONS } from '../api'
import type { DocumentProfile } from '../api'
import type { ModelsResponse, AgentInfo } from '../api'
import { saveChatState, saveChatStateLocalSync, loadChatState, clearChatState } from '../utils/storage'
import { buildChatHistoryPayload } from '../utils/chatHistory'
import ChatMessage from '../components/ChatMessage.vue'
import ChatInput from '../components/ChatInput.vue'
import SourcePanel from '../components/SourcePanel.vue'

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
const workMode = ref<'agent' | 'linear'>(
  (localStorage.getItem('rag-work-mode') as 'agent' | 'linear') || 'agent'
)

function setWorkMode(mode: 'agent' | 'linear') {
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

onMounted(async () => {
  messages.value = await loadChatState()
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
  if (initialized.value && messages.value.length > 0) {
    saveChatStateLocalSync(messages.value)
  }
}

onUnmounted(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
  clearInterval(gpuPollTimer)
  clearTimeout(gpuNoticeTimer)
  clearTimeout(gpuPopoverTimer)
})

async function persist() {
  if (!initialized.value) return
  await saveChatState(messages.value)
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
  if (!data?.needs_clarification || !data.options || data.options.length < 2) return false
  msg.loading = false
  msg.status = undefined
  msg.clarification = {
    ask_question: data.ask_question || '请选择您要查询的具体模块或方向：',
    trigger: data.trigger,
    reason: data.reason,
    options: data.options,
  }
  loading.value = false
  return true
}

function createStreamHandler(targetMsg: Message) {
  let inThinkTag = false
  let thinkStartTime = Date.now()

  if (!targetMsg.timelineItems) {
    targetMsg.timelineItems = []
  }

  function getActiveThinkItem(): Extract<AgentTimelineItem, { type: 'think' }> {
    if (!targetMsg.timelineItems) targetMsg.timelineItems = []
    const last = targetMsg.timelineItems[targetMsg.timelineItems.length - 1]
    if (last && last.type === 'think') {
      return last
    }
    const newItem: Extract<AgentTimelineItem, { type: 'think' }> = {
      type: 'think',
      content: '',
      isThinking: true,
      _startTime: Date.now(),
    }
    targetMsg.timelineItems.push(newItem)
    return newItem
  }

  return {
    onStatus: (status: string) => {
      targetMsg.status = status
      scrollDown()
    },
    onToken: (token: string) => {
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
        targetMsg.isThinking = true
        thinkStartTime = Date.now()
        text = parts.slice(1).join('<think>')
      }

      if (inThinkTag) {
        if (text.includes('</think>')) {
          const parts = text.split('</think>')
          const thinkText = parts[0]
          targetMsg.thinking = (targetMsg.thinking || '') + thinkText
          const activeThink = getActiveThinkItem()
          activeThink.content = (activeThink.content || '') + thinkText
          activeThink.isThinking = false
          const durSec = ((Date.now() - thinkStartTime) / 1000).toFixed(1)
          activeThink.duration = `${durSec}s`
          targetMsg.thinkingDuration = `${durSec}s`

          inThinkTag = false
          targetMsg.isThinking = false
          text = parts.slice(1).join('</think>')
          if (text) {
            targetMsg.content += text
          }
        } else {
          targetMsg.thinking = (targetMsg.thinking || '') + text
          const activeThink = getActiveThinkItem()
          activeThink.content = (activeThink.content || '') + text
          activeThink.isThinking = true
        }
      } else {
        if (targetMsg.timelineItems) {
          const last = targetMsg.timelineItems[targetMsg.timelineItems.length - 1]
          if (last && last.type === 'think' && last.isThinking) {
            last.isThinking = false
            if ((last as any)._startTime && !last.duration) {
              const dur = Math.max(0.1, (Date.now() - (last as any)._startTime) / 1000).toFixed(1)
              last.duration = `${dur}s`
            }
          }
        }
        targetMsg.content += text
      }
      scrollDown()
    },
    onThinking: (thought: string) => {
      targetMsg.isThinking = true
      targetMsg.thinking = (targetMsg.thinking || '') + thought
      const activeThink = getActiveThinkItem()
      activeThink.content = (activeThink.content || '') + thought
      activeThink.isThinking = true
      scrollDown()
    },
    onToolStart: (data: any) => {
      if (targetMsg.timelineItems) {
        const last = targetMsg.timelineItems[targetMsg.timelineItems.length - 1]
        if (last && last.type === 'think') {
          last.isThinking = false
          if ((last as any)._startTime && !last.duration) {
            const dur = Math.max(0.1, (Date.now() - (last as any)._startTime) / 1000).toFixed(1)
            last.duration = `${dur}s`
          }
        }
      }
      if (!targetMsg.agentTools) targetMsg.agentTools = []
      targetMsg.agentTools.push({
        name: data.name || 'retrieve_kb',
        status: 'running',
        arguments: data.arguments || {},
        gap_type: data.gap_type,
        recovery_strategy: data.recovery_strategy,
      })

      const toolLabel = data.name === 'retrieve_kb' ? '知识库检索' : (data.name === 'web_search' ? '外部网页检索' : data.name)
      const desc = data.arguments?.query ? String(data.arguments.query) : data.name
      if (!targetMsg.timelineItems) targetMsg.timelineItems = []
      targetMsg.timelineItems.push({
        type: 'tool_call',
        tool: data.name || 'retrieve_kb',
        label: toolLabel,
        description: desc,
        in: data.arguments || {},
        status: 'running',
        source: data.source,
        gap_type: data.gap_type,
        recovery_strategy: data.recovery_strategy,
      })
      scrollDown()
    },
    onToolEnd: (data: any) => {
      if (!targetMsg.agentTools) targetMsg.agentTools = []
      let runningIdx = -1
      for (let i = targetMsg.agentTools.length - 1; i >= 0; i--) {
        const item = targetMsg.agentTools[i]
        if (item.name === data.name && item.status === 'running') {
          runningIdx = i
          break
        }
      }
      const record: AgentToolCall = {
        name: data.name || 'retrieve_kb',
        ok: data.ok,
        elapsed_ms: data.elapsed_ms,
        summary: data.summary,
        error: data.error,
        fallback: data.fallback,
        arguments: data.arguments || {},
        gap_type: data.gap_type,
        recovery_strategy: data.recovery_strategy,
        status: data.ok === false ? 'error' : (data.gap_type ? 'recovery' : 'success'),
      }
      if (runningIdx >= 0) {
        targetMsg.agentTools[runningIdx] = record
      } else {
        targetMsg.agentTools.push(record)
      }

      if (targetMsg.timelineItems) {
        let toolTimelineIdx = -1
        for (let i = targetMsg.timelineItems.length - 1; i >= 0; i--) {
          const it = targetMsg.timelineItems[i]
          if (it.type === 'tool_call' && it.tool === data.name && it.status === 'running') {
            toolTimelineIdx = i
            break
          }
        }
        const outData = data.summary ? { summary: data.summary, ok: data.ok } : (data.error ? { error: data.error } : data.data)
        const toolItem: AgentTimelineItem = {
          type: 'tool_call',
          tool: data.name || 'retrieve_kb',
          label: data.name === 'retrieve_kb' ? '知识库检索' : data.name,
          description: data.arguments?.query || data.name,
          in: data.arguments || {},
          out: outData,
          status: data.ok === false ? 'failed' : 'completed',
          elapsed_ms: data.elapsed_ms,
          exitCode: data.ok === false ? 1 : 0,
          source: data.source,
          gap_type: data.gap_type,
          recovery_strategy: data.recovery_strategy,
          error: data.error,
        }
        if (toolTimelineIdx >= 0) {
          targetMsg.timelineItems[toolTimelineIdx] = toolItem
        } else {
          targetMsg.timelineItems.push(toolItem)
        }
      }

      scrollDown()
    },
    onFinalAnswer: (answer: string) => {
      targetMsg.status = undefined
      let cleanAnswer = answer || ''
      if (cleanAnswer.includes('<think>')) {
        const parts = cleanAnswer.split('</think>')
        cleanAnswer = parts.length > 1 ? parts.slice(1).join('</think>').trim() : parts[0].split('<think>')[0].trim()
      }
      targetMsg.content = cleanAnswer
      targetMsg.loading = false
      targetMsg.isThinking = false
      if (targetMsg.timelineItems) {
        const last = targetMsg.timelineItems[targetMsg.timelineItems.length - 1]
        if (last && last.type === 'think') {
          last.isThinking = false
          if ((last as any)._startTime && !last.duration) {
            const dur = Math.max(0.1, (Date.now() - (last as any)._startTime) / 1000).toFixed(1)
            last.duration = `${dur}s`
          }
        }
      }
      scrollDown()
    },
    onSources: (sources: any[]) => {
      currentSources.value = sources
      targetMsg.sources = sources
    },
    onTrace: (traceId: string) => {
      targetMsg.trace_id = traceId
    },
    onPipeline: (pipelineData: any) => {
      if (!targetMsg.pipelineSteps) targetMsg.pipelineSteps = []
      targetMsg.pipelineSteps.push(pipelineData)
      if (pipelineData.evidence) {
        targetMsg.evidencePack = pipelineData.evidence
      }
      const agentInfo = pipelineData.agent
      if (agentInfo) {
        const toolsList: AgentToolCall[] = []
        const steps = agentInfo.agent_steps || []
        const tools = agentInfo.tools || []

        tools.forEach((t: any, idx: number) => {
          const stepMatch = steps[idx]
          toolsList.push({
            name: t.name || 'retrieve_kb',
            ok: t.ok,
            elapsed_ms: t.elapsed_ms,
            summary: t.summary,
            error: t.error,
            fallback: t.fallback,
            arguments: stepMatch?.decision?.arguments || {},
            gap_type: stepMatch?.decision?.gap_type || t.gap_type,
            recovery_strategy: stepMatch?.decision?.recovery_strategy || t.recovery_strategy,
            status: t.ok === false ? 'error' : (stepMatch?.decision?.gap_type ? 'recovery' : 'success')
          })
        })

        if (toolsList.length > 0) {
          targetMsg.agentTools = toolsList
        }
      }
    },
    onNotice: (notice: string) => {
      showGpuNotice(notice)
      if (!targetMsg.timelineItems) targetMsg.timelineItems = []
      targetMsg.timelineItems.push({
        type: 'notice',
        content: notice,
        level: 'warning',
      })
      scrollDown()
    },
    onClarify: (data: ClarifyResult) => {
      applyClarification(targetMsg, data)
    },
    onDone: () => {
      targetMsg.status = undefined
      targetMsg.loading = false
      targetMsg.isThinking = false
      if (targetMsg.content.includes('<think>')) {
        const parts = targetMsg.content.split('</think>')
        targetMsg.content = parts.length > 1 ? parts.slice(1).join('</think>').trim() : parts[0].split('<think>')[0].trim()
      }
      if (targetMsg.timelineItems) {
        targetMsg.timelineItems.forEach(item => {
          if (item.type === 'think') item.isThinking = false
          if (item.type === 'tool_call' && item.status === 'running') item.status = 'completed'
        })
      }
      if (targetMsg.thinking && !targetMsg.thinkingDuration && thinkStartTime) {
        const durSec = ((Date.now() - thinkStartTime) / 1000).toFixed(1)
        targetMsg.thinkingDuration = `${durSec}s`
      }
      loading.value = false
      abortController.value = null
      pinnedChunks.value = []
      excludedChunks.value = []
      persist()
      scrollDown()
    },
    onError: () => { throw new Error('stream failed') },
  }
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

    // 1. 提问前预检：线性模式时走 /query/clarify；Agent 模式时由 stream 的 clarify 事件出卡
    if (workMode.value === 'linear') {
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
    }

    // 2. 无歧义或预检跳过，执行常规流式检索问答
    const history = chatHistory.value.slice(0, -1)
    let streamOk = false
    try {
      const llmModel = currentModel.value || undefined
      await queryKnowledgeStream(
        text,
        history,
        createStreamHandler(lastAiMsg()),
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
        workMode.value,
      )
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
          workMode.value,
        )
        const msg = lastAiMsg()
        msg.content = result.answer
        msg.loading = false
        currentSources.value = result.source_documents
        msg.sources = result.source_documents
        applyClarification(msg, result.clarification)
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

  if (option.id === 'other' && !option.filter?.entity_name) {
    aiMsg.status = '无法匹配自定义输入，请选择卡片上的选项。'
    return
  }

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
        createStreamHandler(aiMsg),
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
        workMode.value,
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
          workMode.value,
        )
        aiMsg.content = result.answer
        aiMsg.loading = false
        currentSources.value = result.source_documents
        aiMsg.sources = result.source_documents
        applyClarification(aiMsg, result.clarification)
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

  // 3. 清空页面消息并彻底清空持久化存储（服务端/localStorage/IndexedDB）
  messages.value = []
  await clearChatState()

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
    <div class="chat-main">
      <header class="header">
        <div class="header-left">
          <h1 class="title">RAG 知识库</h1>
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
            :is-thinking="msg.isThinking"
            :thinking-duration="msg.thinkingDuration"
            :agent-tools="msg.agentTools"
            :timeline-items="msg.timelineItems"
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
.chat-layout { display: flex; height: 100%; background: #fff; }
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
.header-left { display: flex; align-items: center; gap: 10px; }
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
</style>
