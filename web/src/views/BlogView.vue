<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { crawl, listBlogPosts, getBlogPost, deleteBlogPost, publishBlogPost, syncPublishedPosts } from '../api'
import type { BlogPostItem } from '../types'

const url = ref('')
const crawling = ref(false)
const posts = ref<BlogPostItem[]>([])
const total = ref(0)
const totalPages = ref(1)
const loading = ref(false)
const message = ref('')

const searchQ = ref('')
const filterPlatform = ref('')
const currentPage = ref(1)
const pageSize = ref(20)

const showDetail = ref(false)
const detailContent = ref('')
const detailFilename = ref('')
const detailLoading = ref(false)
const syncing = ref(false)
const publishing = ref('')  // 正在发布的文章 filename

const PLATFORMS = ['CSDN', '博客园', '掘金', '微信公众号'] as const

const PLATFORM_COLORS: Record<string, string> = {
  'CSDN': '#c9390a',
  '博客园': '#1e8346',
  '掘金': '#1e80ff',
  '微信公众号': '#07c160',
}

let searchTimer: ReturnType<typeof setTimeout> | null = null

function onSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    currentPage.value = 1
    loadPosts()
  }, 300)
}

function onPlatformChange() {
  currentPage.value = 1
  loadPosts()
}

function onPageSizeChange() {
  currentPage.value = 1
  loadPosts()
}

async function handleCrawl() {
  const target = url.value.trim()
  if (!target) return
  crawling.value = true
  message.value = ''
  try {
    const result = await crawl(target)
    message.value = `[${result.platform}] ${result.title}`
    url.value = ''
    await loadPosts()
  } catch (e: any) {
    message.value = `失败：${e.message}`
  } finally {
    crawling.value = false
  }
}

async function loadPosts() {
  loading.value = true
  try {
    const result = await listBlogPosts({
      page: currentPage.value,
      page_size: pageSize.value,
      q: searchQ.value || undefined,
      platform: filterPlatform.value || undefined,
    })
    posts.value = result.posts
    total.value = result.total
    totalPages.value = result.total_pages
  } catch (e: any) {
    message.value = `失败：${e.message}`
  } finally {
    loading.value = false
  }
}

async function handleSync() {
  syncing.value = true
  message.value = ''
  try {
    const result = await syncPublishedPosts()
    message.value = result.message
    await loadPosts()
  } catch (e: any) {
    message.value = `同步失败：${e.message}`
  } finally {
    syncing.value = false
  }
}

function goPage(p: number) {
  if (p < 1 || p > totalPages.value) return
  currentPage.value = p
  loadPosts()
}

function goPrev() { goPage(currentPage.value - 1) }
function goNext() { goPage(currentPage.value + 1) }

async function openDetail(post: BlogPostItem) {
  detailFilename.value = post.filename
  detailContent.value = ''
  showDetail.value = true
  detailLoading.value = true
  try {
    const result = await getBlogPost(post.filename)
    detailContent.value = result.content
  } catch (e: any) {
    detailContent.value = `加载失败：${e.message}`
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  showDetail.value = false
  detailContent.value = ''
  detailFilename.value = ''
}

const confirmPublish = ref('')
function handlePublish(filename: string) {
  confirmPublish.value = filename
}
async function doPublish() {
  const name = confirmPublish.value
  confirmPublish.value = ''
  if (!name) return
  publishing.value = name
  message.value = ''
  try {
    const result = await publishBlogPost(name)
    message.value = result.message
    await loadPosts()
  } catch (e: any) {
    message.value = `发布失败：${e.message}`
  } finally {
    publishing.value = ''
  }
}

// ---- 删除 ----
const confirmDelete = ref('')
function handleDelete(filename: string) {
  confirmDelete.value = filename
}
async function doDelete() {
  const name = confirmDelete.value
  confirmDelete.value = ''
  if (!name) return
  try {
    await deleteBlogPost(name)
    message.value = `已删除 ${name}`
    await loadPosts()
  } catch (e: any) {
    message.value = `删除失败：${e.message}`
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function ago(dateStr: string): string {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return dateStr
  const diff = Date.now() - d.getTime()
  const days = Math.floor(diff / 86400000)
  if (days === 0) return '今天'
  if (days === 1) return '昨天'
  if (days < 30) return `${days} 天前`
  return dateStr.slice(0, 10)
}

const PAGE_SIZE_OPTIONS = [20, 50, 100]

const blogRenderer = new marked.Renderer()
blogRenderer.image = ({ href, title, text }) => {
  return `<img src="${href}" alt="${text || ''}" referrerpolicy="no-referrer"${title ? ` title="${title}"` : ''} />`
}

const renderedContent = computed(() => {
  if (!detailContent.value || detailLoading.value) return ''
  const raw = marked.parse(detailContent.value, { async: false, renderer: blogRenderer }) as string
  return DOMPurify.sanitize(raw)
})

onMounted(loadPosts)
</script>

<template>
  <div class="blog-layout">
    <!-- 顶部栏 -->
    <header class="blog-header">
      <div class="header-row">
        <h1 class="title">博客管理</h1>
        <span v-if="total > 0" class="stat">{{ total }} 篇</span>
        <span v-if="message && !message.startsWith('失败')" class="stat success" :title="message">{{ message }}</span>
        <span v-else-if="message.startsWith('失败')" class="stat error">{{ message }}</span>
      </div>
      <div class="crawl-row">
        <input
          v-model="url"
          class="crawl-input"
          placeholder="粘贴博客文章链接，自动识别平台（CSDN / 博客园 / 掘金 / 微信公众号）"
          @keyup.enter="handleCrawl"
          :disabled="crawling"
        />
        <button class="btn btn-primary" @click="handleCrawl" :disabled="crawling || !url.trim()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          {{ crawling ? '抓取中…' : '抓取' }}
        </button>
        <button class="btn btn-secondary" @click="loadPosts" :disabled="loading">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="23 4 23 10 17 10"/>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
          </svg>
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
        <button class="btn btn-publish" @click="handleSync" :disabled="syncing">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M23 4v6h-6"/><path d="M1 20v-6h6"/>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
          </svg>
          {{ syncing ? '同步中…' : '同步' }}
        </button>
      </div>
      <!-- 搜索 + 筛选 -->
      <div class="filter-row">
        <span class="filter-icon">🔍</span>
        <input
          v-model="searchQ"
          class="search-input"
          placeholder="搜索文章标题…"
          @input="onSearchInput"
        />
        <select v-model="filterPlatform" class="filter-select" @change="onPlatformChange">
          <option value="">全部平台</option>
          <option v-for="p in PLATFORMS" :key="p" :value="p">{{ p }}</option>
        </select>
        <select v-model="pageSize" class="page-size-select" @change="onPageSizeChange">
          <option v-for="n in PAGE_SIZE_OPTIONS" :key="n" :value="n">{{ n }} 条/页</option>
        </select>
      </div>
    </header>

    <!-- 文章列表 -->
    <main class="blog-body">
      <div v-if="posts.length === 0 && !loading" class="empty">
        {{ searchQ || filterPlatform ? '没有匹配的文章' : '还没有保存的文章' }}
      </div>

      <div v-if="loading" class="loading">加载中…</div>

      <div v-for="post in posts" :key="post.filename" class="post-item" @click="openDetail(post)">
        <div class="post-info">
          <div class="post-title-row">
            <span v-if="post.platform" class="platform-tag" :style="{ background: PLATFORM_COLORS[post.platform] || '#8a8f99' }">{{ post.platform }}</span>
            <span class="post-title">{{ post.title }}</span>
          </div>
          <div class="post-meta">
            <span v-if="post.author">作者 {{ post.author }}</span>
            <span v-if="post.crawled_at">抓取 {{ ago(post.crawled_at) }}</span>
          </div>
        </div>
        <div class="post-extras">
          <button
            class="pub-btn"
            :disabled="publishing === post.filename"
            title="发布到博客系统"
            @click.stop="handlePublish(post.filename)"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M23 4v6h-6"/><path d="M1 20v-6h6"/>
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
            </svg>
            {{ publishing === post.filename ? '发布中…' : '发布' }}
          </button>
          <button class="del-btn" title="删除" @click.stop="handleDelete(post.filename)">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
          </button>
          <span class="post-size">{{ formatSize(post.file_size) }}</span>
          <svg class="post-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="9 18 15 12 9 6"/>
          </svg>
        </div>
      </div>
    </main>

    <!-- 分页 -->
    <footer v-if="totalPages > 1" class="pagination">
      <button class="page-btn" :disabled="currentPage <= 1" @click="goPrev">‹</button>
      <template v-for="p in totalPages" :key="p">
        <button
          v-if="p === 1 || p === totalPages || Math.abs(p - currentPage) <= 2"
          class="page-btn"
          :class="{ active: p === currentPage }"
          @click="goPage(p)"
        >{{ p }}</button>
        <span v-else-if="p === currentPage - 3 || p === currentPage + 3" class="page-dots">…</span>
      </template>
      <button class="page-btn" :disabled="currentPage >= totalPages" @click="goNext">›</button>
    </footer>

    <!-- 文章详情弹窗 -->
    <Teleport to="body">
      <div v-if="showDetail" class="modal-overlay" @click.self="closeDetail">
        <div class="modal-panel">
          <header class="modal-hd">
            <h3>{{ detailFilename }}</h3>
            <button class="close-btn" @click="closeDetail">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </header>
          <div class="modal-bd">
            <div v-if="detailLoading" class="loading">加载中…</div>
            <div v-else class="markdown-body" v-html="renderedContent"></div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 发布确认弹窗 -->
    <Teleport to="body">
      <div v-if="confirmPublish" class="confirm-overlay" @click.self="confirmPublish = ''">
        <div class="confirm-box">
          <p class="confirm-msg">确定发布 <strong>{{ confirmPublish }}</strong>？<br/>文章将发布到博客系统并入库知识库。</p>
          <div class="confirm-actions">
            <button class="btn" @click="confirmPublish = ''">取消</button>
            <button class="btn btn-primary" @click="doPublish">发布</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 删除确认弹窗 -->
    <Teleport to="body">
      <div v-if="confirmDelete" class="confirm-overlay" @click.self="confirmDelete = ''">
        <div class="confirm-box">
          <p class="confirm-msg">确定删除 <strong>{{ confirmDelete }}</strong>？<br/>文件及对应的向量数据将一并清除。</p>
          <div class="confirm-actions">
            <button class="btn" @click="confirmDelete = ''">取消</button>
            <button class="btn btn-danger" @click="doDelete">删除</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.blog-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #fff;
}

/* ---- 顶部栏 ---- */
.blog-header {
  flex-shrink: 0;
  border-bottom: 1px solid #e8eaed;
  background: #fff;
}
.header-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 24px;
  height: 52px;
}
.title {
  font-size: 16px;
  font-weight: 600;
  color: #1e2a41;
  margin: 0;
}
.stat {
  font-size: 12px;
  color: #8a8f99;
  background: #f7f8fa;
  padding: 2px 8px;
  border-radius: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 300px;
}
.stat.success { color: #22a65e; background: #e8f8ef; }
.stat.error { color: #f25d5d; background: #fef0f0; }

.crawl-row {
  display: flex;
  gap: 8px;
  padding: 0 24px 10px;
}

.crawl-input {
  flex: 1;
  padding: 7px 12px;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  font-size: 13px;
  color: #1e2a41;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
  font-family: inherit;
}
.crawl-input:focus {
  border-color: #3370ff;
  box-shadow: 0 0 0 2px rgba(51,112,255,0.08);
}
.crawl-input:disabled {
  background: #f7f8fa;
  cursor: not-allowed;
}
.crawl-input::placeholder {
  color: #b0b5be;
  font-size: 13px;
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 14px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid #e8eaed;
  background: #fff;
  color: #4b5563;
  transition: all 0.15s;
  white-space: nowrap;
}
.btn:hover:not(:disabled) { background: #f7f8fa; border-color: #d1d5db; }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }

.btn-primary {
  background: #3370ff;
  color: #fff;
  border-color: #3370ff;
}
.btn-primary:hover:not(:disabled) { background: #2860e0; border-color: #2860e0; }

.btn-publish {
  background: #07c160;
  color: #fff;
  border-color: #07c160;
}
.btn-publish:hover:not(:disabled) { background: #06ad56; border-color: #06ad56; }

/* ---- 筛选栏 ---- */
.filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 24px 12px;
}
.filter-icon {
  font-size: 14px;
  color: #b0b5be;
}
.search-input {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  font-size: 13px;
  color: #1e2a41;
  outline: none;
  font-family: inherit;
  transition: border-color 0.15s;
}
.search-input:focus {
  border-color: #3370ff;
  box-shadow: 0 0 0 2px rgba(51,112,255,0.08);
}
.search-input::placeholder {
  color: #b0b5be;
}
.filter-select,
.page-size-select {
  padding: 6px 8px;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  font-size: 12px;
  color: #4b5563;
  background: #fff;
  outline: none;
  cursor: pointer;
  font-family: inherit;
}
.filter-select:focus,
.page-size-select:focus {
  border-color: #3370ff;
}

/* ---- 列表 ---- */
.blog-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 24px;
}

.empty {
  text-align: center;
  color: #b0b5be;
  margin-top: 80px;
  font-size: 14px;
}

.loading {
  text-align: center;
  color: #b0b5be;
  padding: 40px 0;
  font-size: 14px;
}

.post-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 0;
  cursor: pointer;
  border-bottom: 1px solid #f3f4f6;
  transition: background 0.1s;
  padding-left: 8px;
  padding-right: 8px;
  margin: 0 -8px;
  border-radius: 6px;
}
.post-item:hover { background: #f7f8fa; }

.post-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1;
}
.post-title-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.platform-tag {
  display: inline-block;
  font-size: 11px;
  color: #fff;
  padding: 1px 6px;
  border-radius: 3px;
  white-space: nowrap;
  flex-shrink: 0;
  line-height: 18px;
}
.post-title {
  font-size: 14px;
  font-weight: 500;
  color: #1e2a41;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.post-meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: #9ca3af;
}

.post-extras {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.post-size {
  font-size: 12px;
  color: #b0b5be;
}
.post-arrow {
  color: #d1d5db;
  transition: color 0.15s;
}
.post-item:hover .post-arrow { color: #3370ff; }

/* ---- 分页 ---- */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 12px 24px;
  border-top: 1px solid #f3f4f6;
  flex-shrink: 0;
}
.page-btn {
  min-width: 32px;
  height: 32px;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  background: #fff;
  color: #4b5563;
  font-size: 13px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.page-btn:hover:not(:disabled):not(.active) {
  background: #f7f8fa;
  border-color: #d1d5db;
}
.page-btn.active {
  background: #3370ff;
  color: #fff;
  border-color: #3370ff;
}
.page-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.page-dots {
  color: #d1d5db;
  font-size: 13px;
  width: 20px;
  text-align: center;
}

/* ---- 模态框 ---- */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-panel {
  width: min(90vw, 820px);
  height: min(85vh, 700px);
  background: #fff;
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.15);
}

.modal-hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 52px;
  border-bottom: 1px solid #e8eaed;
  flex-shrink: 0;
}
.modal-hd h3 {
  font-size: 14px;
  font-weight: 600;
  color: #1e2a41;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.close-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: #9ca3af;
  padding: 4px;
  border-radius: 4px;
  display: flex;
}
.close-btn:hover { color: #1e2a41; background: #f7f8fa; }

.modal-bd {
  flex: 1;
  overflow: auto;
  padding: 24px 28px;
}

/* ---- 删除按钮 ---- */
.pub-btn, .del-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px 6px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  transition: all 0.15s;
}
.pub-btn {
  color: #3370ff;
}
.pub-btn:hover:not(:disabled) {
  background: #eef2ff;
  color: #2860e0;
}
.pub-btn:disabled {
  color: #93b0ff;
  cursor: not-allowed;
}
.del-btn { color: #d1d5db; }
.del-btn:hover { color: #f25d5d; }
.post-item:hover .del-btn { color: #b0b5be; }
.post-item:hover .del-btn:hover { color: #f25d5d; }

/* ---- 确认弹窗 ---- */
.confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
}
.confirm-box {
  background: #fff;
  border-radius: 10px;
  padding: 24px 28px 20px;
  min-width: 320px;
  box-shadow: 0 12px 40px rgba(0,0,0,0.15);
}
.confirm-msg {
  margin: 0 0 20px;
  font-size: 14px;
  color: #1e2a41;
  line-height: 1.6;
}
.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.confirm-actions .btn {
  padding: 7px 18px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid #e8eaed;
  background: #fff;
  color: #4b5563;
  transition: all 0.15s;
}
.confirm-actions .btn:hover { background: #f7f8fa; }
.confirm-actions .btn-primary {
  background: #3370ff;
  color: #fff;
  border-color: #3370ff;
}
.confirm-actions .btn-primary:hover { background: #2860e0; }
.confirm-actions .btn-danger {
  background: #f25d5d;
  color: #fff;
  border-color: #f25d5d;
}
.confirm-actions .btn-danger:hover { background: #e04848; }

/* ===== 移动端响应式 ===== */
@media (max-width: 768px) {
  /* 顶部栏 */
  .header-row {
    padding: 0 12px;
    height: 44px;
    gap: 6px;
  }
  .title {
    font-size: 14px;
  }
  .stat {
    font-size: 11px;
    max-width: 200px;
  }

  /* 抓取行 */
  .crawl-row {
    flex-wrap: wrap;
    padding: 0 12px 8px;
    gap: 6px;
  }
  .crawl-input {
    flex: 1 1 100%;
    font-size: 13px;
  }
  .btn {
    padding: 7px 10px;
    font-size: 12px;
  }
  .crawl-row .btn {
    flex: 1;
  }

  /* 筛选行 */
  .filter-row {
    flex-wrap: wrap;
    padding: 0 12px 8px;
    gap: 6px;
  }
  .filter-icon {
    display: none;
  }
  .search-input {
    flex: 1 1 100%;
    font-size: 13px;
  }
  .filter-select,
  .page-size-select {
    flex: 1;
    font-size: 12px;
  }

  /* 列表 */
  .blog-body {
    padding: 4px 12px;
  }
  .post-item {
    flex-wrap: wrap;
    gap: 6px;
    padding: 10px 8px;
  }
  .post-info {
    flex: 1 1 100%;
  }
  .post-title-row {
    flex-wrap: wrap;
    gap: 4px;
  }
  .post-title {
    font-size: 13px;
    white-space: normal;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .post-meta {
    gap: 8px;
    font-size: 11px;
  }
  .post-extras {
    width: 100%;
    justify-content: flex-end;
    gap: 6px;
  }
  .post-arrow {
    display: none;
  }

  /* 分页 */
  .pagination {
    padding: 8px 12px;
  }
  .page-btn {
    min-width: 28px;
    height: 28px;
    font-size: 12px;
  }

  /* 弹窗 */
  .modal-panel {
    width: calc(100vw - 16px);
    height: calc(100vh - 60px);
    border-radius: 12px 12px 0 0;
    position: fixed;
    bottom: 0;
  }
  .modal-overlay {
    align-items: flex-end;
  }
  .modal-hd {
    padding: 0 16px;
    height: 48px;
  }
  .modal-hd h3 {
    font-size: 13px;
  }
  .modal-bd {
    padding: 16px;
  }

  /* 确认弹窗 */
  .confirm-box {
    min-width: unset;
    width: calc(100vw - 40px);
    max-width: 360px;
    padding: 20px;
  }
  .confirm-msg {
    font-size: 13px;
  }
}

@media (max-width: 480px) {
  .crawl-input::placeholder {
    font-size: 12px;
  }
  .platform-tag {
    font-size: 10px;
    padding: 0 4px;
  }
  .post-title {
    font-size: 12px;
  }
}
</style>
