import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AdminChunksView from './AdminChunksView.vue'
import * as api from '../api'
import type { AdminChunk, AdminDoc } from '../types'

vi.mock('../api', () => ({
  listAdminDocuments: vi.fn(),
  listDocumentChunks: vi.fn(),
  updateAdminChunk: vi.fn(),
  batchReviewChunks: vi.fn(),
}))

const chunks: AdminChunk[] = [
  {
    chunk_id: 'c1',
    file_name: 'StampServer用户手册.docx',
    source: 'StampServer用户手册.docx',
    section_title: '安装',
    doc_category: 'StampServer',
    review_status: 'pending',
    content_preview: '第一段内容',
    content: '第一段完整内容',
    kb_name: '文章附件',
    page_label: '1',
    indexed_at: '2026-07-01T10:00:00',
    file_path: 'word/StampServer用户手册.docx',
    kb_path: 'word',
    title: 'StampServer 用户手册',
    source_url: 'https://example.com/manual',
    author: '技术部',
    platform: '内部文档',
    publish_date: '2026-07-01',
    last_modified: '2026-06-30T10:00:00',
  },
  {
    chunk_id: 'c2',
    file_name: 'StampServer用户手册.docx',
    source: 'StampServer用户手册.docx',
    section_title: '卸载',
    doc_category: 'StampServer',
    review_status: 'pending',
    content_preview: '第二段内容',
    content: '第二段完整内容',
    kb_name: '文章附件',
    page_label: '2',
    indexed_at: '2026-07-01T10:00:00',
  },
]

const docs: AdminDoc[] = [
  {
    file_name: 'StampServer用户手册.docx',
    file_path: 'word/StampServer用户手册.docx',
    source: 'StampServer用户手册.docx',
    doc_category: 'StampServer',
    kb_name: '文章附件',
    indexed_at: '2026-07-01T10:00:00',
    total_count: 2,
    pending_count: 2,
    approved_count: 0,
    rejected_count: 0,
    chunk_ids: ['c1', 'c2'],
  },
  {
    file_name: '博客.md',
    file_path: null,
    source: '博客.md',
    doc_category: '博客',
    kb_name: '已发布文章',
    indexed_at: null,
    total_count: 1,
    pending_count: 1,
    approved_count: 0,
    rejected_count: 0,
    chunk_ids: ['c3'],
  },
]

describe('AdminChunksView', () => {
  beforeEach(() => {
    vi.mocked(api.listAdminDocuments).mockResolvedValue({
      items: docs,
      total: 2,
      page: 1,
      page_size: 50,
      total_pages: 1,
    })
    vi.mocked(api.listDocumentChunks).mockResolvedValue({
      items: chunks,
      total: 2,
      page: 1,
      page_size: 2,
      total_pages: 1,
    })
    vi.mocked(api.updateAdminChunk).mockResolvedValue({
      message: 'ok', updated_chunks: 1, requested_chunks: 1, status: 'approved',
    })
    vi.mocked(api.batchReviewChunks).mockResolvedValue({
      message: 'ok', updated_chunks: 2, requested_chunks: 2, status: 'approved',
    })
  })

  it('loads all documents on mount and shows file names', async () => {
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    expect(api.listAdminDocuments).toHaveBeenCalledWith(
      expect.objectContaining({ audit_status: 'all', doc_category: 'all', page: 1 }),
      undefined,
      false,
    )
    expect(wrapper.text()).toContain('StampServer用户手册.docx')
    expect(wrapper.text()).toContain('博客.md')
  })

  it('filters by category and debounces filename search', async () => {
    vi.useFakeTimers()
    const wrapper = mount(AdminChunksView)
    await flushPromises()
    vi.mocked(api.listAdminDocuments).mockClear()

    // Category filter triggers immediately on change
    const categorySelect = wrapper.find('select')
    await categorySelect.setValue('博客')
    await flushPromises()
    expect(api.listAdminDocuments).toHaveBeenLastCalledWith(
      expect.objectContaining({ doc_category: '博客', page: 1 }),
      undefined,
      false,
    )

    // Filename search debounces 300ms
    const filenameInput = wrapper.find('input')
    await filenameInput.setValue('MySQL')
    await vi.advanceTimersByTimeAsync(299)
    expect(api.listAdminDocuments).not.toHaveBeenLastCalledWith(
      expect.objectContaining({ filename: 'MySQL' }),
    )
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()
    expect(api.listAdminDocuments).toHaveBeenLastCalledWith(
      expect.objectContaining({ filename: 'MySQL' }),
      undefined,
      false,
    )
    vi.useRealTimers()
  })

  it('expands a document row and shows its chunks', async () => {
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    // Click expand button on first doc row
    const expandBtns = wrapper.findAll('.doc-row')
    await expandBtns[0].trigger('click')
    await flushPromises()

    expect(api.listDocumentChunks).toHaveBeenCalledWith(
      'StampServer用户手册.docx',
      'word/StampServer用户手册.docx',
    )
    expect(wrapper.text()).toContain('第一段内容')
    expect(wrapper.text()).toContain('第二段内容')
  })

  it('opens detail panel from expanded chunk row', async () => {
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    const expandBtns = wrapper.findAll('.doc-row')
    await expandBtns[0].trigger('click')
    await flushPromises()

    // Click view on first chunk
    const viewBtns = wrapper.findAll('.text-button')
    const viewBtn = viewBtns.find(b => b.text() === '查看')
    await viewBtn!.trigger('click')

    const panel = wrapper.get('[data-test="detail-panel"]')
    expect(panel.text()).toContain('第一段完整内容')
    expect(panel.text()).toContain('来源信息')
    expect(panel.text()).toContain('StampServer 用户手册')
    expect(panel.text()).toContain('https://example.com/manual')
    expect(panel.text()).toContain('技术部')
  })

  it('saves category and title edits from the detail panel', async () => {
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    const expandBtns = wrapper.findAll('.doc-row')
    await expandBtns[0].trigger('click')
    await flushPromises()

    const viewBtns = wrapper.findAll('.text-button')
    const viewBtn = viewBtns.find(b => b.text() === '查看')
    await viewBtn!.trigger('click')

    await wrapper.get('[data-test="detail-category"]').setValue('基础环境')
    await wrapper.get('[data-test="detail-title"]').setValue('部署准备')
    await wrapper.get('[data-test="save-detail"]').trigger('click')
    await flushPromises()

    expect(api.updateAdminChunk).toHaveBeenCalledWith('c1', {
      doc_category: '基础环境',
      section_title: '部署准备',
    })
  })

  it('approves an entire document in one click', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    const approveBtns = wrapper.findAll('.approve-sm')
    await approveBtns[0].trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringContaining('StampServer用户手册.docx'),
    )
    expect(api.batchReviewChunks).toHaveBeenCalledWith(['c1', 'c2'], 'approved')
  })

  it('rejects an entire document in one click', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    const rejectBtns = wrapper.findAll('.reject-sm')
    await rejectBtns[0].trigger('click')
    await flushPromises()

    expect(api.batchReviewChunks).toHaveBeenCalledWith(['c1', 'c2'], 'rejected')
  })

  it('changes page and page size', async () => {
    vi.mocked(api.listAdminDocuments).mockResolvedValue({
      items: docs,
      total: 60,
      page: 1,
      page_size: 50,
      total_pages: 2,
    })
    const wrapper = mount(AdminChunksView)
    await flushPromises()
    vi.mocked(api.listAdminDocuments).mockClear()

    // Navigate to next page
    const pageButtons = wrapper.findAll('.page-button')
    const nextBtn = pageButtons.find(b => b.text() === '下一页' && !b.element.hasAttribute('disabled'))
    await nextBtn!.trigger('click')
    await flushPromises()
    expect(api.listAdminDocuments).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 2 }),
      undefined,
      false,
    )

    // Change page size
    const pageSizeSelects = wrapper.findAll('.pagination select')
    await pageSizeSelects[0].setValue('20')
    await flushPromises()
    expect(api.listAdminDocuments).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 1, page_size: 20 }),
      undefined,
      false,
    )
  })

  it('shows request failures in the page', async () => {
    vi.mocked(api.listAdminDocuments).mockRejectedValueOnce(new Error('后端不可用'))
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    expect(wrapper.text()).toContain('后端不可用')
  })

  it('collapses a document row when clicked again', async () => {
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    const expandBtns = wrapper.findAll('.doc-row')
    await expandBtns[0].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('第一段内容')

    // Click again to collapse
    await expandBtns[0].trigger('click')
    expect(wrapper.text()).not.toContain('第一段内容')
  })
})
