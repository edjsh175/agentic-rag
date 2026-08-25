import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AdminChunksView from './AdminChunksView.vue'
import * as api from '../api'
import type { AdminChunk } from '../types'

vi.mock('../api', () => ({
  listAdminChunks: vi.fn(),
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
    file_name: '博客.md',
    source: '博客.md',
    section_title: '摘要',
    doc_category: '博客',
    review_status: 'pending',
    content_preview: '第二段内容',
    content: '第二段完整内容',
    kb_name: '已发布文章',
    page_label: '无页码',
    indexed_at: null,
  },
]

describe('AdminChunksView', () => {
  beforeEach(() => {
    vi.mocked(api.listAdminChunks).mockResolvedValue({
      items: chunks,
      total: 2,
      page: 1,
      page_size: 20,
      total_pages: 1,
    })
    vi.mocked(api.updateAdminChunk).mockResolvedValue({
      message: 'ok', updated_chunks: 1, requested_chunks: 1, status: 'approved',
    })
    vi.mocked(api.batchReviewChunks).mockResolvedValue({
      message: 'ok', updated_chunks: 2, requested_chunks: 2, status: 'approved',
    })
  })

  it('loads pending chunks on mount', async () => {
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    expect(api.listAdminChunks).toHaveBeenCalledWith({
      review_status: 'pending',
      doc_category: 'all',
      page: 1,
      page_size: 20,
    })
    expect(wrapper.text()).toContain('StampServer用户手册.docx')
    expect(wrapper.text()).toContain('博客.md')
  })

  it('filters by category and debounces filename search', async () => {
    vi.useFakeTimers()
    const wrapper = mount(AdminChunksView)
    await flushPromises()
    vi.mocked(api.listAdminChunks).mockClear()

    await wrapper.get('[data-test="category-filter"]').setValue('博客')
    await flushPromises()
    expect(api.listAdminChunks).toHaveBeenLastCalledWith(expect.objectContaining({
      doc_category: '博客', page: 1,
    }))

    await wrapper.get('[data-test="filename-search"]').setValue('MySQL')
    await vi.advanceTimersByTimeAsync(299)
    expect(api.listAdminChunks).not.toHaveBeenLastCalledWith(expect.objectContaining({ filename: 'MySQL' }))
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()
    expect(api.listAdminChunks).toHaveBeenLastCalledWith(expect.objectContaining({ filename: 'MySQL' }))
    vi.useRealTimers()
  })

  it('selects only the current page', async () => {
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    await wrapper.get('[data-test="select-all"]').setValue(true)

    expect(wrapper.get('[data-test="selection-label"]').text()).toContain('已选择 2 项')
  })

  it('selects all matching results across pages', async () => {
    vi.mocked(api.listAdminChunks)
      .mockResolvedValueOnce({
        items: chunks,
        total: 3,
        page: 1,
        page_size: 20,
        total_pages: 2,
      })
      .mockResolvedValueOnce({
        items: [
          { ...chunks[0], chunk_id: 'c1' },
          { ...chunks[1], chunk_id: 'c2' },
        ],
        total: 3,
        page: 1,
        page_size: 100,
        total_pages: 2,
      })
      .mockResolvedValueOnce({
        items: [{ ...chunks[0], chunk_id: 'c3', file_name: 'extra.md' }],
        total: 3,
        page: 2,
        page_size: 100,
        total_pages: 2,
      })

    const wrapper = mount(AdminChunksView)
    await flushPromises()
    vi.mocked(api.listAdminChunks).mockClear()

    await wrapper.get('[data-test="select-all-matching"]').trigger('click')
    await flushPromises()

    expect(api.listAdminChunks).toHaveBeenCalledWith(expect.objectContaining({
      page: 1,
      page_size: 100,
      review_status: 'pending',
    }))
    expect(api.listAdminChunks).toHaveBeenCalledWith(expect.objectContaining({
      page: 2,
      page_size: 100,
    }))
    expect(wrapper.get('[data-test="selection-label"]').text()).toContain('已选择全部 3 项（当前筛选）')
  })

  it('approves all matching filter results in one click', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(api.listAdminChunks)
      .mockResolvedValueOnce({
        items: [chunks[0]],
        total: 2,
        page: 1,
        page_size: 20,
        total_pages: 1,
      })
      .mockResolvedValueOnce({
        items: chunks,
        total: 2,
        page: 1,
        page_size: 100,
        total_pages: 1,
      })
      .mockResolvedValue({
        items: [],
        total: 0,
        page: 1,
        page_size: 20,
        total_pages: 0,
      })

    const wrapper = mount(AdminChunksView)
    await flushPromises()

    await wrapper.get('[data-test="approve-all-matching"]').trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledWith('确认通过当前筛选下全部 2 个知识块？')
    expect(api.batchReviewChunks).toHaveBeenCalledWith(['c1', 'c2'], 'approved')
  })

  it('opens the detail panel and saves category and title edits', async () => {
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    await wrapper.get('[data-test="view-c1"]').trigger('click')
    const detailText = wrapper.get('[data-test="detail-panel"]').text()
    expect(detailText).toContain('第一段完整内容')
    expect(detailText).toContain('来源信息')
    expect(detailText).toContain('StampServer 用户手册')
    expect(detailText).toContain('word/StampServer用户手册.docx')
    expect(detailText).toContain('https://example.com/manual')
    expect(detailText).toContain('技术部')

    await wrapper.get('[data-test="detail-category"]').setValue('基础环境')
    await wrapper.get('[data-test="detail-title"]').setValue('部署准备')
    await wrapper.get('[data-test="save-detail"]').trigger('click')
    await flushPromises()

    expect(api.updateAdminChunk).toHaveBeenCalledWith('c1', {
      doc_category: '基础环境',
      section_title: '部署准备',
    })
    expect(api.listAdminChunks).toHaveBeenCalledTimes(2)
  })

  it('approves one chunk and batch rejects selected chunks', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    await wrapper.get('[data-test="approve-c1"]').trigger('click')
    await flushPromises()
    expect(api.updateAdminChunk).toHaveBeenCalledWith('c1', { review_status: 'approved' })

    await wrapper.get('[data-test="select-all"]').setValue(true)
    await wrapper.get('[data-test="batch-reject"]').trigger('click')
    await flushPromises()
    expect(api.batchReviewChunks).toHaveBeenCalledWith(['c1', 'c2'], 'rejected')
  })

  it('hides redundant review actions for chunks already in that status', async () => {
    vi.mocked(api.listAdminChunks).mockResolvedValueOnce({
      items: [
        { ...chunks[0], review_status: 'approved' },
        { ...chunks[1], review_status: 'rejected' },
      ],
      total: 2,
      page: 1,
      page_size: 20,
      total_pages: 1,
    })
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    expect(wrapper.find('[data-test="approve-c1"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="approve-c2"]').exists()).toBe(true)

    await wrapper.get('[data-test="view-c1"]').trigger('click')
    expect(wrapper.get('[data-test="detail-panel"]').text()).not.toContain('通过')
    expect(wrapper.get('[data-test="detail-panel"]').text()).toContain('驳回')
  })

  it('changes page and page size', async () => {
    vi.mocked(api.listAdminChunks).mockResolvedValue({
      items: chunks,
      total: 42,
      page: 1,
      page_size: 20,
      total_pages: 3,
    })
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    await wrapper.get('[data-test="next-page"]').trigger('click')
    await flushPromises()
    expect(api.listAdminChunks).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))

    await wrapper.get('[data-test="page-size"]').setValue('50')
    await flushPromises()
    expect(api.listAdminChunks).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, page_size: 50 }))
  })

  it('paginates from the top batch bar controls', async () => {
    vi.mocked(api.listAdminChunks).mockResolvedValue({
      items: chunks,
      total: 42,
      page: 1,
      page_size: 20,
      total_pages: 3,
    })
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    await wrapper.get('[data-test="next-page-top"]').trigger('click')
    await flushPromises()
    expect(api.listAdminChunks).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))

    await wrapper.get('[data-test="page-size-top"]').setValue('100')
    await flushPromises()
    expect(api.listAdminChunks).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, page_size: 100 }))
  })

  it('shows request failures in the page', async () => {
    vi.mocked(api.listAdminChunks).mockRejectedValueOnce(new Error('后端不可用'))
    const wrapper = mount(AdminChunksView)
    await flushPromises()

    expect(wrapper.text()).toContain('后端不可用')
  })
})
