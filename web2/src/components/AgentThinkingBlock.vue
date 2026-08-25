<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{
  thinking: string
  isThinking?: boolean
  duration?: string
}>()

const isOpen = ref(true)
const copied = ref(false)
const scrollContainer = ref<HTMLElement | null>(null)

// 思考完成时默认折叠，正在思考时自动展开
watch(
  () => props.isThinking,
  (val, oldVal) => {
    if (val) {
      isOpen.value = true
    } else if (oldVal === true && !val) {
      // 思考完成时保持当前状态
    }
  },
  { immediate: true }
)

// 实时流式自动滚动到底部
watch(
  () => props.thinking,
  () => {
    if (props.isThinking && isOpen.value && scrollContainer.value) {
      nextTick(() => {
        if (scrollContainer.value) {
          scrollContainer.value.scrollTop = scrollContainer.value.scrollHeight
        }
      })
    }
  }
)

const renderedThinking = computed(() => {
  if (!props.thinking) return ''
  const raw = marked.parse(props.thinking, { async: false }) as string
  return DOMPurify.sanitize(raw)
})

const charCount = computed(() => props.thinking?.length || 0)

async function copyThinking() {
  if (!props.thinking) return
  try {
    await navigator.clipboard.writeText(props.thinking)
    copied.value = true
    setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch (err) {
    console.error('Failed to copy thinking text:', err)
  }
}
</script>

<template>
  <div class="thinking-block" :class="{ 'is-active': isThinking }">
    <div class="thinking-header" @click="isOpen = !isOpen">
      <div class="header-left">
        <span class="icon-indicator" :class="{ 'is-pulsing': isThinking }">
          <svg v-if="isThinking" class="sparkle-icon animate-spin-slow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
          </svg>
          <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/>
            <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
            <line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
        </span>
        <span class="thinking-title">
          <template v-if="isThinking">
            思考中<span class="dot-flashing">...</span>
          </template>
          <template v-else>
            已深度思考 <span v-if="duration" class="duration-badge">{{ duration }}</span>
            <span v-else class="count-badge">({{ charCount }} 字)</span>
          </template>
        </span>
      </div>

      <div class="header-right" @click.stop>
        <button
          v-if="thinking && !isThinking"
          type="button"
          class="action-btn"
          :title="copied ? '已复制' : '复制思考内容'"
          @click="copyThinking"
        >
          <svg v-if="copied" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"/>
          </svg>
          <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
          </svg>
          <span>{{ copied ? '已复制' : '复制' }}</span>
        </button>

        <button type="button" class="toggle-btn" :class="{ 'is-open': isOpen }" @click="isOpen = !isOpen" title="展开/折叠">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
        </button>
      </div>
    </div>

    <div v-show="isOpen" ref="scrollContainer" class="thinking-body">
      <div class="thinking-content markdown-body" v-html="renderedThinking"></div>
      <div v-if="isThinking" class="streaming-cursor"></div>
    </div>
  </div>
</template>

<style scoped>
.thinking-block {
  margin-bottom: 12px;
  border-radius: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-left: 3px solid #94a3b8;
  overflow: hidden;
  transition: all 0.2s ease;
}

.thinking-block.is-active {
  border-left-color: #3b82f6;
  background: #f0f7ff;
  border-color: #dbeafe;
}

.thinking-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
  font-size: 13px;
  color: #475569;
  background: transparent;
  transition: background-color 0.15s ease;
}

.thinking-header:hover {
  background: rgba(0, 0, 0, 0.02);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.icon-indicator {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
}

.icon-indicator.is-pulsing {
  color: #2563eb;
}

.thinking-title {
  font-weight: 500;
  color: #334155;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.duration-badge,
.count-badge {
  font-size: 11px;
  font-weight: 400;
  color: #64748b;
  background: rgba(148, 163, 184, 0.15);
  padding: 1px 6px;
  border-radius: 10px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  font-size: 11px;
  color: #64748b;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.action-btn:hover {
  color: #1e293b;
  border-color: #94a3b8;
  background: #f1f5f9;
}

.toggle-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 4px;
  border: none;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  transition: transform 0.2s ease, color 0.15s ease;
}

.toggle-btn:hover {
  color: #0f172a;
  background: rgba(0, 0, 0, 0.05);
}

.toggle-btn.is-open {
  transform: rotate(180deg);
}

.thinking-body {
  padding: 10px 14px 14px 14px;
  border-top: 1px solid rgba(226, 232, 240, 0.8);
  max-height: 380px;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.65;
  color: #475569;
}

.thinking-content :deep(p) {
  margin: 0 0 8px 0;
}

.thinking-content :deep(p:last-child) {
  margin-bottom: 0;
}

.thinking-content :deep(pre) {
  background: #1e293b;
  color: #f8fafc;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  overflow-x: auto;
  margin: 6px 0;
}

.thinking-content :deep(code) {
  font-family: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
  font-size: 12px;
  background: rgba(148, 163, 184, 0.18);
  padding: 1px 4px;
  border-radius: 3px;
}

.streaming-cursor {
  display: inline-block;
  width: 6px;
  height: 14px;
  background-color: #3b82f6;
  margin-left: 4px;
  vertical-align: middle;
  animation: cursor-blink 1s infinite;
}

@keyframes cursor-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.animate-spin-slow {
  animation: spin 3s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.dot-flashing {
  display: inline-block;
  letter-spacing: 2px;
  animation: blink 1.4s infinite steps(1);
}

@keyframes blink {
  0%, 100% { opacity: 0.2; }
  50% { opacity: 1; }
}
</style>
