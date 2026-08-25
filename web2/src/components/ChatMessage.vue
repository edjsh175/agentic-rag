<script setup lang="ts">
import { computed, ref, nextTick } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { Role, SourceDoc, MessageClarification, ClarificationOption, PipelineStep, EvidencePack, EvidenceItem } from '../types'
import { decorateCitations } from '../utils/citations'
import { copyToClipboard } from '../utils/clipboard'
import EvidencePanel from './EvidencePanel.vue'

const props = defineProps<{
  role: Role
  content: string
  imageUrl?: string
  loading?: boolean
  status?: string
  thinking?: string
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
  resend: [payload: { content: string; imageFile?: File; imageUrl?: string } | string]
  editInInput: [content: string]
  copy: [content: string]
}>()

const showThinking = ref(true)
const otherInputVal = ref('')
const showOtherInput = ref(false)

const isEditing = ref(false)
const editDraft = ref('')
const editImagePreview = ref<string | null>(null)
const editImageFile = ref<File | null>(null)
const editFileInputRef = ref<HTMLInputElement>()
const editTextareaRef = ref<HTMLTextAreaElement>()
const copied = ref(false)
let copiedTimer = 0

function submitOther() {
  const text = otherInputVal.value.trim()
  if (!text) return
  emit('selectClarificationOption', {
    id: 'other',
    label: text,
    filter: {}
  })
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

function adjustEditTextareaHeight() {
  const el = editTextareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.max(56, el.scrollHeight + 2) + 'px'
}

function startEdit() {
  editDraft.value = props.content
  editImagePreview.value = props.imageUrl || null
  editImageFile.value = null
  isEditing.value = true
  nextTick(() => {
    adjustEditTextareaHeight()
    editTextareaRef.value?.focus()
    editTextareaRef.value?.select()
  })
}

function cancelEdit() {
  isEditing.value = false
  editDraft.value = ''
  editImagePreview.value = null
  editImageFile.value = null
}

function submitEdit() {
  const text = editDraft.value.trim()
  if (!text && !editImagePreview.value) return
  isEditing.value = false
  emit('resend', {
    content: text || '请描述这张图片',
    imageFile: editImageFile.value || undefined,
    imageUrl: editImagePreview.value || undefined,
  })
}

function handleEditKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submitEdit()
  } else if (e.key === 'Escape') {
    e.preventDefault()
    cancelEdit()
  }
}

function pickEditImage() {
  editFileInputRef.value?.click()
}

function handleEditFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) {
    setEditImage(file)
  }
  input.value = ''
}

function setEditImage(file: File) {
  editImageFile.value = file
  const reader = new FileReader()
  reader.onload = () => {
    editImagePreview.value = reader.result as string
  }
  reader.readAsDataURL(file)
}

function clearEditImage() {
  editImagePreview.value = null
  editImageFile.value = null
}

function handleEditPaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items
  if (!items) return
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) {
        setEditImage(file)
        e.preventDefault()
        return
      }
    }
  }
}

async function handleCopy() {
  if (!props.content) return
  const success = await copyToClipboard(props.content)
  if (success) {
    copied.value = true
    clearTimeout(copiedTimer)
    copiedTimer = window.setTimeout(() => {
      copied.value = false
    }, 2000)
    emit('copy', props.content)
  }
}
</script>

<template>
  <div class="msg" :class="{ 'msg--user': isUser, 'msg--assistant': !isUser }">
    <div class="body">
      <!-- 用户消息编辑态 -->
      <div v-if="isUser && isEditing" class="edit-card">
        <!-- 隐藏的图片选择 input -->
        <input
          ref="editFileInputRef"
          type="file"
          accept="image/*"
          style="display: none"
          @change="handleEditFileChange"
        />

        <!-- 图片预览区域 -->
        <div v-if="editImagePreview" class="edit-preview-row">
          <div class="edit-preview-item">
            <img :src="editImagePreview" class="edit-preview-thumb" alt="预览图片" />
            <button
              type="button"
              class="edit-preview-remove"
              title="移除图片"
              @click="clearEditImage"
            >
              &times;
            </button>
          </div>
        </div>

        <textarea
          ref="editTextareaRef"
          v-model="editDraft"
          class="edit-textarea"
          rows="2"
          placeholder="编辑您的问题（支持 Ctrl+V 粘贴图片）..."
          @input="adjustEditTextareaHeight"
          @keydown="handleEditKeydown"
          @paste="handleEditPaste"
        />
        <div class="edit-footer">
          <div class="edit-tools">
            <button
              type="button"
              class="edit-tool-btn"
              :class="{ 'has-image': !!editImagePreview }"
              title="上传图片"
              @click="pickEditImage"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                <circle cx="8.5" cy="8.5" r="1.5"/>
                <polyline points="21 15 16 10 5 21"/>
              </svg>
            </button>
          </div>
          <div class="edit-actions">
            <button
              type="button"
              class="edit-btn edit-btn--cancel"
              @click="cancelEdit"
            >
              取消
            </button>
            <button
              type="button"
              class="edit-btn edit-btn--submit"
              :disabled="!editDraft.trim() && !editImagePreview"
              @click="submitEdit"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>
              </svg>
              <span>发送</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 气泡（常规态） -->
      <div v-else class="bubble-container" :class="{ 'bubble-container--user': isUser }">
        <div class="bubble" :class="{ 'bubble--user': isUser, 'bubble--loading': loading && !content && !clarification }">
          <img v-if="imageUrl" :src="imageUrl" class="msg-image" />

          <div v-if="thinking && !isUser" class="thinking-wrap">
            <button class="thinking-toggle" @click="showThinking = !showThinking">
              <svg :class="{ rotated: showThinking }" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="9 18 15 12 9 6"/>
              </svg>
              深度思考
            </button>
            <div v-if="showThinking" class="thinking-content">{{ thinking }}</div>
          </div>

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
            </div>
          </div>

          <div v-if="loading && status" class="stream-status">
            <span class="status-dot"></span>
            <span>{{ status }}</span>
          </div>

          <div v-else-if="loading && !content" class="typing">
            <span class="dot"></span>
            <span class="dot"></span>
            <span class="dot"></span>
          </div>

          <div v-if="content" class="md" v-html="rendered" @click="handleContentClick"></div>
        </div>

        <!-- AI 回答操作与反馈按钮组（置于气泡外部下方） -->
        <div v-if="!isUser && !loading && content" class="feedback-toolbar">
          <button
            type="button"
            class="ai-op-btn btn-copy"
            :class="{ 'is-copied': copied }"
            :title="copied ? '已复制到剪贴板' : '复制'"
            @click="handleCopy"
          >
            <svg v-if="!copied" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
            <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
          </button>
          <span class="feedback-divider"></span>
          <button
            type="button"
            class="ai-op-btn btn-useful"
            :class="{ active: feedback === 'useful' }"
            title="有用"
            @click="emit('feedbackChange', 'useful')"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M7 10v12"/>
              <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2h0a3 3 0 0 1 3 3.88Z"/>
            </svg>
          </button>
          <button
            type="button"
            class="ai-op-btn btn-unuseful"
            :class="{ active: feedback === 'unuseful' }"
            title="无用"
            @click="emit('feedbackChange', 'unuseful')"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M17 14V2"/>
              <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22h0a3 3 0 0 1-3-3.88Z"/>
            </svg>
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

        <!-- 用户消息浮动操作栏（纯图标，悬停显示） -->
        <div v-if="isUser && !loading" class="user-msg-toolbar">
          <button
            type="button"
            class="user-op-btn"
            :class="{ 'is-copied': copied }"
            :title="copied ? '已复制' : '复制'"
            @click="handleCopy"
          >
            <svg v-if="!copied" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
            <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
          </button>
          <button
            type="button"
            class="user-op-btn"
            title="修改"
            @click="startEdit"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 20h9M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
            </svg>
          </button>
        </div>
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
  gap: 4px;
  margin-top: 4px;
}

.feedback-divider {
  display: inline-block;
  width: 1px;
  height: 12px;
  background: #e2e8f0;
  margin: 0 2px;
}

.ai-op-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border-radius: 4px;
  border: none;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  transition: all 0.15s ease;
}

.ai-op-btn:hover {
  background: #f1f5f9;
  color: #3370ff;
}

.ai-op-btn.is-copied {
  color: #059669;
}

.ai-op-btn.btn-useful.active {
  background: #ecfdf5;
  color: #059669;
}

.ai-op-btn.btn-unuseful.active {
  background: #fef2f2;
  color: #dc2626;
}

.bubble-container {
  display: flex;
  flex-direction: column;
}
.bubble-container--user {
  align-items: flex-end;
}

/* 用户消息气泡下方工具栏（常驻显示、无边框） */
.user-msg-toolbar {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
  opacity: 1;
}

.user-op-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border-radius: 4px;
  border: none;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  transition: all 0.15s ease;
}

.user-op-btn:hover {
  background: #f1f5f9;
  color: #3370ff;
}

.user-op-btn.is-copied {
  color: #059669;
}

.feedback-btn.btn-copy {
  padding: 3px 6px;
  min-width: 24px;
  justify-content: center;
}

/* 行内编辑卡片（现代化圆角设计） */
.edit-card {
  width: 100%;
  min-width: 320px;
  background: #ffffff;
  border: 1px solid #3370ff;
  box-shadow: 0 4px 20px rgba(51, 112, 255, 0.14);
  border-radius: 20px;
  padding: 12px 16px;
  box-sizing: border-box;
}

.edit-preview-row {
  display: flex;
  margin-bottom: 8px;
}

.edit-preview-item {
  position: relative;
  display: inline-block;
}

.edit-preview-thumb {
  max-width: 140px;
  max-height: 100px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  object-fit: cover;
  display: block;
}

.edit-preview-remove {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.65);
  color: #ffffff;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
  transition: background 0.15s ease;
}

.edit-preview-remove:hover {
  background: #ef4444;
}

.edit-textarea {
  width: 100%;
  min-height: 56px;
  border: none;
  outline: none;
  resize: none;
  font-size: 14px;
  line-height: 1.6;
  color: #1e2a41;
  background: transparent;
  font-family: inherit;
  box-sizing: border-box;
  display: block;
}

.edit-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #f1f5f9;
  flex-wrap: wrap;
}

.edit-tools {
  display: flex;
  align-items: center;
  gap: 6px;
}

.edit-tool-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  background: transparent;
  border: none;
  border-radius: 6px;
  color: #64748b;
  cursor: pointer;
  transition: all 0.15s ease;
}

.edit-tool-btn:hover {
  background: #f1f5f9;
  color: #3370ff;
}

.edit-tool-btn.has-image {
  color: #3370ff;
  background: #eff6ff;
}

.edit-hint {
  font-size: 11px;
  color: #94a3b8;
  user-select: none;
}

.edit-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}

.edit-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  border-radius: 16px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  border: 1px solid transparent;
}

.edit-btn--fill {
  background: #f8fafc;
  border-color: #e2e8f0;
  color: #64748b;
}
.edit-btn--fill:hover {
  background: #f1f5f9;
  color: #334155;
  border-color: #cbd5e1;
}

.edit-btn--cancel {
  background: #ffffff;
  border-color: #e2e8f0;
  color: #64748b;
}
.edit-btn--cancel:hover {
  background: #f8fafc;
  color: #334155;
  border-color: #cbd5e1;
}

.edit-btn--submit {
  background: #3370ff;
  color: #ffffff;
}
.edit-btn--submit:hover:not(:disabled) {
  background: #1a56db;
}
.edit-btn--submit:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.body {
  max-width: 100%;
  min-width: 0;
  width: 100%;
}
.msg--user .body {
  max-width: 72%;
  width: auto;
}

.bubble {
  font-size: 14px;
  line-height: 1.7;
  color: #1e2a41;
  word-wrap: break-word;
}
.bubble--user {
  padding: 10px 16px;
  background: #3370ff;
  color: #fff;
  border-radius: 30px 30px 30px 30px;
}
.bubble:not(.bubble--user) {
  padding: 0;
  background: transparent;
  border-radius: 0;
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

/* ===== 移动端响应式 ===== */
@media (max-width: 768px) {
  .body {
    max-width: 100%;
  }
  .msg--user .body {
    max-width: 88%;
  }
  .bubble--user {
    padding: 8px 12px;
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
  .edit-card {
    min-width: 240px;
  }
  .edit-footer {
    flex-direction: column;
    align-items: flex-start;
  }
  .edit-actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
