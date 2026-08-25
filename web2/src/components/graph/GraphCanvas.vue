<script setup lang="ts">
import { ref } from 'vue'
import type { GraphNode } from '../../types'

const props = defineProps<{
  selectedNode: GraphNode | null
  selectedNodeScreenPos: { x: number; y: number } | null
  isLinkMode: boolean
  linkModeHint: string
  progressiveReveal: boolean
  scale: number
  loading: boolean
  errorMsg: string
  isNodeExpanded: (id: string) => boolean
}>()

const emit = defineEmits<{
  (e: 'open-edit'): void
  (e: 'start-link'): void
  (e: 'focus-node', id: string): void
  (e: 'expand-node', id: string): void
  (e: 'collapse-node', id: string): void
  (e: 'initiate-delete'): void
  (e: 'zoom-in'): void
  (e: 'zoom-out'): void
  (e: 'reset-view'): void
  (e: 'fit-view'): void
  (e: 'retry-fetch'): void
  (e: 'mousedown', evt: MouseEvent): void
  (e: 'mousemove', evt: MouseEvent): void
  (e: 'mouseup', evt: MouseEvent): void
  (e: 'dblclick', evt: MouseEvent): void
  (e: 'wheel', evt: WheelEvent): void
}>()

const containerRef = ref<HTMLDivElement | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)
const isHelpOpen = ref(false)

defineExpose({
  containerRef,
  canvasRef,
})
</script>

<template>
  <div
    class="canvas-container"
    ref="containerRef"
    :class="{ 'link-mode': isLinkMode }"
  >
    <!-- 连线模式提示 -->
    <div
      v-if="isLinkMode"
      class="link-mode-hint"
      data-test="link-mode-hint"
    >
      <span class="pulse-dot"></span>
      {{ linkModeHint }}
      <span class="hint-key">按 Esc 退出</span>
    </div>
    <div
      v-else-if="progressiveReveal"
      class="link-mode-hint progressive-hint"
      data-test="progressive-hint"
    >
      渐进展开：种子为 Product/Document 或搜索命中；双击节点展开/收起一跳邻居；左侧点选可加入画布
    </div>

    <!-- 选中节点画布浮动快捷操作栏 (Floating Quick Action Bar) -->
    <div
      v-if="selectedNode && !isLinkMode && selectedNodeScreenPos"
      class="floating-node-actions"
      :style="{
        left: `${selectedNodeScreenPos.x}px`,
        top: `${selectedNodeScreenPos.y}px`
      }"
    >
      <button
        @click="emit('open-edit')"
        class="floating-act-btn"
        title="编辑此实体"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
        </svg>
        编辑
      </button>
      <button
        @click="emit('start-link')"
        class="floating-act-btn"
        title="以此节点为起点快速连线"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="5" y1="12" x2="19" y2="12"></line>
          <polyline points="12 5 19 12 12 19"></polyline>
        </svg>
        连线
      </button>
      <button
        @click="emit('focus-node', selectedNode.id)"
        class="floating-act-btn"
        title="居中聚焦此节点 (快捷键 F)"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"></circle>
          <circle cx="12" cy="12" r="3"></circle>
        </svg>
        聚焦
      </button>
      <button
        v-if="progressiveReveal"
        @click="isNodeExpanded(selectedNode.id) ? emit('collapse-node', selectedNode.id) : emit('expand-node', selectedNode.id)"
        class="floating-act-btn"
        :title="isNodeExpanded(selectedNode.id) ? '收起邻居节点' : '展开一跳邻居'"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"></path>
        </svg>
        {{ isNodeExpanded(selectedNode.id) ? '收起' : '展开' }}
      </button>
      <button
        @click="emit('initiate-delete')"
        class="floating-act-btn danger"
        title="删除实体 (快捷键 Delete)"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="3 6 5 6 21 6"></polyline>
          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
        </svg>
      </button>
    </div>

    <canvas
      ref="canvasRef"
      @mousedown="emit('mousedown', $event)"
      @mousemove="emit('mousemove', $event)"
      @mouseup="emit('mouseup', $event)"
      @mouseleave="emit('mouseup', $event)"
      @dblclick="emit('dblclick', $event)"
      @wheel="emit('wheel', $event)"
    ></canvas>

    <!-- 画布浮动微型控制器 (右下角) -->
    <div class="floating-canvas-controls">
      <button @click="emit('zoom-in')" class="zoom-btn" title="放大视角 (+)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <line x1="12" y1="5" x2="12" y2="19"></line>
          <line x1="5" y1="12" x2="19" y2="12"></line>
        </svg>
      </button>
      <button @click="emit('zoom-out')" class="zoom-btn" title="缩小视角 (-)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <line x1="5" y1="12" x2="19" y2="12"></line>
        </svg>
      </button>
      <button @click="emit('reset-view')" class="zoom-btn text-zoom-btn" title="100% 原始比例">
        {{ Math.round(scale * 100) }}%
      </button>
      <button @click="emit('fit-view')" class="zoom-btn" title="自适应居中全图 (Fit)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="15 3 21 3 21 9"></polyline>
          <polyline points="9 21 3 21 3 15"></polyline>
          <line x1="21" y1="3" x2="14" y2="10"></line>
          <line x1="3" y1="21" x2="10" y2="14"></line>
        </svg>
      </button>
      <button @click="isHelpOpen = !isHelpOpen" class="zoom-btn help-btn" title="操作快捷指南">
        ?
      </button>
    </div>

    <!-- 快捷操作指南气泡卡片 -->
    <div v-if="isHelpOpen" class="help-popover">
      <div class="help-popover-header">
        <h4>💡 图谱操作快捷指南</h4>
        <button @click="isHelpOpen = false" class="close-help-btn">&times;</button>
      </div>
      <ul class="help-list">
        <li><span>🖱️ 拖拽空白</span> 平移画布</li>
        <li><span>🎡 滚轮滚动</span> 无级缩放视口</li>
        <li><span>🖱️ 双击实体</span> 渐进模式展开/收起一跳邻居</li>
        <li><span>✨ 选中实体</span> 弹出就近快捷工具栏</li>
        <li><span>⌨️ 按键 F</span> 居中聚焦选中实体 / 自适应全图</li>
        <li><span>⌨️ 按键 Esc</span> 退出连线 / 清除高亮选中</li>
        <li><span>⌨️ 按键 Del</span> 级联删除当前选中实体</li>
      </ul>
    </div>

    <div v-if="loading" class="canvas-overlay">
      <div class="loader"></div>
      <span>加载中...</span>
    </div>
    <div v-if="errorMsg" class="canvas-overlay error">
      <span>⚠️ 加载出错: {{ errorMsg }}</span>
      <button @click="emit('retry-fetch')" class="retry-btn">重试</button>
    </div>
  </div>
</template>

<style scoped>
.canvas-container {
  flex: 1;
  position: relative;
  overflow: hidden;
  background-color: #f8fafc;
  background-image: radial-gradient(#cbd5e1 1.2px, transparent 1.2px);
  background-size: 24px 24px;
  cursor: grab;
  user-select: none;
}

.canvas-container:active {
  cursor: grabbing;
}

.canvas-container.link-mode {
  cursor: crosshair !important;
}

canvas {
  width: 100%;
  height: 100%;
  display: block;
}

.link-mode-hint {
  position: absolute;
  top: 14px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(15, 23, 42, 0.85);
  backdrop-filter: blur(4px);
  color: #ffffff;
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  pointer-events: none;
  z-index: 20;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.progressive-hint {
  background: rgba(30, 41, 59, 0.78);
  font-size: 11px;
}

.pulse-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #22c55e;
  animation: pulse 1.5s infinite;
}

.hint-key {
  color: #94a3b8;
  font-size: 11px;
  border-left: 1px solid rgba(255, 255, 255, 0.2);
  padding-left: 8px;
}

.floating-node-actions {
  position: absolute;
  transform: translate(-50%, -100%) translateY(-14px);
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.15), 0 8px 10px -6px rgba(0, 0, 0, 0.05);
  padding: 3px;
  display: flex;
  align-items: center;
  gap: 2px;
  z-index: 30;
  pointer-events: auto;
  animation: pop-in 0.15s cubic-bezier(0.16, 1, 0.3, 1);
}

.floating-act-btn {
  height: 26px;
  padding: 0 8px;
  border-radius: 4px;
  border: none;
  background: transparent;
  color: #334155;
  font-size: 11px;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  transition: all 0.12s;
  white-space: nowrap;
}

.floating-act-btn:hover {
  background: #f1f5f9;
  color: #3370ff;
}

.floating-act-btn.danger {
  color: #ef4444;
  padding: 0 6px;
}

.floating-act-btn.danger:hover {
  background: #fef2f2;
  color: #dc2626;
}

.floating-canvas-controls {
  position: absolute;
  right: 20px;
  bottom: 20px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  display: flex;
  flex-direction: column;
  padding: 3px;
  gap: 2px;
  z-index: 20;
}

.zoom-btn {
  width: 28px;
  height: 28px;
  border-radius: 4px;
  border: none;
  background: transparent;
  color: #475569;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s;
}

.zoom-btn:hover {
  background: #f1f5f9;
  color: #1e293b;
}

.text-zoom-btn {
  font-size: 10px;
  font-weight: 600;
  width: auto;
  padding: 0 4px;
}

.help-btn {
  font-weight: bold;
  font-size: 12px;
  color: #64748b;
  border-top: 1px solid #f1f5f9;
  border-radius: 0 0 4px 4px;
}

.help-popover {
  position: absolute;
  right: 58px;
  bottom: 20px;
  width: 260px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.15);
  padding: 12px 14px;
  z-index: 25;
  animation: pop-in 0.15s cubic-bezier(0.16, 1, 0.3, 1);
}

.help-popover-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.help-popover-header h4 {
  font-size: 12px;
  font-weight: 600;
  color: #1e293b;
  margin: 0;
}

.close-help-btn {
  border: none;
  background: transparent;
  color: #94a3b8;
  font-size: 16px;
  cursor: pointer;
}

.help-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.help-list li {
  font-size: 11px;
  color: #475569;
  display: flex;
  justify-content: space-between;
}

.help-list li span {
  color: #1e293b;
  font-weight: 500;
}

.canvas-overlay {
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(2px);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  font-size: 13px;
  color: #475569;
  z-index: 40;
}

.loader {
  width: 24px;
  height: 24px;
  border: 3px solid #cbd5e1;
  border-top-color: #3370ff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.canvas-overlay.error {
  color: #ef4444;
}

.retry-btn {
  padding: 6px 14px;
  background: #3370ff;
  color: #ffffff;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}

@keyframes pop-in {
  from { opacity: 0; transform: scale(0.95); }
  to { opacity: 1; transform: scale(1); }
}
</style>
