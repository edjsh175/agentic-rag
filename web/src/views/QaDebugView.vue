<script setup lang="ts">
defineOptions({ name: 'QaDebugView' })
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { deleteQaTrace, getQaTrace, listQaTraces, queryAdminDebugStream, updateQaTraceFeedback, queryClarify, getModels, getKnowledgeBases, getAgents } from '../api'
import type { DebugStreamOptions } from '../api'
import type { EvidenceChain, QaTraceDetail, QaTraceSummary, ClarificationOption, ClarifyResult } from '../types'

const question = ref('')
const loading = ref(false)
const debugClarification = ref<ClarifyResult | null>(null)
const clarifiedQuestion = ref('')
const selectedClarificationOptionId = ref<string | undefined>(undefined)

// 丰富调试参数选择
const availableModels = ref<{ name: string; type?: string }[]>([])
const kbList = ref<string[]>([])
const agents = ref<any[]>([])

const debugLlmModel = ref('')
const debugKbName = ref('')
const debugDocCategory = ref('')
const debugEntityName = ref('')
const debugThinking = ref<boolean | undefined>(undefined)
const debugWebSearch = ref<boolean | undefined>(undefined)
const debugAllowGeneralKnowledge = ref<boolean | undefined>(undefined)
const debugAgentPrompt = ref('')
const showDebugConfigPanel = ref(false)

const listLoading = ref(false)
const error = ref('')
const liveStatus = ref('')
const filterQ = ref('')
const errorsOnly = ref(false)
const items = ref<QaTraceSummary[]>([])
const total = ref(0)
const selectedId = ref('')
const detail = ref<QaTraceDetail | null>(null)
const liveAnswer = ref('')
const activeTab = ref<'timeline' | 'plan' | 'retrieval' | 'evidence' | 'answer'>('timeline')
let abortCtrl: AbortController | null = null

const mdRenderer = new marked.Renderer()
mdRenderer.image = ({ href, title, text }) => {
  return `<img src="${href}" alt="${text || ''}" referrerpolicy="no-referrer"${title ? ` title="${title}"` : ''} />`
}

function renderMd(text?: string): string {
  if (!text) return ''
  const raw = marked.parse(text, { async: false, renderer: mdRenderer }) as string
  return DOMPurify.sanitize(raw)
}

function formatTextOrJson(val: any): string {
  if (!val) return ''
  if (typeof val === 'string') return val
  return '```json\n' + JSON.stringify(val, null, 2) + '\n```'
}

const emptyEvidence: EvidenceChain = {
  cited: [],
  retrieved_uncited: [],
  gaps: [],
  conflicts: [],
}

function emptyDetail(q: string): QaTraceDetail {
  return {
    meta: {
      trace_id: '(运行中)',
      path: 'qa-debug',
      elapsed_ms: 0,
      error: null,
    },
    request: { question: q },
    runtime: {},
    stages: {},
    plan: {},
    retrieval: { candidates: [], candidate_count: 0 },
    evidence: { ...emptyEvidence },
    answer: { text: '', source_documents: [] },
  }
}

const evidence = computed<EvidenceChain>(() => {
  const chain = detail.value?.evidence
  if (!chain) return emptyEvidence
  return {
    cited: chain.cited || [],
    retrieved_uncited: chain.retrieved_uncited || [],
    gaps: chain.gaps || [],
    conflicts: chain.conflicts || [],
  }
})

const stages = computed(() => Object.entries(detail.value?.stages || {}))

const maxStageMs = computed(() => {
  const list = stages.value
  if (!list.length) return 1
  return Math.max(...list.map(([_, ms]) => Number(ms) || 0), 1)
})

const planQueries = computed(() => {
  const queries = (detail.value?.plan as any)?.queries
  return Array.isArray(queries) ? queries : []
})
const candidates = computed(() => detail.value?.retrieval?.candidates || [])

const runtimeTags = computed(() => {
  const rt = detail.value?.runtime || {}
  return [
    rt.retrieval_method ? `检索模式: ${rt.retrieval_method}` : '',
    rt.reranker_enabled != null ? `重排: ${rt.reranker_enabled ? '开启' : '关闭'}` : '',
    rt.graph_retrieval_enabled != null ? `图谱检索: ${rt.graph_retrieval_enabled ? '开启' : '关闭'}` : '',
    rt.query_rewrite_enabled != null ? `查询改写: ${rt.query_rewrite_enabled ? '开启' : '关闭'}` : '',
  ].filter(Boolean)
})

async function refreshList(selectId?: string) {
  listLoading.value = true
  try {
    const res = await listQaTraces({
      limit: 80,
      q: filterQ.value.trim() || undefined,
      errors_only: errorsOnly.value || undefined,
    })
    items.value = res.items || []
    total.value = res.total || 0
    const target = selectId || selectedId.value
    if (target && target !== '(运行中)' && items.value.some((i) => i.trace_id === target)) {
      await openTrace(target)
    } else if (!selectedId.value && items.value[0]) {
      await openTrace(items.value[0].trace_id)
    }
  } catch (e: any) {
    error.value = e.message || '加载历史记录失败'
  } finally {
    listLoading.value = false
  }
}

async function openTrace(traceId: string) {
  if (loading.value) return
  selectedId.value = traceId
  error.value = ''
  liveStatus.value = ''
  liveAnswer.value = ''
  try {
    detail.value = await getQaTrace(traceId)
  } catch (e: any) {
    error.value = e.message || '加载详情失败'
    detail.value = null
  }
}

const STAGE_LABELS: Record<string, string> = {
  start: '任务启动',
  queries: '查询改写产出',
  plan: '检索计划生成',
  graph_rewrite: '图谱改写完成',
  retrieve: '检索候选获取',
  done: '流程执行完成',
}

function applyPipeline(data: any) {
  if (!detail.value) return
  const stage = data?.stage
  if (stage && STAGE_LABELS[stage]) {
    liveStatus.value = STAGE_LABELS[stage]
  }
  if (data.runtime) detail.value.runtime = data.runtime
  if (data.request) detail.value.request = { ...detail.value.request, ...data.request }
  if (data.stages) detail.value.stages = { ...detail.value.stages, ...data.stages }
  if (data.plan) {
    detail.value.plan = {
      ...(detail.value.plan || {}),
      ...data.plan,
    }
    if (stage === 'queries' || stage === 'plan' || stage === 'graph_rewrite') {
      activeTab.value = 'plan'
    }
  }
  if (data.retrieval) {
    detail.value.retrieval = data.retrieval
    activeTab.value = 'retrieval'
  }
  if (data.evidence) {
    detail.value.evidence = data.evidence
    activeTab.value = 'evidence'
  }
  if (typeof data.answer === 'string') {
    detail.value.answer = {
      text: data.answer,
      source_documents: data.source_documents || detail.value.answer?.source_documents || [],
    }
    liveAnswer.value = data.answer
    if (stage === 'done') activeTab.value = 'answer'
  }
  if (stage === 'start') activeTab.value = 'timeline'
}

function loadTraceParamsToForm(traceDetail: QaTraceDetail | null) {
  if (!traceDetail) return
  const req = traceDetail.request || {}
  question.value = String(req.question || '')
  debugLlmModel.value = req.llm_model ? String(req.llm_model) : ''
  debugKbName.value = req.kb_name ? String(req.kb_name) : ''
  debugDocCategory.value = req.doc_category ? String(req.doc_category) : ''
  debugEntityName.value = req.entity_name ? String(req.entity_name) : ''
  debugThinking.value = req.thinking != null ? Boolean(req.thinking) : undefined
  debugWebSearch.value = req.web_search != null ? Boolean(req.web_search) : undefined
  debugAllowGeneralKnowledge.value = req.allow_general_knowledge != null ? Boolean(req.allow_general_knowledge) : undefined
  debugAgentPrompt.value = req.agent_prompt ? String(req.agent_prompt) : ''
  showDebugConfigPanel.value = true
}

function resetDebugParams() {
  debugLlmModel.value = ''
  debugKbName.value = ''
  debugDocCategory.value = ''
  debugEntityName.value = ''
  debugThinking.value = undefined
  debugWebSearch.value = undefined
  debugAllowGeneralKnowledge.value = undefined
  debugAgentPrompt.value = ''
}

async function runActualDebugStream(
  qText: string,
  debugOpts?: DebugStreamOptions,
) {
  loading.value = true
  error.value = ''
  liveStatus.value = '发起调试中...'
  liveAnswer.value = ''
  selectedId.value = '(运行中)'
  detail.value = emptyDetail(qText)
  activeTab.value = 'timeline'

  let finishedTraceId = ''
  try {
    await queryAdminDebugStream(
      qText,
      {
        onStatus: (status) => {
          liveStatus.value = status
        },
        onPipeline: (data) => {
          applyPipeline(data)
        },
        onToken: (token) => {
          liveAnswer.value += token
          if (detail.value) {
            detail.value.answer = {
              ...(detail.value.answer || {}),
              text: liveAnswer.value,
            }
          }
          if (activeTab.value !== 'answer') activeTab.value = 'answer'
        },
        onFinalAnswer: (answer) => {
          liveAnswer.value = answer
          if (detail.value) {
            detail.value.answer = {
              ...(detail.value.answer || {}),
              text: answer,
            }
          }
        },
        onSources: (sources) => {
          if (detail.value) {
            detail.value.answer = {
              ...(detail.value.answer || { text: liveAnswer.value }),
              source_documents: sources || [],
            }
          }
        },
        onTrace: (traceId) => {
          finishedTraceId = String(traceId || '')
          selectedId.value = finishedTraceId
          if (detail.value) detail.value.meta.trace_id = finishedTraceId
        },
        onDone: () => {
          liveStatus.value = '执行完成'
        },
      },
      abortCtrl?.signal,
      debugOpts,
    )
    if (finishedTraceId) {
      await refreshList(finishedTraceId)
    } else {
      await refreshList()
    }
  } catch (e: any) {
    if (e?.name === 'AbortError') {
      liveStatus.value = '已终止调试'
    } else {
      error.value = e.message || '调试请求异常'
      liveStatus.value = ''
    }
  } finally {
    loading.value = false
    abortCtrl = null
  }
}

async function runDebug() {
  if (!question.value.trim()) return

  debugClarification.value = null
  clarifiedQuestion.value = ''
  selectedClarificationOptionId.value = undefined

  abortCtrl?.abort()
  abortCtrl = new AbortController()
  loading.value = true
  error.value = ''
  liveStatus.value = '歧义预检中...'
  liveAnswer.value = ''
  selectedId.value = '(运行中)'
  detail.value = emptyDetail(question.value.trim())
  activeTab.value = 'timeline'

  const currentOpts: DebugStreamOptions = {
    kbName: debugKbName.value || undefined,
    docCategory: debugDocCategory.value || undefined,
    entityName: debugEntityName.value || undefined,
    llmModel: debugLlmModel.value || undefined,
    thinking: debugThinking.value,
    webSearch: debugWebSearch.value,
    allowGeneralKnowledge: debugAllowGeneralKnowledge.value,
    agentPrompt: debugAgentPrompt.value || undefined,
  }

  try {
    const clarifyRes = await queryClarify(
      question.value.trim(),
      currentOpts.kbName,
      currentOpts.docCategory,
      abortCtrl.signal,
    )
    if (clarifyRes && clarifyRes.needs_clarification && clarifyRes.options.length >= 2) {
      debugClarification.value = clarifyRes
      clarifiedQuestion.value = question.value.trim()
      loading.value = false
      liveStatus.value = '检测到潜在歧义，请选择确认...'
      return
    }
  } catch (err: any) {
    if (err?.name === 'AbortError') {
      liveStatus.value = '已终止调试'
      loading.value = false
      return
    }
    // 预检服务异常时，优雅降级直接进入调试
  }

  await runActualDebugStream(question.value.trim(), currentOpts)
}

async function handleSelectClarificationOption(option: ClarificationOption) {
  if (!debugClarification.value || loading.value) return

  selectedClarificationOptionId.value = option.id
  loading.value = true
  liveStatus.value = `已选择「${option.label}」，正在检索回答...`

  abortCtrl?.abort()
  abortCtrl = new AbortController()

  const originalQ = clarifiedQuestion.value
  const opts: DebugStreamOptions = {
    kbName: debugKbName.value || undefined,
    docCategory: option.filter.doc_category || debugDocCategory.value || undefined,
    entityName: option.filter.entity_name || debugEntityName.value || undefined,
    llmModel: debugLlmModel.value || undefined,
    thinking: debugThinking.value,
    webSearch: debugWebSearch.value,
    allowGeneralKnowledge: debugAllowGeneralKnowledge.value,
    agentPrompt: debugAgentPrompt.value || undefined,
    clarificationQuestion: debugClarification.value.ask_question,
    clarificationSelected: option.label,
  }

  await runActualDebugStream(originalQ, opts)
}

function stopDebug() {
  abortCtrl?.abort()
  debugClarification.value = null
}


function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.isComposing) {
    if (e.ctrlKey) {
      e.preventDefault()
      const target = e.target as HTMLTextAreaElement
      const start = target.selectionStart
      const end = target.selectionEnd
      question.value = question.value.substring(0, start) + '\n' + question.value.substring(end)
      nextTick(() => {
        target.selectionStart = target.selectionEnd = start + 1
      })
    } else if (!e.shiftKey) {
      e.preventDefault()
      if (!loading.value && question.value.trim()) {
        runDebug()
      }
    }
  }
}

const currentFeedback = computed(() => {
  return (
    detail.value?.feedback ||
    (items.value.find((i) => i.trace_id === selectedId.value)?.feedback as string | undefined) ||
    null
  )
})

async function handleTraceFeedback(fb: 'useful' | 'unuseful') {
  if (!selectedId.value || selectedId.value === '(运行中)') return
  const nextFb = currentFeedback.value === fb ? null : fb
  if (detail.value) detail.value.feedback = nextFb
  const item = items.value.find((i) => i.trace_id === selectedId.value)
  if (item) item.feedback = nextFb
  try {
    await updateQaTraceFeedback(selectedId.value, nextFb)
  } catch (e: any) {
    error.value = e.message || '更新反馈失败'
  }
}

async function removeSelected() {
  if (!selectedId.value || selectedId.value === '(运行中)') return
  if (!confirm(`确认删除追踪记录 ${selectedId.value}？`)) return
  try {
    await deleteQaTrace(selectedId.value)
    selectedId.value = ''
    detail.value = null
    await refreshList()
  } catch (e: any) {
    error.value = e.message || '删除失败'
  }
}

function fmtTime(iso?: string) {
  if (!iso) return '-'
  return iso.replace('T', ' ').slice(0, 19)
}

function formatDuration(val: number | string | null | undefined): string {
  if (val == null || val === '') return '-'
  const num = Number(val)
  if (isNaN(num) || num < 0) return '-'
  const ms = Math.round(num)
  if (ms === 0) return '0毫秒'

  const minutes = Math.floor(ms / 60000)
  const seconds = Math.floor((ms % 60000) / 1000)
  const remMs = ms % 1000

  const parts: string[] = []
  if (minutes > 0) parts.push(`${minutes}分`)
  if (seconds > 0) parts.push(`${seconds}秒`)
  if (remMs > 0) parts.push(`${remMs}毫秒`)

  return parts.length > 0 ? parts.join('') : '0毫秒'
}

function getItemSnippet(item: any): string {
  if (!item) return ''
  if (typeof item === 'string') return item
  return item.snippet || item.content || ''
}

function getItemTitle(item: any): string {
  if (!item || typeof item === 'string') return ''
  return item.document || item.source || item.chunk_id || ''
}

onMounted(async () => {
  refreshList()
  try {
    const modelsResp = await getModels()
    availableModels.value = modelsResp.models || []
    const kbs = await getKnowledgeBases()
    kbList.value = kbs.bases || []
    const ags = await getAgents()
    agents.value = ags.agents || []
  } catch {
    /* ignore */
  }
})

onUnmounted(() => {
  abortCtrl?.abort()
})
</script>

<template>
  <section class="qa-debug">
    <header class="page-head">
      <div class="head-title">
        <h1>问答证据调试</h1>
        <span class="head-badge">诊断中心</span>
      </div>
      <p class="sub">流式诊断检索计划、改写策略、检索候选片段与证据链一致性分析</p>
    </header>

    <div class="layout">
      <aside class="history">
        <div class="history-tools">
          <div class="search-box">
            <input v-model="filterQ" placeholder="搜索问题关键词 / 追踪 ID" @keyup.enter="refreshList()" />
          </div>
          <div class="filter-row">
            <label class="check">
              <input v-model="errorsOnly" type="checkbox" @change="refreshList()" />
              仅显示异常
            </label>
            <button type="button" class="btn-sm ghost" :disabled="listLoading" @click="refreshList()">
              {{ listLoading ? '加载中...' : '刷新列表' }}
            </button>
          </div>
        </div>

        <div class="meta-bar">
          <span>共 <strong>{{ total }}</strong> 条记录</span>
        </div>

        <ul class="trace-list">
          <li
            v-for="item in items"
            :key="item.trace_id"
            :class="{ active: item.trace_id === selectedId, errored: !!item.error }"
            @click="openTrace(item.trace_id)"
          >
            <div class="item-header">
              <span class="status-badge" :class="item.error ? 'badge-error' : 'badge-success'">
                {{ item.error ? '异常' : '正常' }}
              </span>
              <span v-if="item.feedback === 'unuseful'" class="feedback-badge badge-unuseful">无用</span>
              <span v-else-if="item.feedback === 'useful'" class="feedback-badge badge-useful">有用</span>
              <span class="time">{{ fmtTime(item.created_at) }}</span>
              <span class="ms-tag" :title="item.elapsed_ms != null ? `${item.elapsed_ms} ms` : ''">
                {{ formatDuration(item.elapsed_ms) }}
              </span>
            </div>

            <div class="q-text" :title="item.question">
              {{ item.question || '(未记录问题)' }}
            </div>

            <div class="item-footer">
              <span class="path-tag">{{ item.path || 'qa-debug' }}</span>
              <div class="counts">
                <span class="count-badge count-cand">候选 {{ item.candidate_count ?? 0 }}</span>
                <span class="count-badge count-cite">引用 {{ item.cited_count ?? 0 }}</span>
              </div>
            </div>
          </li>

          <li v-if="!items.length && !listLoading" class="empty-state">
            暂无历史追踪记录
          </li>
        </ul>
      </aside>

      <main class="main">
        <form class="ask-card" @submit.prevent="runDebug">
          <div class="ask-header">
            <label class="ask-label">发起问答调试请求</label>
            <div class="header-right-tools">
              <button
                type="button"
                class="btn-toggle-params"
                :class="{ active: showDebugConfigPanel }"
                @click="showDebugConfigPanel = !showDebugConfigPanel"
              >
                调试参数设置
              </button>
              <span v-if="liveStatus" class="live-status-pill">
                <span class="dot"></span>
                {{ liveStatus }}
              </span>
            </div>
          </div>
          <textarea
            v-model="question"
            placeholder="请输入需要复现与诊断的问答测试文本..."
            rows="3"
            @keydown="onKeydown"
          />

          <!-- 调试高级参数配置面板 -->
          <div v-if="showDebugConfigPanel" class="debug-params-panel">
            <div class="params-panel-header">
              <span>自定义当次调试请求参数</span>
              <button type="button" class="btn-text-sm" @click="resetDebugParams">重置参数</button>
            </div>
            <div class="params-grid">
              <div class="param-item">
                <label>问答模型</label>
                <select v-model="debugLlmModel" class="param-input">
                  <option value="">(系统默认模型)</option>
                  <option v-for="m in availableModels" :key="m.name" :value="m.name">{{ m.name }}</option>
                </select>
              </div>
              <div class="param-item">
                <label>知识库范围</label>
                <select v-model="debugKbName" class="param-input">
                  <option value="">(全部知识库)</option>
                  <option v-for="kb in kbList" :key="kb" :value="kb">{{ kb }}</option>
                </select>
              </div>
              <div class="param-item">
                <label>分类领域 (doc_category)</label>
                <input v-model="debugDocCategory" placeholder="如: 技术文档 / 论坛" class="param-input" />
              </div>
              <div class="param-item">
                <label>产品/实体锚定 (entity_name)</label>
                <input v-model="debugEntityName" placeholder="如: StampServer / UE" class="param-input" />
              </div>
              <div class="param-item param-checks">
                <label class="check-inline">
                  <input type="checkbox" v-model="debugThinking" :indeterminate="debugThinking === undefined" />
                  深度思考
                </label>
                <label class="check-inline">
                  <input type="checkbox" v-model="debugWebSearch" :indeterminate="debugWebSearch === undefined" />
                  联网搜索
                </label>
                <label class="check-inline">
                  <input type="checkbox" v-model="debugAllowGeneralKnowledge" :indeterminate="debugAllowGeneralKnowledge === undefined" />
                  通用知识兜底
                </label>
              </div>
            </div>
            <div class="param-item full-width">
              <label>Agent 系统提示词重载 (agent_prompt)</label>
              <textarea v-model="debugAgentPrompt" placeholder="可选：覆盖 Agent 角色设定..." rows="2" class="param-textarea"></textarea>
            </div>
          </div>

          <div class="ask-actions">
            <button type="submit" class="btn-primary" :disabled="loading || !question.trim()">
              {{ loading ? '调试执行中...' : '发起调试' }}
            </button>
            <button v-if="loading" type="button" class="btn-warning ghost" @click="stopDebug">终止</button>
            <button type="button" class="btn-danger ghost" :disabled="!selectedId || selectedId === '(运行中)'" @click="removeSelected">
              删除当前记录
            </button>
          </div>
        </form>

        <!-- 歧义确认卡片 (只在发起新调试触发歧义时显示) -->
        <div v-if="debugClarification" class="clarification-card">
          <div class="clarification-header">
            <svg class="clarification-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"/>
              <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
              <line x1="12" y1="17" x2="12.01" y2="17"/>
            </svg>
            <span class="clarification-title">调试歧义确认</span>
            <span v-if="debugClarification.trigger" class="clarification-trigger-badge">
              包含「{{ debugClarification.trigger }}」
            </span>
          </div>
          <div class="clarification-question">
            {{ debugClarification.ask_question }}
          </div>
          <div class="clarification-options">
            <button
              v-for="opt in debugClarification.options"
              :key="opt.id"
              type="button"
              class="clarification-option-btn"
              :class="{
                'is-selected': selectedClarificationOptionId === opt.id,
                'is-disabled': selectedClarificationOptionId && selectedClarificationOptionId !== opt.id
              }"
              :disabled="!!selectedClarificationOptionId"
              @click="handleSelectClarificationOption(opt)"
            >
              <span class="option-badge">{{ opt.id.toUpperCase() }}</span>
              <span class="option-label">{{ opt.label }}</span>
              <svg v-if="selectedClarificationOptionId === opt.id" class="check-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
            </button>
          </div>
        </div>

        <div v-if="error" class="error-banner">
          <span class="banner-title">系统提示：</span>
          <span>{{ error }}</span>
        </div>

        <template v-if="detail">
          <div class="summary-card">
            <!-- 调试问题与历史反问选择展示 -->
            <div class="trace-question-panel">
              <div class="trace-q-row">
                <div class="trace-q-main">
                  <span class="trace-q-lbl">调试问题：</span>
                  <span class="trace-q-val">{{ detail.request?.question || '-' }}</span>
                </div>
                <button
                  type="button"
                  class="btn-rerun-params"
                  title="载入该 Trace 记录的全部参数到上方调试面板"
                  @click="loadTraceParamsToForm(detail)"
                >
                  载入参数并重新调试
                </button>
              </div>
              <div v-if="detail.request?.clarification_question" class="trace-clarify-row">
                <span class="trace-c-lbl">歧义反问：</span>
                <span class="trace-c-q">{{ detail.request.clarification_question }}</span>
                <span class="trace-c-arrow">→</span>
                <span class="trace-c-selected-badge">用户选择：{{ detail.request.clarification_selected || '未记录选择' }}</span>
              </div>
            </div>

            <!-- 全量 Request 请求参数可视化列表 -->
            <div class="request-params-box">
              <div class="box-title">请求全量参数 (Request Parameters)</div>
              <div class="params-tag-grid">
                <div class="p-tag"><span class="p-key">模型:</span> <span class="p-val">{{ detail.request?.llm_model || '全局默认' }}</span></div>
                <div class="p-tag"><span class="p-key">知识库:</span> <span class="p-val">{{ detail.request?.kb_name || '全部' }}</span></div>
                <div class="p-tag"><span class="p-key">分类:</span> <span class="p-val">{{ detail.request?.doc_category || '无' }}</span></div>
                <div class="p-tag"><span class="p-key">实体锚定:</span> <span class="p-val">{{ detail.request?.entity_name || '无' }}</span></div>
                <div class="p-tag"><span class="p-key">深度思考:</span> <span class="p-val">{{ detail.request?.thinking == null ? '未指定' : (detail.request.thinking ? '开启' : '关闭') }}</span></div>
                <div class="p-tag"><span class="p-key">联网搜索:</span> <span class="p-val">{{ detail.request?.web_search == null ? '未指定' : (detail.request.web_search ? '开启' : '关闭') }}</span></div>
                <div class="p-tag"><span class="p-key">通用知识兜底:</span> <span class="p-val">{{ detail.request?.allow_general_knowledge == null ? '未指定' : (detail.request.allow_general_knowledge ? '允许' : '禁用') }}</span></div>
                <div v-if="detail.request?.pinned_chunk_ids?.length" class="p-tag"><span class="p-key">固定 Chunk:</span> <span class="p-val">{{ detail.request.pinned_chunk_ids.length }} 个</span></div>
                <div v-if="detail.request?.excluded_chunk_ids?.length" class="p-tag"><span class="p-key">排除 Chunk:</span> <span class="p-val">{{ detail.request.excluded_chunk_ids.length }} 个</span></div>
              </div>
              <div v-if="detail.request?.agent_prompt" class="p-agent-prompt">
                <span class="p-key">Agent 系统提示词：</span>
                <span class="p-prompt-text">{{ detail.request.agent_prompt }}</span>
              </div>
            </div>

            <div class="summary-grid">
              <div class="summary-item">
                <span class="lbl">追踪 ID</span>
                <span class="val font-mono">{{ detail.meta.trace_id }}</span>
              </div>

              <div class="summary-item">
                <span class="lbl">响应耗时</span>
                <span class="val highlight-val" :title="detail.meta.elapsed_ms != null ? `${detail.meta.elapsed_ms} ms` : ''">
                  {{ formatDuration(detail.meta.elapsed_ms) }}
                  <span v-if="detail.meta.elapsed_ms != null" class="raw-ms">({{ detail.meta.elapsed_ms }} ms)</span>
                </span>
              </div>
              <div class="summary-item">
                <span class="lbl">接口路径</span>
                <span class="val">{{ detail.meta.path || '-' }}</span>
              </div>
              <div class="summary-item">
                <span class="lbl">请求 ID</span>
                <span class="val font-mono">{{ detail.meta.request_id || '-' }}</span>
              </div>
            </div>

            <div v-if="runtimeTags.length" class="runtime-tags">
              <span v-for="tag in runtimeTags" :key="tag" class="tag-pill">{{ tag }}</span>
            </div>

            <div v-if="detail.meta.error" class="error-detail-box">
              <div class="error-title">执行失败详情</div>
              <div class="error-msg">{{ detail.meta.error }}</div>
            </div>
          </div>

          <nav class="tabs-nav">
            <button type="button" :class="{ active: activeTab === 'timeline' }" @click="activeTab = 'timeline'">
              耗时分布
            </button>
            <button type="button" :class="{ active: activeTab === 'plan' }" @click="activeTab = 'plan'">
              检索计划
            </button>
            <button type="button" :class="{ active: activeTab === 'retrieval' }" @click="activeTab = 'retrieval'">
              检索候选 ({{ candidates.length }})
            </button>
            <button type="button" :class="{ active: activeTab === 'evidence' }" @click="activeTab = 'evidence'">
              证据分析
            </button>
            <button type="button" :class="{ active: activeTab === 'answer' }" @click="activeTab = 'answer'">
              回答与来源
            </button>
          </nav>

          <section v-if="activeTab === 'timeline'" class="panel-card">
            <h3 class="section-title">流程各阶段耗时统计</h3>
            <div v-if="stages.length" class="stage-timeline">
              <div v-for="[name, ms] in stages" :key="name" class="stage-row">
                <div class="stage-info">
                  <span class="stage-name">{{ STAGE_LABELS[name] || name }}</span>
                  <span class="stage-code">({{ name }})</span>
                </div>
                <div class="stage-bar-wrapper">
                  <div class="stage-bar" :style="{ width: `${Math.max((Number(ms) / maxStageMs) * 100, 4)}%` }"></div>
                </div>
                <div class="stage-ms" :title="ms != null ? `${ms} ms` : ''">
                  {{ formatDuration(ms) }}
                </div>
              </div>
            </div>
            <div v-else class="empty-hint">
              {{ loading ? '阶段数据生成中...' : '无阶段耗时记录' }}
            </div>
          </section>

          <section v-else-if="activeTab === 'plan'" class="panel-card">
            <h3 class="section-title">改写查询列表 (Queries)</h3>
            <div v-if="planQueries.length" class="query-grid">
              <div v-for="(qq, idx) in planQueries" :key="idx" class="query-card">
                <div class="query-head">
                  <span class="kind-badge">{{ qq.kind || '通用查询' }}</span>
                  <span class="weight-badge">权重: {{ qq.weight ?? 1.0 }}</span>
                </div>
                <div class="query-body markdown-body" v-html="renderMd(qq.text)"></div>
              </div>
            </div>
            <div v-else-if="loading" class="empty-hint">正在生成检索计划...</div>
            <div v-else class="empty-hint">无改写查询项</div>

            <!-- 图谱实体与关系拓扑展卡 -->
            <div v-if="(detail.plan as any)?.linked_entities?.length || (detail.plan as any)?.backbone_relation_summary" class="graph-topo-box margin-top-lg">
              <h3 class="section-title">图谱实体链与关系拓扑 (Graph 1-Hop / Multi-Hop Context)</h3>

              <!-- 关联实体列表 -->
              <div v-if="(detail.plan as any)?.linked_entities?.length" class="topo-entity-list">
                <div class="topo-label">关联实体 (Linked Entities)：</div>
                <div class="entity-badges">
                  <div v-for="(ent, eIdx) in (detail.plan as any).linked_entities" :key="eIdx" class="entity-badge-item">
                    <span class="ent-name">{{ ent.canonical_name }}</span>
                    <span v-if="ent.entity_type" class="ent-type">[{{ ent.entity_type }}]</span>
                    <span v-if="ent.match_method" class="ent-method">({{ ent.match_method }})</span>
                    <span v-if="ent.confidence != null" class="ent-conf">置信度: {{ ent.confidence }}</span>
                  </div>
                </div>
              </div>

              <!-- 一跳/多跳线索网络摘要 (传给改写 LLM) -->
              <div v-if="(detail.plan as any)?.backbone_relation_summary" class="topo-summary-box">
                <div class="topo-label">传给改写 LLM 的拓扑与上下文摘要：</div>
                <pre class="topo-summary-text">{{ (detail.plan as any).backbone_relation_summary }}</pre>
              </div>
            </div>

            <h3 class="section-title margin-top-lg">检索计划配置 JSON</h3>
            <pre class="code-block">{{ JSON.stringify(detail.plan || {}, null, 2) }}</pre>
          </section>

          <section v-else-if="activeTab === 'retrieval'" class="panel-card">
            <div class="panel-header">
              <h3 class="section-title">检索候选切片列表</h3>
              <span class="count-summary">共 {{ detail.retrieval?.candidate_count ?? candidates.length }} 条片段</span>
            </div>

            <div v-if="candidates.length" class="candidate-list">
              <article v-for="(c, idx) in candidates" :key="idx" class="cand-card">
                <div class="cand-header">
                  <span class="rank-badge">#{{ idx + 1 }}</span>

                  <!-- 检索来源标识：图谱独占 / 图文双重 / 文本召回 -->
                  <span
                    v-if="c.retrieval_source === 'graph_only'"
                    class="source-badge source-graph-only"
                    title="仅通过知识图谱关系拓扑一跳/多跳扩展召回"
                  >
                    图谱扩召
                  </span>
                  <span
                    v-else-if="c.retrieval_source === 'hybrid_hit'"
                    class="source-badge source-hybrid"
                    title="图谱关系拓扑与文本向量/BM25双重匹配命中"
                  >
                    图文双重命中
                  </span>
                  <span
                    v-else
                    class="source-badge source-text"
                    title="通过向量/BM25文本混合检索命中"
                  >
                    文本召回
                  </span>

                  <span class="source-tag">{{ c.source || c.chunk_id || '未标识来源' }}</span>
                  <span v-if="c.score != null" class="score-badge">
                    匹配得分：{{ typeof c.score === 'number' ? c.score.toFixed(4) : c.score }}
                  </span>
                </div>
                <div v-if="c.section_title" class="sec-title">
                  所属章节：{{ c.section_title }}
                </div>
                <div class="cand-content markdown-body" v-html="renderMd(String(c.content_preview || c.content || ''))"></div>
              </article>
            </div>
            <div v-else-if="loading" class="empty-hint">检索候选加载中...</div>
            <div v-else class="empty-hint">未获取到检索候选片段</div>
          </section>

          <section v-else-if="activeTab === 'evidence'" class="panel-card">
            <div class="evidence-columns">
              <div class="evidence-col col-cited">
                <div class="col-header header-cited">
                  <h4>已引用证据 ({{ evidence.cited.length }})</h4>
                </div>
                <div class="col-body">
                  <div v-for="(item, idx) in evidence.cited" :key="idx" class="evidence-item item-cited">
                    <div class="item-meta">
                      <span class="item-doc">{{ getItemTitle(item) || `证据 #${idx + 1}` }}</span>
                    </div>
                    <div class="item-text markdown-body" v-html="renderMd(getItemSnippet(item))"></div>
                  </div>
                  <div v-if="!evidence.cited.length" class="empty-hint">无显式引用片段</div>
                </div>
              </div>

              <div class="evidence-col col-uncited">
                <div class="col-header header-uncited">
                  <h4>未引用候选 ({{ evidence.retrieved_uncited.length }})</h4>
                </div>
                <div class="col-body">
                  <div v-for="(item, idx) in evidence.retrieved_uncited" :key="idx" class="evidence-item item-uncited">
                    <div class="item-meta">
                      <span class="item-doc">{{ getItemTitle(item) || `候选 #${idx + 1}` }}</span>
                      <span v-if="item.drop_reason" class="drop-tag">舍弃原因: {{ item.drop_reason }}</span>
                    </div>
                    <div class="item-text markdown-body" v-html="renderMd(getItemSnippet(item))"></div>
                  </div>
                  <div v-if="!evidence.retrieved_uncited.length" class="empty-hint">无未引用片段</div>
                </div>
              </div>

              <div class="evidence-col col-gaps">
                <div class="col-header header-gaps">
                  <h4>证据缺口 ({{ evidence.gaps.length }})</h4>
                </div>
                <div class="col-body">
                  <div v-for="(gap, idx) in evidence.gaps" :key="idx" class="evidence-item item-gap">
                    <div class="item-text markdown-body" v-html="renderMd(formatTextOrJson(gap))"></div>
                  </div>
                  <div v-if="!evidence.gaps.length" class="empty-hint">无明显知识缺口</div>
                </div>
              </div>

              <div class="evidence-col col-conflicts">
                <div class="col-header header-conflicts">
                  <h4>冲突项 ({{ evidence.conflicts.length }})</h4>
                </div>
                <div class="col-body">
                  <div v-for="(conf, idx) in evidence.conflicts" :key="idx" class="evidence-item item-conflict">
                    <div class="item-text markdown-body" v-html="renderMd(formatTextOrJson(conf))"></div>
                  </div>
                  <div v-if="!evidence.conflicts.length" class="empty-hint">无逻辑冲突</div>
                </div>
              </div>
            </div>
          </section>

          <section v-else class="panel-card">
            <h3 class="section-title">最终生成回答</h3>
            <div class="answer-box">
              <div
                class="answer-text markdown-body"
                v-html="renderMd(detail.answer?.text || liveAnswer || (loading ? '答案生成中...' : '暂无回答'))"
              ></div>
            </div>

            <!-- 反馈按钮组 -->
            <div v-if="detail.answer?.text || liveAnswer" class="debug-feedback-toolbar">
              <span class="feedback-label">反馈记录：</span>
              <button
                type="button"
                class="feedback-btn btn-useful"
                :class="{ active: currentFeedback === 'useful' }"
                title="有用"
                @click="handleTraceFeedback('useful')"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M7 10v12"/>
                  <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2h0a3 3 0 0 1 3 3.88Z"/>
                </svg>
                <span>有用</span>
              </button>
              <button
                type="button"
                class="feedback-btn btn-unuseful"
                :class="{ active: currentFeedback === 'unuseful' }"
                title="无用"
                @click="handleTraceFeedback('unuseful')"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M17 14V2"/>
                  <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22h0a3 3 0 0 1-3-3.88Z"/>
                </svg>
                <span>无用</span>
              </button>
            </div>

            <h3 class="section-title margin-top-lg">引用参考来源</h3>
            <div v-if="detail.answer?.source_documents?.length" class="source-list">
              <div v-for="(doc, idx) in detail.answer.source_documents" :key="idx" class="source-card">
                <div class="source-header">
                  <span class="source-type-badge">
                    {{ doc.metadata?.source_type === 'external' ? '外部联网' : '知识库文档' }}
                  </span>
                  <span class="source-name">{{ doc.metadata?.file_name || doc.metadata?.title || doc.metadata?.source || `文档 #${idx + 1}` }}</span>
                  <span v-if="doc.metadata?.page_label" class="source-page">P.{{ doc.metadata.page_label }}</span>
                </div>
                <div v-if="doc.metadata?.section_title" class="source-sec">
                  章节：{{ doc.metadata.section_title }}
                </div>
                <div class="source-body markdown-body" v-html="renderMd(doc.content)"></div>
              </div>
            </div>
            <div v-else class="empty-hint">无关联文档来源</div>
          </section>
        </template>

        <div v-else class="empty-placeholder">
          <div class="placeholder-content">
            <p class="primary-text">请从左侧选择历史追踪记录，或在上方框内输入问题发起调试</p>
          </div>
        </div>
      </main>
    </div>
  </section>
</template>

<style scoped>
.qa-debug {
  height: calc(100vh - 64px);
  display: flex;
  flex-direction: column;
  padding: 16px 20px;
  box-sizing: border-box;
  background-color: #f8fafc;
  color: #1e293b;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}

.page-head {
  margin-bottom: 12px;
}

.head-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.page-head h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
}

.head-badge {
  background: #e0e7ff;
  color: #3730a3;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
}

.sub {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 13px;
}

.layout {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 16px;
}

.history, .main {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #ffffff;
  min-height: 0;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.history {
  display: flex;
  flex-direction: column;
  padding: 12px;
  background: #f8fafc;
}

.history-tools {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.search-box input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  font-size: 13px;
  box-sizing: border-box;
  background: #ffffff;
  outline: none;
  transition: border-color 0.15s;
}

.search-box input:focus {
  border-color: #2563eb;
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.1);
}

.filter-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.check {
  font-size: 12px;
  color: #475569;
  display: flex;
  gap: 6px;
  align-items: center;
  cursor: pointer;
}

.meta-bar {
  margin: 10px 0 6px;
  font-size: 12px;
  color: #64748b;
  border-bottom: 1px solid #e2e8f0;
  padding-bottom: 6px;
}

.meta-bar strong {
  color: #0f172a;
}

.trace-list {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  flex: 1;
}

.trace-list li {
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid #e2e8f0;
  background: #ffffff;
  margin-bottom: 8px;
  transition: all 0.15s ease;
}

.trace-list li:hover {
  border-color: #cbd5e1;
  background: #f1f5f9;
}

.trace-list li.active {
  border-color: #2563eb;
  background: #eff6ff;
  box-shadow: 0 0 0 1px #2563eb;
}

.trace-list li.errored {
  border-left: 4px solid #ef4444;
}

.item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  font-size: 11px;
  margin-bottom: 6px;
}

.status-badge {
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 4px;
}

.badge-success {
  background: #dcfce7;
  color: #15803d;
}

.badge-error {
  background: #fee2e2;
  color: #b91c1c;
}

.time {
  color: #64748b;
  font-family: monospace;
}

.ms-tag {
  color: #334155;
  font-weight: 600;
}

.q-text {
  font-size: 13px;
  font-weight: 500;
  color: #0f172a;
  line-height: 1.4;
  margin-bottom: 8px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.item-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  font-size: 11px;
}

.path-tag {
  color: #64748b;
  background: #f1f5f9;
  padding: 1px 6px;
  border-radius: 4px;
}

.counts {
  display: flex;
  gap: 4px;
}

.count-badge {
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}

.count-cand {
  background: #e0f2fe;
  color: #0369a1;
}

.count-cite {
  background: #f0fdf4;
  color: #166534;
}

.empty-state {
  text-align: center;
  color: #94a3b8;
  font-size: 13px;
  padding: 24px 0;
}

.main {
  padding: 16px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.ask-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px;
}

.ask-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.ask-label {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
}

.live-status-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #2563eb;
  background: #eff6ff;
  padding: 2px 8px;
  border-radius: 999px;
  font-weight: 500;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: #2563eb;
}

textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.5;
  box-sizing: border-box;
  resize: vertical;
  background: #ffffff;
}

textarea:focus {
  outline: none;
  border-color: #2563eb;
}

.ask-actions {
  display: flex;
  gap: 8px;
}

button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  border: 1px solid transparent;
}

button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-primary {
  background: #2563eb;
  color: #ffffff;
}

.btn-primary:hover:not(:disabled) {
  background: #1d4ed8;
}

.btn-sm {
  padding: 4px 10px;
  font-size: 12px;
}

.ghost {
  background: #ffffff;
  border-color: #cbd5e1;
  color: #475569;
}

.ghost:hover:not(:disabled) {
  background: #f1f5f9;
  color: #0f172a;
}

.btn-warning.ghost {
  color: #d97706;
  border-color: #fde68a;
}

.btn-danger.ghost {
  color: #dc2626;
  border-color: #fecdd3;
}

.btn-danger.ghost:hover:not(:disabled) {
  background: #fef2f2;
}

.error-banner {
  background: #fef2f2;
  border: 1px solid #fecdd3;
  color: #991b1b;
  padding: 10px 14px;
  border-radius: 6px;
  font-size: 13px;
}

.banner-title {
  font-weight: 600;
}

.summary-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
}

.summary-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.summary-item .lbl {
  font-size: 11px;
  color: #64748b;
  font-weight: 500;
}

.summary-item .val {
  font-size: 13px;
  color: #0f172a;
  font-weight: 600;
}

.highlight-val {
  color: #2563eb !important;
}

.raw-ms {
  font-size: 11px;
  color: #64748b;
  font-weight: 400;
  margin-left: 4px;
}

.font-mono {
  font-family: monospace;
}

.runtime-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-top: 6px;
  border-top: 1px dashed #e2e8f0;
}

.tag-pill {
  background: #f1f5f9;
  color: #334155;
  border: 1px solid #e2e8f0;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 11px;
  font-weight: 500;
}

.error-detail-box {
  background: #fff1f2;
  border: 1px solid #fecdd3;
  border-radius: 6px;
  padding: 8px 12px;
}

.error-title {
  font-size: 12px;
  font-weight: 600;
  color: #991b1b;
  margin-bottom: 2px;
}

.error-msg {
  font-size: 12px;
  color: #b91c1c;
  font-family: monospace;
}

.tabs-nav {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid #e2e8f0;
  padding-bottom: 8px;
}

.tabs-nav button {
  background: transparent;
  color: #64748b;
  border: none;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
}

.tabs-nav button:hover {
  background: #f1f5f9;
  color: #0f172a;
}

.tabs-nav button.active {
  background: #2563eb;
  color: #ffffff;
}

.panel-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.section-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: #0f172a;
}

.margin-top-lg {
  margin-top: 12px;
}

.stage-timeline {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.stage-row {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr) 140px;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  background: #f8fafc;
  border-radius: 6px;
  border: 1px solid #f1f5f9;
}

.stage-info {
  display: flex;
  flex-direction: column;
}

.stage-name {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
}

.stage-code {
  font-size: 11px;
  color: #94a3b8;
  font-family: monospace;
}

.stage-bar-wrapper {
  height: 8px;
  background: #e2e8f0;
  border-radius: 999px;
  overflow: hidden;
}

.stage-bar {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #2563eb);
  border-radius: 999px;
}

.stage-ms {
  text-align: right;
  font-size: 12px;
  font-weight: 600;
  color: #0f172a;
  white-space: nowrap;
}

.query-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
}

.query-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.query-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.kind-badge {
  background: #e0e7ff;
  color: #3730a3;
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 4px;
}

.weight-badge {
  font-size: 11px;
  color: #64748b;
}

.query-body {
  font-size: 13px;
  color: #0f172a;
}

.code-block {
  margin: 0;
  padding: 12px;
  background: #0f172a;
  color: #f8fafc;
  border-radius: 6px;
  font-family: monospace;
  font-size: 12px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.count-summary {
  font-size: 12px;
  color: #64748b;
}

.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.cand-card {
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 12px;
  background: #ffffff;
}

.cand-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.rank-badge {
  background: #0f172a;
  color: #ffffff;
  font-weight: 700;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
}

.source-tag {
  font-size: 12px;
  font-weight: 600;
  color: #1e293b;
}

.score-badge {
  margin-left: auto;
  background: #fef3c7;
  color: #92400e;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
}

.sec-title {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 6px;
}

.cand-content {
  padding: 10px 12px;
  background: #f8fafc;
  border: 1px solid #f1f5f9;
  border-radius: 4px;
}

.evidence-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.evidence-col {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.col-header {
  padding: 10px 12px;
}

.col-header h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
}

.header-cited {
  background: #f0fdf4;
  color: #166534;
  border-bottom: 1px solid #bbf7d0;
}

.header-uncited {
  background: #f8fafc;
  color: #475569;
  border-bottom: 1px solid #e2e8f0;
}

.header-gaps {
  background: #fffbeb;
  color: #92400e;
  border-bottom: 1px solid #fde68a;
}

.header-conflicts {
  background: #fff1f2;
  color: #991b1b;
  border-bottom: 1px solid #fecdd3;
}

.col-body {
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 480px;
  overflow-y: auto;
}

.evidence-item {
  border-radius: 6px;
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  background: #ffffff;
}

.item-cited {
  border-left: 3px solid #16a34a;
}

.item-uncited {
  border-left: 3px solid #94a3b8;
}

.item-gap {
  border-left: 3px solid #d97706;
  background: #fffbeb;
}

.item-conflict {
  border-left: 3px solid #dc2626;
  background: #fff1f2;
}

.item-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.item-doc {
  font-size: 11px;
  font-weight: 600;
  color: #334155;
}

.drop-tag {
  font-size: 10px;
  background: #fee2e2;
  color: #991b1b;
  padding: 1px 4px;
  border-radius: 4px;
}

.item-text {
  border-radius: 4px;
}

.answer-box {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 14px;
}

.answer-text {
  min-height: 40px;
}

.source-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.source-card {
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 10px 12px;
  background: #ffffff;
}

.source-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.source-type-badge {
  background: #e0e7ff;
  color: #3730a3;
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 4px;
}

.source-name {
  font-size: 12px;
  font-weight: 600;
  color: #0f172a;
}

.source-page {
  font-size: 11px;
  color: #64748b;
}

.source-sec {
  font-size: 11px;
  color: #475569;
  margin-bottom: 6px;
}

.source-body {
  padding: 8px 10px;
  background: #f8fafc;
  border-radius: 4px;
  border: 1px solid #f1f5f9;
}

/* Markdown 包含元素的适配样式 */
.markdown-body {
  font-size: 13px;
  line-height: 1.6;
  color: #334155;
  background: transparent;
}

.markdown-body :deep(p) {
  margin: 4px 0;
}

.markdown-body :deep(p:first-child) {
  margin-top: 0;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(pre) {
  margin: 6px 0;
  padding: 10px 12px;
  background: #0f172a;
  color: #f8fafc;
  border-radius: 6px;
  overflow-x: auto;
}

.markdown-body :deep(code) {
  font-family: monospace;
  font-size: 12px;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 20px;
  margin: 6px 0;
}

.markdown-body :deep(blockquote) {
  margin: 6px 0;
  padding: 4px 12px;
  color: #64748b;
  border-left: 3px solid #cbd5e1;
  background: #f8fafc;
}

.markdown-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 6px 0;
  font-size: 12px;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #cbd5e1;
  padding: 4px 8px;
}

.markdown-body :deep(th) {
  background: #f1f5f9;
}

.empty-hint {
  color: #94a3b8;
  font-size: 13px;
  text-align: center;
  padding: 16px 0;
}

.empty-placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px dashed #e2e8f0;
  border-radius: 8px;
  padding: 40px;
}

.placeholder-content {
  text-align: center;
}

.primary-text {
  font-size: 14px;
  color: #64748b;
  margin: 0;
}

.feedback-badge {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.2;
}
.badge-unuseful {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
}
.badge-useful {
  background: #ecfdf5;
  color: #059669;
  border: 1px solid #a7f3d0;
}

.debug-feedback-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
  padding: 8px 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
}

.debug-feedback-toolbar .feedback-label {
  font-size: 13px;
  color: #64748b;
  font-weight: 500;
}

.debug-feedback-toolbar .feedback-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 12px;
  border-radius: 6px;
  border: 1px solid #cbd5e1;
  background: #ffffff;
  color: #475569;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.debug-feedback-toolbar .feedback-btn:hover {
  background: #f1f5f9;
  color: #1e293b;
  border-color: #94a3b8;
}

.debug-feedback-toolbar .feedback-btn.btn-useful.active {
  background: #ecfdf5;
  color: #059669;
  border-color: #a7f3d0;
  font-weight: 600;
}

.debug-feedback-toolbar .feedback-btn.btn-unuseful.active {
  background: #fef2f2;
  color: #dc2626;
  border-color: #fecaca;
  font-weight: 600;
}

/* 歧义确认卡片 (调试页版) */
.clarification-card {
  margin-top: 16px;
  margin-bottom: 16px;
  padding: 16px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
}

.clarification-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  font-size: 14px;
  font-weight: 600;
  color: #3b82f6;
}

.clarification-icon {
  width: 16px;
  height: 16px;
}

.clarification-title {
  flex: 1;
}

.clarification-trigger-badge {
  font-size: 11px;
  font-weight: normal;
  padding: 2px 8px;
  background: #eff6ff;
  color: #3b82f6;
  border-radius: 12px;
  border: 1px solid #bfdbfe;
}

.clarification-question {
  font-size: 14px;
  color: #334155;
  font-weight: 500;
  margin-bottom: 14px;
  line-height: 1.5;
}

.clarification-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.clarification-option-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 14px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  text-align: left;
  cursor: pointer;
  transition: all 0.2s ease;
  font-size: 13px;
  color: #334155;
}

.clarification-option-btn:hover:not(:disabled) {
  background: #eff6ff;
  border-color: #93c5fd;
  color: #2563eb;
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(59, 130, 246, 0.1);
}

.clarification-option-btn.is-selected {
  background: #eff6ff;
  border-color: #3b82f6;
  color: #2563eb;
  font-weight: 600;
}

.clarification-option-btn.is-disabled:not(.is-selected) {
  opacity: 0.5;
  cursor: not-allowed;
  background: #fafbfc;
}

.option-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  background: #cbd5e1;
  color: #475569;
  border-radius: 4px;
  font-size: 11px;
  font-weight: bold;
}

.clarification-option-btn.is-selected .option-badge {
  background: #3b82f6;
  color: #ffffff;
}

.option-label {
  flex: 1;
}

.check-icon {
  color: #3b82f6;
}

/* 调试问题与历史反问展示区域 */
.trace-question-panel {
  margin-bottom: 16px;
  padding: 14px 16px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.trace-q-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.trace-q-lbl {
  font-size: 13px;
  font-weight: 600;
  color: #475569;
  white-space: nowrap;
}

.trace-q-val {
  font-size: 14px;
  font-weight: 500;
  color: #1e293b;
  word-break: break-all;
}

.trace-clarify-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e2e8f0;
  font-size: 13px;
}

.trace-c-icon {
  font-size: 14px;
}

.trace-c-lbl {
  font-weight: 600;
  color: #64748b;
}

.trace-c-q {
  color: #475569;
}

.trace-c-arrow {
  color: #94a3b8;
  font-weight: bold;
}

.trace-c-selected-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  background: #ecfdf5;
  color: #065f46;
  border: 1px solid #a7f3d0;
  border-radius: 4px;
  font-weight: 600;
}

.header-right-tools {
  display: flex;
  align-items: center;
  gap: 12px;
}

.btn-toggle-params {
  padding: 4px 10px;
  background: #f1f5f9;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  color: #334155;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn-toggle-params:hover, .btn-toggle-params.active {
  background: #eff6ff;
  border-color: #3b82f6;
  color: #2563eb;
}

.btn-rerun-params {
  padding: 4px 10px;
  background: #2563eb;
  color: #ffffff;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.2s ease;
}

.btn-rerun-params:hover {
  background: #1d4ed8;
}

.trace-q-main {
  flex: 1;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.debug-params-panel {
  margin-top: 12px;
  padding: 12px 14px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.params-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  font-size: 12px;
  font-weight: 600;
  color: #475569;
}

.btn-text-sm {
  background: none;
  border: none;
  color: #64748b;
  font-size: 12px;
  cursor: pointer;
  text-decoration: underline;
}

.btn-text-sm:hover {
  color: #ef4444;
}

.params-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
}

.param-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.param-item.full-width {
  grid-column: 1 / -1;
  margin-top: 6px;
}

.param-item label {
  font-size: 11px;
  font-weight: 600;
  color: #64748b;
}

.param-input, .param-textarea {
  width: 100%;
  padding: 6px 8px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  font-size: 12px;
  color: #1e293b;
}

.param-input:focus, .param-textarea:focus {
  border-color: #3b82f6;
  outline: none;
}

.param-checks {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12px;
  margin-top: 18px;
}

.check-inline {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #334155;
  cursor: pointer;
}

.request-params-box {
  margin-bottom: 16px;
  padding: 12px 14px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.box-title {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 8px;
}

.params-tag-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.p-tag {
  padding: 3px 8px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  font-size: 12px;
}

.p-key {
  color: #64748b;
  font-weight: 500;
  margin-right: 4px;
}

.p-val {
  color: #0f172a;
  font-weight: 600;
}

.p-agent-prompt {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed #cbd5e1;
  font-size: 12px;
}

.p-prompt-text {
  color: #334155;
  font-family: monospace;
  white-space: pre-wrap;
  word-break: break-all;
}

/* 检索候选来源徽章 */
.source-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}

.source-graph-only {
  background: #dbeafe;
  color: #1d4ed8;
  border: 1px solid #93c5fd;
}

.source-hybrid {
  background: #f3e8ff;
  color: #6b21a8;
  border: 1px solid #d8b4fe;
}

.source-text {
  background: #f1f5f9;
  color: #475569;
  border: 1px solid #cbd5e1;
}

/* 图谱拓扑展卡 */
.graph-topo-box {
  padding: 14px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.topo-label {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 6px;
}

.entity-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.entity-badge-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  background: #ffffff;
  border: 1px solid #93c5fd;
  border-radius: 6px;
  font-size: 12px;
}

.ent-name {
  color: #1e3a8a;
  font-weight: 600;
}

.ent-type {
  color: #2563eb;
  font-size: 11px;
}

.ent-method {
  color: #64748b;
  font-size: 11px;
}

.ent-conf {
  color: #059669;
  font-size: 11px;
}

.topo-summary-box {
  margin-top: 8px;
  padding: 10px;
  background: #ffffff;
  border: 1px dashed #cbd5e1;
  border-radius: 6px;
}

.topo-summary-text {
  margin: 0;
  font-size: 12px;
  color: #334155;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: monospace;
}

@media (max-width: 1024px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .evidence-columns {
    grid-template-columns: 1fr;
  }
}
</style>
