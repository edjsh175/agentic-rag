<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { deleteQaTrace, getQaTrace, listQaTraces, queryAdminDebugStream } from '../api'
import type { EvidenceChain, QaTraceDetail, QaTraceSummary } from '../types'

const question = ref('')
const loading = ref(false)
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

async function runDebug() {
  if (!question.value.trim()) return
  abortCtrl?.abort()
  abortCtrl = new AbortController()
  loading.value = true
  error.value = ''
  liveStatus.value = '发起调试中...'
  liveAnswer.value = ''
  selectedId.value = '(运行中)'
  detail.value = emptyDetail(question.value.trim())
  activeTab.value = 'timeline'

  let finishedTraceId = ''
  try {
    await queryAdminDebugStream(
      question.value.trim(),
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
      abortCtrl.signal,
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

function stopDebug() {
  abortCtrl?.abort()
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

function getItemSnippet(item: any): string {
  if (!item) return ''
  if (typeof item === 'string') return item
  return item.snippet || item.content || ''
}

function getItemTitle(item: any): string {
  if (!item || typeof item === 'string') return ''
  return item.document || item.source || item.chunk_id || ''
}

onMounted(() => {
  refreshList()
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
              <span class="time">{{ fmtTime(item.created_at) }}</span>
              <span class="ms-tag">{{ item.elapsed_ms ?? '-' }} ms</span>
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
            <span v-if="liveStatus" class="live-status-pill">
              <span class="dot"></span>
              {{ liveStatus }}
            </span>
          </div>
          <textarea
            v-model="question"
            placeholder="请输入需要复现与诊断的问答测试文本..."
            rows="3"
            @keydown="onKeydown"
          />
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

        <div v-if="error" class="error-banner">
          <span class="banner-title">系统提示：</span>
          <span>{{ error }}</span>
        </div>

        <template v-if="detail">
          <div class="summary-card">
            <div class="summary-grid">
              <div class="summary-item">
                <span class="lbl">追踪 ID</span>
                <span class="val font-mono">{{ detail.meta.trace_id }}</span>
              </div>
              <div class="summary-item">
                <span class="lbl">响应耗时</span>
                <span class="val highlight-val">{{ detail.meta.elapsed_ms ?? '-' }} ms</span>
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
                <div class="stage-ms">{{ ms }} ms</div>
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
  grid-template-columns: 180px minmax(0, 1fr) 80px;
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
  font-family: monospace;
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

@media (max-width: 1024px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .evidence-columns {
    grid-template-columns: 1fr;
  }
}
</style>
