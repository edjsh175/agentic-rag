<script setup lang="ts">
import { ref, computed } from 'vue'
import type { PipelineStep, EvidencePack, EvidenceItem } from '../types'

const props = defineProps<{
  pipelineSteps?: PipelineStep[]
  evidencePack?: EvidencePack
}>()

const emit = defineEmits<{
  pinChunk: [chunkId: string, item: EvidenceItem]
  excludeChunk: [chunkId: string, item: EvidenceItem]
}>()

const expanded = ref(false)
const activeTab = ref<'pipeline' | 'candidates' | 'uncited'>('uncited')
const previewItem = ref<EvidenceItem | null>(null)

// 提取查询改写词
const queries = computed(() => {
  const step = props.pipelineSteps?.find(s => s.stage === 'queries')
  return step?.plan?.queries || []
})

// 提取全量候选
const candidates = computed(() => {
  const step = props.pipelineSteps?.find(s => s.stage === 'retrieve')
  return step?.retrieval?.candidates || []
})

// 提取采纳的证据
const citedItems = computed(() => {
  return props.evidencePack?.cited || []
})

// 提取落选/未采纳证据
const uncitedItems = computed(() => {
  return props.evidencePack?.retrieved_uncited || []
})

function formatDropReason(reason?: string) {
  if (!reason) return '已检索未引用'
  if (reason === 'budget_trim') return '上下文长度超限被裁切'
  if (reason === 'not_cited') return '作为上下文输入，但未在回答中显式引用'
  if (reason === 'rerank_filtered') return '重排得分较低被过滤'
  return reason
}

function handlePin(item: EvidenceItem) {
  if (item.chunk_id) {
    emit('pinChunk', item.chunk_id, item)
  }
}

function handleExclude(item: EvidenceItem) {
  if (item.chunk_id) {
    emit('excludeChunk', item.chunk_id, item)
  }
}
</script>

<template>
  <div class="evidence-debug-panel" :class="{ expanded }">
    <button type="button" class="toggle-btn" @click="expanded = !expanded">
      <svg class="icon" :class="{ rotated: expanded }" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="9 18 15 12 9 6"/>
      </svg>
      <span class="btn-text">问答证据与阶段调试</span>
      <span v-if="uncitedItems.length" class="badge badge-warning">
        {{ uncitedItems.length }} 条落选
      </span>
      <span v-if="citedItems.length" class="badge badge-success">
        {{ citedItems.length }} 条采纳
      </span>
    </button>

    <div v-if="expanded" class="panel-content">
      <!-- 选项卡导航 -->
      <div class="tab-header">
        <button
          type="button"
          class="tab-item"
          :class="{ active: activeTab === 'uncited' }"
          @click="activeTab = 'uncited'"
        >
          落选 Chunk ({{ uncitedItems.length }})
        </button>
        <button
          type="button"
          class="tab-item"
          :class="{ active: activeTab === 'candidates' }"
          @click="activeTab = 'candidates'"
        >
          已采纳 Chunk ({{ citedItems.length }})
        </button>
        <button
          type="button"
          class="tab-item"
          :class="{ active: activeTab === 'pipeline' }"
          @click="activeTab = 'pipeline'"
        >
          流水线轨迹
        </button>
      </div>

      <!-- Tab 1: 落选 Chunk 列表 -->
      <div v-if="activeTab === 'uncited'" class="tab-pane">
        <div v-if="!uncitedItems.length" class="empty-tip">
          没有落选或被裁切的 Chunk。
        </div>
        <div v-else class="item-list">
          <div v-for="(item, idx) in uncitedItems" :key="item.chunk_id || idx" class="chunk-card chunk-card--uncited">
            <div class="card-header">
              <span class="file-name" :title="item.document">
                {{ item.document || '未知文档' }}
              </span>
              <span class="reason-tag">
                {{ formatDropReason(item.drop_reason) }}
              </span>
            </div>
            <div class="card-body">
              <p class="snippet">{{ item.snippet }}</p>
            </div>
            <div class="card-footer">
              <button
                type="button"
                class="action-btn btn-pin"
                title="在下一轮对话中强制将此 Chunk 放入 AI 上下文"
                @click="handlePin(item)"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="12" y1="5" x2="12" y2="19"/>
                  <line x1="5" y1="12" x2="19" y2="12"/>
                </svg>
                + 引入下轮参考
              </button>
              <button
                type="button"
                class="action-btn btn-preview"
                @click="previewItem = item"
              >
                预览片段
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Tab 2: 已采纳 Chunk 列表 -->
      <div v-if="activeTab === 'candidates'" class="tab-pane">
        <div v-if="!citedItems.length" class="empty-tip">
          暂无显式被引用的 Chunk。
        </div>
        <div v-else class="item-list">
          <div v-for="(item, idx) in citedItems" :key="item.chunk_id || idx" class="chunk-card chunk-card--cited">
            <div class="card-header">
              <span class="file-name" :title="item.document">
                [引用 {{ item.index || idx + 1 }}] {{ item.document || '未知文档' }}
              </span>
            </div>
            <div class="card-body">
              <p class="snippet">{{ item.snippet }}</p>
            </div>
            <div class="card-footer">
              <button
                type="button"
                class="action-btn btn-exclude"
                title="在下一轮对话中忽略并排除此 Chunk"
                @click="handleExclude(item)"
              >
                - 下轮忽略此段
              </button>
              <button
                type="button"
                class="action-btn btn-preview"
                @click="previewItem = item"
              >
                预览片段
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Tab 3: 流水线轨迹 -->
      <div v-if="activeTab === 'pipeline'" class="tab-pane">
        <div class="pipeline-flow">
          <div class="flow-step">
            <div class="step-title">1. 问题查询改写</div>
            <div v-if="queries.length" class="query-tags">
              <span v-for="(q, idx) in queries" :key="idx" class="q-tag">
                {{ typeof q === 'string' ? q : (q.query || q.text) }}
              </span>
            </div>
            <div v-else class="step-desc">未进行改写或仅使用原始问题</div>
          </div>

          <div class="flow-step">
            <div class="step-title">2. 向量/混合检索</div>
            <div class="step-desc">
              检索到 <strong>{{ candidates.length }}</strong> 条初始候选片段
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 预览弹窗/抽屉 -->
    <div v-if="previewItem" class="preview-modal-backdrop" @click="previewItem = null">
      <div class="preview-modal" @click.stop>
        <div class="modal-header">
          <span>Chunk 片段预览</span>
          <button type="button" class="close-btn" @click="previewItem = null">✕</button>
        </div>
        <div class="modal-body">
          <div class="meta-row"><strong>文档:</strong> {{ previewItem.document }}</div>
          <div class="meta-row" v-if="previewItem.chunk_id"><strong>Chunk ID:</strong> {{ previewItem.chunk_id }}</div>
          <div class="meta-row" v-if="previewItem.drop_reason"><strong>状态说明:</strong> {{ formatDropReason(previewItem.drop_reason) }}</div>
          <hr />
          <div class="content-preview">{{ previewItem.snippet }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.evidence-debug-panel {
  margin-top: 8px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 8px;
  background: var(--color-bg-secondary, #f8fafc);
  font-size: 13px;
  overflow: hidden;
}

.toggle-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 8px 12px;
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--color-text-secondary, #64748b);
  font-weight: 500;
  transition: background 0.2s;
}

.toggle-btn:hover {
  background: rgba(0, 0, 0, 0.03);
}

.icon {
  transition: transform 0.2s;
}
.icon.rotated {
  transform: rotate(90deg);
}

.badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 10px;
  margin-left: 4px;
}
.badge-warning {
  background: #fef3c7;
  color: #d97706;
}
.badge-success {
  background: #d1fae5;
  color: #059669;
}

.panel-content {
  border-top: 1px solid var(--color-border, #e2e8f0);
  padding: 10px 12px;
}

.tab-header {
  display: flex;
  gap: 8px;
  border-bottom: 1px solid #e2e8f0;
  padding-bottom: 6px;
  margin-bottom: 10px;
}

.tab-item {
  padding: 4px 10px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: #64748b;
  border-radius: 4px;
  font-size: 12px;
}
.tab-item.active {
  background: #3b82f6;
  color: #ffffff;
  font-weight: 600;
}

.item-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 320px;
  overflow-y: auto;
}

.chunk-card {
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #ffffff;
  padding: 8px 10px;
}

.chunk-card--uncited {
  border-left: 3px solid #f59e0b;
}

.chunk-card--cited {
  border-left: 3px solid #10b981;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  font-size: 12px;
  margin-bottom: 4px;
}

.file-name {
  max-width: 70%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reason-tag {
  font-size: 11px;
  color: #d97706;
  background: #fffbe completed;
}

.snippet {
  font-size: 12px;
  color: #334155;
  line-height: 1.4;
  margin: 4px 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 6px;
}

.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
  border: 1px solid #cbd5e1;
  background: #f8fafc;
  color: #334155;
}

.action-btn:hover {
  background: #f1f5f9;
}

.btn-pin {
  border-color: #93c5fd;
  color: #1d4ed8;
  background: #eff6ff;
}

.btn-pin:hover {
  background: #dbeafe;
}

.btn-exclude {
  border-color: #fca5a5;
  color: #dc2626;
  background: #fef2f2;
}

.btn-exclude:hover {
  background: #fee2e2;
}

.empty-tip {
  color: #94a3b8;
  font-size: 12px;
  padding: 12px 0;
  text-align: center;
}

.pipeline-flow {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.flow-step {
  padding: 6px 8px;
  background: #ffffff;
  border-radius: 4px;
  border: 1px solid #e2e8f0;
}

.step-title {
  font-weight: 600;
  font-size: 12px;
  color: #1e293b;
}

.query-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}

.q-tag {
  background: #e0f2fe;
  color: #0369a1;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
}

.preview-modal-backdrop {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.preview-modal {
  background: #ffffff;
  width: 90%;
  max-width: 600px;
  max-height: 80vh;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
}

.modal-header {
  padding: 12px 16px;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  justify-content: space-between;
  font-weight: 600;
}

.modal-body {
  padding: 16px;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.5;
}

.close-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  font-size: 16px;
}
</style>
