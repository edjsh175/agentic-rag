<script setup lang="ts">
import { computed, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { Role, SourceDoc, MessageClarification, ClarificationOption, PipelineStep, EvidencePack, EvidenceItem, AgentToolCall, AgentTimelineItem } from '../types'
import { decorateCitations } from '../utils/citations'
import EvidencePanel from './EvidencePanel.vue'
import AgentThinkingBlock from './AgentThinkingBlock.vue'
import AgentToolTimeline from './AgentToolTimeline.vue'
import AgentStepStream from './AgentStepStream.vue'

const props = defineProps<{
  role: Role
  content: string
  imageUrl?: string
  loading?: boolean
  status?: string
  thinking?: string
  isThinking?: boolean
  thinkingDuration?: string
  agentTools?: AgentToolCall[]
  timelineItems?: AgentTimelineItem[]
  sources?: SourceDoc[]
  clarification?: MessageClarification
  feedback?: 'useful' | 'unuseful' | null
  traceId?: string | null
  pipelineSteps?: PipelineStep[]
  evidencePack?: EvidencePack
}>()

const emit = defineEmits<{
  citationClick: [citationId: number]
  selectClarificationOption: [option: ClarificationOption]
  feedbackChange: [feedback: 'useful' | 'unuseful']
  pinChunk: [chunkId: string, item: EvidenceItem]
  excludeChunk: [chunkId: string, item: EvidenceItem]
  cancelClarification: []
}>()

const otherInputVal = ref('')
const showOtherInput = ref(false)
const otherError = ref('')

function foldKey(text: string) {
  return text.replace(/[\s_\-]+/g, '').toLowerCase()
}

function matchOtherToOption(text: string): ClarificationOption | null {
  const needle = foldKey(text)
  if (needle.length < 3) return null
  const options = props.clarification?.options || []
  for (const opt of options) {
    const ent = foldKey(opt.filter?.entity_name || '')
    const lab = foldKey(opt.label)
    if (ent && (needle.includes(ent) || ent.includes(needle))) return opt
    if (lab && (needle.includes(lab) || lab.includes(needle))) return opt
  }
  return null
}

function submitOther() {
  const text = otherInputVal.value.trim()
  if (!text) return
  const matched = matchOtherToOption(text)
  if (!matched) {
    otherError.value = '无法匹配到上方选项。请直接点选，或输入 StampWebRTC / StampWebGL / COM / Explorer。'
  } else {
    otherError.value = ''
    emit('selectClarificationOption', { ...matched })
  }
}

const isUser = computed(() => props.role === 'user')

const renderer = new marked.Renderer()
renderer.image = ({ href, title, text }) => {
  return `<img src="${href}" alt="${text || ''}" referrerpolicy="no-referrer"${title ? ` title="${title}"` : ''} />`
}

const rendered = computed(() => {
  if (props.loading) return ''
  const content = isUser.value ? props.content : decorateCitations(props.content, props.sources)
  const raw = marked.parse(content, { async: false, renderer }) as string
  return DOMPurify.sanitize(raw)
})

function handleContentClick(event: MouseEvent) {
  const target = (event.target as HTMLElement).closest<HTMLElement>('[data-citation-id]')
  if (!target) return
  const citationId = Number(target.dataset.citationId)
  if (Number.isInteger(citationId)) emit('citationClick', citationId)
}
</script>

<template>
  <div class="msg" :class="{ 'msg--user': isUser, 'msg--assistant': !isUser }">
    <!-- AI 头像（左侧） -->
    <div v-if="!isUser" class="avatar">R</div>

    <div class="body">
      <!-- 名称行 -->
      <div class="name" v-if="!isUser">RAG 知识库</div>
      <div class="name name--user" v-else>你</div>

      <!-- 气泡 -->
      <div class="bubble" :class="{ 'bubble--user': isUser, 'bubble--loading': loading && !content && !clarification }">
        <img v-if="imageUrl" :src="imageUrl" class="msg-image" />

        <!-- Coding Agent 完整时序流（Think 与 Tool IN/OUT） -->
        <AgentStepStream
          v-if="!isUser && ((timelineItems && timelineItems.length > 0) || (loading && status))"
          :items="timelineItems || []"
          :loading="loading"
          :active-status="status"
        />

        <!-- 降级兼容：若只有旧版 thinking 且无 timelineItems -->
        <AgentThinkingBlock
          v-else-if="thinking && !isUser && (!timelineItems || timelineItems.length === 0)"
          :thinking="thinking"
          :is-thinking="isThinking"
          :duration="thinkingDuration"
        />

        <!-- 降级兼容：若只有旧版 agentTools 且无 timelineItems -->
        <AgentToolTimeline
          v-if="!isUser && (!timelineItems || timelineItems.length === 0) && (agentTools && agentTools.length > 0)"
          :tools="agentTools || []"
          :loading="loading"
          :active-status="status"
        />

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
              v-for="opt in clarification.options"
              :key="opt.id"
              class="clarification-option-btn"
              :class="{
                'is-selected': clarification.selectedId === opt.id,
                'is-disabled': clarification.selectedId && clarification.selectedId !== opt.id
              }"
              :disabled="!!clarification.selectedId"
              @click="emit('selectClarificationOption', opt)"
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
                'is-selected': clarification.selectedId === 'other',
                'is-disabled': clarification.selectedId && clarification.selectedId !== 'other',
                'is-active': showOtherInput && !clarification.selectedId
              }"
              :disabled="!!clarification.selectedId"
              @click="showOtherInput = !showOtherInput"
            >
              <span class="option-badge">+</span>
              <span class="option-label">{{ clarification.selectedId === 'other' ? (clarification.otherText || '自定义输入') : '其他（自定义输入）' }}</span>
              <svg v-if="clarification.selectedId === 'other'" class="check-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
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
            <p v-if="otherError && showOtherInput && !clarification.selectedId" class="clarification-other-error">
              {{ otherError }}
            </p>
          </div>
        </div>

        <div v-if="loading && !content && !thinking && (!agentTools || agentTools.length === 0) && !status" class="typing">
          <span class="dot"></span>
          <span class="dot"></span>
          <span class="dot"></span>
        </div>

        <div v-if="content" class="md" v-html="rendered" @click="handleContentClick"></div>

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
              <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22h0a3 3 0 0 1-3-3.88Z"/>
            </svg>
            <span>无用</span>
          </button>
        </div>

        <!-- 问答过程与证据调试面板 -->
        <EvidencePanel
          v-if="!isUser && !loading && (pipelineSteps?.length || evidencePack)"
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

.name {
  font-size: 12px;
  color: #8a8f99;
  margin-bottom: 6px;
  padding-left: 4px;
}
.name--user {
  text-align: right;
  padding-right: 4px;
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
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #3370ff;
}

.clarification-icon {
  font-size: 14px;
}

.clarification-title {
  flex: 1;
}

.clarification-trigger-badge {
  font-size: 11px;
  font-weight: normal;
  padding: 2px 8px;
  background: #eef3fe;
  color: #3370ff;
  border-radius: 10px;
  border: 1px solid #d0e1fd;
}

.clarification-question {
  font-size: 14px;
  color: #1e2a41;
  font-weight: 500;
  margin-bottom: 12px;
  line-height: 1.5;
}

.clarification-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 280px;
  overflow-y: auto;
}

.clarification-option-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 14px;
  background: #f7f9fc;
  border: 1px solid #e4e8f0;
  border-radius: 6px;
  text-align: left;
  cursor: pointer;
  transition: all 0.2s ease;
  font-size: 13px;
  color: #2c3e50;
}

.clarification-option-btn:hover:not(:disabled) {
  background: #eef4ff;
  border-color: #a4c2ff;
  color: #3370ff;
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(51, 112, 255, 0.12);
}

.clarification-option-btn.is-selected {
  background: #eef4ff;
  border-color: #3370ff;
  color: #3370ff;
  font-weight: 600;
}

.clarification-option-btn.is-disabled:not(.is-selected) {
  opacity: 0.5;
  cursor: not-allowed;
  background: #fafbfc;
}

.option-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 4px;
  background: #e4e8f0;
  color: #5c6475;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.is-selected .option-badge {
  background: #3370ff;
  color: #ffffff;
}

.option-label {
  flex: 1;
  word-break: break-word;
}

.check-icon {
  flex-shrink: 0;
  color: #3370ff;
}

.clarification-close-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  background: transparent;
  color: #94a3b8;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.clarification-close-btn:hover {
  background: #f1f5f9;
  color: #ef4444;
}

.clarification-other-input-panel {
  display: flex;
  gap: 8px;
  margin-top: 4px;
  padding: 4px;
  background: #f8fafc;
  border: 1px dashed #cbd5e1;
  border-radius: 6px;
}

.clarification-other-input-panel .other-input {
  flex: 1;
  height: 32px;
  padding: 0 10px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  font-size: 13px;
  outline: none;
  background: #ffffff;
}

.clarification-other-input-panel .other-input:focus {
  border-color: #3370ff;
}

.clarification-other-input-panel .other-submit-btn {
  height: 32px;
  padding: 0 16px;
  border: none;
  background: #3370ff;
  color: #ffffff;
  border-radius: 4px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.2s;
}

.clarification-other-input-panel .other-submit-btn:hover:not(:disabled) {
  background: #1a56db;
}

.clarification-other-input-panel .other-submit-btn:disabled {
  background: #cbd5e1;
  color: #94a3b8;
  cursor: not-allowed;
}

.clarification-other-error {
  margin: 6px 4px 0;
  font-size: 12px;
  color: #b42318;
}

/* ===== 移动端响应式 ===== */
@media (max-width: 768px) {
  .msg {
    gap: 8px;
    margin-bottom: 16px;
  }
  .avatar {
    width: 28px;
    height: 28px;
    border-radius: 6px;
    font-size: 11px;
  }
  .body {
    max-width: 85%;
  }
  .name {
    font-size: 11px;
    margin-bottom: 4px;
  }
  .bubble {
    padding: 10px 12px;
    font-size: 13px;
  }
  .msg-image {
    max-height: 200px;
  }
  .md :deep(pre) {
    padding: 8px;
    font-size: 12px;
  }
  .md :deep(code) {
    font-size: 12px;
  }
  .md :deep(.citation-chip) {
    max-width: 220px;
    font-size: 10px;
  }
  .thinking-content {
    font-size: 12px;
  }
}
</style>
