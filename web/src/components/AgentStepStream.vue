<script setup lang="ts">
import { ref, computed, watchEffect, onUnmounted } from 'vue'
import type { AssistantBlock, ToolBlock, ActivityBlock } from '../types'
import { getReasoningStageTitle } from '../utils/agentBlockProjector'

const props = defineProps<{
  blocks?: AssistantBlock[]
}>()

// 用户展开状态记录
const expandedMap = ref<Record<string | number, boolean>>({})

// Block Stream 按真实到达顺序渲染全部用户可见 Block。
const executionBlocks = computed<AssistantBlock[]>(() => props.blocks || [])

const now = ref(Date.now())
let timerId: ReturnType<typeof setInterval> | null = null

const hasRunningActivity = computed(() =>
  executionBlocks.value.some(b => b.kind === 'activity' && b.status === 'running')
)

watchEffect(() => {
  if (hasRunningActivity.value) {
    if (!timerId) {
      timerId = setInterval(() => {
        now.value = Date.now()
      }, 100)
    }
  } else {
    if (timerId) {
      clearInterval(timerId)
      timerId = null
    }
  }
})

onUnmounted(() => {
  if (timerId) {
    clearInterval(timerId)
    timerId = null
  }
})

function getLiveElapsed(block: ActivityBlock): string {
  if (block.elapsedMs !== undefined) {
    return `${(block.elapsedMs / 1000).toFixed(1)}s`
  }
  if (block.startedAt) {
    const ms = Math.max(0, now.value - block.startedAt)
    return `${(ms / 1000).toFixed(1)}s`
  }
  return ''
}

function getBlockKey(block: AssistantBlock | undefined, index: number): string | number {
  return block?.id || index
}

function isDefaultExpanded(block: AssistantBlock | undefined): boolean {
  if (!block) return false
  if (block.kind === 'reasoning') {
    // Main Controller 等思考过程框默认展开
    return true
  }
  if (block.kind === 'tool') {
    // 工具调用框默认收起
    return false
  }
  return false
}

function isExpanded(index: number): boolean {
  const block = executionBlocks.value[index]
  const key = getBlockKey(block, index)
  const override = expandedMap.value[key]
  return override === undefined ? isDefaultExpanded(block) : override
}

function toggleExpand(index: number) {
  const block = executionBlocks.value[index]
  const key = getBlockKey(block, index)
  expandedMap.value[key] = !isExpanded(index)
}

function firstLine(text: string): string {
  if (!text) return ''
  const newline = text.indexOf('\n')
  return (newline === -1 ? text : text.slice(0, newline)).trim()
}

function latestLine(text: string): string {
  if (!text) return ''
  const visible = text.trimEnd()
  const newline = visible.lastIndexOf('\n')
  return (newline === -1 ? visible : visible.slice(newline + 1)).trim()
}

function formatPayload(data: any): string {
  if (data === undefined || data === null) return ''
  if (typeof data === 'string') return data
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
}

function getToolSummary(block: ToolBlock): string {
  if (block.error) return block.error
  if (typeof block.input === 'object' && block.input !== null) {
    const inp = block.input as Record<string, any>
    if (inp.query) return String(inp.query)
    if (inp.command) return String(inp.command)
  }
  if (typeof block.output === 'object' && block.output !== null) {
    const out = block.output as Record<string, any>
    if (out.summary) return String(out.summary)
  }
  return block.label || block.tool
}

const showInspectModal = ref(false)
const inspectPayload = ref<any>(null)

function openInspect(payload: any) {
  inspectPayload.value = payload
  showInspectModal.value = true
}

function closeInspect() {
  showInspectModal.value = false
  inspectPayload.value = null
}

function reviewActionText(action?: string): string {
  if (action === 'correct_to_evidence') return '按现有证据改正表述。'
  if (action === 'add_limitation_statement') return '删除断言，并说明当前证据无法确认。'
  if (action === 'rewrite_to_supported_scope_or_remove') return '删除或缩限到证据直接支持的范围。'
  return ''
}
</script>

<template>
  <div v-if="executionBlocks.length > 0" class="step-stream-container">
    <template v-for="(block, index) in executionBlocks" :key="block.id || index">
      <!-- 1. System/Event 节点 (SystemEventBlock) -->
      <div
        v-if="block.kind === 'system_event'"
        class="disclosure-root notice-root"
        :data-level="block.level || 'warning'"
      >
        <div class="notice-row">
          <div class="leading-slot">
            <span class="notice-indicator"></span>
          </div>
          <span class="row-title">{{ block.level === 'error' ? '系统提示' : '审查提示' }}</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary notice-content">{{ block.text }}</span>
        </div>
      </div>

      <!-- 2. Activity 运行行 (ActivityBlock) -->
      <div
        v-else-if="block.kind === 'activity'"
        class="disclosure-root activity-root"
        :data-activity="block.activity"
        :data-state="block.status"
      >
        <div class="activity-row">
          <div class="leading-slot">
            <span v-if="block.status === 'running'" class="state-dot running"></span>
            <span v-else-if="block.status === 'completed'" class="activity-icon completed">✓</span>
            <span v-else-if="block.status === 'warning'" class="activity-icon warning">!</span>
            <span v-else-if="block.status === 'failed'" class="activity-icon failed">✕</span>
          </div>
          <span class="row-summary activity-text" :class="`activity-text--${block.status}`">
            {{ block.text }}
          </span>
          <span v-if="block.status === 'running' && getLiveElapsed(block)" class="activity-timer">
            {{ getLiveElapsed(block) }}
          </span>
          <span v-else-if="block.elapsedMs !== undefined" class="activity-suffix">
            · {{ (block.elapsedMs / 1000).toFixed(1) }}s
          </span>
        </div>
      </div>

      <section v-else-if="block.kind === 'review_finding'" class="review-finding-root">
        <div class="review-finding-title">证据审查发现 {{ block.findings.length }} 个需要修正的表述</div>
        <p v-if="block.summary" class="review-finding-summary">{{ block.summary }}</p>
        <ul class="review-finding-list">
          <li v-for="(finding, findingIndex) in block.findings" :key="findingIndex">
            <strong>{{ finding.claim }}</strong>
            <span class="review-finding-status">{{ finding.status === 'contradicted' ? '与证据冲突' : '当前证据未支持' }}</span>
            <p v-if="finding.reason">原因：{{ finding.reason }}</p>
            <p v-if="finding.instruction">修正：{{ finding.instruction }}</p>
            <p v-else-if="finding.action">修正：{{ reviewActionText(finding.action) }}</p>
          </li>
        </ul>
      </section>

      <!-- 3. Reasoning 思考节点 (ReasoningBlock) -->
      <div
        v-else-if="block.kind === 'reasoning'"
        class="disclosure-root disclosure-root--reasoning"
        data-variant="think"
        :data-state="block.status === 'running' ? 'running' : 'ok'"
      >
        <div
          class="disclosure-row"
          role="button"
          tabindex="0"
          :aria-expanded="isExpanded(index)"
          @click="toggleExpand(index)"
        >
          <div class="leading-slot">
            <svg class="icon-idle" width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M7.06431 5.93342C7.68763 5.93342 8.19307 6.43904 8.19322 7.06233C8.19322 7.68573 7.68772 8.19123 7.06431 8.19123C6.44099 8.19113 5.9354 7.68567 5.9354 7.06233C5.93555 6.43911 6.44108 5.93353 7.06431 5.93342Z" fill="currentColor"/>
              <path fill-rule="evenodd" clip-rule="evenodd" d="M8.6815 0.963693C10.1169 0.447019 11.6266 0.374829 12.5633 1.31135C13.5 2.24805 13.4277 3.75776 12.911 5.19319C12.7126 5.74431 12.4386 6.31796 12.0965 6.89729C12.4969 7.54638 12.8141 8.19018 13.036 8.80647C13.5527 10.2419 13.6251 11.7516 12.6883 12.6883C11.7516 13.625 10.242 13.5527 8.8065 13.036C8.19022 12.8141 7.54641 12.4969 6.89732 12.0965C6.31797 12.4386 5.74435 12.7125 5.19322 12.911C3.75777 13.4276 2.2481 13.5 1.31138 12.5633C0.374859 11.6266 0.447049 10.1168 0.963724 8.68147C1.17185 8.10338 1.46321 7.50063 1.82896 6.8924C1.52182 6.35711 1.27235 5.82825 1.08872 5.31819C0.572068 3.88278 0.499714 2.37306 1.43638 1.43635C2.37308 0.499655 3.8828 0.572044 5.31822 1.08869C5.82828 1.27232 6.35715 1.5218 6.89243 1.82893C7.50066 1.46318 8.10341 1.17181 8.6815 0.963693ZM11.3573 8.01154C10.9083 8.62253 10.3901 9.22873 9.80943 9.8094C9.22877 10.3901 8.62255 10.9083 8.01158 11.3572C8.4257 11.5841 8.8287 11.7688 9.21275 11.9071C10.5456 12.3868 11.4246 12.2547 11.8397 11.8397C12.2548 11.4246 12.3869 10.5456 11.9071 9.21272C11.7688 8.82866 11.5841 8.42568 11.3573 8.01154ZM2.56529 8.02912C2.37344 8.39322 2.21495 8.74796 2.09263 9.08772C1.61291 10.4204 1.74512 11.2995 2.16001 11.7147C2.57505 12.1297 3.45415 12.2618 4.78697 11.7821C5.11057 11.6656 5.44786 11.5164 5.7938 11.3367C5.249 10.9223 4.70922 10.4533 4.19029 9.9344C3.57578 9.31987 3.03169 8.67633 2.56529 8.02912ZM6.90708 3.2469C6.24065 3.70479 5.5646 4.26321 4.91392 4.91389C4.26325 5.56456 3.70482 6.24063 3.24693 6.90705C3.72674 7.63325 4.32777 8.37459 5.03892 9.08576C5.64943 9.69627 6.28183 10.2265 6.90806 10.6678C7.59368 10.2025 8.2908 9.63076 8.96079 8.96076C9.6308 8.29075 10.2025 7.59366 10.6678 6.90803C10.2265 6.2818 9.69631 5.6494 9.08579 5.03889C8.37462 4.32773 7.63328 3.72672 6.90708 3.2469ZM11.7147 2.15998C11.2996 1.74509 10.4204 1.61288 9.08775 2.0926C8.74835 2.21479 8.39382 2.37271 8.03013 2.56428C8.67728 3.03065 9.31995 3.5758 9.93443 4.19026C10.4534 4.7092 10.9223 5.24896 11.3368 5.79377C11.5164 5.44785 11.6656 5.11052 11.7821 4.78694C12.2618 3.45416 12.1297 2.57502 11.7147 2.15998ZM4.91197 2.2176C3.57922 1.73788 2.70004 1.86995 2.28501 2.28498C1.87001 2.70003 1.73791 3.5792 2.21763 4.91194C2.31709 5.18822 2.44112 5.47427 2.58677 5.7674C3.01931 5.1887 3.51474 4.6158 4.06529 4.06526C4.61584 3.5147 5.18872 3.01928 5.76743 2.58674C5.47431 2.4411 5.18824 2.31706 4.91197 2.2176Z" fill="currentColor"/>
            </svg>
            <svg
              class="chevron-hover"
              :class="{ 'is-open': isExpanded(index) }"
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
            >
              <path d="M3.5 5.25L7 8.75L10.5 5.25" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </div>

          <span class="row-title">{{ getReasoningStageTitle(block.stage, block.contentSource) }}</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary" :data-follow-end="block.status === 'running' || undefined">
            {{ block.status === 'running' ? latestLine(block.text) : firstLine(block.text) }}
          </span>
          <span v-if="block.elapsedMs && block.status !== 'running'" class="row-suffix">
            [{{ (block.elapsedMs / 1000).toFixed(1) }}s]
          </span>
        </div>

        <div v-show="isExpanded(index)" class="think-body">
          {{ block.text }}
          <span v-if="block.status === 'running'" class="cursor-blink"></span>
        </div>
      </div>

      <!-- 3. Tool Call 节点 (ToolBlock) -->
      <div
        v-else-if="block.kind === 'tool'"
        class="disclosure-root disclosure-root--tool"
        :data-variant="block.tool"
        :data-state="block.status === 'running' ? 'running' : (block.status === 'failed' || block.error ? 'error' : 'ok')"
      >
        <div
          class="disclosure-row"
          role="button"
          tabindex="0"
          :aria-expanded="isExpanded(index)"
          @click="toggleExpand(index)"
        >
          <div class="leading-slot">
            <span v-if="block.status === 'failed' || block.error" class="state-dot error"></span>
            <span v-else-if="block.status === 'running'" class="state-dot running"></span>
            <svg v-else class="icon-idle" width="14" height="14" viewBox="0 0 16 16" fill="none">
              <path d="M12.3368 1.53569L11.931 4.43172H14.8086V5.79673H11.7404L11.1962 9.67859H14.2839V11.0436H11.0056L10.4994 14.6529L9.14873 14.4643L9.62731 11.0436H5.75876L5.25252 14.6529L3.90186 14.4643L4.38043 11.0436H1.69141V9.67859H4.57104L5.11417 5.79673H2.21609V4.43172H5.30581L5.73724 1.34713L7.08995 1.53569L6.68414 4.43172H10.5527L10.9841 1.34713L12.3368 1.53569ZM5.94937 9.67859H9.81791L10.361 5.79673H6.49353L5.94937 9.67859Z" fill="currentColor"/>
            </svg>
            <svg
              class="chevron-hover"
              :class="{ 'is-open': isExpanded(index) }"
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
            >
              <path d="M3.5 5.25L7 8.75L10.5 5.25" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </div>

          <span class="row-title">{{ block.label || block.tool }}</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span
            class="row-summary"
            :class="{ 'error-text': block.status === 'failed' || block.error }"
          >
            {{ getToolSummary(block) }}
          </span>
          <span v-if="block.gap" class="row-badge recovery">定向补检</span>
        </div>

        <div v-show="isExpanded(index)" class="tool-body-wrap">
          <div class="io-card">
            <!-- IN Section -->
            <div v-if="block.input !== undefined && block.input !== null" class="io-section">
              <span class="io-label">输入参数</span>
              <span class="io-text">{{ formatPayload(block.input) }}</span>
            </div>

            <span v-if="block.input !== undefined && block.output !== undefined" class="io-divider" aria-hidden="true"></span>

            <!-- OUT Section -->
            <div v-if="block.output !== undefined && block.output !== null" class="io-section">
              <span class="io-label">执行结果</span>
              <span class="io-text" :data-error="block.status === 'failed' || block.error || undefined">
                {{ formatPayload(block.output) }}
              </span>
            </div>
          </div>

          <!-- Inspect Button -->
          <button
            type="button"
            class="inspect-button"
            @click.stop="openInspect(block)"
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M16 8L10.8571 12V10.552L14.1383 8L10.8571 5.448V4L16 8ZM5.14286 10.552L1.86171 8L5.14286 5.448V4L0 8L5.14286 12V10.552ZM9.02514 4L5.59657 12H6.84057L10.2691 4H9.02514Z" fill="currentColor"/>
            </svg>
            查看详情
          </button>
        </div>
      </div>

      <!-- 4. Final Markdown 节点 -->
      <div v-else-if="block.kind === 'markdown'" class="markdown-block" data-testid="final-answer">
        <slot name="markdown" :block="block">
          <div class="markdown-fallback">{{ block.text }}</div>
        </slot>
      </div>
    </template>

    <!-- Inspect Modal 弹窗 -->
    <div v-if="showInspectModal" class="inspect-overlay" @click.self="closeInspect">
      <div class="inspect-dialog">
        <div class="inspect-dialog-header">
          <span class="inspect-dialog-title">工具调用详细数据</span>
          <button type="button" class="inspect-close-btn" @click="closeInspect">✕</button>
        </div>
        <div class="inspect-dialog-body">
          <pre><code>{{ JSON.stringify(inspectPayload, null, 2) }}</code></pre>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.step-stream-container {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 6px 0 12px 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
}

.disclosure-root {
  display: flex;
  flex-direction: column;
  width: 100%;
  min-width: 0;
}

.disclosure-row {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  height: 24px;
  min-width: 0;
  cursor: pointer;
  user-select: none;
  border-radius: 4px;
  padding: 0 4px;
  transition: background-color 100ms ease;
}

.disclosure-row:hover {
  background-color: #f1f5f9;
}

.disclosure-root[data-state='running'] .disclosure-row::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 260px;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(241, 245, 249, 0.8) 55%,
    transparent 100%
  );
  animation: dsh-sweep 2.4s ease-out infinite;
  pointer-events: none;
}

@keyframes dsh-sweep {
  0% { left: -260px; }
  90%, 100% { left: 100%; }
}

.leading-slot {
  position: relative;
  flex: none;
  width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-right: 6px;
  color: #64748b;
}

.icon-idle {
  display: inline-flex;
  opacity: 1;
  transition: opacity 100ms ease;
}

.chevron-hover {
  position: absolute;
  inset: 0;
  margin: auto;
  opacity: 0;
  transition: opacity 100ms ease, transform 150ms ease;
  color: #64748b;
}

.chevron-hover.is-open {
  transform: rotate(0deg);
}

.chevron-hover:not(.is-open) {
  transform: rotate(-90deg);
}

.disclosure-row:hover .icon-idle {
  opacity: 0;
}

.disclosure-row:hover .chevron-hover {
  opacity: 1;
}

.state-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.state-dot.running {
  background-color: #3b82f6;
  animation: pulse-dot 1s infinite;
}

.state-dot.error {
  background-color: #ef4444;
}

@keyframes pulse-dot {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(0.6); opacity: 0.5; }
}

.row-title {
  flex: none;
  font-size: 13px;
  line-height: 24px;
  font-weight: 500;
  color: #334155;
}

.dot-sep {
  flex: none;
  width: 2px;
  height: 2px;
  border-radius: 1px;
  margin: 0 8px;
  background: #94a3b8;
}

.row-summary {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  line-height: 24px;
  color: #64748b;
}

.row-summary.error-text {
  color: #dc2626;
}

.row-suffix {
  flex: none;
  margin-left: 6px;
  font-size: 12px;
  color: #94a3b8;
}

.row-badge {
  flex: none;
  margin-left: 8px;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  line-height: 16px;
  font-weight: 500;
}

.row-badge.recovery {
  background-color: #e0f2fe;
  color: #0369a1;
  border: 1px solid #bae6fd;
}

/* Notice Row */
.notice-root {
  margin: 4px 0;
}

.notice-row {
  display: flex;
  align-items: center;
  height: 24px;
  padding: 0 4px;
  border-radius: 4px;
  background-color: #fffbeb;
  border: 1px solid #fef3c7;
}

.notice-root[data-level='error'] .notice-row {
  background-color: #fef2f2;
  border-color: #fee2e2;
}

.notice-indicator {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: #f59e0b;
}

.notice-root[data-level='error'] .notice-indicator {
  background-color: #ef4444;
}

.notice-content {
  color: #92400e;
  font-size: 12px;
}

.notice-root[data-level='error'] .notice-content {
  color: #b91c1c;
}

/* Think Body */
.think-body {
  padding: 8px 12px 10px 24px;
  background: #fafbfc;
  border-left: 2px solid #e2e8f0;
  margin: 2px 0 6px 8px;
  font-size: 13px;
  line-height: 1.6;
  color: #475569;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow-y: auto;
}

.cursor-blink {
  display: inline-block;
  width: 6px;
  height: 14px;
  background-color: #3b82f6;
  margin-left: 3px;
  vertical-align: middle;
  animation: blink 1s infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* Tool Body */
.tool-body-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 4px 0 8px 24px;
}

.io-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-size: 12px;
  max-height: 280px;
  overflow-y: auto;
}

.io-section {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.io-label {
  font-size: 10px;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
}

.io-text {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 11px;
  color: #1e293b;
  white-space: pre-wrap;
  word-break: break-all;
}

.io-text[data-error] {
  color: #dc2626;
}

.io-divider {
  height: 1px;
  background: #e2e8f0;
  margin: 2px 0;
}

.inspect-button {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #475569;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  padding: 2px 8px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.inspect-button:hover {
  background: #f1f5f9;
  color: #0f172a;
}

/* Reasoning (黑色/深色系，主阅读字号 13px) 与 Tool (浅色系，辅助字号 12px/11px) 样式与字号区分 */
.disclosure-root--reasoning .leading-slot {
  color: #0f172a;
}

.disclosure-root--reasoning .row-title {
  font-size: 13px;
  color: #0f172a;
  font-weight: 600;
}

.disclosure-root--reasoning .row-summary {
  font-size: 13px;
  color: #334155;
}

.disclosure-root--reasoning .disclosure-row:hover {
  background-color: #f1f5f9;
}

.disclosure-root--reasoning .think-body {
  font-size: 13px;
  line-height: 1.6;
  background: #eef2f6;
  border: 1px solid #e2e8f0;
  border-left: 3px solid #0f172a;
  border-radius: 0 4px 4px 0;
  color: #0f172a;
}

.disclosure-root--reasoning .cursor-blink {
  background-color: #0f172a;
}

.disclosure-root--tool .leading-slot {
  color: #94a3b8;
}

.disclosure-root--tool .row-title {
  font-size: 12px;
  color: #64748b;
  font-weight: 500;
}

.disclosure-root--tool .row-summary {
  font-size: 12px;
  color: #94a3b8;
}

.disclosure-root--tool .disclosure-row:hover {
  background-color: #f8fafc;
}

.disclosure-root--tool .io-card {
  border-color: #f1f5f9;
  background: #ffffff;
}

.disclosure-root--tool .io-label {
  font-size: 10px;
  color: #94a3b8;
}

.disclosure-root--tool .io-text {
  font-size: 11px;
  color: #475569;
}

.disclosure-root--tool .inspect-button {
  font-size: 11px;
  color: #64748b;
  border-color: #e2e8f0;
}

.disclosure-root--tool .inspect-button:hover {
  background: #f8fafc;
  color: #334155;
  border-color: #cbd5e1;
}

/* Modal */
.inspect-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 999;
}

.inspect-dialog {
  width: 600px;
  max-width: 90vw;
  max-height: 80vh;
  background: #ffffff;
  border-radius: 8px;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.inspect-dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  border-bottom: 1px solid #e2e8f0;
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
}

.inspect-close-btn {
  background: none;
  border: none;
  font-size: 14px;
  color: #64748b;
  cursor: pointer;
}

.inspect-dialog-body {
  padding: 16px;
  overflow-y: auto;
  font-size: 12px;
}

.inspect-dialog-body pre {
  margin: 0;
  padding: 10px;
  background: #0f172a;
  color: #f8fafc;
  border-radius: 6px;
  font-family: 'JetBrains Mono', Consolas, monospace;
}

/* Activity Row */
.activity-root {
  margin: 2px 0;
}

.activity-row {
  display: flex;
  align-items: center;
  height: 24px;
  min-width: 0;
  padding: 0 4px;
  border-radius: 4px;
  background-color: transparent;
  transition: background-color 100ms ease;
}

.activity-root[data-state='running'] .activity-row {
  position: relative;
  overflow: hidden;
}

.activity-root[data-state='running'] .activity-row::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 260px;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(241, 245, 249, 0.8) 55%,
    transparent 100%
  );
  animation: dsh-sweep 2.4s ease-out infinite;
  pointer-events: none;
}

.activity-icon {
  width: 14px;
  height: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
}

.activity-icon.completed {
  color: #16a34a;
}

.activity-icon.warning {
  color: #d97706;
}

.activity-icon.failed {
  color: #dc2626;
}

.activity-text {
  font-size: 13px;
  line-height: 24px;
  color: #475569;
}

.activity-text--warning {
  color: #b45309;
}

.activity-text--failed {
  color: #b91c1c;
}

.activity-timer {
  flex: none;
  margin-left: 6px;
  font-size: 12px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  color: #3b82f6;
  font-weight: 500;
}

.activity-suffix {
  flex: none;
  margin-left: 4px;
  font-size: 12px;
  color: #94a3b8;
}

.review-finding-root {
  margin: 6px 0;
  padding: 10px 12px;
  border-left: 3px solid #d97706;
  border-radius: 4px;
  background: #fffbeb;
  color: #78350f;
}

.review-finding-title { font-size: 13px; font-weight: 700; }
.review-finding-summary, .review-finding-list p { margin: 4px 0 0; font-size: 12px; line-height: 1.55; }
.review-finding-list { margin: 7px 0 0; padding-left: 18px; }
.review-finding-list li { margin: 6px 0; }
.review-finding-status { margin-left: 6px; font-size: 12px; color: #b45309; }

</style>
