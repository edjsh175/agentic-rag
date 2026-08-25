<script setup lang="ts">
import { ref } from 'vue'
import type { GraphNode, GraphEdge, EntityChunkDetail, GraphAliasItem } from '../../types'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import {
  entitySubtypeLabel,
  entityTypeLabel,
  relationTypeLabel,
  linkTypeLabel,
  getShortLabel,
  getLabelPrefix,
} from '../../utils/graphLabels'

const props = defineProps<{
  isOpen: boolean
  selectedNode: GraphNode | null
  selectedNodeProperties: Record<string, any>
  selectedNodeRelations: GraphEdge[]
  nodeMap: Map<string, GraphNode>
  colors: Record<string, string>
  isProductBackbonePreviewAny: boolean
  aliases: GraphAliasItem[]
  aliasesLoading: boolean
  aliasSaving: boolean
  evidenceChunks: EntityChunkDetail[]
  loadingChunks: boolean
  chunkLinkSaving: boolean
  progressiveReveal: boolean
  isNodeExpanded: (id: string) => boolean
  hiddenNeighborCount: (id: string) => number
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'open-edit'): void
  (e: 'initiate-delete'): void
  (e: 'focus-node', id: string): void
  (e: 'expand-node', id: string): void
  (e: 'collapse-node', id: string): void
  (e: 'add-alias', text: string): void
  (e: 'remove-alias', id: string): void
  (e: 'add-chunk-link', chunkId: string): void
  (e: 'unlink-chunk', chunkId: string): void
  (e: 'delete-relation', relId: string): void
}>()

const detailsTab = ref<'relations' | 'chunks'>('relations')
const aliasInput = ref('')
const chunkLinkInput = ref('')
const expandedChunkId = ref<string | null>(null)

function handleAddAlias() {
  const text = aliasInput.value.trim()
  if (!text) return
  emit('add-alias', text)
  aliasInput.value = ''
}

function handleAddChunkLink() {
  const chunkId = chunkLinkInput.value.trim()
  if (!chunkId) return
  emit('add-chunk-link', chunkId)
  chunkLinkInput.value = ''
}

function renderMarkdown(content?: string) {
  if (!content) return ''
  return DOMPurify.sanitize(marked.parse(content) as string)
}
</script>

<template>
  <aside
    class="kg-right-panel"
    :class="{ open: isOpen && selectedNode }"
  >
    <div v-if="selectedNode" class="panel-layout">
      <header class="right-header">
        <div class="header-main">
          <span
            class="type-badge"
            :style="{ backgroundColor: colors[selectedNode.type] || colors.Default }"
          >
            {{ entityTypeLabel(selectedNode.type) }}
          </span>
          <div class="header-actions">
            <button @click="emit('open-edit')" class="mini-btn primary-action" data-test="open-edit-entity">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
              </svg>
              编辑实体
            </button>
            <button @click="emit('close')" class="close-btn" title="关闭详情面板">&times;</button>
          </div>
        </div>
        <h2 :title="selectedNode.label">{{ getShortLabel(selectedNode.label) }}</h2>
        <div v-if="getLabelPrefix(selectedNode.label)" class="entity-detail-prefix" :title="selectedNode.label">
          路径: {{ getLabelPrefix(selectedNode.label) }}
        </div>
        <p class="category-meta" v-if="selectedNode.doc_category">
          分类: {{ selectedNode.doc_category }}
        </p>
      </header>

      <!-- 详细元数据属性 -->
      <div class="metadata-card" v-if="selectedNode.canonical_name || selectedNode.review_status || selectedNode.description || selectedNodeProperties.layer">
        <div class="meta-row" v-if="selectedNode.canonical_name">
          <span class="meta-label">规范名称:</span>
          <span class="meta-val">{{ selectedNode.canonical_name }}</span>
        </div>
        <div class="meta-row" v-if="selectedNode.review_status">
          <span class="meta-label">审核状态:</span>
          <span
            class="status-tag"
            :class="selectedNode.review_status"
          >
            {{ selectedNode.review_status === 'approved' ? '已审核' : (selectedNode.review_status === 'rejected' ? '已拒绝' : '待审核') }}
          </span>
        </div>
        <div class="meta-row" v-if="selectedNode.confidence !== undefined && selectedNode.confidence !== null">
          <span class="meta-label">置信度:</span>
          <span class="meta-val">{{ selectedNode.confidence.toFixed(2) }}</span>
        </div>
        <div class="meta-row description" v-if="selectedNode.description">
          <span class="meta-label">实体说明:</span>
          <p class="meta-text">{{ selectedNode.description }}</p>
        </div>
        <div class="meta-row" v-if="selectedNodeProperties.layer">
          <span class="meta-label">功能层:</span>
          <span class="meta-val">{{ selectedNodeProperties.layer }}</span>
        </div>
        <div class="meta-row" v-if="selectedNodeProperties.subtype">
          <span class="meta-label">实体子类型:</span>
          <span class="meta-val">{{ entitySubtypeLabel(selectedNodeProperties.subtype) }}</span>
        </div>
        <div class="meta-row" v-if="selectedNodeProperties.source">
          <span class="meta-label">来源:</span>
          <span class="meta-val">{{ selectedNodeProperties.source }}</span>
        </div>
        <div class="meta-row" v-if="selectedNodeProperties.status">
          <span class="meta-label">整理状态:</span>
          <span class="meta-val">{{ selectedNodeProperties.status }}</span>
        </div>
        <div class="meta-row" v-if="selectedNodeProperties.alias_candidates?.length">
          <span class="meta-label">别名候选:</span>
          <span class="meta-val">{{ selectedNodeProperties.alias_candidates.join('、') }}</span>
        </div>
      </div>

      <!-- 别名卡片 -->
      <div class="metadata-card alias-card">
        <div class="alias-head">
          <span class="meta-label font-bold">Aliases</span>
          <span class="meta-val-sub" v-if="aliasesLoading">加载中...</span>
        </div>
        <div v-if="!isProductBackbonePreviewAny" class="alias-create-row">
          <input
            v-model="aliasInput"
            class="filter-input compact-input"
            placeholder="新增 alias"
            data-test="alias-input"
            @keyup.enter="handleAddAlias"
          />
          <button class="mini-btn primary-action" :disabled="aliasSaving" @click="handleAddAlias" data-test="add-alias">
            添加
          </button>
        </div>
        <ul class="alias-list">
          <li v-for="alias in aliases" :key="alias.id" class="alias-item">
            <span>{{ alias.alias }}</span>
            <button v-if="!isProductBackbonePreviewAny" class="delete-text-btn" @click="emit('remove-alias', alias.id)">删除</button>
          </li>
          <li v-if="!aliasesLoading && aliases.length === 0" class="empty-inline">暂无 alias</li>
        </ul>
      </div>

      <!-- 详情 Tab 菜单 -->
      <nav class="detail-tabs">
        <button
          :class="{ active: detailsTab === 'relations' }"
          @click="detailsTab = 'relations'"
        >
          关系连接 ({{ selectedNodeRelations.length }})
        </button>
        <button
          :class="{ active: detailsTab === 'chunks' }"
          @click="detailsTab = 'chunks'"
        >
          原文证据
        </button>
      </nav>

      <!-- Tab 内容容器 -->
      <div class="tab-content scrollable">
        <!-- 关系连线展示 -->
        <div v-if="detailsTab === 'relations'" class="tab-pane">
          <div
            v-if="progressiveReveal && selectedNode"
            class="progressive-actions"
            data-test="progressive-actions"
          >
            <button
              type="button"
              class="mini-btn full-width"
              data-test="expand-neighbors"
              :disabled="!isNodeExpanded(selectedNode.id) && hiddenNeighborCount(selectedNode.id) === 0"
              @click="isNodeExpanded(selectedNode.id) ? emit('collapse-node', selectedNode.id) : emit('expand-node', selectedNode.id)"
            >
              {{
                isNodeExpanded(selectedNode.id)
                  ? '收起邻居'
                  : `展开邻居${hiddenNeighborCount(selectedNode.id) ? ` (+${hiddenNeighborCount(selectedNode.id)})` : ''}`
              }}
            </button>
          </div>
          <ul class="relation-ul">
            <li
              v-for="rel in selectedNodeRelations"
              :key="rel.id"
              class="relation-li"
            >
              <div class="relation-path">
                <span
                  class="node-link"
                  @click="emit('focus-node', rel.source)"
                  :class="{ current: rel.source === selectedNode.id }"
                  :title="rel.source === selectedNode.id ? '当前实体' : '源端实体'"
                >
                  {{ nodeMap.get(rel.source)?.label || rel.source }}
                </span>
                <span class="rel-arrow">
                  ── <strong>{{ relationTypeLabel(rel.label) }}</strong> ──&gt;
                </span>
                <span
                  class="node-link"
                  @click="emit('focus-node', rel.target)"
                  :class="{ current: rel.target === selectedNode.id }"
                  :title="rel.target === selectedNode.id ? '当前实体' : '目标实体'"
                >
                  {{ nodeMap.get(rel.target)?.label || rel.target }}
                </span>
              </div>
              <div class="relation-footer">
                <span class="rel-meta" v-if="rel.evidence_text">
                  证据: "{{ rel.evidence_text.substring(0, 25) }}..."
                </span>
                <button
                  @click="emit('delete-relation', rel.id)"
                  class="delete-text-btn"
                >
                  删除边
                </button>
              </div>
            </li>
            <li v-if="selectedNodeRelations.length === 0" class="empty-tab">
              当前实体无任何关系连接。
            </li>
          </ul>
        </div>

        <!-- 证据 Chunk 展示 -->
        <div v-else class="tab-pane">
          <div v-if="loadingChunks" class="tab-loader">
            <div class="loader-sm"></div>
            <span>加载证据片段...</span>
          </div>

          <div v-else class="chunks-container">
            <div v-if="!isProductBackbonePreviewAny" class="chunk-link-form">
              <input
                v-model="chunkLinkInput"
                class="filter-input compact-input"
                placeholder="输入 chunk_id 建立证据关联"
                data-test="chunk-link-input"
                @keyup.enter="handleAddChunkLink"
              />
              <button class="mini-btn primary-action" :disabled="chunkLinkSaving" @click="handleAddChunkLink" data-test="add-chunk-link">
                关联
              </button>
            </div>
            <div
              v-for="chunk in evidenceChunks"
              :key="chunk.chunk_id"
              class="chunk-card"
            >
              <header class="chunk-card-header">
                <div class="chunk-title-sec">
                  <h4>{{ chunk.file_name }}</h4>
                  <p class="chunk-path" v-if="chunk.section_title">
                    章节: {{ chunk.section_title }}
                  </p>
                </div>
                <span class="chunk-type">{{ linkTypeLabel(chunk.link_type) }}</span>
              </header>

              <div class="chunk-preview-text">
                {{ chunk.content_preview }}
              </div>

              <div
                v-if="expandedChunkId === chunk.chunk_id"
                class="chunk-markdown markdown-body"
                v-html="renderMarkdown(chunk.content)"
              ></div>

              <footer class="chunk-card-footer">
                <button
                  @click="expandedChunkId = expandedChunkId === chunk.chunk_id ? null : chunk.chunk_id"
                  class="action-text-btn"
                >
                  {{ expandedChunkId === chunk.chunk_id ? '收起原文' : '查看完整原文' }}
                </button>
                <button
                  v-if="!isProductBackbonePreviewAny"
                  @click="emit('unlink-chunk', chunk.chunk_id)"
                  class="delete-text-btn"
                >
                  解除关联
                </button>
              </footer>
            </div>
            <div v-if="evidenceChunks.length === 0" class="empty-tab">
              当前实体未建立与任何 Chunk 的证据关联。
            </div>
          </div>
        </div>
      </div>

      <!-- 删除实体底部操作栏 -->
      <footer class="panel-footer">
        <button @click="emit('initiate-delete')" class="danger-btn">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
          </svg>
          删除当前实体
        </button>
      </footer>
    </div>
  </aside>
</template>

<style scoped>
.kg-right-panel {
  width: 320px;
  background: #ffffff;
  border-left: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1), width 0.25s ease;
  overflow: hidden;
  z-index: 10;
  transform: translateX(100%);
}

.kg-right-panel.open {
  transform: translateX(0);
}

.panel-layout {
  width: 320px;
  height: 100%;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}

.right-header {
  padding: 16px;
  border-bottom: 1px solid #f1f5f9;
}

.header-main {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.type-badge {
  font-size: 11px;
  color: #ffffff;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 600;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.mini-btn {
  height: 24px;
  padding: 0 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  border: 1px solid #cbd5e1;
  background: #ffffff;
  color: #475569;
  transition: all 0.15s;
}

.mini-btn:hover {
  background: #f8fafc;
  color: #1e293b;
}

.mini-btn.primary-action {
  background: #eff6ff;
  color: #3370ff;
  border-color: #bfdbfe;
}

.mini-btn.primary-action:hover {
  background: #dbeafe;
}

.close-btn {
  border: none;
  background: transparent;
  color: #94a3b8;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  padding: 0 4px;
}

.close-btn:hover {
  color: #1e293b;
}

.right-header h2 {
  font-size: 16px;
  font-weight: 600;
  color: #1e293b;
  margin: 0 0 4px;
  line-height: 1.4;
  word-break: break-all;
}

.entity-detail-prefix {
  font-size: 11px;
  color: #94a3b8;
  margin-bottom: 4px;
  word-break: break-all;
}

.category-meta {
  font-size: 11px;
  color: #64748b;
  margin: 0;
}

.metadata-card {
  margin: 12px 16px 0;
  padding: 10px 12px;
  background: #f8fafc;
  border: 1px solid #eef2f6;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.meta-row {
  display: flex;
  font-size: 11px;
  line-height: 1.5;
}

.meta-row.description {
  flex-direction: column;
}

.meta-label {
  color: #64748b;
  width: 72px;
  flex-shrink: 0;
}

.meta-val {
  color: #1e293b;
  word-break: break-all;
}

.status-tag {
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 600;
}

.status-tag.approved {
  background: #dcfce7;
  color: #15803d;
}

.status-tag.rejected {
  background: #fee2e2;
  color: #b91c1c;
}

.meta-text {
  margin: 2px 0 0;
  color: #334155;
  white-space: pre-wrap;
}

.alias-card {
  gap: 8px;
}

.alias-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.font-bold {
  font-weight: 600;
}

.meta-val-sub {
  font-size: 10px;
  color: #94a3b8;
}

.alias-create-row {
  display: flex;
  gap: 6px;
}

.compact-input {
  flex: 1;
  height: 26px;
  padding: 0 6px;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  font-size: 11px;
  outline: none;
}

.alias-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.alias-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
  color: #334155;
}

.delete-text-btn {
  border: none;
  background: transparent;
  color: #ef4444;
  font-size: 10px;
  cursor: pointer;
  padding: 0;
}

.delete-text-btn:hover {
  text-decoration: underline;
}

.empty-inline {
  font-size: 11px;
  color: #94a3b8;
}

.detail-tabs {
  display: flex;
  border-bottom: 1px solid #e2e8f0;
  padding: 0 16px;
  margin-top: 12px;
}

.detail-tabs button {
  border: none;
  background: transparent;
  padding: 8px 12px;
  font-size: 12px;
  font-weight: 500;
  color: #64748b;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.15s;
}

.detail-tabs button.active {
  color: #3370ff;
  border-bottom-color: #3370ff;
  font-weight: 600;
}

.tab-content {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
}

.tab-pane {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.progressive-actions {
  margin-bottom: 6px;
}

.full-width {
  width: 100%;
  justify-content: center;
}

.relation-ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.relation-li {
  padding: 8px 10px;
  background: #f8fafc;
  border: 1px solid #eef2f6;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.relation-path {
  font-size: 11px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}

.node-link {
  color: #3370ff;
  cursor: pointer;
  text-decoration: none;
}

.node-link:hover {
  text-decoration: underline;
}

.node-link.current {
  color: #1e293b;
  font-weight: 600;
}

.rel-arrow {
  color: #94a3b8;
  font-size: 10px;
}

.rel-arrow strong {
  color: #475569;
}

.relation-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 2px;
}

.rel-meta {
  font-size: 10px;
  color: #94a3b8;
}

.empty-tab {
  font-size: 12px;
  color: #94a3b8;
  text-align: center;
  padding: 24px 0;
}

.chunks-container {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.chunk-link-form {
  display: flex;
  gap: 6px;
  margin-bottom: 4px;
}

.chunk-card {
  padding: 10px;
  background: #f8fafc;
  border: 1px solid #eef2f6;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.chunk-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 6px;
}

.chunk-title-sec h4 {
  font-size: 12px;
  font-weight: 600;
  color: #1e293b;
  margin: 0;
  word-break: break-all;
}

.chunk-path {
  font-size: 10px;
  color: #94a3b8;
  margin: 2px 0 0;
}

.chunk-type {
  font-size: 10px;
  background: #e2e8f0;
  color: #475569;
  padding: 1px 5px;
  border-radius: 3px;
  flex-shrink: 0;
}

.chunk-preview-text {
  font-size: 11px;
  color: #475569;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.chunk-markdown {
  font-size: 11px;
  background: #ffffff;
  padding: 8px;
  border-radius: 4px;
  border: 1px solid #e2e8f0;
  max-height: 200px;
  overflow-y: auto;
}

.chunk-card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 4px;
}

.action-text-btn {
  border: none;
  background: transparent;
  color: #3370ff;
  font-size: 11px;
  cursor: pointer;
  padding: 0;
}

.action-text-btn:hover {
  text-decoration: underline;
}

.tab-loader {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 20px 0;
  color: #94a3b8;
  font-size: 11px;
}

.loader-sm {
  width: 12px;
  height: 12px;
  border: 2px solid #cbd5e1;
  border-top-color: #3370ff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.panel-footer {
  padding: 12px 16px;
  border-top: 1px solid #f1f5f9;
  background: #f8fafc;
}

.danger-btn {
  width: 100%;
  height: 32px;
  border-radius: 6px;
  border: 1px solid #fecaca;
  background: #fef2f2;
  color: #dc2626;
  font-size: 12px;
  font-weight: 500;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
  transition: all 0.15s;
}

.danger-btn:hover {
  background: #fee2e2;
  border-color: #fca5a5;
}
</style>
