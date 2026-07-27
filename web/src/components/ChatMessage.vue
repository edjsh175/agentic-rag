<script setup lang="ts">
import { computed, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { Role, SourceDoc, MessageClarification, ClarificationOption } from '../types'
import { decorateCitations } from '../utils/citations'

const props = defineProps<{
  role: Role
  content: string
  imageUrl?: string
  loading?: boolean
  status?: string
  thinking?: string
  sources?: SourceDoc[]
  clarification?: MessageClarification
}>()

const emit = defineEmits<{
  citationClick: [citationId: number]
  selectClarificationOption: [option: ClarificationOption]
}>()

const showThinking = ref(true)

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
            <span class="clarification-icon">💡</span>
            <span class="clarification-title">歧义确认</span>
            <span v-if="clarification.trigger" class="clarification-trigger-badge">
              包含「{{ clarification.trigger }}」
            </span>
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
              <svg v-if="clarification.selectedId === opt.id" class="check-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
            </button>
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
  max-width: 70%;
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
