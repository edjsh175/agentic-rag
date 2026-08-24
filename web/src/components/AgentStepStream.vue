<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { AgentTimelineItem } from '../types'

const props = defineProps<{
  items: AgentTimelineItem[]
  loading?: boolean
  activeStatus?: string
}>()

// 最新执行项默认展开；历史项默认折叠，用户操作会覆盖默认值。
const expandedMap = ref<Record<number, boolean>>({})

function isExpanded(index: number): boolean {
  const override = expandedMap.value[index]
  return override === undefined ? index === props.items.length - 1 : override
}

function toggleExpand(index: number) {
  expandedMap.value[index] = !isExpanded(index)
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

function getToolTitle(item: Extract<AgentTimelineItem, { type: 'tool_call' }>): string {
  switch (item.tool) {
    case 'retrieve_kb':
      return '知识库检索'
    case 'web_search':
      return '网页搜索'
    case 'understand':
      return '意图理解'
    case 'rewrite':
      return '查询改写'
    case 'link_entities':
      return '图谱实体检索'
    case 'reuse_evidence':
      return '复用已有证据'
    case 'clarify':
      return '反问澄清'
    case 'environment.read_status':
      return '系统状态读取'
    default:
      if (item.tool.startsWith('environment.')) {
        return `环境工具: ${item.tool.replace('environment.', '')}`
      }
      return item.tool
  }
}

function getToolSummary(item: Extract<AgentTimelineItem, { type: 'tool_call' }>): string {
  if (item.error) return item.error
  if (item.description) return item.description
  if (item.in?.query) return String(item.in.query)
  if (item.in?.command) return String(item.in.command)
  if (item.out?.summary) return String(item.out.summary)
  return item.tool
}

function formatNoticeContent(raw: string): string {
  return raw || ''
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
</script>

<template>
  <div v-if="(items && items.length > 0) || (loading && activeStatus)" class="step-stream-container">
    <template v-for="(item, index) in items" :key="item.eventKey || index">
      <!-- 0. 状态/回退通知节点 (Notice / Fallback Row) -->
      <div
        v-if="item.type === 'notice'"
        class="disclosure-root notice-root"
        :data-level="item.level || 'warning'"
      >
        <div class="notice-row">
          <div class="leading-slot">
            <span class="notice-indicator"></span>
          </div>
          <span class="row-title">系统提示</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary notice-content">{{ formatNoticeContent(item.content) }}</span>
        </div>
      </div>

      <!-- 1. 上下文注入节点 (Context Injection Row) -->
      <div
        v-else-if="item.type === 'context_inject'"
        class="disclosure-root"
        data-variant="context"
      >
        <div
          class="disclosure-row"
          role="button"
          tabindex="0"
          :aria-expanded="isExpanded(index)"
          @click="toggleExpand(index)"
        >
          <div class="leading-slot">
            <svg class="icon-idle" width="14" height="14" viewBox="0 0 16 16" fill="none">
              <path d="M14.5 13.5H1.5V2.5H14.5V13.5ZM1.5 1.5C0.947715 1.5 0.5 1.94772 0.5 2.5V13.5C0.5 14.0523 0.947715 14.5 1.5 14.5H14.5C15.0523 14.5 15.5 14.0523 15.5 13.5V2.5C15.5 1.94772 15.0523 1.5 14.5 1.5H1.5Z" fill="currentColor"/>
              <path d="M4 5H12V6H4V5ZM4 8H12V9H4V8ZM4 11H9V12H4V11Z" fill="currentColor"/>
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

          <span class="row-title">上下文注入</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary">{{ item.label }}</span>
        </div>

        <div v-show="isExpanded(index)" class="context-body">
          <div class="context-code">{{ item.details }}</div>
        </div>
      </div>

      <!-- 2. 意图理解节点 (Understanding Row) -->
      <div
        v-else-if="item.type === 'understanding'"
        class="disclosure-root"
        data-variant="understanding"
      >
        <div
          class="disclosure-row"
          role="button"
          tabindex="0"
          :aria-expanded="isExpanded(index)"
          @click="toggleExpand(index)"
        >
          <div class="leading-slot">
            <span class="state-dot" style="background-color: #06b6d4;"></span>
          </div>
          <span class="row-title">意图理解</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary">{{ item.summary }}</span>
          <span v-if="item.entity" class="row-badge">{{ item.entity }}</span>
        </div>
        <div v-show="isExpanded(index)" class="context-body">
          <div class="context-code">主体状态: {{ item.identity_status || '已确认' }} | 任务类型: {{ item.task_type || '知识问答' }}</div>
        </div>
      </div>

      <!-- 3. 控制器决策节点 (Decision Row) -->
      <div
        v-else-if="item.type === 'decision'"
        class="disclosure-root"
        data-variant="decision"
      >
        <div
          class="disclosure-row"
          role="button"
          tabindex="0"
          :aria-expanded="isExpanded(index)"
          @click="toggleExpand(index)"
        >
          <div class="leading-slot">
            <span class="state-dot" style="background-color: #8b5cf6;"></span>
          </div>
          <span class="row-title">控制器决策</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary">{{ item.reason }}</span>
          <span v-if="item.tool" class="row-badge">{{ item.tool }}</span>
        </div>
        <div v-show="isExpanded(index)" class="context-body">
          <div class="context-code">
            动作: {{ item.action }} {{ item.tool ? `(${item.tool})` : '' }}
            <template v-if="item.gap"><br />缺失事实 (Gap): {{ item.gap }}</template>
            <template v-if="item.expected_gain"><br />预期增量: {{ item.expected_gain }}</template>
          </div>
        </div>
      </div>

      <!-- 4. 守卫检查节点 (Guard Row) -->
      <div
        v-else-if="item.type === 'guard'"
        class="disclosure-root"
        :data-state="item.allowed ? 'ok' : 'error'"
      >
        <div class="disclosure-row">
          <div class="leading-slot">
            <span class="state-dot" :class="item.allowed ? 'ok' : 'error'"></span>
          </div>
          <span class="row-title">执行守卫</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary" :class="{ 'error-text': !item.allowed }">{{ item.message }}</span>
        </div>
      </div>

      <!-- 5. 证据增量节点 (Evidence Update Row) -->
      <div
        v-else-if="item.type === 'evidence_update'"
        class="disclosure-root"
        data-variant="evidence"
        :data-state="item.status === 'NO_PROGRESS' ? 'error' : 'ok'"
      >
        <div class="disclosure-row">
          <div class="leading-slot">
            <span class="state-dot" :class="item.status === 'NO_PROGRESS' ? 'error' : 'ok'"></span>
          </div>
          <span class="row-title">证据增量</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary" :class="{ 'error-text': item.status === 'NO_PROGRESS' }">
            <template v-if="item.status === 'NO_PROGRESS'">NO_PROGRESS：没有发现新增证据。</template>
            <template v-else>新增 {{ item.new_chunks }} 个文档片段，{{ item.new_entities }} 个实体。</template>
            当前覆盖：{{ item.coverage || 'PARTIAL' }}
          </span>
          <span v-if="item.status" class="row-badge">{{ item.status }}</span>
        </div>
      </div>

      <!-- 6. 证据缺口 (Evidence Gap Row) -->
      <div
        v-else-if="item.type === 'evidence_gap'"
        class="disclosure-root"
        data-variant="evidence-gap"
        data-state="error"
      >
        <div class="disclosure-row">
          <div class="leading-slot">
            <span class="state-dot error"></span>
          </div>
          <span class="row-title">证据缺口</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary error-text">
            {{ [...(item.missing_facts || []), ...(item.missing_relations || [])].join('；') || item.reason || '仍缺少回答所需的关键证据。' }}
          </span>
          <span class="row-badge heuristic">{{ item.coverage }}</span>
        </div>
      </div>

      <!-- 7. 证据门禁评估 (Finalization Check Row) -->
      <div
        v-else-if="item.type === 'finalization_check'"
        class="disclosure-root"
        data-variant="finalization"
      >
        <div class="disclosure-row">
          <div class="leading-slot">
            <span class="state-dot" style="background-color: #f59e0b;"></span>
          </div>
          <span class="row-title">证据门禁</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary">{{ item.message }}</span>
          <span v-if="item.forced" class="row-badge heuristic">预算终态</span>
        </div>
      </div>

      <!-- 8. 候选生成状态 (Candidate Status Row) -->
      <div
        v-else-if="item.type === 'candidate_status'"
        class="disclosure-root"
        data-variant="candidate"
      >
        <div class="disclosure-row">
          <div class="leading-slot">
            <span class="state-dot running"></span>
          </div>
          <span class="row-title">回答组织</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary">{{ item.message }}</span>
        </div>
      </div>

      <div
        v-else-if="item.type === 'helper_grounding_review_started'"
        class="disclosure-root"
        data-variant="review"
      >
        <div class="disclosure-row">
          <div class="leading-slot">
            <span class="state-dot running"></span>
          </div>
          <span class="row-title">证据审查 ({{ item.review_count === 1 ? '初审' : '二审' }})</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary">{{ item.message }}</span>
        </div>
      </div>

      <!-- 9. 证据审查节点 (Review Status Row) -->
      <div
        v-else-if="item.type === 'review_status'"
        class="disclosure-root"
        :data-state="item.verdict === 'PASS' ? 'ok' : 'error'"
      >
        <div
          class="disclosure-row"
          role="button"
          tabindex="0"
          :aria-expanded="isExpanded(index)"
          @click="toggleExpand(index)"
        >
          <div class="leading-slot">
            <span class="state-dot" :class="item.verdict === 'PASS' ? 'ok' : 'error'"></span>
          </div>
          <span class="row-title">证据审查 ({{ item.review_count === 1 ? '初审' : '二审' }})</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary" :class="{ 'error-text': item.verdict !== 'PASS' }">{{ item.message }}</span>
          <span class="row-badge" :class="item.verdict === 'PASS' ? 'recovery' : 'heuristic'">{{ item.verdict }}</span>
        </div>
        <div
          v-if="(item.claim_reviews && item.claim_reviews.length > 0) || (item.rewrite_actions && item.rewrite_actions.length > 0) || item.error"
          v-show="isExpanded(index)"
          class="context-body"
        >
          <div class="context-code">
            <div v-for="(c, cIdx) in (item.claim_reviews || [])" :key="cIdx" style="margin-bottom: 4px;">
              • {{ c.claim_id ? `${c.claim_id} · ` : '' }}[{{ c.status === 'supported' ? '已验证' : '未支持' }}] {{ c.claim || c.statement }}
              <template v-if="c.evidence_ids?.length">（证据 {{ c.evidence_ids.join(', ') }}）</template>
            </div>
            <div v-for="(action, actionIdx) in (item.rewrite_actions || [])" :key="`action-${actionIdx}`" style="margin-bottom: 4px;">
              ↳ {{ action.claim_id }}：{{ action.action }}<template v-if="action.instruction"> — {{ action.instruction }}</template>
            </div>
            <div v-if="item.error" class="error-text">审核错误：{{ item.error }}</div>
          </div>
        </div>
      </div>

      <!-- 10. 定向重写节点 (Rewrite Status Row) -->
      <div
        v-else-if="item.type === 'rewrite_status'"
        class="disclosure-root"
        data-variant="rewrite"
        :data-state="item.status === 'failed' ? 'error' : (item.status === 'started' ? 'running' : 'ok')"
      >
        <div class="disclosure-row">
          <div class="leading-slot">
            <span class="state-dot" :class="item.status === 'failed' ? 'error' : (item.status === 'started' ? 'running' : 'ok')"></span>
          </div>
          <span class="row-title">定向修正</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary" :class="{ 'error-text': item.status === 'failed' }">
            {{ item.message || (item.status === 'started' ? '正在修正未受支持内容...' : (item.status === 'failed' ? '修正失败' : '修正完成')) }}
          </span>
        </div>
      </div>

      <!-- 11. 发布节点 (Publication Row) -->
      <div
        v-else-if="item.type === 'publication'"
        class="disclosure-root"
        data-variant="publication"
      >
        <div class="disclosure-row">
          <div class="leading-slot">
            <span class="state-dot" style="background-color: #3b82f6;"></span>
          </div>
          <span class="row-title">答案发布</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary">{{ item.message }} [模式: {{ item.final_mode }}]</span>
        </div>
      </div>

      <!-- 12. 错误节点 (Error Row) -->
      <div
        v-else-if="item.type === 'error'"
        class="disclosure-root"
        data-variant="error"
        data-state="error"
      >
        <div class="disclosure-row">
          <div class="leading-slot">
            <span class="state-dot error"></span>
          </div>
          <span class="row-title">执行错误</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary error-text">{{ item.message }}</span>
          <span v-if="item.stage || item.phase || item.code" class="row-badge heuristic">
            {{ item.stage || item.phase || item.code }}
          </span>
        </div>
      </div>

      <!-- 13. Think 思考节点 (Reasoning Row) -->
      <div
        v-else-if="item.type === 'think'"
        class="disclosure-root"
        data-variant="think"
        :data-state="item.isThinking ? 'running' : 'ok'"
      >
        <div
          class="disclosure-row"
          role="button"
          tabindex="0"
          :aria-expanded="isExpanded(index)"
          @click="toggleExpand(index)"
        >
          <!-- 16px Leading Slot with Think Icon / Chevron -->
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

          <span class="row-title">思考过程</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span class="row-summary" :data-follow-end="item.isThinking || undefined">
            {{ item.isThinking ? latestLine(item.content) : firstLine(item.content) }}
          </span>
          <span v-if="item.duration && !item.isThinking" class="row-suffix">[{{ item.duration }}]</span>
        </div>

        <div v-show="isExpanded(index)" class="think-body">
          {{ item.content }}
          <span v-if="item.isThinking" class="cursor-blink"></span>
        </div>
      </div>

      <!-- 14. Tool Call 节点 (Tool Row with IN/OUT Gutter Card) -->
      <div
        v-else-if="item.type === 'tool_call'"
        class="disclosure-root"
        :data-variant="item.tool"
        :data-state="item.status === 'running' ? 'running' : (item.status === 'failed' || item.error ? 'error' : 'ok')"
      >
        <div
          class="disclosure-row"
          role="button"
          tabindex="0"
          :aria-expanded="isExpanded(index)"
          @click="toggleExpand(index)"
        >
          <!-- 16px Leading Slot -->
          <div class="leading-slot">
            <span v-if="item.status === 'failed' || item.error" class="state-dot error"></span>
            <span v-else-if="item.status === 'running'" class="state-dot running"></span>
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

          <span class="row-title">{{ getToolTitle(item) }}</span>
          <span class="dot-sep" aria-hidden="true"></span>
          <span
            class="row-summary"
            :class="{ 'error-text': item.status === 'failed' || item.error }"
          >
            {{ getToolSummary(item) }}
          </span>
          <span v-if="item.gap" class="row-badge recovery">定向补检</span>
        </div>

        <!-- Expanded Body: Gutter-Labeled IN/OUT Card from DeepSeek-Harness -->
        <div v-show="isExpanded(index)" class="tool-body-wrap">
          <div class="io-card">
            <!-- IN Section -->
            <div v-if="item.in !== undefined && item.in !== null" class="io-section">
              <span class="io-label">输入参数</span>
              <span class="io-text">{{ formatPayload(item.in) }}</span>
            </div>

            <span v-if="item.in !== undefined && item.out !== undefined" class="io-divider" aria-hidden="true"></span>

            <!-- OUT Section -->
            <div v-if="item.out !== undefined && item.out !== null" class="io-section">
              <span class="io-label">执行结果</span>
              <span class="io-text" :data-error="item.status === 'failed' || item.error || undefined">
                {{ formatPayload(item.out) }}
              </span>
            </div>
          </div>

          <!-- Inspect Pill Button -->
          <button
            type="button"
            class="inspect-button"
            @click.stop="openInspect(item)"
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M16 8L10.8571 12V10.552L14.1383 8L10.8571 5.448V4L16 8ZM5.14286 10.552L1.86171 8L5.14286 5.448V4L0 8L5.14286 12V10.552ZM9.02514 4L5.59657 12H6.84057L10.2691 4H9.02514Z" fill="currentColor"/>
            </svg>
            查看详情
          </button>
        </div>
      </div>
    </template>


    <!-- 正在运行的实时状态 -->
    <div v-if="loading && activeStatus" class="disclosure-root" data-state="running">
      <div class="disclosure-row">
        <div class="leading-slot">
          <span class="state-dot running"></span>
        </div>
        <span class="row-title">{{ activeStatus }}</span>
        <span class="dot-sep" aria-hidden="true"></span>
        <span class="row-summary">执行中...</span>
      </div>
    </div>

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

/* Single-Line 24px Disclosure Row (DeepSeek-Harness pattern) */
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

/* Running sweep glare animation */
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

/* Leading 16px slot */
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

.row-badge.heuristic {
  background-color: #fef3c7;
  color: #92400e;
  border: 1px solid #fde68a;
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
  color: #991b1b;
}

/* Expanded Think Body */
.think-body {
  padding: 4px 0 6px 24px;
  color: #475569;
  font-size: 13px;
  line-height: 22px;
  white-space: pre-wrap;
  word-break: break-word;
}

.cursor-blink {
  display: inline-block;
  width: 4px;
  height: 12px;
  background-color: #3b82f6;
  margin-left: 2px;
  vertical-align: middle;
  animation: blink 1s infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* Expanded Context Body */
.context-body {
  padding: 4px 0 6px 24px;
}

.context-code {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 8px 12px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 11px;
  color: #334155;
  white-space: pre-wrap;
  word-break: break-all;
}

/* Tool Body Wrap with IN/OUT Card (figma 1249:35657) */
.tool-body-wrap {
  display: flex;
  flex-direction: column;
  margin-left: 22px;
}

.io-card {
  display: flex;
  flex-direction: column;
  margin: 4px 0 4px 0;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #f8fafc;
  font-family: 'JetBrains Mono', Consolas, monospace;
}

.io-section {
  display: grid;
  grid-template-columns: max-content 1fr;
  column-gap: 14px;
  align-items: baseline;
  padding: 10px 14px;
  max-height: 160px;
  overflow-y: auto;
}

.io-section::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

.io-section::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 4px;
}

.io-label {
  position: sticky;
  top: 0;
  align-self: start;
  font-size: 10px;
  font-weight: 700;
  color: #94a3b8;
  letter-spacing: 0.5px;
}

.io-divider {
  flex: none;
  height: 1px;
  background: #e2e8f0;
}

.io-text {
  min-width: 0;
  white-space: pre-wrap;
  word-break: break-all;
  color: #334155;
  font-size: 11px;
  line-height: 1.5;
}

.io-text[data-error] {
  color: #dc2626;
}

/* Inspect Pill Button */
.inspect-button {
  display: inline-flex;
  align-self: flex-start;
  align-items: center;
  gap: 4px;
  margin: 2px 0 4px 0;
  padding: 2px 8px;
  border: 1px solid #e2e8f0;
  border-radius: 999px;
  background: #ffffff;
  color: #64748b;
  font-size: 11px;
  line-height: 16px;
  cursor: pointer;
  opacity: 0.85;
  transition: all 120ms ease;
}

.tool-body-wrap:hover .inspect-button,
.inspect-button:focus-visible {
  opacity: 1;
}

.inspect-button:hover {
  background: #f1f5f9;
  color: #0f172a;
  border-color: #cbd5e1;
}

/* Inspect Modal */
.inspect-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}

.inspect-dialog {
  background: #ffffff;
  border-radius: 10px;
  width: 90%;
  max-width: 680px;
  max-height: 80vh;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.inspect-dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
}

.inspect-dialog-title {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
}

.inspect-close-btn {
  background: none;
  border: none;
  font-size: 14px;
  color: #94a3b8;
  cursor: pointer;
}

.inspect-close-btn:hover {
  color: #334155;
}

.inspect-dialog-body {
  padding: 16px;
  overflow-y: auto;
  background: #0f172a;
}

.inspect-dialog-body pre {
  margin: 0;
  color: #f8fafc;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
}
</style>
