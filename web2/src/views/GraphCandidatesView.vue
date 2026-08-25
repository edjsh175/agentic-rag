<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  applyGraphCandidateBatch,
  getGraphCandidateQuality,
  listGraphCandidateBatches,
  listGraphCandidateItems,
  reviewGraphCandidates,
} from '../api'
import type { GraphCandidateBatch, GraphCandidateItem, GraphQualityReport } from '../types'
import {
  entityTypeLabel,
  linkTypeLabel,
  relationTypeLabel,
  candidateKindLabel,
  docCategoryLabel,
} from '../utils/graphLabels'

const batchStatus = ref('all')
const candidateStatus = ref('all')
const loadingBatches = ref(false)
const loadingCandidates = ref(false)
const actionLoading = ref(false)
const errorMsg = ref('')
const batches = ref<GraphCandidateBatch[]>([])
interface ProcessedCandidateItem extends GraphCandidateItem {
  _summary: string
  _fields: CandidateField[]
  _preview: CandidatePreview
}

const candidates = ref<ProcessedCandidateItem[]>([])
const selectedBatchId = ref('')
const selectedIds = ref<string[]>([])
const quality = ref<GraphQualityReport | null>(null)
const page = ref(1)
const pageSize = ref(30)

const currentBatch = computed(() => batches.value.find((item) => item.id === selectedBatchId.value) || null)
const selectableCandidates = computed(() => candidates.value.filter((item) => item.candidate_kind !== 'diagnostic'))
const totalPages = computed(() => Math.max(1, Math.ceil(candidates.value.length / pageSize.value)))
const displayCandidates = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return candidates.value.slice(start, start + pageSize.value)
})

type CandidateField = {
  label: string
  value: string
}

type CandidatePreview = {
  source: string
  sourceType: string
  edge: string
  target: string
  targetType: string
}

function textValue(value: unknown, fallback = '-') {
  const text = String(value ?? '').trim()
  return text || fallback
}

function candidateSummary(item: GraphCandidateItem) {
  if (item.candidate_kind === 'entity') {
    return `新增实体：${textValue(item.payload.name)}（${entityTypeLabel(textValue(item.payload.entity_type))}）`
  }
  if (item.candidate_kind === 'alias') return `${textValue(item.payload.entity_name)} 又名 ${textValue(item.payload.alias)}`
  if (item.candidate_kind === 'relation') {
    return `${textValue(item.payload.source_name)} --${relationTypeLabel(textValue(item.payload.relation_type))}--> ${textValue(item.payload.target_name)}`
  }
  if (item.candidate_kind === 'field') return `${textValue(item.payload.table_name)} 包含字段 ${textValue(item.payload.field_name)}`
  if (item.candidate_kind === 'link') return `${textValue(item.payload.entity_name)} --证据来自--> ${textValue(item.payload.chunk_id)}`
  return item.payload.message || item.payload.code || '-'
}

function candidateFields(item: GraphCandidateItem): CandidateField[] {
  const chunkId = textValue(item.payload.source_chunk_id || item.payload.chunk_id || item.source_chunk_id, '')
  const rows: CandidateField[] = []
  if (item.candidate_kind === 'entity') {
    rows.push({ label: '实体', value: textValue(item.payload.name) })
    rows.push({ label: '实体类型', value: entityTypeLabel(textValue(item.payload.entity_type)) })
  } else if (item.candidate_kind === 'alias') {
    rows.push({ label: '实体', value: textValue(item.payload.entity_name) })
    rows.push({ label: '别名', value: textValue(item.payload.alias) })
  } else if (item.candidate_kind === 'relation') {
    rows.push({ label: '源实体', value: textValue(item.payload.source_name) })
    rows.push({ label: '关系', value: relationTypeLabel(textValue(item.payload.relation_type)) })
    rows.push({ label: '目标实体', value: textValue(item.payload.target_name) })
  } else if (item.candidate_kind === 'field') {
    rows.push({ label: '数据表', value: textValue(item.payload.table_name) })
    rows.push({ label: '字段', value: textValue(item.payload.field_name) })
  } else if (item.candidate_kind === 'link') {
    rows.push({ label: '实体', value: textValue(item.payload.entity_name) })
    rows.push({ label: '证据 chunk', value: textValue(item.payload.chunk_id) })
    rows.push({ label: '关联类型', value: linkTypeLabel(textValue(item.payload.link_type, 'evidence')) })
  }
  if (chunkId) rows.push({ label: 'chunk_id', value: chunkId })
  if (item.evidence_text) rows.push({ label: '证据文本', value: item.evidence_text })
  return rows
}

function candidatePreview(item: GraphCandidateItem): CandidatePreview {
  if (item.candidate_kind === 'entity') {
    return {
      source: textValue(item.payload.name),
      sourceType: entityTypeLabel(textValue(item.payload.entity_type, 'Entity')),
      edge: '',
      target: '',
      targetType: '',
    }
  }
  if (item.candidate_kind === 'alias') {
    return {
      source: textValue(item.payload.entity_name),
      sourceType: '实体',
      edge: '别名',
      target: textValue(item.payload.alias),
      targetType: '别名',
    }
  }
  if (item.candidate_kind === 'relation') {
    return {
      source: textValue(item.payload.source_name),
      sourceType: '源实体',
      edge: relationTypeLabel(textValue(item.payload.relation_type)),
      target: textValue(item.payload.target_name),
      targetType: '目标实体',
    }
  }
  if (item.candidate_kind === 'field') {
    return {
      source: textValue(item.payload.table_name),
      sourceType: entityTypeLabel('DataTable'),
      edge: relationTypeLabel('has_field'),
      target: textValue(item.payload.field_name),
      targetType: entityTypeLabel('Field'),
    }
  }
  if (item.candidate_kind === 'link') {
    return {
      source: textValue(item.payload.entity_name),
      sourceType: '实体',
      edge: linkTypeLabel('evidence'),
      target: textValue(item.payload.chunk_id),
      targetType: '知识块',
    }
  }
  return {
    source: textValue(item.payload.message || item.payload.code),
    sourceType: '诊断',
    edge: '',
    target: '',
    targetType: '',
  }
}

async function loadBatches() {
  loadingBatches.value = true
  errorMsg.value = ''
  try {
    batches.value = await listGraphCandidateBatches(batchStatus.value === 'all' ? undefined : batchStatus.value)
    if (!batches.value.some((item) => item.id === selectedBatchId.value)) {
      selectedBatchId.value = batches.value[0]?.id || ''
    }
  } catch (error: any) {
    errorMsg.value = error.message || '加载批次失败'
  } finally {
    loadingBatches.value = false
  }
}

async function loadCandidates() {
  if (!selectedBatchId.value) {
    candidates.value = []
    quality.value = null
    return
  }
  loadingCandidates.value = true
  selectedIds.value = []
  errorMsg.value = ''
  try {
    const rawItems = await listGraphCandidateItems(
      selectedBatchId.value,
      candidateStatus.value === 'all' ? undefined : candidateStatus.value,
    )
    candidates.value = rawItems.map((item) => ({
      ...item,
      _summary: candidateSummary(item),
      _fields: candidateFields(item),
      _preview: candidatePreview(item),
    }))
    quality.value = await getGraphCandidateQuality(selectedBatchId.value)
    page.value = 1
  } catch (error: any) {
    errorMsg.value = error.message || '加载候选失败'
  } finally {
    loadingCandidates.value = false
  }
}

async function submitReview(payload: { approve_all?: boolean; approve_ids?: string[]; reject_ids?: string[]; reason?: string }) {
  if (!selectedBatchId.value) return
  actionLoading.value = true
  try {
    await reviewGraphCandidates(selectedBatchId.value, payload)
    await loadBatches()
    await loadCandidates()
  } catch (error: any) {
    errorMsg.value = error.message || '提交审批失败'
  } finally {
    actionLoading.value = false
  }
}

async function applyBatch() {
  if (!selectedBatchId.value) return
  actionLoading.value = true
  try {
    await applyGraphCandidateBatch(selectedBatchId.value)
    await loadBatches()
    await loadCandidates()
  } catch (error: any) {
    errorMsg.value = error.message || '应用批次失败'
  } finally {
    actionLoading.value = false
  }
}

function toggleSelection(candidateId: string, checked: boolean) {
  if (checked) {
    selectedIds.value = [...new Set([...selectedIds.value, candidateId])]
    return
  }
  selectedIds.value = selectedIds.value.filter((item) => item !== candidateId)
}

watch(batchStatus, loadBatches)
watch(selectedBatchId, loadCandidates)
watch(candidateStatus, loadCandidates)

onMounted(loadBatches)

function formatBatchStatus(status: string) {
  const mapping: Record<string, string> = {
    draft: '草稿',
    approved: '已批准',
    rejected: '已拒绝',
    applied: '已应用',
    failed: '失败',
    superseded: '已废弃',
  }
  return mapping[status] || status
}

function formatBatchMode(mode: string) {
  const mapping: Record<string, string> = {
    full: '全量提取',
    incremental: '增量提取',
    profile_sync: '配置同步',
  }
  return mapping[mode] || mode
}

function formatCandidateKind(kind: string) {
  return candidateKindLabel(kind)
}

function formatCandidateStatus(status: string) {
  const mapping: Record<string, string> = {
    pending: '待审批',
    approved: '已批准',
    rejected: '已拒绝',
    applied: '已应用',
  }
  return mapping[status] || status
}
</script>

<template>
  <div class="graph-candidates-page">
    <section class="batch-panel">
      <header class="panel-head">
        <div>
          <p class="eyebrow">图谱候选审批台</p>
          <h2>批次概览</h2>
        </div>
        <select v-model="batchStatus" data-test="batch-status-filter" class="filter-select">
          <option value="all">全部状态</option>
          <option value="draft">草稿</option>
          <option value="approved">已批准</option>
          <option value="rejected">已拒绝</option>
          <option value="applied">已应用</option>
          <option value="failed">应用失败</option>
          <option value="superseded">已废弃</option>
        </select>
      </header>

      <p v-if="errorMsg" class="error-box">{{ errorMsg }}</p>
      <p v-if="loadingBatches" class="muted">加载批次中...</p>

      <ul v-else class="batch-list">
        <li
          v-for="batch in batches"
          :key="batch.id"
          :class="['batch-card', { active: batch.id === selectedBatchId }]"
          :data-test="`batch-${batch.id}`"
          @click="selectedBatchId = batch.id"
        >
          <div class="batch-topline">
            <strong>{{ formatBatchMode(batch.mode) }}</strong>
            <span class="status-pill" :class="batch.status">{{ formatBatchStatus(batch.status) }}</span>
          </div>
          <p class="batch-id">{{ batch.id }}</p>
          <p class="batch-meta">
            总数 {{ batch.stats.total || 0 }} / 待审批 {{ batch.stats.pending || 0 }} / 已批准 {{ batch.stats.approved || 0 }}
          </p>
          <p v-if="batch.error_text" class="batch-error">{{ batch.error_text }}</p>
        </li>
        <li v-if="batches.length === 0" class="empty-state">暂无批次</li>
      </ul>
    </section>

    <section class="candidate-panel">
      <header class="panel-head">
        <div>
          <p class="eyebrow">候选明细</p>
          <h2>{{ currentBatch?.id || '未选择批次' }}</h2>
        </div>
        <div class="head-actions">
          <select v-model="candidateStatus" data-test="candidate-status-filter" class="filter-select">
            <option value="all">全部候选</option>
            <option value="pending">待审批</option>
            <option value="approved">已批准</option>
            <option value="rejected">已拒绝</option>
            <option value="applied">已应用</option>
          </select>
          <button
            class="primary-btn"
            data-test="apply-batch"
            :disabled="currentBatch?.status !== 'approved' || actionLoading"
            @click="applyBatch"
          >
            应用批次
          </button>
        </div>
      </header>

      <div v-if="quality" class="quality-card" data-test="quality-panel">
        <span :class="['quality-pill', quality.ok ? 'ok' : 'bad']">{{ quality.ok ? '质量合格' : '质量受限' }}</span>
        <span>候选数 {{ quality.stats.candidates || 0 }}</span>
        <span v-if="quality.errors.length">错误原因: {{ quality.errors.join(', ') }}</span>
      </div>

      <div class="bulk-actions">
        <button
          class="secondary-btn"
          data-test="approve-all-pending"
          :disabled="!selectedBatchId || actionLoading"
          @click="submitReview({ approve_all: true })"
        >
          批准全部待审批
        </button>
        <button
          class="secondary-btn"
          data-test="approve-selected"
          :disabled="selectedIds.length === 0 || actionLoading"
          @click="submitReview({ approve_ids: selectedIds })"
        >
          批准选中项
        </button>
        <button
          class="secondary-btn danger"
          data-test="reject-selected"
          :disabled="selectedIds.length === 0 || actionLoading"
          @click="submitReview({ reject_ids: selectedIds, reason: 'rejected from admin ui' })"
        >
          驳回选中项
        </button>
      </div>

      <p v-if="loadingCandidates" class="muted">加载候选中...</p>
      <ul v-else class="candidate-list">
        <li v-for="item in displayCandidates" :key="item.id" class="candidate-card" :data-test="`candidate-${item.id}`">
          <label class="candidate-check">
            <input
              v-if="item.candidate_kind !== 'diagnostic'"
              type="checkbox"
              :checked="selectedIds.includes(item.id)"
              :data-test="`select-${item.id}`"
              @change="toggleSelection(item.id, ($event.target as HTMLInputElement).checked)"
            />
            <span class="kind-pill" :class="item.candidate_kind">{{ formatCandidateKind(item.candidate_kind) }}</span>
            <span class="status-pill small" :class="item.status">{{ formatCandidateStatus(item.status) }}</span>
          </label>
          <div class="candidate-body">
            <div class="candidate-readable">
              <p class="candidate-summary">{{ item._summary }}</p>
              <dl v-if="item._fields.length" class="candidate-fields">
                <template v-for="field in item._fields" :key="`${item.id}-${field.label}`">
                  <dt>{{ field.label }}</dt>
                  <dd>{{ field.value }}</dd>
                </template>
              </dl>
              <p v-else class="candidate-evidence">{{ item.evidence_text || '暂无凭证' }}</p>
            </div>
            <div class="mini-graph" :class="item.candidate_kind" :data-test="`preview-${item.id}`">
              <div class="mini-node source">
                <strong>{{ item._preview.source }}</strong>
                <span>{{ item._preview.sourceType }}</span>
              </div>
              <div v-if="item._preview.target" class="mini-edge">
                <span>{{ item._preview.edge }}</span>
              </div>
              <div v-if="item._preview.target" class="mini-node target">
                <strong>{{ item._preview.target }}</strong>
                <span>{{ item._preview.targetType }}</span>
              </div>
            </div>
          </div>
          <div class="candidate-actions">
            <button
              v-if="item.status === 'pending' && item.candidate_kind !== 'diagnostic'"
              class="inline-btn"
              :data-test="`approve-${item.id}`"
              @click="submitReview({ approve_ids: [item.id] })"
            >
              批准
            </button>
            <button
              v-if="item.status === 'pending' && item.candidate_kind !== 'diagnostic'"
              class="inline-btn danger"
              :data-test="`reject-${item.id}`"
              @click="submitReview({ reject_ids: [item.id], reason: 'rejected from admin ui' })"
            >
              驳回
            </button>
          </div>
        </li>
        <li v-if="selectedBatchId && candidates.length === 0" class="empty-state">当前批次没有候选</li>
      </ul>

      <div v-if="candidates.length > pageSize" class="pagination-bar">
        <button class="page-btn" :disabled="page <= 1" @click="page--">上一页</button>
        <span class="page-info">第 {{ page }} / {{ totalPages }} 页 (共 {{ candidates.length }} 条)</span>
        <button class="page-btn" :disabled="page >= totalPages" @click="page++">下一页</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.graph-candidates-page {
  display: grid;
  grid-template-columns: 340px 1fr;
  gap: 18px;
  height: 100%;
  padding: 18px;
  background: #f8fafc;
}

.batch-panel,
.candidate-panel {
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.04);
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px 12px;
  border-bottom: 1px solid #f3f4f6;
}

.eyebrow {
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #3370ff;
  font-weight: 700;
}

h2 {
  font-size: 18px;
  font-weight: 600;
  color: #1e2a41;
  margin-top: 2px;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.filter-select,
.primary-btn,
.secondary-btn {
  height: 36px;
  border-radius: 8px;
  border: 1px solid #e8eaed;
  padding: 0 12px;
  font-size: 13px;
  outline: none;
  transition: all 0.15s;
}

.filter-select {
  background: #fff;
  color: #1e2a41;
  cursor: pointer;
}
.filter-select:focus {
  border-color: #3370ff;
  box-shadow: 0 0 0 2px rgba(51, 112, 255, 0.08);
}

.primary-btn {
  background: #3370ff;
  color: #fff;
  border-color: #3370ff;
  font-weight: 600;
  cursor: pointer;
}
.primary-btn:hover:not(:disabled) {
  background: #2860e0;
  border-color: #2860e0;
}

.secondary-btn {
  background: #fff;
  color: #4b5563;
  font-weight: 600;
  cursor: pointer;
}
.secondary-btn:hover:not(:disabled) {
  background: #f9fafb;
  border-color: #cbd5e1;
  color: #1f2937;
}

.secondary-btn.danger,
.inline-btn.danger {
  color: #ef4444;
}
.secondary-btn.danger:hover:not(:disabled) {
  background: #fff1f2;
  border-color: #fca5a5;
  color: #b91c1c;
}

.primary-btn:disabled,
.secondary-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.batch-list,
.candidate-list {
  flex: 1;
  min-height: 0;
  overflow: auto;
  list-style: none;
  padding: 16px;
}

.batch-card,
.candidate-card {
  padding: 14px;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  background: #fff;
  transition: all 0.15s;
}

.batch-card {
  cursor: pointer;
}
.batch-card:hover {
  border-color: #cbd5e1;
  background: #fafbfc;
}

.batch-card + .batch-card,
.candidate-card + .candidate-card {
  margin-top: 12px;
}

.batch-card.active {
  border-color: #3370ff;
  background: #f8fbff;
  box-shadow: 0 0 0 2px rgba(51, 112, 255, 0.08);
}

.batch-topline,
.candidate-check,
.candidate-actions,
.bulk-actions,
.quality-card {
  display: flex;
  align-items: center;
  gap: 10px;
}

.batch-topline {
  justify-content: space-between;
}

.status-pill,
.kind-pill,
.quality-pill {
  display: inline-flex;
  align-items: center;
  border-radius: 99px;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 600;
}

/* 状态标签配色映射 */
.status-pill.draft { background: #f3f4f6; color: #4b5563; }
.status-pill.pending { background: #fff4d8; color: #b45309; }
.status-pill.approved { background: #def5e9; color: #10b981; }
.status-pill.rejected { background: #fde7e7; color: #ef4444; }
.status-pill.applied { background: #e8f0ff; color: #3370ff; }
.status-pill.failed { background: #fde7e7; color: #ef4444; }
.status-pill.superseded { background: #f3f4f6; color: #9ca3af; }

.status-pill.small {
  padding: 1px 6px;
  font-size: 10px;
}

/* 候选类型标签配色映射 */
.kind-pill.entity { background: #e8f0ff; color: #3370ff; }
.kind-pill.relation { background: #e0f2fe; color: #0284c7; }
.kind-pill.alias { background: #f3e8ff; color: #7c3aed; }
.kind-pill.field { background: #d1fae5; color: #059669; }
.kind-pill.link { background: #ffedd5; color: #ea580c; }
.kind-pill.diagnostic { background: #f1f5f9; color: #475569; }

.quality-card {
  flex-wrap: wrap;
  margin: 14px 16px 0;
  padding: 12px 14px;
  border-radius: 8px;
  background: #fafbfc;
  border: 1px solid #e8eaed;
  font-size: 12px;
  color: #4b5563;
}

.quality-pill.ok {
  background: #def5e9;
  color: #10b981;
}

.quality-pill.bad {
  background: #fde7e7;
  color: #ef4444;
}

.bulk-actions {
  flex-wrap: wrap;
  padding: 14px 16px 0;
}

.batch-id,
.batch-meta,
.candidate-evidence,
.muted,
.empty-state,
.batch-error {
  font-size: 12px;
  color: #5e6673;
}

.batch-id {
  font-family: monospace;
  margin: 4px 0;
}

.batch-meta {
  color: #8a8f99;
}

.candidate-summary {
  margin: 8px 0 6px;
  color: #1e2a41;
  font-size: 14px;
  font-weight: 500;
}

.candidate-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 34%);
  gap: 14px;
  align-items: stretch;
}

.candidate-readable {
  min-width: 0;
}

.candidate-fields {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  gap: 6px 10px;
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  background: #fafbfc;
}

.candidate-fields dt {
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
}

.candidate-fields dd {
  min-width: 0;
  overflow-wrap: anywhere;
  color: #1e2a41;
  font-size: 12px;
  line-height: 1.5;
}

.mini-graph {
  min-height: 108px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid #e8eaed;
  background:
    linear-gradient(135deg, rgba(51, 112, 255, 0.05), rgba(16, 185, 129, 0.05)),
    #fff;
}

.mini-node {
  width: 112px;
  min-height: 56px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  padding: 8px;
  border-radius: 8px;
  border: 1px solid #dbe3ef;
  background: #fff;
  box-shadow: 0 6px 16px rgba(15, 23, 42, 0.06);
}

.mini-node strong {
  color: #1e2a41;
  font-size: 12px;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.mini-node span {
  color: #64748b;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
}

.mini-edge {
  position: relative;
  flex: 1 1 58px;
  min-width: 46px;
  max-width: 110px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #3370ff;
  font-size: 10px;
  font-weight: 700;
}

.mini-edge::before {
  content: '';
  position: absolute;
  left: 0;
  right: 8px;
  top: 50%;
  height: 2px;
  background: #93b4ff;
  transform: translateY(-50%);
}

.mini-edge::after {
  content: '';
  position: absolute;
  right: 0;
  top: 50%;
  border-left: 8px solid #93b4ff;
  border-top: 5px solid transparent;
  border-bottom: 5px solid transparent;
  transform: translateY(-50%);
}

.mini-edge span {
  position: relative;
  z-index: 1;
  max-width: 96px;
  padding: 2px 6px;
  border-radius: 99px;
  background: #fff;
  border: 1px solid #dbeafe;
  overflow-wrap: anywhere;
  text-align: center;
}

.candidate-evidence {
  background: #fafbfc;
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid #e8eaed;
  font-family: monospace;
  margin-top: 4px;
}

.candidate-actions {
  margin-top: 10px;
}

.inline-btn {
  border: none;
  background: transparent;
  color: #3370ff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  transition: all 0.15s;
}
.inline-btn:hover {
  background: #eef2ff;
  color: #2860e0;
}
.inline-btn.danger:hover {
  background: #fff0f0;
  color: #dc2626;
}

.pagination-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 12px 16px;
  border-top: 1px solid #f3f4f6;
  background: #fff;
}

.page-btn {
  height: 28px;
  padding: 0 12px;
  border: 1px solid #e5e7eb;
  background: #fff;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  color: #374151;
  transition: all 0.15s;
}

.page-btn:hover:not(:disabled) {
  background: #f3f4f6;
  border-color: #d1d5db;
}

.page-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.page-info {
  font-size: 12px;
  color: #6b7280;
}

.error-box {
  margin: 12px 16px 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: #fff1f2;
  border: 1px solid #fecaca;
  color: #ef4444;
  font-size: 12px;
}

@media (max-width: 980px) {
  .graph-candidates-page {
    grid-template-columns: 1fr;
    gap: 16px;
  }

  .candidate-body {
    grid-template-columns: 1fr;
  }
}
</style>
