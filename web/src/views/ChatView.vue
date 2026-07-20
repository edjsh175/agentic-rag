<script setup lang="ts">
import { ref, onMounted, nextTick, computed } from 'vue'
import type { Message, SourceDoc, Stats } from '../types'
import { queryKnowledgeStream, queryKnowledge, queryImageStream, getStats, triggerScan, uploadDocument, getModels, getKnowledgeBases, getAgents, DOCUMENT_PROFILE_OPTIONS } from '../api'
import type { DocumentProfile } from '../api'
import type { ModelsResponse, AgentInfo } from '../api'
import { saveChatState, loadChatState, clearChatState } from '../utils/storage'
import ChatMessage from '../components/ChatMessage.vue'
import ChatInput from '../components/ChatInput.vue'
import SourcePanel from '../components/SourcePanel.vue'

const messages = ref<Message[]>([])
const currentSources = ref<SourceDoc[]>([])
const loading = ref(false)
const stats = ref<Stats | null>(null)
const showSources = ref(false)
const sourcePanel = ref<InstanceType<typeof SourcePanel> | null>(null)
const msgContainer = ref<HTMLElement | null>()
const initialized = ref(false)
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

const llmModels = computed(() => availableModels.value.filter(m => modelType(m.name, m.type) === 'llm').map(m => m.name))
const visionModels = computed(() => availableModels.value.filter(m => modelType(m.name, m.type) === 'vision').map(m => m.name))
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

function selectKb(name: string) {
  currentKb.value = name
  localStorage.setItem('rag-kb-name', name)
}

function onLayoutClick(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (showUploadPicker.value && !target.closest('.upload-wrap')) {
    showUploadPicker.value = false
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

const chatHistory = computed(() => {
  const msgs = messages.value
  const list = msgs[msgs.length - 1]?.loading ? msgs.slice(0, -1) : msgs
  return list.slice(-60).map((m) => ({
    role: m.role,
    content: m.content,
    ...(m.role === 'assistant' && m.sources?.length ? {
      sources: m.sources.slice(0, 4).map((s) => ({
        file_name: s.metadata?.file_name || s.metadata?.source || undefined,
        source: s.metadata?.source || undefined,
        section_title: s.metadata?.section_title || undefined,
        page_label: s.metadata?.page_label || undefined,
        chunk_id: s.metadata?.chunk_id || undefined,
        preview: s.content?.slice(0, 200) || undefined,
      }))
    } : {}),
  }))
})
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
    if (!currentModel.value) currentModel.value = modelsResp.current.llm
    if (!visionModel.value) visionModel.value = modelsResp.current.vision
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
    const history = chatHistory.value.slice(0, -1)
    let streamOk = false
    try {
      abortController.value = new AbortController()
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
        onDone: () => {
          streamOk = true
          abortController.value = null
          lastAiMsg().status = undefined
          lastAiMsg().loading = false
          loading.value = false
          persist()
          scrollDown()
        },
        onError: () => { throw new Error('stream failed') },
      }, llmModel, currentKb.value, thinkingEnabled.value || undefined, webSearchEnabled.value || undefined, abortController.value?.signal, activeAgent.value?.system_prompt)
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
        )
        const msg = lastAiMsg()
        msg.content = result.answer
        msg.loading = false
        currentSources.value = result.source_documents
        msg.sources = result.source_documents
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
            <span class="model-pill">
              问答
              <select v-if="llmModels.length > 0" class="model-select" v-model="currentModel" @change="selectModel(currentModel)">
                <option v-for="m in llmModels" :key="m" :value="m">{{ m.replace(':latest', '') }}</option>
              </select>
              <span v-else class="model-tag">{{ currentModel.replace(':latest','') || '…' }}</span>
            </span>
            <span class="model-pill">
              视觉
              <select v-if="visionModels.length > 0" class="model-select" v-model="visionModel" @change="selectVision(visionModel)">
                <option v-for="m in visionModels" :key="m" :value="m">{{ m.replace(':latest', '') }}</option>
              </select>
              <span v-else class="model-tag">{{ visionModel.replace(':latest','') || '…' }}</span>
            </span>
            <span class="model-pill" title="嵌入模型需通过配置文件修改">嵌入 {{ embeddingModel.replace(':latest','') || '…' }}</span>
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
              联网搜索
            </button>
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
            @citation-click="handleCitationClick(msg, $event)"
          />
        </div>
      </div>

      <ChatInput :disabled="loading" @send="handleSend" @stop="handleStop" />
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
        <SourcePanel ref="sourcePanel" :sources="currentSources" />
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
</style>
