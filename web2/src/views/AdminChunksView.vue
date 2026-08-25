<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { batchReviewChunks, listAdminChunks, updateAdminChunk } from '../api'
import { DOC_CATEGORIES } from '../types'
import type { AdminChunk, DocCategory, ReviewStatus } from '../types'

const FETCH_ALL_PAGE_SIZE = 100

const items = ref<AdminChunk[]>([])
const total = ref(0)
const totalPages = ref(0)
const page = ref(1)
const pageSize = ref(20)
const reviewStatus = ref<ReviewStatus | 'all'>('pending')
const docCategory = ref<DocCategory | 'all'>('all')
const filenameInput = ref('')
const filename = ref('')
const selectedIds = ref(new Set<string>())
const selectAllMatching = ref(false)
const loading = ref(false)
const error = ref('')
const message = ref('')
const busy = ref(false)
const currentChunk = ref<AdminChunk | null>(null)
const draftCategory = ref<DocCategory>('其他')
const draftTitle = ref('')
function formatReviewStatus(status: ReviewStatus) {
  const mapping: Record<ReviewStatus, string> = {
    pending: '待审核',
    approved: '已批准',
    rejected: '已驳回',
  }
  return mapping[status] || status
}

let searchTimer: ReturnType<typeof setTimeout> | undefined

const allSelected = computed({
  get: () => items.value.length > 0 && items.value.every(item => selectedIds.value.has(item.chunk_id)),
  set: (checked: boolean) => {
    selectAllMatching.value = false
    selectedIds.value = checked
      ? new Set(items.value.map(item => item.chunk_id))
      : new Set()
  },
})

const selectionLabel = computed(() => {
  if (selectAllMatching.value && selectedIds.value.size > 0) {
    return `已选择全部 ${selectedIds.value.size} 项（当前筛选）`
  }
  return `已选择 ${selectedIds.value.size} 项`
})

function clearSelection() {
  selectedIds.value = new Set()
  selectAllMatching.value = false
}

function listFilterParams() {
  return {
    review_status: reviewStatus.value,
    doc_category: docCategory.value,
    ...(filename.value ? { filename: filename.value } : {}),
  }
}

async function fetchAllMatchingIds(): Promise<string[]> {
  const ids: string[] = []
  let current = 1
  let pages = 1
  do {
    const result = await listAdminChunks({
      ...listFilterParams(),
      page: current,
      page_size: FETCH_ALL_PAGE_SIZE,
    })
    ids.push(...result.items.map(item => item.chunk_id))
    pages = Math.max(1, result.total_pages)
    current += 1
  } while (current <= pages)
  return ids
}

async function loadChunks(forceRefresh = false) {
  loading.value = true
  error.value = ''
  clearSelection()
  try {
    const params = {
      ...listFilterParams(),
      page: page.value,
      page_size: pageSize.value,
    }
    const result = forceRefresh
      ? await listAdminChunks(params, undefined, true)
      : await listAdminChunks(params)
    items.value = result.items
    total.value = result.total
    totalPages.value = result.total_pages
    if (currentChunk.value) {
      const refreshed = result.items.find(item => item.chunk_id === currentChunk.value?.chunk_id)
      if (refreshed) openDetail(refreshed)
      else closeDetail()
    }
  } catch (e: any) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function resetAndLoad() {
  page.value = 1
  loadChunks()
}

function onFilenameInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    filename.value = filenameInput.value.trim()
    resetAndLoad()
  }, 300)
}

function toggleItem(chunkId: string, checked: boolean) {
  selectAllMatching.value = false
  const next = new Set(selectedIds.value)
  if (checked) next.add(chunkId)
  else next.delete(chunkId)
  selectedIds.value = next
}

function openDetail(item: AdminChunk) {
  currentChunk.value = item
  draftCategory.value = item.doc_category
  draftTitle.value = item.section_title
}

function closeDetail() {
  currentChunk.value = null
  draftTitle.value = ''
}

async function runMutation(action: () => Promise<unknown>, successMessage: string) {
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    await action()
    message.value = successMessage
    await loadChunks()
    if (items.value.length === 0 && page.value > 1) {
      page.value -= 1
      await loadChunks()
    }
  } catch (e: any) {
    error.value = e.message || '操作失败'
  } finally {
    busy.value = false
  }
}

async function saveDetail() {
  if (!currentChunk.value) return
  const chunkId = currentChunk.value.chunk_id
  await runMutation(
    () => updateAdminChunk(chunkId, {
      doc_category: draftCategory.value,
      section_title: draftTitle.value.trim(),
    }),
    '修改已保存',
  )
}

function canReview(item: AdminChunk | null, status: 'approved' | 'rejected') {
  return !!item && item.review_status !== status
}

const selectedItems = computed(() => items.value.filter(item => selectedIds.value.has(item.chunk_id)))
const canBatchApprove = computed(() => {
  if (selectAllMatching.value) {
    return selectedIds.value.size > 0 && reviewStatus.value !== 'approved'
  }
  return selectedItems.value.some(item => item.review_status !== 'approved')
})
const canBatchReject = computed(() => {
  if (selectAllMatching.value) {
    return selectedIds.value.size > 0 && reviewStatus.value !== 'rejected'
  }
  return selectedItems.value.some(item => item.review_status !== 'rejected')
})

async function selectAllResults() {
  if (!total.value) return
  busy.value = true
  error.value = ''
  try {
    const ids = await fetchAllMatchingIds()
    selectedIds.value = new Set(ids)
    selectAllMatching.value = true
  } catch (e: any) {
    error.value = e.message || '全选失败'
  } finally {
    busy.value = false
  }
}

async function reviewOne(item: AdminChunk, status: 'approved' | 'rejected') {
  if (!canReview(item, status)) return
  const label = status === 'approved' ? '通过' : '驳回'
  if (!window.confirm(`确认${label}该知识块？`)) return
  await runMutation(
    () => updateAdminChunk(item.chunk_id, { review_status: status }),
    `知识块已${label}`,
  )
}

async function batchReview(status: 'approved' | 'rejected') {
  const ids = selectAllMatching.value
    ? Array.from(selectedIds.value)
    : selectedItems.value
      .filter(item => item.review_status !== status)
      .map(item => item.chunk_id)
  if (!ids.length) return
  const label = status === 'approved' ? '通过' : '驳回'
  if (!window.confirm(`确认批量${label}当前选中的 ${ids.length} 个知识块？`)) return
  await runMutation(
    () => batchReviewChunks(ids, status),
    `已批量${label} ${ids.length} 个知识块`,
  )
}

async function approveAllMatching() {
  if (!total.value) return
  const count = total.value
  if (!window.confirm(`确认通过当前筛选下全部 ${count} 个知识块？`)) return
  await runMutation(async () => {
    const ids = await fetchAllMatchingIds()
    if (!ids.length) throw new Error('当前筛选下没有可更新的知识块')
    await batchReviewChunks(ids, 'approved')
  }, `已批量通过 ${count} 个知识块`)
}

function goPage(target: number) {
  if (target < 1 || target > totalPages.value || target === page.value) return
  page.value = target
  loadChunks()
}

function changePageSize() {
  page.value = 1
  loadChunks()
}

onMounted(loadChunks)
onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})
</script>

<template>
  <main class="review-page">
    <header class="review-header">
      <div>
        <p class="eyebrow">KNOWLEDGE REVIEW</p>
        <h1>知识块审核台</h1>
        <p class="subtitle">筛选、校对并发布可检索的知识片段</p>
      </div>
      <div class="summary"><strong>{{ total }}</strong><span>条结果</span></div>
    </header>

    <section class="filter-bar">
      <label>分类
        <select data-test="category-filter" v-model="docCategory" @change="resetAndLoad">
          <option value="all">全部分类</option>
          <option v-for="category in DOC_CATEGORIES" :key="category" :value="category">{{ category }}</option>
        </select>
      </label>
      <label>审核状态
        <select v-model="reviewStatus" @change="resetAndLoad">
          <option value="all">全部状态</option>
          <option value="pending">待审核</option>
          <option value="approved">已通过</option>
          <option value="rejected">已驳回</option>
        </select>
      </label>
      <label class="search-label">文件名
        <input data-test="filename-search" v-model="filenameInput" placeholder="搜索文件名" @input="onFilenameInput" />
      </label>
      <button class="button secondary" :disabled="loading" @click="loadChunks(true)">{{ loading ? '刷新中…' : '刷新' }}</button>
    </section>

    <p v-if="error" class="notice error">{{ error }}</p>
    <p v-else-if="message" class="notice success">{{ message }}</p>
    <div class="workspace" :class="{ 'with-detail': currentChunk }">
    <section class="table-card">
      <div class="batch-bar">
        <label class="check-label"><input data-test="select-all" type="checkbox" v-model="allSelected" /> 全选当前页</label>
        <button data-test="select-all-matching" class="text-button" :disabled="busy || loading || total === 0" @click="selectAllResults">全选全部结果</button>
        <span data-test="selection-label">{{ selectionLabel }}</span>
        <div class="batch-actions">
          <button v-if="canBatchApprove" class="button approve" :disabled="busy || selectedIds.size === 0" @click="batchReview('approved')">批量通过</button>
          <button v-if="canBatchReject" data-test="batch-reject" class="button reject" :disabled="busy || selectedIds.size === 0" @click="batchReview('rejected')">批量驳回</button>
          <button data-test="approve-all-matching" class="button approve" :disabled="busy || loading || total === 0 || reviewStatus === 'approved'" @click="approveAllMatching">通过全部筛选结果</button>
        </div>
        <div class="pagination batch-pagination">
          <span>第 {{ page }} / {{ totalPages || 1 }} 页</span>
          <select data-test="page-size-top" v-model="pageSize" @change="changePageSize">
            <option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option>
          </select>
          <button class="page-button" :disabled="page <= 1 || loading" @click="goPage(page - 1)">上一页</button>
          <button data-test="next-page-top" class="page-button" :disabled="page >= totalPages || loading" @click="goPage(page + 1)">下一页</button>
        </div>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th></th><th>文件名</th><th>章节名</th><th>分类</th><th>状态</th><th>内容预览</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-if="!loading && items.length === 0"><td colspan="7" class="empty">没有符合条件的知识块</td></tr>
            <tr v-for="item in items" :key="item.chunk_id">
              <td><input type="checkbox" :checked="selectedIds.has(item.chunk_id)" @change="toggleItem(item.chunk_id, ($event.target as HTMLInputElement).checked)" /></td>
              <td class="file-name">{{ item.file_name }}</td>
              <td>{{ item.section_title || '未命名章节' }}</td>
              <td><span class="category-chip">{{ item.doc_category }}</span></td>
              <td><span class="status-chip" :class="item.review_status">{{ formatReviewStatus(item.review_status) }}</span></td>
              <td class="preview">{{ item.content_preview }}</td>
              <td class="row-actions">
                <button class="text-button" :data-test="`view-${item.chunk_id}`" @click="openDetail(item)">查看</button>
                <button v-if="canReview(item, 'approved')" class="text-button approve-text" :data-test="`approve-${item.chunk_id}`" :disabled="busy" @click="reviewOne(item, 'approved')">通过</button>
                <button v-if="canReview(item, 'rejected')" class="text-button reject-text" :disabled="busy" @click="reviewOne(item, 'rejected')">驳回</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <footer class="pagination">
        <span>第 {{ page }} / {{ totalPages || 1 }} 页</span>
        <select data-test="page-size" v-model="pageSize" @change="changePageSize">
          <option :value="20">20 条/页</option><option :value="50">50 条/页</option><option :value="100">100 条/页</option>
        </select>
        <button class="page-button" :disabled="page <= 1 || loading" @click="goPage(page - 1)">上一页</button>
        <button data-test="next-page" class="page-button" :disabled="page >= totalPages || loading" @click="goPage(page + 1)">下一页</button>
      </footer>
    </section>

    <aside v-if="currentChunk" data-test="detail-panel" class="detail-panel">
      <header class="detail-header">
        <div><p class="eyebrow">CHUNK DETAIL</p><h2>知识块详情</h2></div>
        <button class="close-button" aria-label="关闭详情" @click="closeDetail">×</button>
      </header>
      <dl class="detail-meta">
        <div><dt>文件</dt><dd>{{ currentChunk.file_name }}</dd></div>
        <div><dt>页码</dt><dd>{{ currentChunk.page_label }}</dd></div>
        <div><dt>状态</dt><dd><span class="status-chip" :class="currentChunk.review_status">{{ formatReviewStatus(currentChunk.review_status) }}</span></dd></div>
      </dl>
      <section class="source-card">
        <h3>来源信息</h3>
        <dl class="source-meta">
          <div v-if="currentChunk.title"><dt>文章标题</dt><dd>{{ currentChunk.title }}</dd></div>
          <div><dt>来源文件</dt><dd>{{ currentChunk.source || currentChunk.file_name }}</dd></div>
          <div v-if="currentChunk.file_path"><dt>文件路径</dt><dd>{{ currentChunk.file_path }}</dd></div>
          <div v-if="currentChunk.kb_name"><dt>知识库</dt><dd>{{ currentChunk.kb_name }}</dd></div>
          <div v-if="currentChunk.kb_path"><dt>知识库路径</dt><dd>{{ currentChunk.kb_path }}</dd></div>
          <div v-if="currentChunk.source_url"><dt>来源链接</dt><dd><a :href="currentChunk.source_url" target="_blank" rel="noreferrer">{{ currentChunk.source_url }}</a></dd></div>
          <div v-if="currentChunk.author"><dt>作者</dt><dd>{{ currentChunk.author }}</dd></div>
          <div v-if="currentChunk.platform"><dt>平台</dt><dd>{{ currentChunk.platform }}</dd></div>
          <div v-if="currentChunk.publish_date"><dt>发布时间</dt><dd>{{ currentChunk.publish_date }}</dd></div>
          <div v-if="currentChunk.indexed_at"><dt>入库时间</dt><dd>{{ currentChunk.indexed_at }}</dd></div>
          <div v-if="currentChunk.last_modified"><dt>文件修改</dt><dd>{{ currentChunk.last_modified }}</dd></div>
          <div v-if="currentChunk.crawled_at"><dt>抓取时间</dt><dd>{{ currentChunk.crawled_at }}</dd></div>
          <div><dt>Chunk ID</dt><dd>{{ currentChunk.chunk_id }}</dd></div>
        </dl>
      </section>
      <label class="detail-field">分类
        <select data-test="detail-category" v-model="draftCategory">
          <option v-for="category in DOC_CATEGORIES" :key="category" :value="category">{{ category }}</option>
        </select>
      </label>
      <label class="detail-field">章节标题
        <input data-test="detail-title" v-model="draftTitle" placeholder="输入章节标题" />
      </label>
      <div class="content-label">完整内容</div>
      <pre class="chunk-content">{{ currentChunk.content }}</pre>
      <div class="detail-actions">
        <button data-test="save-detail" class="button primary" :disabled="busy" @click="saveDetail">保存修改</button>
        <button v-if="canReview(currentChunk, 'approved')" class="button approve" :disabled="busy" @click="reviewOne(currentChunk, 'approved')">通过</button>
        <button v-if="canReview(currentChunk, 'rejected')" class="button reject" :disabled="busy" @click="reviewOne(currentChunk, 'rejected')">驳回</button>
      </div>
    </aside>
    </div>
  </main>
</template>

<style scoped>
.review-page { height: 100%; overflow: auto; padding: 24px 24px 32px; background: #f8fafc; color: #1e2a41; }
.review-header { display: flex; align-items: flex-end; justify-content: space-between; max-width: 1500px; margin: 0 auto 20px; }
.eyebrow { color: #3370ff; font-size: 11px; font-weight: 800; letter-spacing: .16em; }
h1 { margin-top: 4px; font-size: 24px; font-weight: 600; color: #1e2a41; letter-spacing: -.02em; }
.subtitle { margin-top: 5px; color: #5e6673; font-size: 13px; }
.summary { display: flex; align-items: baseline; gap: 6px; color: #5e6673; }
.summary strong { color: #3370ff; font-size: 26px; font-weight: 600; }
.filter-bar, .table-card, .detail-panel { background: #fff; border: 1px solid #e8eaed; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.04); }
.filter-bar { max-width: 1500px; margin-inline: auto; }
.filter-bar { display: flex; align-items: end; gap: 14px; padding: 14px 16px; border-radius: 12px 12px 0 0; }
.filter-bar label { display: grid; gap: 5px; color: #5e6673; font-size: 12px; font-weight: 600; }
.filter-bar select, .filter-bar input { min-width: 156px; height: 36px; padding: 0 11px; border: 1px solid #e8eaed; border-radius: 6px; background: #fff; color: #1e2a41; transition: all 0.15s; outline: none; }
.filter-bar select:focus, .filter-bar input:focus { border-color: #3370ff; box-shadow: 0 0 0 2px rgba(51, 112, 255, 0.08); }
.search-label { flex: 1; }.search-label input { width: 100%; }
.button { height: 36px; padding: 0 16px; border: 0; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 13px; transition: all 0.15s; display: inline-flex; align-items: center; justify-content: center; }
.button.secondary { background: #f3f4f6; color: #4b5563; }
.button.secondary:hover { background: #e5e7eb; color: #1f2937; }
.workspace { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; max-width: 1500px; margin-inline: auto; }.workspace.with-detail { grid-template-columns: minmax(0, 1fr) 420px; }
.table-card { border-radius: 0 0 12px 12px; overflow: hidden; min-width: 0; }
.batch-bar { display: flex; align-items: center; gap: 14px; min-height: 44px; padding: 0 16px; border-bottom: 1px solid #f3f4f6; color: #5e6673; font-size: 13px; flex-wrap: wrap; }
.check-label { display: flex; align-items: center; gap: 7px; color: #1e2a41; font-weight: 600; }.batch-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.batch-pagination { margin-left: auto; min-height: 0; padding: 0; border-top: 0; }
.table-scroll { overflow-x: auto; } table { width: 100%; min-width: 1050px; border-collapse: collapse; }
th { padding: 11px 12px; background: #f8fafc; color: #64748b; font-size: 11px; text-align: left; text-transform: uppercase; letter-spacing: .04em; font-weight: 600; border-bottom: 1px solid #f3f4f6; }
td { padding: 12px 12px; border-top: 1px solid #f3f4f6; font-size: 13px; vertical-align: middle; color: #1e2a41; }
tbody tr:hover { background: #fafbfc; }.file-name { max-width: 220px; font-weight: 500; }.preview { max-width: 330px; color: #5e6673; }
.category-chip, .status-chip { display: inline-flex; padding: 2px 8px; border-radius: 99px; white-space: nowrap; font-size: 11px; font-weight: 600; }
.category-chip { background: #e8f0ff; color: #3370ff; }.status-chip.pending { background: #fff4d8; color: #b45309; }.status-chip.approved { background: #def5e9; color: #10b981; }.status-chip.rejected { background: #fde7e7; color: #ef4444; }
.text-button { border: 0; background: none; color: #3370ff; font-weight: 600; cursor: pointer; transition: color 0.15s; font-size: 13px; }.text-button:hover { color: #2860e0; }.text-button:disabled { opacity: .45; cursor: not-allowed; }.row-actions { white-space: nowrap; }.row-actions .text-button + .text-button { margin-left: 10px; }.approve-text { color: #10b981; }.approve-text:hover { color: #059669; }.reject-text { color: #ef4444; }.reject-text:hover { color: #dc2626; }.empty { padding: 48px; color: #9ca3af; text-align: center; }
.notice { max-width: 1500px; margin: 10px auto; padding: 10px 14px; border-radius: 6px; font-size: 13px; }.notice.error { background: #fff0f0; color: #ef4444; }
.notice.success { background: #e8f6ee; color: #10b981; }.button.primary { background: #3370ff; color: #fff; }.button.primary:hover { background: #2860e0; }.button.approve { background: #def5e9; color: #10b981; }.button.approve:hover { background: #d1fae5; }.button.reject { background: #fde7e7; color: #ef4444; }.button.reject:hover { background: #fee2e2; }.button:disabled, .page-button:disabled { opacity: .45; cursor: not-allowed; }
.pagination { display: flex; justify-content: flex-end; align-items: center; gap: 10px; min-height: 52px; padding: 0 16px; border-top: 1px solid #f3f4f6; color: #5e6673; font-size: 12px; }.pagination select, .page-button { height: 32px; border: 1px solid #e8eaed; border-radius: 6px; background: #fff; color: #4b5563; font-size: 12px; outline: none; transition: all 0.15s; }.pagination select { padding: 0 6px; cursor: pointer; }.pagination select:focus { border-color: #3370ff; }.page-button { padding: 0 12px; cursor: pointer; }.page-button:hover:not(:disabled) { background: #f9fafb; border-color: #cbd5e1; }
.detail-panel { position: sticky; top: 0; align-self: start; max-height: calc(100vh - 170px); padding: 18px; border-radius: 12px; overflow: auto; }.detail-header { display: flex; justify-content: space-between; align-items: start; padding-bottom: 14px; border-bottom: 1px solid #f3f4f6; }.detail-header h2 { margin-top: 3px; font-size: 18px; font-weight: 600; color: #1e2a41; }.close-button { border: 0; background: none; color: #9ca3af; font-size: 24px; cursor: pointer; transition: color 0.15s; }.close-button:hover { color: #1e2a41; }.detail-meta { display: grid; gap: 8px; margin: 14px 0; }.detail-meta div { display: grid; grid-template-columns: 58px 1fr; gap: 8px; }.detail-meta dt, .content-label { color: #8a8f99; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; }.detail-meta dd { min-width: 0; overflow-wrap: anywhere; font-size: 13px; color: #1e2a41; }.source-card { margin: 14px 0; padding: 12px; border: 1px solid #e8eaed; border-radius: 8px; background: #fafbfc; }.source-card h3 { margin: 0 0 10px; color: #1e2a41; font-size: 13px; font-weight: 600; }.source-meta { display: grid; gap: 7px; margin: 0; }.source-meta div { display: grid; grid-template-columns: 72px 1fr; gap: 8px; }.source-meta dt { color: #8a8f99; font-size: 11px; font-weight: 600; }.source-meta dd { min-width: 0; margin: 0; overflow-wrap: anywhere; font-size: 12px; color: #1e2a41; }.source-meta a { color: #3370ff; text-decoration: none; }.source-meta a:hover { text-decoration: underline; }.detail-field { display: grid; gap: 5px; margin-top: 12px; color: #5e6673; font-size: 12px; font-weight: 600; }.detail-field select, .detail-field input { height: 36px; padding: 0 10px; border: 1px solid #e8eaed; border-radius: 6px; background: #fff; color: #1e2a41; outline: none; transition: border-color 0.15s; }.detail-field select:focus, .detail-field input:focus { border-color: #3370ff; }.content-label { margin-top: 18px; }.chunk-content { margin-top: 7px; padding: 14px; border-radius: 8px; background: #fafbfc; color: #1e2a41; font: 12px/1.7 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid #e8eaed; }.detail-actions { position: sticky; bottom: -18px; display: flex; gap: 8px; margin: 18px -18px -18px; padding: 12px 18px; border-top: 1px solid #f3f4f6; background: rgba(255,255,255,.96); }
@media (max-width: 1100px) { .workspace.with-detail { grid-template-columns: minmax(0, 1fr) 360px; } }
@media (max-width: 760px) { .review-page { padding: 16px 12px 24px; }.review-header { align-items: start; }.summary { display: none; }.filter-bar { flex-wrap: wrap; }.filter-bar label { width: calc(50% - 7px); }.filter-bar .search-label { width: 100%; flex-basis: 100%; }.filter-bar select { width: 100%; min-width: 0; }.filter-bar .button { flex: 1; }.batch-bar { flex-wrap: wrap; padding-block: 8px; }.batch-actions { width: 100%; margin-left: 0; }.batch-actions .button { flex: 1; }.batch-pagination { width: 100%; margin-left: 0; justify-content: center; }.workspace.with-detail { display: block; }.detail-panel { position: fixed; inset: 56px 0 0; z-index: 30; max-height: none; border-radius: 12px 12px 0 0; box-shadow: 0 -12px 40px rgba(0,0,0,.1); }.pagination { justify-content: center; flex-wrap: wrap; padding-block: 10px; } }
</style>
