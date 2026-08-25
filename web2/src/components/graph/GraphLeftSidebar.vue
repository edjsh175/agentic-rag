<script setup lang="ts">
import { ref } from 'vue'
import type { GraphNode } from '../../types'
import { DOC_CATEGORIES } from '../../types'
import {
  docCategoryLabel,
  getShortLabel,
  getLabelPrefix,
} from '../../utils/graphLabels'

const isTypeFilterExpanded = ref(true)

const props = defineProps<{
  isOpen: boolean
  graphMode: 'product' | 'document'
  isProductBackbonePreviewAny: boolean
  searchQuery: string
  selectedCategory: string
  documentSelectedCategory: string
  availableCategories: string[]
  documentAvailableCategories: string[]
  availableTypes: string[]
  selectedTypes: Record<string, boolean>
  documentSelectedTypes: Record<string, boolean>
  linkClassBackbone: boolean
  linkClassExtraction: boolean
  docLinkClassStructure: boolean
  docLinkClassExtraction: boolean
  docLinkClassAssociation: boolean
  filteredNodesList: GraphNode[]
  selectedNodeId: string | null
  colors: Record<string, string>
  getTypeCount: (type: string) => number
  filterTypeLabelLocal: (type: string) => string
  filterTypeDotColor: (type: string) => string
  nodeListBadge: (node: GraphNode) => { text: string; color: string }
}>()

const emit = defineEmits<{
  (e: 'update:searchQuery', val: string): void
  (e: 'update:selectedCategory', val: string): void
  (e: 'update:documentSelectedCategory', val: string): void
  (e: 'update:linkClassBackbone', val: boolean): void
  (e: 'update:linkClassExtraction', val: boolean): void
  (e: 'update:docLinkClassStructure', val: boolean): void
  (e: 'update:docLinkClassExtraction', val: boolean): void
  (e: 'update:docLinkClassAssociation', val: boolean): void
  (e: 'select-all-types'): void
  (e: 'clear-all-types'): void
  (e: 'select-node', id: string): void
}>()
</script>

<template>
  <aside class="kg-left-panel" :class="{ collapsed: !isOpen }">
    <div class="panel-inner" v-show="isOpen">
      <header class="left-header">
        <h2>实体过滤筛选</h2>
      </header>

      <!-- 搜索栏 -->
      <div class="search-box">
        <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"></circle>
          <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
        </svg>
        <input
          :value="searchQuery"
          @input="emit('update:searchQuery', ($event.target as HTMLInputElement).value)"
          type="text"
          placeholder="搜索实体名称..."
          class="search-input"
        />
        <button
          v-if="searchQuery"
          @click="emit('update:searchQuery', '')"
          class="clear-search"
        >
          &times;
        </button>
      </div>

      <!-- 分类筛选器 -->
      <div class="filter-section">
        <label class="section-label">所属分类</label>
        <select
          v-if="graphMode === 'product'"
          :value="selectedCategory"
          @change="emit('update:selectedCategory', ($event.target as HTMLSelectElement).value)"
          class="filter-select"
        >
          <option value="all">全部分类</option>
          <option
            v-for="cat in availableCategories"
            :key="cat"
            :value="cat"
          >
            {{ docCategoryLabel(cat) }}
          </option>
        </select>
        <select
          v-else
          :value="documentSelectedCategory"
          @change="emit('update:documentSelectedCategory', ($event.target as HTMLSelectElement).value)"
          class="filter-select"
        >
          <option value="all">全部分类</option>
          <option
            v-for="cat in documentAvailableCategories"
            :key="cat"
            :value="cat"
          >
            {{ docCategoryLabel(cat) }}
          </option>
        </select>
      </div>

      <!-- 实体类型多选过滤（流式胶囊标签） -->
      <div class="filter-section types-section">
        <div class="flex-between section-header">
          <div class="section-title-with-toggle" @click="isTypeFilterExpanded = !isTypeFilterExpanded">
            <span class="section-label">实体类型</span>
            <svg
              class="collapse-arrow"
              :class="{ collapsed: !isTypeFilterExpanded }"
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.5"
            >
              <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
          </div>
          <div class="quick-actions" v-if="isTypeFilterExpanded">
            <button @click.stop="emit('select-all-types')" class="text-btn">全选</button>
            <span class="divider">/</span>
            <button @click.stop="emit('clear-all-types')" class="text-btn">清空</button>
          </div>
        </div>

        <!-- 业务链路维度筛选 -->
        <div class="link-class-filter-box" data-test="link-class-filter-box" v-if="isTypeFilterExpanded">
          <span class="link-class-title">数据来源:</span>
          <div class="link-class-tags" v-if="graphMode === 'product'">
            <label class="link-tag-pill" :class="{ active: linkClassBackbone }">
              <input
                type="checkbox"
                class="hidden-checkbox"
                :checked="linkClassBackbone"
                @change="emit('update:linkClassBackbone', ($event.target as HTMLInputElement).checked)"
                data-test="link-class-backbone"
              />
              <span>主干架构</span>
            </label>
            <label class="link-tag-pill" :class="{ active: linkClassExtraction }">
              <input
                type="checkbox"
                class="hidden-checkbox"
                :checked="linkClassExtraction"
                @change="emit('update:linkClassExtraction', ($event.target as HTMLInputElement).checked)"
                data-test="link-class-extraction"
              />
              <span>业务抽取</span>
            </label>
          </div>
          <div class="link-class-tags" v-else>
            <label class="link-tag-pill" :class="{ active: docLinkClassStructure }">
              <input
                type="checkbox"
                class="hidden-checkbox"
                :checked="docLinkClassStructure"
                @change="emit('update:docLinkClassStructure', ($event.target as HTMLInputElement).checked)"
                data-test="link-class-structure"
              />
              <span>文档结构</span>
            </label>
            <label class="link-tag-pill" :class="{ active: docLinkClassExtraction }">
              <input
                type="checkbox"
                class="hidden-checkbox"
                :checked="docLinkClassExtraction"
                @change="emit('update:docLinkClassExtraction', ($event.target as HTMLInputElement).checked)"
                data-test="link-class-extraction-doc"
              />
              <span>抽取知识</span>
            </label>
            <label class="link-tag-pill" :class="{ active: docLinkClassAssociation }">
              <input
                type="checkbox"
                class="hidden-checkbox"
                :checked="docLinkClassAssociation"
                @change="emit('update:docLinkClassAssociation', ($event.target as HTMLInputElement).checked)"
                data-test="link-class-association"
              />
              <span>关联实体</span>
            </label>
          </div>
        </div>

        <!-- 胶囊 Pill 标签流式容器（无需内部滚动条） -->
        <div v-show="isTypeFilterExpanded" class="type-pill-wrap" v-if="graphMode === 'product'">
          <button
            v-for="type in availableTypes"
            :key="type"
            type="button"
            class="type-pill-btn"
            :class="{ active: selectedTypes[type] }"
            :title="`${filterTypeLabelLocal(type)} (${getTypeCount(type) || 0})`"
            @click="selectedTypes[type] = !selectedTypes[type]"
          >
            <span
              class="type-dot"
              :style="{ backgroundColor: filterTypeDotColor(type) }"
            ></span>
            <span class="type-pill-name">{{ filterTypeLabelLocal(type) }}</span>
            <span class="type-pill-count" v-if="getTypeCount(type)">{{ getTypeCount(type) }}</span>
          </button>
        </div>
        <div v-show="isTypeFilterExpanded" class="type-pill-wrap" v-else>
          <button
            v-for="type in availableTypes"
            :key="`doc-${type}`"
            type="button"
            class="type-pill-btn"
            :class="{ active: documentSelectedTypes[type] }"
            :title="`${filterTypeLabelLocal(type)} (${getTypeCount(type) || 0})`"
            @click="documentSelectedTypes[type] = !documentSelectedTypes[type]"
          >
            <span
              class="type-dot"
              :style="{ backgroundColor: filterTypeDotColor(type) }"
            ></span>
            <span class="type-pill-name">{{ filterTypeLabelLocal(type) }}</span>
            <span class="type-pill-count" v-if="getTypeCount(type)">{{ getTypeCount(type) }}</span>
          </button>
        </div>
      </div>

      <!-- 实体列表展示 -->
      <div class="entity-list-section">
        <div class="flex-between entity-list-header">
          <label>筛选结果 ({{ filteredNodesList.length }} 个实体)</label>
        </div>
        <ul class="entity-ul">
          <li
            v-for="node in filteredNodesList"
            :key="node.id"
            class="entity-li"
            :class="{ active: selectedNodeId === node.id }"
            @click="emit('select-node', node.id)"
          >
            <span
              class="type-tag"
              :style="{ backgroundColor: nodeListBadge(node).color }"
            >
              {{ nodeListBadge(node).text }}
            </span>
            <div class="entity-info-col">
              <span class="entity-name" :title="node.label">{{ getShortLabel(node.label) }}</span>
              <span v-if="getLabelPrefix(node.label)" class="entity-prefix-label" :title="node.label">
                {{ getLabelPrefix(node.label) }}
              </span>
            </div>
          </li>
          <li v-if="filteredNodesList.length === 0" class="empty-list">
            无匹配的实体
          </li>
        </ul>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.kg-left-panel {
  width: 280px;
  background: #ffffff;
  border-right: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  transition: width 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  z-index: 10;
}

.kg-left-panel.collapsed {
  width: 0;
  border-right: none;
}

.panel-inner {
  width: 280px;
  height: 100%;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}

.left-header {
  padding: 14px 16px;
  border-bottom: 1px solid #f1f5f9;
}

.left-header h2 {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
  margin: 0;
}

.search-box {
  position: relative;
  padding: 10px 14px;
  border-bottom: 1px solid #f8fafc;
}

.search-icon {
  position: absolute;
  left: 22px;
  top: 50%;
  transform: translateY(-50%);
  color: #94a3b8;
  pointer-events: none;
}

.search-input {
  width: 100%;
  height: 32px;
  padding: 0 28px 0 28px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-size: 12px;
  color: #1e293b;
  background: #f8fafc;
  outline: none;
  box-sizing: border-box;
  transition: all 0.15s;
}

.search-input:focus {
  border-color: #3370ff;
  background: #ffffff;
  box-shadow: 0 0 0 2px rgba(51, 112, 255, 0.12);
}

.clear-search {
  position: absolute;
  right: 22px;
  top: 50%;
  transform: translateY(-50%);
  border: none;
  background: transparent;
  color: #94a3b8;
  font-size: 16px;
  cursor: pointer;
}

.filter-section {
  padding: 10px 14px;
  border-bottom: 1px solid #f1f5f9;
}

.section-label {
  display: block;
  font-size: 11px;
  font-weight: 600;
  color: #64748b;
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.filter-select {
  width: 100%;
  height: 30px;
  padding: 0 8px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  font-size: 12px;
  color: #334155;
  background: #ffffff;
  outline: none;
  box-sizing: border-box;
}

.types-section {
  display: flex;
  flex-direction: column;
}

.flex-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.section-header {
  margin-bottom: 6px;
}

.section-title-with-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  user-select: none;
}

.section-title-with-toggle:hover .section-label {
  color: #3370ff;
}

.collapse-arrow {
  color: #94a3b8;
  transition: transform 0.2s ease;
}

.collapse-arrow.collapsed {
  transform: rotate(-90deg);
}

.quick-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.text-btn {
  border: none;
  background: transparent;
  color: #3370ff;
  font-size: 11px;
  cursor: pointer;
  padding: 0;
}

.text-btn:hover {
  text-decoration: underline;
}

.divider {
  color: #cbd5e1;
  font-size: 11px;
}

.link-class-filter-box {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding: 4px 6px;
  background: #f8fafc;
  border-radius: 6px;
  border: 1px solid #eef2f6;
}

.link-class-title {
  font-size: 10px;
  font-weight: 600;
  color: #64748b;
  flex-shrink: 0;
}

.link-class-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.link-tag-pill {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 11px;
  color: #64748b;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  cursor: pointer;
  user-select: none;
  transition: all 0.12s ease;
}

.link-tag-pill:hover {
  border-color: #cbd5e1;
  color: #334155;
}

.link-tag-pill.active {
  background: #eff6ff;
  border-color: #93c5fd;
  color: #2563eb;
  font-weight: 500;
}

.hidden-checkbox {
  display: none;
}

/* 实体类型流式胶囊药丸 (Pill Tags) */
.type-pill-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 2px 0 4px 0;
}

.type-pill-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 7px;
  border-radius: 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  color: #94a3b8;
  cursor: pointer;
  user-select: none;
  font-size: 11px;
  transition: all 0.12s ease;
  line-height: 1.2;
}

.type-pill-btn:hover {
  background: #f1f5f9;
  border-color: #cbd5e1;
  color: #475569;
}

.type-pill-btn.active {
  background: #ffffff;
  border-color: #cbd5e1;
  color: #1e293b;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}

.type-pill-btn.active:hover {
  border-color: #94a3b8;
}

.type-dot {
  width: 6.5px;
  height: 6.5px;
  border-radius: 50%;
  flex-shrink: 0;
  opacity: 0.45;
  transition: opacity 0.12s ease;
}

.type-pill-btn.active .type-dot {
  opacity: 1;
}

.type-pill-name {
  font-size: 11px;
}

.type-pill-count {
  font-size: 10px;
  font-weight: 600;
  color: #94a3b8;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.type-pill-btn.active .type-pill-count {
  color: #64748b;
}

.entity-list-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 10px 14px;
}

.entity-list-header {
  margin-bottom: 6px;
  font-size: 11px;
  font-weight: 600;
  color: #64748b;
}

.entity-ul {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.entity-li {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.12s;
  border: 1px solid transparent;
}

.entity-li:hover {
  background: #f1f5f9;
}

.entity-li.active {
  background: #eff6ff;
  border-color: #bfdbfe;
}

.type-tag {
  font-size: 10px;
  color: #ffffff;
  padding: 1px 5px;
  border-radius: 3px;
  font-weight: 600;
  flex-shrink: 0;
}

.entity-info-col {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.entity-name {
  font-size: 12px;
  color: #1e293b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entity-prefix-label {
  font-size: 10px;
  color: #94a3b8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-list {
  text-align: center;
  color: #94a3b8;
  font-size: 12px;
  padding: 24px 0;
}
</style>
