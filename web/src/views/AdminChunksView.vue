<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  batchReviewChunks,
  listAdminDocuments,
  listDocumentChunks,
  updateAdminChunk,
} from '../api'
import { DOC_CATEGORIES } from '../types'
import type { AdminChunk, AdminDoc, DocCategory, ReviewStatus } from '../types'

// ---- 筛选状态 ----
const docCategory = ref<DocCategory | 'all'>('all')
const auditStatus = ref<'all' | 'pending' | 'done'>('all')
const filenameInput = ref('')
const filename = ref('')

// ---- 文档列表状态 ----
const docs = ref<AdminDoc[]>([])
const total = ref(0)
const totalPages = ref(0)
const page = ref(1)
const pageSize = ref(50)
const loading = ref(false)
const error = ref('')
const message = ref('')
const busy = ref(false)

// ---- 展开的文档 ----
const expandedFileName = ref<string | null>(null)
const expandedChunks = ref<AdminChunk[]>([])
const chunksLoading = ref(false)

// ---- 单 Chunk 详情侧边栏 ----
const currentChunk = ref<AdminChunk | null>(null)
const draftCategory = ref<DocCategory>('其他')
const draftTitle = ref('')

let searchTimer: ReturnType<typeof setTimeout> | undefined

// ---- 格式化工具 ----
function formatReviewStatus(status: ReviewStatus) {
  const mapping: Record<ReviewStatus, string> = {
    pending: '待审核',
    approved: '已批准',
    rejected: '已驳回',
  }
  return mapping[status] || status
}

function progressLabel(doc: AdminDoc) {
  if (doc.total_count === 0) return '无切块'
  if (doc.pending_count === 0) return '已完成'
  return `待审核 ${doc.pending_count}/${doc.total_count}`
}

function progressPercent(doc: AdminDoc) {
  if (!doc.total_count) return 0
  return Math.round(((doc.approved_count + doc.rejected_count) / doc.total_count) * 100)
}

function docAuditClass(doc: AdminDoc) {
  if (!doc.total_count) return 'empty'
  if (doc.pending_count === 0) return 'done'
  if (doc.approved_count > 0 || doc.rejected_count > 0) return 'partial'
  return 'pending'
}

// ---- 数据加载 ----
async function loadDocs(forceRefresh = false) {
  loading.value = true
  error.value = ''
  try {
    const result = await listAdminDocuments(
      {
        doc_category: docCategory.value,
        filename: filename.value || undefined,
        audit_status: auditStatus.value,
        page: page.value,
        page_size: pageSize.value,
      },
      undefined,
      forceRefresh,
    )
    docs.value = result.items
    total.value = result.total
    totalPages.value = result.total_pages
    if (expandedFileName.value && !docs.value.find(d => d.file_name === expandedFileName.value)) {
      collapseDoc()
    }
  } catch (e: any) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function resetAndLoad() {
  page.value = 1
  loadDocs()
}

function onFilenameInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    filename.value = filenameInput.value.trim()
    resetAndLoad()
  }, 300)
}

// ---- 展开/收起文档 Chunk ----
async function toggleDoc(doc: AdminDoc) {
  if (expandedFileName.value === doc.file_name) {
    collapseDoc()
    return
  }
  expandedFileName.value = doc.file_name
  expandedChunks.value = []
  currentChunk.value = null
  chunksLoading.value = true
  try {
    const result = await listDocumentChunks(doc.file_name, doc.file_path)
    expandedChunks.value = result.items
  } catch (e: any) {
    error.value = e.message || '加载切块失败'
    expandedFileName.value = null
  } finally {
    chunksLoading.value = false
  }
}

function collapseDoc() {
  expandedFileName.value = null
  expandedChunks.value = []
  currentChunk.value = null
}

// ---- 单 Chunk 详情 ----
function openDetail(item: AdminChunk) {
  currentChunk.value = item
  draftCategory.value = item.doc_category
  draftTitle.value = item.section_title
}

function closeDetail() {
  currentChunk.value = null
  draftTitle.value = ''
}

// ---- 通用 mutation 包装 ----
async function runMutation(action: () => Promise<unknown>, successMessage: string) {
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    await action()
    message.value = successMessage
    await loadDocs(true)
    if (expandedFileName.value) {
      const doc = docs.value.find(d => d.file_name === expandedFileName.value)
      if (doc) {
        const result = await listDocumentChunks(doc.file_name, doc.file_path)
        expandedChunks.value = result.items
        if (currentChunk.value) {
          const refreshed = expandedChunks.value.find(c => c.chunk_id === currentChunk.value?.chunk_id)
          if (refreshed) openDetail(refreshed)
          else closeDetail()
        }
      }
    }
  } catch (e: any) {
    error.value = e.message || '操作失败'
  } finally {
    busy.value = false
  }
}

// ---- 整篇审核 ----
async function reviewDoc(doc: AdminDoc, status: 'approved' | 'rejected') {
  const label = status === 'approved' ? '通过' : '驳回'
  if (!window.confirm(`确认${label}《${doc.file_name}》下的全部 ${doc.total_count} 个知识块？`)) return
  await runMutation(
    () => batchReviewChunks(doc.chunk_ids, status),
    `已${label}《${doc.file_name}》全部 ${doc.chunk_ids.length} 个知识块`,
  )
}

// ---- 单 Chunk 审核 ----
function canReview(item: AdminChunk | null, status: 'approved' | 'rejected') {
  return !!item && item.review_status !== status
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

// ---- 单 Chunk 保存 ----
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

// ---- 分页 ----
function goPage(target: number) {
  if (target < 1 || target > totalPages.value || target === page.value) return
  page.value = target
  loadDocs()
}

function changePageSize() {
  page.value = 1
  loadDocs()
}

onMounted(loadDocs)
onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})
</script>

<template>
  <main class="review-page">
    <header class="review-header">
      <div>
        <p class="eyebrow">KNOWLEDGE REVIEW</p>
        <h1>知识库审核台</h1>
        <p class="subtitle">按来源文档集中审核，支持整篇通过/驳回</p>
      </div>
      <div class="summary"><strong>{{ total }}</strong><span>份文档</span></div>
    </header>

    <section class="filter-bar">
      <label>分类
        <select v-model="docCategory" @change="resetAndLoad">
          <option value="all">全部分类</option>
          <option v-for="cat in DOC_CATEGORIES" :key="cat" :value="cat">{{ cat }}</option>
        </select>
      </label>
      <label>审核状态
        <select v-model="auditStatus" @change="resetAndLoad">
          <option value="all">全部文档</option>
          <option value="pending">含待审核</option>
          <option value="done">已全部审核</option>
        </select>
      </label>
      <label class="search-label">文件名
        <input v-model="filenameInput" placeholder="搜索文件名" @input="onFilenameInput" />
      </label>
      <button class="button secondary" :disabled="loading" @click="loadDocs(true)">
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </section>

    <p v-if="error" class="notice error">{{ error }}</p>
    <p v-else-if="message" class="notice success">{{ message }}</p>

    <div class="workspace" :class="{ 'with-detail': currentChunk }">
      <section class="doc-list-card">
        <div class="toolbar">
          <span class="toolbar-count">共 {{ total }} 份文档</span>
          <div class="pagination">
            <button class="page-button" :disabled="page <= 1 || loading" @click="goPage(page - 1)">上一页</button>
            <span>第 {{ page }} / {{ totalPages || 1 }} 页</span>
            <select v-model="pageSize" @change="changePageSize">
              <option :value="20">20 条/页</option>
              <option :value="50">50 条/页</option>
              <option :value="100">100 条/页</option>
            </select>
            <button class="page-button" :disabled="page >= totalPages || loading" @click="goPage(page + 1)">下一页</button>
          </div>
          <span></span>
        </div>

        <div v-if="!loading && docs.length === 0" class="empty">没有符合条件的文档</div>

        <div v-for="doc in docs" :key="doc.file_name" class="doc-row-wrap">
          <div class="doc-row" :class="{ expanded: expandedFileName === doc.file_name }" @click="toggleDoc(doc)">
            <span class="expand-icon" aria-hidden="true">{{ expandedFileName === doc.file_name ? '▾' : '▸' }}</span>

            <div class="doc-info">
              <div class="doc-title">{{ doc.file_name }}</div>
              <div class="doc-meta">
                <span class="category-chip">{{ doc.doc_category }}</span>
                <span v-if="doc.kb_name" class="meta-tag">{{ doc.kb_name }}</span>
                <span v-if="doc.indexed_at" class="meta-tag">{{ doc.indexed_at.slice(0, 10) }}</span>
              </div>
            </div>

            <div class="doc-progress">
              <div class="progress-label" :class="docAuditClass(doc)">{{ progressLabel(doc) }}</div>
              <div class="progress-bar-wrap">
                <div class="progress-bar" :style="{ width: progressPercent(doc) + '%' }" :class="docAuditClass(doc)"></div>
              </div>
            </div>

            <div class="doc-actions">
              <button
                class="approve-sm"
                :disabled="busy || doc.pending_count === 0"
                @click.stop="reviewDoc(doc, 'approved')"
              >整篇通过</button>
              <button
                class="reject-sm"
                :disabled="busy || doc.total_count === 0"
                @click.stop="reviewDoc(doc, 'rejected')"
              >整篇驳回</button>
            </div>
          </div>

          <div v-if="expandedFileName === doc.file_name" class="chunks-panel">
            <div v-if="chunksLoading" class="chunks-loading">加载切块中…</div>
            <table v-else class="chunks-table">
              <thead>
                <tr>
                  <th>章节</th>
                  <th>页码</th>
                  <th>状态</th>
                  <th>内容预览</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="expandedChunks.length === 0">
                  <td colspan="5" class="empty">该文档暂无切块数据</td>
                </tr>
                <tr
                  v-for="chunk in expandedChunks"
                  :key="chunk.chunk_id"
                  :class="{ 'active-chunk': currentChunk?.chunk_id === chunk.chunk_id }"
                >
                  <td>{{ chunk.section_title || '—' }}</td>
                  <td>{{ chunk.page_label }}</td>
                  <td><span class="status-chip" :class="chunk.review_status">{{ formatReviewStatus(chunk.review_status) }}</span></td>
                  <td class="preview">{{ chunk.content_preview }}</td>
                  <td class="row-actions">
                    <button class="text-button" @click="openDetail(chunk)">查看</button>
                    <button v-if="canReview(chunk, 'approved')" class="text-button approve-text" :disabled="busy" @click="reviewOne(chunk, 'approved')">通过</button>
                    <button v-if="canReview(chunk, 'rejected')" class="text-button reject-text" :disabled="busy" @click="reviewOne(chunk, 'rejected')">驳回</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <footer class="toolbar bottom">
          <span class="toolbar-count">共 {{ total }} 份文档</span>
          <div class="pagination">
            <button class="page-button" :disabled="page <= 1 || loading" @click="goPage(page - 1)">上一页</button>
            <span>第 {{ page }} / {{ totalPages || 1 }} 页</span>
            <select v-model="pageSize" @change="changePageSize">
              <option :value="20">20 条/页</option>
              <option :value="50">50 条/页</option>
              <option :value="100">100 条/页</option>
            </select>
            <button class="page-button" :disabled="page >= totalPages || loading" @click="goPage(page + 1)">下一页</button>
          </div>
          <span></span>
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
            <div v-if="currentChunk.source_url"><dt>来源链接</dt><dd><a :href="currentChunk.source_url" target="_blank" rel="noreferrer">{{ currentChunk.source_url }}</a></dd></div>
            <div v-if="currentChunk.author"><dt>作者</dt><dd>{{ currentChunk.author }}</dd></div>
            <div v-if="currentChunk.platform"><dt>平台</dt><dd>{{ currentChunk.platform }}</dd></div>
            <div v-if="currentChunk.publish_date"><dt>发布时间</dt><dd>{{ currentChunk.publish_date }}</dd></div>
            <div v-if="currentChunk.indexed_at"><dt>入库时间</dt><dd>{{ currentChunk.indexed_at }}</dd></div>
            <div><dt>Chunk ID</dt><dd>{{ currentChunk.chunk_id }}</dd></div>
          </dl>
        </section>
        <label class="detail-field">分类
          <select data-test="detail-category" v-model="draftCategory">
            <option v-for="cat in DOC_CATEGORIES" :key="cat" :value="cat">{{ cat }}</option>
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
.filter-bar { max-width: 1500px; margin-inline: auto; background: #fff; border: 1px solid #e8eaed; box-shadow: 0 8px 24px rgba(0,0,0,.04); display: flex; align-items: end; gap: 14px; padding: 14px 16px; border-radius: 12px 12px 0 0; }
.filter-bar label { display: grid; gap: 5px; color: #5e6673; font-size: 12px; font-weight: 600; }
.filter-bar select, .filter-bar input { min-width: 156px; height: 36px; padding: 0 11px; border: 1px solid #e8eaed; border-radius: 6px; background: #fff; color: #1e2a41; transition: all 0.15s; outline: none; }
.filter-bar select:focus, .filter-bar input:focus { border-color: #3370ff; box-shadow: 0 0 0 2px rgba(51,112,255,.08); }
.search-label { flex: 1; }.search-label input { width: 100%; }
.button { height: 36px; padding: 0 16px; border: 0; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 13px; transition: all 0.15s; display: inline-flex; align-items: center; justify-content: center; }
.button.secondary { background: #f3f4f6; color: #4b5563; }.button.secondary:hover { background: #e5e7eb; color: #1f2937; }
.button.primary { background: #3370ff; color: #fff; }.button.primary:hover { background: #2860e0; }
.button.approve { background: #def5e9; color: #10b981; }.button.approve:hover { background: #d1fae5; }
.button.reject { background: #fde7e7; color: #ef4444; }.button.reject:hover { background: #fee2e2; }
.button:disabled { opacity: .45; cursor: not-allowed; }
.notice { max-width: 1500px; margin: 10px auto; padding: 10px 14px; border-radius: 6px; font-size: 13px; }
.notice.error { background: #fff0f0; color: #ef4444; }.notice.success { background: #e8f6ee; color: #10b981; }
.workspace { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; max-width: 1500px; margin-inline: auto; }
.workspace.with-detail { grid-template-columns: minmax(0, 1fr) 420px; }
.doc-list-card { background: #fff; border: 1px solid #e8eaed; box-shadow: 0 8px 24px rgba(0,0,0,.04); border-radius: 0 0 12px 12px; overflow: hidden; min-width: 0; }
.toolbar { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; padding: 0 16px; min-height: 52px; border-bottom: 1px solid #f3f4f6; }
.toolbar.bottom { border-bottom: none; border-top: 1px solid #f3f4f6; }
.toolbar-count { color: #5e6673; font-size: 13px; }
.empty { padding: 48px; color: #9ca3af; text-align: center; font-size: 13px; }
.doc-row-wrap { border-bottom: 1px solid #f3f4f6; }
.doc-row-wrap:last-child { border-bottom: none; }
.doc-row { display: flex; align-items: center; gap: 12px; padding: 14px 16px; transition: background 0.12s; cursor: pointer; }
.doc-row:hover { background: #fafbfc; }
.doc-row.expanded { background: #f0f5ff; }
.expand-icon { flex-shrink: 0; width: 28px; height: 28px; font-size: 16px; color: #3370ff; display: flex; align-items: center; justify-content: center; user-select: none; }
.doc-info { flex: 1; min-width: 0; }
.doc-title { font-size: 14px; font-weight: 600; color: #1e2a41; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc-meta { display: flex; gap: 6px; margin-top: 4px; flex-wrap: wrap; }
.category-chip { display: inline-flex; padding: 2px 8px; border-radius: 99px; background: #e8f0ff; color: #3370ff; font-size: 11px; font-weight: 600; white-space: nowrap; }
.meta-tag { display: inline-flex; padding: 2px 7px; border-radius: 99px; background: #f3f4f6; color: #6b7280; font-size: 11px; white-space: nowrap; }
.doc-progress { width: 180px; flex-shrink: 0; }
.progress-label { font-size: 12px; font-weight: 600; margin-bottom: 4px; }
.progress-label.pending { color: #b45309; }
.progress-label.partial { color: #3370ff; }
.progress-label.done { color: #10b981; }
.progress-label.empty { color: #9ca3af; }
.progress-bar-wrap { height: 5px; border-radius: 99px; background: #f3f4f6; overflow: hidden; }
.progress-bar { height: 100%; border-radius: 99px; transition: width 0.3s; background: #d1d5db; }
.progress-bar.pending { background: #fbbf24; }
.progress-bar.partial { background: #3370ff; }
.progress-bar.done { background: #10b981; }
.doc-actions { display: flex; gap: 8px; flex-shrink: 0; }
.approve-sm { height: 30px; padding: 0 12px; font-size: 12px; background: #def5e9; color: #10b981; border: 0; border-radius: 6px; cursor: pointer; font-weight: 600; transition: all 0.15s; }
.approve-sm:hover:not(:disabled) { background: #d1fae5; }
.approve-sm:disabled { opacity: .4; cursor: not-allowed; }
.reject-sm { height: 30px; padding: 0 12px; font-size: 12px; background: #fde7e7; color: #ef4444; border: 0; border-radius: 6px; cursor: pointer; font-weight: 600; transition: all 0.15s; }
.reject-sm:hover:not(:disabled) { background: #fee2e2; }
.reject-sm:disabled { opacity: .4; cursor: not-allowed; }
.chunks-panel { background: #f8faff; border-top: 1px solid #e8eaed; padding: 0 0 0 44px; }
.chunks-loading { padding: 20px; color: #9ca3af; font-size: 13px; }
.chunks-table { width: 100%; border-collapse: collapse; }
.chunks-table th { padding: 9px 12px; background: #f0f5ff; color: #64748b; font-size: 11px; text-align: left; text-transform: uppercase; letter-spacing: .04em; font-weight: 600; }
.chunks-table td { padding: 10px 12px; border-top: 1px solid #e8eaed; font-size: 13px; vertical-align: middle; color: #1e2a41; }
.chunks-table tr:hover td { background: #f5f8ff; }
.chunks-table tr.active-chunk td { background: #eef3ff; }
.preview { max-width: 320px; color: #5e6673; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status-chip { display: inline-flex; padding: 2px 8px; border-radius: 99px; white-space: nowrap; font-size: 11px; font-weight: 600; }
.status-chip.pending { background: #fff4d8; color: #b45309; }
.status-chip.approved { background: #def5e9; color: #10b981; }
.status-chip.rejected { background: #fde7e7; color: #ef4444; }
.text-button { border: 0; background: none; color: #3370ff; font-weight: 600; cursor: pointer; transition: color 0.15s; font-size: 13px; }
.text-button:hover { color: #2860e0; }.text-button:disabled { opacity: .45; cursor: not-allowed; }
.row-actions { white-space: nowrap; }.row-actions .text-button + .text-button { margin-left: 10px; }
.approve-text { color: #10b981; }.approve-text:hover { color: #059669; }
.reject-text { color: #ef4444; }.reject-text:hover { color: #dc2626; }
.pagination { display: flex; justify-content: center; align-items: center; gap: 10px; color: #5e6673; font-size: 12px; }
.pagination select, .page-button { height: 32px; border: 1px solid #e8eaed; border-radius: 6px; background: #fff; color: #4b5563; font-size: 12px; outline: none; transition: all 0.15s; }
.pagination select { padding: 0 6px; cursor: pointer; }.pagination select:focus { border-color: #3370ff; }
.page-button { padding: 0 12px; cursor: pointer; }.page-button:hover:not(:disabled) { background: #f9fafb; border-color: #cbd5e1; }.page-button:disabled { opacity: .45; cursor: not-allowed; }
.detail-panel { position: sticky; top: 0; align-self: start; max-height: calc(100vh - 170px); padding: 18px; border-radius: 12px; overflow: auto; background: #fff; border: 1px solid #e8eaed; box-shadow: 0 8px 24px rgba(0,0,0,.04); }
.detail-header { display: flex; justify-content: space-between; align-items: start; padding-bottom: 14px; border-bottom: 1px solid #f3f4f6; }
.detail-header h2 { margin-top: 3px; font-size: 18px; font-weight: 600; color: #1e2a41; }
.close-button { border: 0; background: none; color: #9ca3af; font-size: 24px; cursor: pointer; transition: color 0.15s; }.close-button:hover { color: #1e2a41; }
.detail-meta { display: grid; gap: 8px; margin: 14px 0; }.detail-meta div { display: grid; grid-template-columns: 58px 1fr; gap: 8px; }
.detail-meta dt, .content-label { color: #8a8f99; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; }
.detail-meta dd { min-width: 0; overflow-wrap: anywhere; font-size: 13px; color: #1e2a41; }
.source-card { margin: 14px 0; padding: 12px; border: 1px solid #e8eaed; border-radius: 8px; background: #fafbfc; }
.source-card h3 { margin: 0 0 10px; color: #1e2a41; font-size: 13px; font-weight: 600; }
.source-meta { display: grid; gap: 7px; margin: 0; }.source-meta div { display: grid; grid-template-columns: 72px 1fr; gap: 8px; }
.source-meta dt { color: #8a8f99; font-size: 11px; font-weight: 600; }.source-meta dd { min-width: 0; margin: 0; overflow-wrap: anywhere; font-size: 12px; color: #1e2a41; }
.source-meta a { color: #3370ff; text-decoration: none; }.source-meta a:hover { text-decoration: underline; }
.detail-field { display: grid; gap: 5px; margin-top: 12px; color: #5e6673; font-size: 12px; font-weight: 600; }
.detail-field select, .detail-field input { height: 36px; padding: 0 10px; border: 1px solid #e8eaed; border-radius: 6px; background: #fff; color: #1e2a41; outline: none; transition: border-color 0.15s; }
.detail-field select:focus, .detail-field input:focus { border-color: #3370ff; }
.content-label { margin-top: 18px; }
.chunk-content { margin-top: 7px; padding: 14px; border-radius: 8px; background: #fafbfc; color: #1e2a41; font: 12px/1.7 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid #e8eaed; }
.detail-actions { position: sticky; bottom: -18px; display: flex; gap: 8px; margin: 18px -18px -18px; padding: 12px 18px; border-top: 1px solid #f3f4f6; background: rgba(255,255,255,.96); }
@media (max-width: 1100px) { .workspace.with-detail { grid-template-columns: minmax(0, 1fr) 360px; } }
@media (max-width: 760px) {
  .review-page { padding: 16px 12px 24px; }
  .summary { display: none; }
  .filter-bar { flex-wrap: wrap; }
  .filter-bar label { width: calc(50% - 7px); }
  .filter-bar .search-label { width: 100%; flex-basis: 100%; }
  .filter-bar select { width: 100%; min-width: 0; }
  .filter-bar .button { flex: 1; }
  .doc-progress { display: none; }
  .workspace.with-detail { display: block; }
  .detail-panel { position: fixed; inset: 56px 0 0; z-index: 30; max-height: none; border-radius: 12px 12px 0 0; box-shadow: 0 -12px 40px rgba(0,0,0,.1); }
  .chunks-panel { padding-left: 16px; }
}
</style>
