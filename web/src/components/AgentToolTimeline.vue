<script setup lang="ts">
import { ref } from 'vue'
import type { AgentToolCall } from '../types'

const props = defineProps<{
  tools: AgentToolCall[]
  loading?: boolean
  activeStatus?: string
}>()

const expandedIndex = ref<number | null>(null)

function toggleExpand(index: number) {
  expandedIndex.value = expandedIndex.value === index ? null : index
}

function getToolMeta(name: string) {
  switch (name) {
    case 'understand':
      return { label: '意图理解', type: 'understand', color: '#6366f1' }
    case 'rewrite':
      return { label: '查询改写', type: 'rewrite', color: '#8b5cf6' }
    case 'retrieve_kb':
      return { label: '知识库检索', type: 'retrieve_kb', color: '#0ea5e9' }
    case 'reuse_evidence':
      return { label: '复用上下文证据', type: 'reuse_evidence', color: '#10b981' }
    case 'link_entities':
      return { label: '图谱实体消歧', type: 'link_entities', color: '#f59e0b' }
    case 'clarify':
      return { label: '反问确认', type: 'clarify', color: '#ec4899' }
    case 'web_search':
      return { label: '外部网页检索', type: 'web_search', color: '#06b6d4' }
    case 'environment.read_status':
      return { label: '系统状态读取', type: 'environment', color: '#14b8a6' }
    default:
      if (name.startsWith('environment.')) {
        return { label: `环境工具: ${name.replace('environment.', '')}`, type: 'environment', color: '#64748b' }
      }
      return { label: name, type: 'tool', color: '#64748b' }
  }
}

function formatJson(val: any): string {
  if (!val) return ''
  if (typeof val === 'string') return val
  try {
    return JSON.stringify(val, null, 2)
  } catch {
    return String(val)
  }
}
</script>

<template>
  <div v-if="(tools && tools.length > 0) || (loading && activeStatus)" class="agent-timeline">
    <div class="timeline-header">
      <div class="header-tag">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
        </svg>
        <span>Agent 工具调用流水线</span>
      </div>
      <span v-if="tools && tools.length" class="step-count">共 {{ tools.length }} 步决策</span>
    </div>

    <div class="timeline-steps">
      <div
        v-for="(tool, index) in tools"
        :key="index"
        class="step-card"
        :class="{
          'is-expanded': expandedIndex === index,
          'is-error': tool.ok === false || tool.status === 'error',
          'is-recovery': !!tool.gap_type || tool.status === 'recovery'
        }"
        @click="toggleExpand(index)"
      >
        <div class="step-main">
          <div class="step-icon-wrap" :style="{ backgroundColor: getToolMeta(tool.name).color + '15', color: getToolMeta(tool.name).color }">
            <!-- 意图理解 -->
            <svg v-if="getToolMeta(tool.name).type === 'understand'" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/>
              <polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/>
            </svg>
            <!-- 查询改写 -->
            <svg v-else-if="getToolMeta(tool.name).type === 'rewrite'" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 20h9"/>
              <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
            </svg>
            <!-- 知识库检索 -->
            <svg v-else-if="getToolMeta(tool.name).type === 'retrieve_kb'" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="11" cy="11" r="8"/>
              <line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <!-- 复用证据 -->
            <svg v-else-if="getToolMeta(tool.name).type === 'reuse_evidence'" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="23 4 23 10 17 10"/>
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
            </svg>
            <!-- 实体链接 -->
            <svg v-else-if="getToolMeta(tool.name).type === 'link_entities'" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
            </svg>
            <!-- 反问确认 -->
            <svg v-else-if="getToolMeta(tool.name).type === 'clarify'" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/>
              <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
              <line x1="12" y1="17" x2="12.01" y2="17"/>
            </svg>
            <!-- 网页检索 -->
            <svg v-else-if="getToolMeta(tool.name).type === 'web_search'" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/>
              <line x1="2" y1="12" x2="22" y2="12"/>
              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
            </svg>
            <!-- 环境工具 -->
            <svg v-else-if="getToolMeta(tool.name).type === 'environment'" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="2" y="2" width="20" height="8" rx="2" ry="2"/>
              <rect x="2" y="14" width="20" height="8" rx="2" ry="2"/>
              <line x1="6" y1="6" x2="6.01" y2="6"/>
              <line x1="6" y1="18" x2="6.01" y2="18"/>
            </svg>
            <!-- 通用工具 -->
            <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="16 18 22 12 16 6"/>
              <polyline points="8 6 2 12 8 18"/>
            </svg>
          </div>

          <div class="step-info">
            <div class="step-title-row">
              <span class="tool-label">{{ getToolMeta(tool.name).label }}</span>
              <code class="tool-name">{{ tool.name }}</code>

              <span v-if="tool.gap_type" class="gap-badge" title="识别出证据缺口并自动触发补检">
                缺口: {{ tool.gap_type }}
              </span>

              <span v-if="tool.elapsed_ms !== undefined && tool.elapsed_ms > 0" class="time-badge">
                {{ tool.elapsed_ms < 1000 ? `${Math.round(tool.elapsed_ms)}ms` : `${(tool.elapsed_ms / 1000).toFixed(1)}s` }}
              </span>
            </div>

            <div v-if="tool.summary" class="step-summary">
              {{ tool.summary }}
            </div>
          </div>

          <div class="step-status-badge">
            <span v-if="tool.status === 'running'" class="badge-running">
              <span class="spin-dot"></span> 执行中
            </span>
            <span v-else-if="tool.ok === false" class="badge-fail">
              失败
            </span>
            <span v-else class="badge-success">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
              成功
            </span>

            <span class="chevron-icon" :class="{ 'is-open': expandedIndex === index }">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="6 9 12 15 18 9"/>
              </svg>
            </span>
          </div>
        </div>

        <!-- 折叠详情区 -->
        <div v-if="expandedIndex === index" class="step-detail" @click.stop>
          <div v-if="tool.arguments && Object.keys(tool.arguments).length" class="detail-section">
            <div class="detail-title">输入参数:</div>
            <pre class="detail-code"><code>{{ formatJson(tool.arguments) }}</code></pre>
          </div>

          <div v-if="tool.recovery_strategy" class="detail-section">
            <div class="detail-title">恢复策略:</div>
            <span class="strategy-pill">{{ tool.recovery_strategy }}</span>
          </div>

          <div v-if="tool.error" class="detail-section error-section">
            <div class="detail-title">错误信息:</div>
            <div class="error-text">{{ tool.error }}</div>
          </div>

          <div v-if="tool.fallback" class="detail-section">
            <div class="detail-title">回退标记:</div>
            <div class="fallback-text">{{ tool.fallback }}</div>
          </div>

          <div v-if="tool.observation" class="detail-section">
            <div class="detail-title">观察数据:</div>
            <pre class="detail-code"><code>{{ formatJson(tool.observation) }}</code></pre>
          </div>
        </div>
      </div>

      <!-- 正在执行的动态活跃步骤 -->
      <div v-if="loading && activeStatus" class="step-card is-active-loading">
        <div class="step-main">
          <div class="step-icon-wrap active-pulse" style="background-color: #dbeafe; color: #2563eb;">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
            </svg>
          </div>
          <div class="step-info">
            <div class="step-title-row">
              <span class="tool-label">{{ activeStatus }}</span>
            </div>
          </div>
          <div class="step-status-badge">
            <span class="badge-running">
              <span class="spin-dot"></span> 执行中
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-timeline {
  margin-bottom: 12px;
  border-radius: 8px;
  background: #fdfdfe;
  border: 1px solid #e2e8f0;
  overflow: hidden;
}

.timeline-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: #f8fafc;
  border-bottom: 1px solid #edf2f7;
  font-size: 11px;
  color: #64748b;
}

.header-tag {
  display: flex;
  align-items: center;
  gap: 5px;
  font-weight: 500;
  color: #475569;
}

.step-count {
  font-size: 11px;
  color: #94a3b8;
}

.timeline-steps {
  display: flex;
  flex-direction: column;
  gap: 1px;
  background: #f1f5f9;
}

.step-card {
  background: #ffffff;
  transition: all 0.15s ease;
  cursor: pointer;
}

.step-card:hover {
  background: #f8fafc;
}

.step-card.is-active-loading {
  background: #f0fdf4;
  cursor: default;
}

.step-card.is-recovery {
  border-left: 3px solid #f59e0b;
}

.step-card.is-error {
  border-left: 3px solid #ef4444;
}

.step-main {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 12px;
}

.step-icon-wrap {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.step-icon-wrap.active-pulse {
  animation: pulse-border 1.5s infinite;
}

@keyframes pulse-border {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.08); opacity: 0.8; }
}

.step-info {
  flex: 1;
  min-width: 0;
}

.step-title-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.tool-label {
  font-size: 12px;
  font-weight: 500;
  color: #1e293b;
}

.tool-name {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 10px;
  color: #64748b;
  background: #f1f5f9;
  padding: 1px 4px;
  border-radius: 3px;
}

.gap-badge {
  font-size: 10px;
  color: #d97706;
  background: #fef3c7;
  border: 1px solid #fde68a;
  padding: 0 5px;
  border-radius: 4px;
  font-weight: 500;
}

.time-badge {
  font-size: 10px;
  color: #94a3b8;
}

.step-summary {
  font-size: 11px;
  color: #64748b;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.step-status-badge {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.badge-success {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 10px;
  color: #059669;
  background: #ecfdf5;
  padding: 1px 6px;
  border-radius: 10px;
  font-weight: 500;
}

.badge-running {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  color: #2563eb;
  background: #eff6ff;
  padding: 1px 6px;
  border-radius: 10px;
  font-weight: 500;
}

.spin-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #3b82f6;
  animation: pulse-dot 1s infinite;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.3; transform: scale(0.6); }
}

.badge-fail {
  font-size: 10px;
  color: #dc2626;
  background: #fef2f2;
  padding: 1px 6px;
  border-radius: 10px;
  font-weight: 500;
}

.chevron-icon {
  color: #94a3b8;
  display: inline-flex;
  align-items: center;
  transition: transform 0.2s ease;
}

.chevron-icon.is-open {
  transform: rotate(180deg);
}

.step-detail {
  padding: 8px 12px 10px 48px;
  background: #fafbfc;
  border-top: 1px dashed #e2e8f0;
  font-size: 12px;
}

.detail-section {
  margin-bottom: 6px;
}

.detail-section:last-child {
  margin-bottom: 0;
}

.detail-title {
  font-size: 11px;
  font-weight: 500;
  color: #64748b;
  margin-bottom: 2px;
}

.detail-code {
  background: #1e293b;
  color: #f8fafc;
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 11px;
  max-height: 180px;
  overflow-y: auto;
  margin: 0;
}

.detail-code code {
  font-family: 'JetBrains Mono', Consolas, monospace;
}

.strategy-pill {
  display: inline-block;
  font-size: 11px;
  color: #0369a1;
  background: #e0f2fe;
  padding: 1px 6px;
  border-radius: 4px;
}

.error-text {
  color: #b91c1c;
  background: #fef2f2;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 11px;
}

.fallback-text {
  color: #b45309;
  background: #fffbeb;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
  display: inline-block;
}
</style>
