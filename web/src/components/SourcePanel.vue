<script setup lang="ts">
import { nextTick, ref } from 'vue'
import type { SourceDoc } from '../types'

defineProps<{
  sources: SourceDoc[]
}>()

const emit = defineEmits<{
  chunkFeedback: [chunkId: string, rating: 'down', reason?: string]
}>()

const panel = ref<HTMLElement | null>(null)
const highlightedId = ref<number | null>(null)
const dislikedChunkIds = ref<Set<string>>(new Set())
let highlightTimer = 0

async function focusCitation(citationId: number) {
  highlightedId.value = citationId
  await nextTick()
  panel.value
    ?.querySelector<HTMLElement>(`[data-source-id="${citationId}"]`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  window.clearTimeout(highlightTimer)
  highlightTimer = window.setTimeout(() => {
    if (highlightedId.value === citationId) highlightedId.value = null
  }, 1800)
}

function handleChunkDislike(chunkId?: string) {
  if (!chunkId) return
  if (dislikedChunkIds.value.has(chunkId)) return
  dislikedChunkIds.value.add(chunkId)
  emit('chunkFeedback', chunkId, 'down', '片段内容不准确或不匹配')
}

defineExpose({ focusCitation })

function categoryColor(cat?: string): string {
  const map: Record<string, string> = {
    text: '#5865f2',
    image: '#eaa238',
    video: '#ed4245',
  }
  return map[cat || ''] || '#8a8f99'
}
</script>

<template>
  <div ref="panel" class="panel">
    <div v-if="sources.length === 0" class="empty">暂无引用来源</div>

    <div
      v-for="(src, i) in sources"
      :key="src.metadata?.citation_id || i"
      class="item"
      :class="{ 'item--highlighted': highlightedId === (src.metadata?.citation_id || i + 1) }"
      :data-source-id="src.metadata?.citation_id || i + 1"
    >
      <div class="item-hd">
        <span class="idx">{{ src.metadata?.citation_id || i + 1 }}</span>
        <span
          class="tag"
          :style="{ background: categoryColor(src.metadata?.category) + '18', color: categoryColor(src.metadata?.category) }"
        >
          {{ src.metadata?.category || '未知' }}
        </span>
        <a
          v-if="src.metadata?.source_type === 'external' && src.metadata?.url"
          class="file external"
          :href="src.metadata.url"
          target="_blank"
          rel="noopener noreferrer"
          :title="src.metadata.url"
        >
          {{ src.metadata?.file_name || src.metadata?.source || '外部来源' }}
        </a>
        <span v-else class="file" :title="src.metadata?.file_name || src.metadata?.source">
          {{ src.metadata?.file_name || src.metadata?.source || '未知文件' }}
        </span>
        <span class="page">{{ src.metadata?.page_label || '无页码' }}</span>
        <button
          v-if="src.metadata?.chunk_id"
          type="button"
          class="chunk-feedback-btn"
          :class="{ active: dislikedChunkIds.has(src.metadata.chunk_id) }"
          title="对该片段点踩"
          @click.stop="handleChunkDislike(src.metadata.chunk_id)"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M17 14V2"/>
            <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22h0a3 3 0 0 1-3-3.88Z"/>
          </svg>
          <span class="feedback-text">{{ dislikedChunkIds.has(src.metadata.chunk_id) ? '已踩' : '点踩' }}</span>
        </button>
      </div>
      <p class="text">{{ src.content }}</p>
    </div>
  </div>
</template>

<style scoped>
.panel { padding: 0; }
.empty { color: #8a8f99; font-size: 13px; }
.item {
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 8px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
}
.item--highlighted {
  border-color: #7ca3e8;
  background: #f4f8ff;
  box-shadow: 0 0 0 3px rgba(51, 112, 255, 0.12);
}
.item-hd {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}
.idx {
  width: 18px;
  height: 18px;
  background: #f2f3f5;
  color: #4e5969;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
}
.tag {
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  flex-shrink: 0;
}
.file {
  font-size: 12px;
  font-weight: 600;
  color: #1d2129;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.file.external {
  color: #3370ff;
  text-decoration: none;
}
.file.external:hover {
  text-decoration: underline;
}
.page {
  font-size: 11px;
  color: #86909c;
  flex-shrink: 0;
}
.chunk-feedback-btn {
  margin-left: 4px;
  background: none;
  border: 1px solid #e5e6eb;
  cursor: pointer;
  color: #86909c;
  padding: 2px 6px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  transition: all 0.2s ease;
}
.chunk-feedback-btn:hover {
  color: #ed4245;
  border-color: #fca5a5;
  background: #fef2f2;
}
.chunk-feedback-btn.active {
  color: #ed4245;
  border-color: #f87171;
  background: #fee2e2;
}
.text {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: #4e5969;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
