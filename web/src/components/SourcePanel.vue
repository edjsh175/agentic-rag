<script setup lang="ts">
import type { SourceDoc } from '../types'

defineProps<{
  sources: SourceDoc[]
}>()

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
  <div class="panel">
    <div v-if="sources.length === 0" class="empty">暂无引用来源</div>

    <div v-for="(src, i) in sources" :key="i" class="item">
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
  border-radius: 4px;
  background: #f7f8fa;
  color: #6b7280;
  font-size: 10px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.tag {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 500;
}
.file {
  font-size: 12px;
  color: #6b7280;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.page {
  color: #8a8f99;
  font-size: 11px;
  white-space: nowrap;
}
.external { color: #2563eb; text-decoration: none; }
.external:hover { text-decoration: underline; }
.text {
  font-size: 13px;
  color: #4b5563;
  line-height: 1.6;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
