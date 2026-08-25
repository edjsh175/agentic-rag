<script setup lang="ts">
const props = defineProps<{
  isLeftSidebarOpen: boolean
  graphMode: 'product' | 'document'
  isProductBackbonePreview: boolean
  isProductBackboneComplexPreview: boolean
  isProductBackbonePreviewAny: boolean
  canvasNodesCount: number
  filteredNodesCount: number
  totalNodesCount: number
  totalEdgesCount: number
  selectedCategory: string
  documentSelectedCategory: string
  isLinkMode: boolean
  progressiveReveal: boolean
  layoutMode: 'dagre' | 'force'
  isPhysicsEnabled: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle-sidebar'): void
  (e: 'update:graphMode', mode: 'product' | 'document'): void
  (e: 'create-entity'): void
  (e: 'create-relation'): void
  (e: 'toggle-link-mode'): void
  (e: 'toggle-progressive'): void
  (e: 'toggle-layout-mode'): void
  (e: 'toggle-physics'): void
  (e: 'restart-layout'): void
  (e: 'reset-view'): void
}>()
</script>

<template>
  <header class="kg-toolbar">
    <div class="toolbar-left-group">
      <!-- 侧边栏折叠按钮 -->
      <button
        @click="emit('toggle-sidebar')"
        class="sidebar-toggle-btn"
        :title="isLeftSidebarOpen ? '折叠左侧筛选' : '展开左侧筛选'"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
          <line x1="9" y1="3" x2="9" y2="21"></line>
        </svg>
      </button>

      <!-- 模式切换分段控件 -->
      <div v-if="!isProductBackbonePreviewAny" class="mode-segmented-control">
        <button
          :class="['mode-btn', { active: graphMode === 'product' }]"
          @click="emit('update:graphMode', 'product')"
          type="button"
          data-test="mode-product-top"
        >
          产品能力图谱
        </button>
        <button
          :class="['mode-btn', { active: graphMode === 'document' }]"
          @click="emit('update:graphMode', 'document')"
          type="button"
          data-test="mode-document-top"
        >
          文档结构树
        </button>
      </div>

      <!-- 精致统计小胶囊 -->
      <div class="kg-stats-pill">
        <span class="stats-badge">
          <strong>{{ canvasNodesCount }}</strong> 画布 /
          <strong>{{ filteredNodesCount }}</strong> 筛选 /
          <strong>{{ totalNodesCount }}</strong> 实体 /
          <strong>{{ totalEdgesCount }}</strong> 关系
        </span>
      </div>

      <span v-if="graphMode === 'product' && selectedCategory !== 'all'" class="category-badge">
        当前分类: {{ selectedCategory }}
      </span>
      <span v-else-if="graphMode === 'document' && documentSelectedCategory !== 'all'" class="category-badge">
        当前分类: {{ documentSelectedCategory }}
      </span>
      <span v-if="isProductBackboneComplexPreview" class="category-badge preview-badge">
        产品架构主干预览（复杂明细版）
      </span>
      <span v-else-if="isProductBackbonePreview" class="category-badge preview-badge">
        产品架构主干预览（精简主干版）
      </span>
      <span v-if="isProductBackbonePreviewAny" class="edge-legend" data-test="edge-legend">
        <span class="edge-legend-item"><i style="background:rgba(120,132,180,0.7)"></i>属于</span>
        <span class="edge-legend-item"><i style="background:rgba(185,150,105,0.7)"></i>依赖</span>
        <span class="edge-legend-item edge-legend-note">颜色区分关系类型</span>
      </span>
    </div>

    <div class="toolbar-right-group">
      <!-- 编辑操作区 (Primary) -->
      <div class="action-group edit-actions-group">
        <button @click="emit('create-entity')" class="btn btn-primary" data-test="open-create-entity">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          新建实体
        </button>
        <button @click="emit('create-relation')" class="btn btn-secondary" data-test="open-create-relation">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="6" cy="6" r="3"></circle>
            <circle cx="18" cy="18" r="3"></circle>
            <line x1="8.5" y1="8.5" x2="15.5" y2="15.5"></line>
          </svg>
          新建关系
        </button>
        <button
          @click="emit('toggle-link-mode')"
          class="btn btn-toggle"
          :class="{ active: isLinkMode, 'btn-link-active': isLinkMode }"
          data-test="toggle-link-mode"
          title="按住鼠标从一个实体拖拽到另一个实体建立关系连线 (Esc 退出)"
        >
          <span class="status-indicator-dot" v-if="isLinkMode"></span>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="5" y1="12" x2="19" y2="12"></line>
            <polyline points="12 5 19 12 12 19"></polyline>
          </svg>
          {{ isLinkMode ? '退出连线' : '连线模式' }}
        </button>
      </div>

      <div class="group-divider"></div>

      <!-- 视图与布局控制区 -->
      <div class="action-group view-actions-group">
        <button
          @click="emit('toggle-progressive')"
          class="btn btn-outline icon-btn"
          :class="{ active: progressiveReveal }"
          data-test="toggle-progressive-reveal"
          :title="progressiveReveal ? '当前为渐进展开：双击节点展开/收起一跳邻居' : '当前显示全部筛选结果；点击启用渐进展开'"
        >
          {{ progressiveReveal ? '渐进展开' : '显示全部' }}
        </button>
        <button
          @click="emit('toggle-layout-mode')"
          class="btn btn-outline icon-btn"
          data-test="toggle-layout-mode"
          style="background-color: #f1f5f9; color: #334155; font-weight: 500; border-color: #cbd5e1;"
        >
          切换: {{ layoutMode === 'dagre' ? '分层组织图 (Dagre)' : '力导向图 (Force)' }}
        </button>
        <button
          @click="emit('toggle-physics')"
          class="btn btn-outline icon-btn"
          :class="{ active: isPhysicsEnabled }"
          data-test="toggle-physics-mode"
          :title="isPhysicsEnabled ? '当前为动态：节点互相推挤碰撞；点击切换为静态冻结' : '当前为静态：布局已冻结；点击切换为动态物理'"
        >
          {{ isPhysicsEnabled ? '动态布局' : '静态布局' }}
        </button>
        <button
          @click="emit('restart-layout')"
          class="btn btn-outline icon-btn icon-only"
          data-test="restart-layout"
          title="重置被拖拽实体的坐标，使其恢复到初始计算布局位置"
          :disabled="layoutMode === 'force' && !isPhysicsEnabled"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M23 4v6h-6"></path>
            <path d="M1 20v-6h6"></path>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
          </svg>
        </button>
        <button @click="emit('reset-view')" class="btn btn-outline icon-btn icon-only" title="重置画布平移与缩放视角">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="3"></circle>
            <path d="M3 12h3M18 12h3M12 3v3M12 18v3"></path>
          </svg>
        </button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.kg-toolbar {
  height: 48px;
  background: #ffffff;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 16px;
  gap: 12px;
  flex-shrink: 0;
  z-index: 5;
}

.toolbar-left-group,
.toolbar-right-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sidebar-toggle-btn {
  width: 32px;
  height: 32px;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
  background: #ffffff;
  color: #64748b;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s;
}

.sidebar-toggle-btn:hover {
  background: #f1f5f9;
  color: #1e293b;
  border-color: #cbd5e1;
}

.mode-segmented-control {
  display: inline-flex;
  background: #f1f5f9;
  padding: 2px;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
}

.mode-btn {
  border: none;
  background: transparent;
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  color: #64748b;
  cursor: pointer;
  transition: all 0.15s;
}

.mode-btn.active {
  background: #ffffff;
  color: #3370ff;
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
}

.kg-stats-pill {
  display: inline-flex;
  align-items: center;
  background: #f8fafc;
  border: 1px solid #eef2f6;
  border-radius: 20px;
  padding: 3px 10px;
}

.stats-badge {
  font-size: 11px;
  color: #64748b;
}

.stats-badge strong {
  color: #1e293b;
  font-weight: 600;
}

.category-badge {
  font-size: 11px;
  color: #0284c7;
  background: #e0f2fe;
  padding: 3px 8px;
  border-radius: 4px;
  font-weight: 500;
}

.preview-badge {
  color: #7c3aed;
  background: #ede9fe;
}

.edge-legend {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: #64748b;
  margin-left: 6px;
}

.edge-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.edge-legend-item i {
  display: inline-block;
  width: 12px;
  height: 2px;
  border-radius: 1px;
}

.edge-legend-note {
  color: #94a3b8;
  font-size: 10px;
}

.action-group {
  display: flex;
  align-items: center;
  gap: 6px;
}

.group-divider {
  width: 1px;
  height: 20px;
  background: #e2e8f0;
  margin: 0 2px;
}

.btn {
  height: 32px;
  padding: 0 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  transition: all 0.15s;
  box-sizing: border-box;
}

.btn-primary {
  background: #3370ff;
  color: #ffffff;
  border: 1px solid #3370ff;
}

.btn-primary:hover {
  background: #1a56db;
  border-color: #1a56db;
}

.btn-secondary {
  background: #ffffff;
  color: #334155;
  border: 1px solid #cbd5e1;
}

.btn-secondary:hover {
  background: #f8fafc;
  border-color: #94a3b8;
}

.btn-toggle {
  background: #ffffff;
  color: #475569;
  border: 1px solid #cbd5e1;
}

.btn-toggle.active,
.btn-toggle.btn-link-active {
  background: #eff6ff;
  color: #3370ff;
  border-color: #93c5fd;
}

.status-indicator-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #3370ff;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(1.3); }
}

.btn-outline {
  background: #ffffff;
  color: #475569;
  border: 1px solid #e2e8f0;
}

.btn-outline:hover {
  background: #f8fafc;
  color: #1e293b;
  border-color: #cbd5e1;
}

.btn-outline.active {
  background: #eff6ff;
  color: #3370ff;
  border-color: #93c5fd;
}

.icon-only {
  width: 32px;
  padding: 0;
  justify-content: center;
}

.icon-only:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
