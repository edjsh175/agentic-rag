<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { Role, SourceDoc, MessageClarification, ClarificationOption, ClarificationSelection, PipelineStep, EvidencePack, EvidenceItem, AssistantBlock, WorkMode } from '../types'
import { decorateCitations } from '../utils/citations'
import EvidencePanel from './EvidencePanel.vue'
import AgentStepStream from './AgentStepStream.vue'

const router = useRouter()

const props = defineProps<{
  role: Role
  content: string
  mode?: WorkMode
  imageUrl?: string
  loading?: boolean
  status?: string
  blocks?: AssistantBlock[]
  sources?: SourceDoc[]
  clarification?: MessageClarification
  feedback?: 'useful' | 'unuseful' | null
  traceId?: string | null
  pipelineSteps?: PipelineStep[]
  evidencePack?: EvidencePack
}>()

const emit = defineEmits<{
  citationClick: [citationId: number]
  selectClarificationOption: [selection: ClarificationSelection]
  feedbackChange: [feedback: 'useful' | 'unuseful']
  pinChunk: [chunkId: string, item: EvidenceItem]
  excludeChunk: [chunkId: string, item: EvidenceItem]
  cancelClarification: []
  sourcesClick: []
  openTrace: [traceId: string]
}>()

const otherInputVal = ref('')
const showOtherInput = ref(false)

function isOtherOption(option: ClarificationOption) {
  return option.id === 'other' || option.source === 'fixed_other'
}

const otherOption = computed<ClarificationOption>(() => (
  props.clarification?.options.find(isOtherOption) || {
    id: 'other',
    label: '以上都不是',
    filter: {},
    source: 'fixed_other',
    binding_status: 'unresolved',
  }
))
const visibleOptions = computed(() => (
  (props.clarification?.options || []).filter(option => !isOtherOption(option))
))

function selectOption(option: ClarificationOption) {
  emit('selectClarificationOption', { option, kind: 'option' })
}

function submitOther() {
  const text = otherInputVal.value.trim()
  if (!text) return
  emit('selectClarificationOption', {
    option: otherOption.value,
    kind: 'free_text',
    freeText: text,
  })
}

const isUser = computed(() => props.role === 'user')
const displayMode = computed<WorkMode>(() => {
  if (props.mode) return props.mode
  return props.blocks?.length ? 'agent' : 'linear'
})
const isAgentMode = computed(() => displayMode.value === 'agent')

function sourceLabel(source: SourceDoc): string {
  return source.metadata?.title || source.metadata?.file_name || source.metadata?.source || '未知来源'
}

function sourceCitationId(source: SourceDoc, index: number): number {
  return source.metadata?.citation_id ?? index + 1
}

const renderer = new marked.Renderer()
renderer.image = ({ href, title, text }) => {
  return `<img src="${href}" alt="${text || ''}" referrerpolicy="no-referrer"${title ? ` title="${title}"` : ''} />`
}

function renderMarkdown(content: string): string {
  const decorated = isUser.value ? content : decorateCitations(content, props.sources)
  const raw = marked.parse(decorated, { async: false, renderer }) as string
  return DOMPurify.sanitize(raw)
}

const rendered = computed(() => props.loading ? '' : renderMarkdown(props.content))

function handleContentClick(event: MouseEvent) {
  const target = (event.target as HTMLElement).closest<HTMLElement>('[data-citation-id]')
  if (!target) return
  const citationId = Number(target.dataset.citationId)
  if (Number.isInteger(citationId)) emit('citationClick', citationId)
}

function handleOpenTrace() {
  if (!props.traceId) return
  emit('openTrace', props.traceId)
  try {
    router.push({ path: '/qa-debug', query: { trace_id: props.traceId } })
  } catch {}
}
</script>

<template>
  <div class="msg" :class="{ 'msg--user': isUser, 'msg--assistant': !isUser }">
    <!-- AI 头像（左侧） -->
    <div v-if="!isUser" class="avatar">R</div>

    <div class="body">
      <!-- 名称行 -->
      <div class="name-row" v-if="!isUser">
        <div class="name">RAG 知识库</div>
        <button
          v-if="traceId"
          type="button"
          class="trace-detail-btn"
          title="在 QA Debug 中查看完整执行事件与工程 Trace"
          @click="handleOpenTrace"
        >
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M16 8L10.8571 12V10.552L14.1383 8L10.8571 5.448V4L16 8ZM5.14286 10.552L1.86171 8L5.14286 5.448V4L0 8L5.14286 12V10.552ZM9.02514 4L5.59657 12H6.84057L10.2691 4H9.02514Z" fill="currentColor"/>
          </svg>
          执行详情
        </button>
      </div>
      <div class="name name--user" v-else>你</div>

      <!-- 气泡 -->
      <div class="bubble" :class="{ 'bubble--user': isUser, 'bubble--loading': loading && !content && !clarification }">
        <img v-if="imageUrl" :src="imageUrl" class="msg-image" />

        <!-- Agent 用户可见 Block Stream：四类 Block 是唯一渲染来源 -->
        <AgentStepStream
          v-if="!isUser && isAgentMode && blocks && blocks.length > 0"
          :blocks="blocks"
        >
          <template #markdown="{ block }">
            <div class="md" v-html="renderMarkdown(block.text)" @click="handleContentClick"></div>
          </template>
        </AgentStepStream>

        <!-- Linear 模式：阶段状态 -->
        <section
          v-if="!isUser && !isAgentMode && loading && status"
          class="answer-segment pipeline-stage-segment"
          data-testid="pipeline-status"
        >
          <div class="stream-status">
            <span class="status-dot"></span>
            <span>{{ status }}</span>
          </div>
        </section>

        <!-- 歧义反问卡片 -->
        <div v-if="clarification && !isUser" class="clarification-card">
          <div class="clarification-header">
            <svg class="clarification-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"/>
              <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
              <line x1="12" y1="17" x2="12.01" y2="17"/>
            </svg>
            <span class="clarification-title">歧义确认</span>
            <span v-if="clarification.trigger" class="clarification-trigger-badge">
              包含「{{ clarification.trigger }}」
            </span>
            <button
              v-if="!clarification.selectedId"
              class="clarification-close-btn"
              title="关闭并终止回答"
              @click="emit('cancelClarification')"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>
          <div class="clarification-question">
            {{ clarification.ask_question }}
          </div>
          <div class="clarification-options">
            <button
              v-for="opt in visibleOptions"
              :key="opt.id"
              class="clarification-option-btn"
              :class="{
                'is-selected': clarification.selectedId === opt.id,
                'is-disabled': clarification.selectedId && clarification.selectedId !== opt.id
              }"
              :disabled="!!clarification.selectedId"
              @click="selectOption(opt)"
            >
              <span class="option-badge">{{ opt.id.toUpperCase() }}</span>
              <span class="option-label">{{ opt.label }}</span>
              <svg v-if="clarification.selectedId === opt.id" class="check-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
            </button>

            <!-- 其他（自定义输入）选项 -->
            <button
              class="clarification-option-btn is-other-option"
              :class="{
                'is-selected': clarification.selectedId === otherOption.id,
                'is-disabled': clarification.selectedId && clarification.selectedId !== otherOption.id,
                'is-active': showOtherInput && !clarification.selectedId
              }"
              :disabled="!!clarification.selectedId"
              @click="showOtherInput = !showOtherInput"
            >
              <span class="option-badge">+</span>
              <span class="option-label">{{ clarification.selectedId === otherOption.id ? (clarification.otherText || '自定义输入') : '其他（自定义输入）' }}</span>
              <svg v-if="clarification.selectedId === otherOption.id" class="check-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
            </button>

            <!-- 其他输入面板 -->
            <div v-if="showOtherInput && !clarification.selectedId" class="clarification-other-input-panel">
              <input
                v-model="otherInputVal"
                class="other-input"
                placeholder="请输入您的具体需求或方向..."
                @keydown.enter="submitOther"
              />
              <button
                class="other-submit-btn"
                :disabled="!otherInputVal.trim()"
                @click="submitOther"
              >
                确定
              </button>
            </div>
          </div>
        </div>

        <div v-if="loading && !content && (!blocks || blocks.length === 0) && !status" class="typing">
          <span class="dot"></span>
          <span class="dot"></span>
          <span class="dot"></span>
        </div>

        <section
          v-if="content && (isUser || !isAgentMode)"
          class="answer-segment final-answer-segment"
          :data-testid="isUser ? undefined : 'final-answer'"
        >
          <div class="md" v-html="rendered" @click="handleContentClick"></div>
        </section>

        <section
          v-if="!isUser && sources && sources.length > 0"
          class="answer-segment sources-segment"
          data-testid="answer-sources"
        >
          <div class="segment-heading sources-heading">
            <span class="segment-index">03</span>
            <span>Sources</span>
            <button type="button" class="sources-open" @click="emit('sourcesClick')">
              {{ sources.length }} 条
            </button>
          </div>
          <div class="source-chips">
            <button
              v-for="(source, index) in sources.slice(0, 3)"
              :key="source.metadata?.chunk_id || `${sourceLabel(source)}-${index}`"
              type="button"
              class="source-chip"
              :title="sourceLabel(source)"
              @click="emit('citationClick', sourceCitationId(source, index))"
            >
              <span class="source-number">{{ sourceCitationId(source, index) }}</span>
              <span class="source-name">{{ sourceLabel(source) }}</span>
            </button>
            <button
              v-if="sources.length > 3"
              type="button"
              class="source-chip source-chip--more"
              @click="emit('sourcesClick')"
            >
              +{{ sources.length - 3 }}
            </button>
          </div>
        </section>

        <!-- 反馈按钮组 -->
        <div v-if="!isUser && !loading && content" class="feedback-toolbar">
          <span class="feedback-label">反馈记录：</span>
          <button
            type="button"
            class="feedback-btn btn-useful"
            :class="{ active: feedback === 'useful' }"
            title="有用"
            @click="emit('feedbackChange', 'useful')"
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
            :class="{ active: feedback === 'unuseful' }"
            title="无用"
            @click="emit('feedbackChange', 'unuseful')"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M17 14V2"/>
              <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0 1.79 1.11L12 22h0a3 3 0 0 1-3-3.88Z"/>
            </svg>
            <span>无用</span>
          </button>
        </div>

        <!-- 问答过程与证据调试面板 (仅在 Linear 模式调试下使用) -->
        <EvidencePanel
          v-if="!isUser && !isAgentMode && !loading && (pipelineSteps?.length || evidencePack)"
          :pipeline-steps="pipelineSteps"
          :evidence-pack="evidencePack"
          @pin-chunk="(id, item) => emit('pinChunk', id, item)"
          @exclude-chunk="(id, item) => emit('excludeChunk', id, item)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.msg {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
}
.msg--user {
  flex-direction: row-reverse;
}

.feedback-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed #e2e8f0;
}

.feedback-label {
  font-size: 12px;
  color: #94a3b8;
}

.feedback-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
  background: #ffffff;
  color: #64748b;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.feedback-btn:hover {
  background: #f8fafc;
  color: #334155;
  border-color: #cbd5e1;
}

.feedback-btn.btn-useful.active {
  background: #ecfdf5;
  color: #059669;
  border-color: #a7f3d0;
  font-weight: 500;
}

.feedback-btn.btn-unuseful.active {
  background: #fef2f2;
  color: #dc2626;
  border-color: #fecaca;
  font-weight: 500;
}

.avatar {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 700;
  color: #fff;
  background: linear-gradient(135deg, #3370ff, #4c6fff);
  letter-spacing: 0.5px;
}

.body {
  max-width: 85%;
  min-width: 0;
}

.name-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  padding-left: 4px;
}

.name {
  font-size: 12px;
  color: #8a8f99;
}
.name--user {
  text-align: right;
  padding-right: 4px;
  margin-bottom: 6px;
}

.trace-detail-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #64748b;
  background: transparent;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 1px 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.trace-detail-btn:hover {
  background: #f1f5f9;
  color: #0f172a;
  border-color: #cbd5e1;
}

.bubble {
  padding: 12px 16px;
  border-radius: 6px;
  font-size: 14px;
  line-height: 1.7;
  background: #f7f8fa;
  color: #1e2a41;
  word-wrap: break-word;
}
.bubble--user {
  background: #3370ff;
  color: #fff;
  border-radius: 6px 6px 2px 6px;
}
.bubble:not(.bubble--user) {
  border-radius: 6px 6px 6px 2px;
}

.msg-image {
  max-width: 100%;
  max-height: 260px;
  border-radius: 6px;
  margin-bottom: 8px;
  display: block;
}

/* 打字动画 */
.typing {
  display: flex;
  gap: 5px;
  padding: 6px 0;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #c0c4cc;
  animation: bounce 1.4s infinite ease-in-out both;
}
.dot:nth-child(1) { animation-delay: -0.32s; }
.dot:nth-child(2) { animation-delay: -0.16s; }
.dot:nth-child(3) { animation-delay: 0s; }

.stream-status {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  color: #6b7280;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #3370ff;
  animation: status-pulse 1.2s ease-in-out infinite;
}
@keyframes status-pulse {
  0%, 100% { opacity: 0.35; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1); }
}
@keyframes bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

/* Markdown */
.md :deep(p) { margin: 0 0 8px; }
.md :deep(p:last-child) { margin-bottom: 0; }
.md :deep(code) {
  background: rgba(0,0,0,0.07);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
}
.md :deep(pre) {
  background: rgba(0,0,0,0.05);
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 8px 0;
}
.md :deep(pre code) { background: none; padding: 0; }
.md :deep(ul), .md :deep(ol) { padding-left: 20px; margin: 8px 0; }
.md :deep(img) { max-width: 100%; height: auto; border-radius: 4px; }
.md :deep(a) { color: inherit; text-decoration: underline; }
.md :deep(.citation-chip) {
  display: inline-flex;
  align-items: center;
  max-width: min(320px, 100%);
  margin: 0 2px;
  padding: 1px 6px;
  border: 1px solid #d9e2f2;
  border-radius: 4px;
  background: #eef4ff;
  color: #49617f;
  font: inherit;
  font-size: 11px;
  line-height: 1.55;
  vertical-align: 0.08em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
.md :deep(.citation-chip:hover) {
  border-color: #8fb0ea;
  background: #e4eeff;
  color: #245fc7;
}
.md :deep(.citation-chip:focus-visible) {
  outline: 2px solid #3370ff;
  outline-offset: 1px;
}
.md :deep(blockquote) {
  margin: 8px 0;
  padding: 2px 12px;
  border-left: 3px solid #3370ff;
  color: #5e6673;
}
.bubble--user .md :deep(code) { background: rgba(255,255,255,0.15); }
.bubble--user .md :deep(blockquote) { border-left-color: rgba(255,255,255,0.5); color: rgba(255,255,255,0.8); }

/* 深度思考 */
.thinking-wrap {
  margin-bottom: 10px;
  border-left: 3px solid #d4d4d8;
  padding-left: 12px;
}
.thinking-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: none;
  padding: 2px 0;
  font-size: 12px;
  color: #a1a1aa;
  cursor: pointer;
  transition: color 0.15s;
}
.thinking-toggle:hover { color: #3370ff; }
.thinking-toggle svg { transition: transform 0.15s; }
.thinking-toggle svg.rotated { transform: rotate(90deg); }
.thinking-content {
  margin-top: 6px;
  font-size: 13px;
  color: #71717a;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

/* ===== 歧义确认卡片 ===== */
.clarification-card {
  margin-bottom: 12px;
  padding: 14px 16px;
  background: #ffffff;
  border: 1px solid #e1e6f0;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.clarification-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.clarification-icon {
  color: #3370ff;
  flex-shrink: 0;
}

.clarification-title {
  font-weight: 600;
  font-size: 13px;
  color: #1e2a41;
  flex: 1;
}

.clarification-trigger-badge {
  font-size: 11px;
  color: #49617f;
  background: #f0f4fc;
  padding: 2px 8px;
  border-radius: 4px;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.clarification-close-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: #8a8f99;
  padding: 2px;
  display: flex;
  align-items: center;
  border-radius: 4px;
}

.clarification-close-btn:hover {
  color: #1e2a41;
  background: #f0f4fc;
}

.clarification-question {
  font-size: 13px;
  color: #374151;
  margin-bottom: 12px;
  line-height: 1.5;
}

.clarification-options {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.clarification-option-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid #d9e2f2;
  border-radius: 6px;
  background: #f8fafc;
  color: #1e2a41;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: all 0.15s ease;
}

.clarification-option-btn:hover:not(:disabled) {
  border-color: #3370ff;
  background: #f0f5ff;
}

.clarification-option-btn.is-selected {
  border-color: #3370ff;
  background: #eef4ff;
  color: #3370ff;
  font-weight: 500;
}

.clarification-option-btn.is-disabled:not(.is-selected) {
  opacity: 0.5;
  cursor: not-allowed;
}

.option-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 5px;
  border-radius: 3px;
  background: #e2e8f0;
  color: #475569;
}

.clarification-option-btn.is-selected .option-badge {
  background: #3370ff;
  color: #fff;
}

.option-label {
  flex: 1;
}

.check-icon {
  color: #3370ff;
  flex-shrink: 0;
}

.clarification-option-btn.is-other-option {
  border-style: dashed;
}

.clarification-option-btn.is-active {
  border-color: #3370ff;
  background: #f0f5ff;
}

.clarification-other-input-panel {
  display: flex;
  gap: 6px;
  margin-top: 4px;
  padding: 4px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
}

.other-input {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  font-size: 12px;
  outline: none;
}

.other-input:focus {
  border-color: #3370ff;
}

.other-submit-btn {
  padding: 6px 12px;
  background: #3370ff;
  color: #fff;
  border: none;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  font-weight: 500;
}

.other-submit-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Sources */
.sources-segment {
  margin-top: 10px;
}
.sources-heading {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #64748b;
  margin-bottom: 6px;
}
.sources-open {
  background: none;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 11px;
  color: #3b82f6;
  cursor: pointer;
}
.source-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.source-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  font-size: 11px;
  color: #334155;
  cursor: pointer;
  max-width: 200px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: all 0.15s ease;
}
.source-chip:hover {
  background: #f8fafc;
  border-color: #3b82f6;
  color: #3b82f6;
}
.source-number {
  font-weight: 600;
  color: #3b82f6;
}
.source-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.source-chip--more {
  background: #f1f5f9;
  color: #64748b;
}
</style>
