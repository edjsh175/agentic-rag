import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import GraphCandidatesView from './GraphCandidatesView.vue'
import * as api from '../api'
import type { GraphCandidateBatch, GraphCandidateItem } from '../types'

vi.mock('../api', () => ({
  listGraphCandidateBatches: vi.fn(),
  listGraphCandidateItems: vi.fn(),
  getGraphCandidateQuality: vi.fn(),
  reviewGraphCandidates: vi.fn(),
  applyGraphCandidateBatch: vi.fn(),
}))

const batches: GraphCandidateBatch[] = [
  {
    id: 'batch-1',
    mode: 'profile_sync',
    status: 'draft',
    created_at: '2026-07-09T10:00:00',
    filters: {},
    stats: { total: 5, pending: 4, approved: 0 },
  },
  {
    id: 'batch-2',
    mode: 'full',
    status: 'approved',
    created_at: '2026-07-09T11:00:00',
    filters: {},
    stats: { total: 2, pending: 0, approved: 2 },
  },
]

const candidates: GraphCandidateItem[] = [
  {
    id: 'cand-1',
    batch_id: 'batch-1',
    candidate_kind: 'entity',
    status: 'pending',
    payload: { name: 'StampServer', entity_type: 'Product' },
    evidence_text: 'profile:profile-a:entity_aliases',
    created_at: '2026-07-09T10:00:01',
  },
  {
    id: 'cand-2',
    batch_id: 'batch-1',
    candidate_kind: 'alias',
    status: 'pending',
    payload: { entity_name: 'PipelineBuilder', alias: '管线发布工具' },
    evidence_text: 'profile:profile-a:entity_aliases',
    created_at: '2026-07-09T10:00:02',
  },
  {
    id: 'cand-3',
    batch_id: 'batch-1',
    candidate_kind: 'relation',
    status: 'pending',
    payload: {
      source_name: 'StampServer用户手册_Rocky9.docx',
      relation_type: 'has_section',
      target_name: '操作系统安装 > 创建虚拟机 > 安装模式',
    },
    evidence_text: '操作系统安装 > 创建虚拟机 > 安装模式',
    created_at: '2026-07-09T10:00:03',
  },
  {
    id: 'cand-4',
    batch_id: 'batch-1',
    candidate_kind: 'link',
    status: 'pending',
    payload: { entity_name: 'StampServer', chunk_id: 'c74ae810-63c9-495f-921a-ef6116a50d36' },
    evidence_text: 'StampServer 部署手册目录',
    created_at: '2026-07-09T10:00:04',
  },
  {
    id: 'cand-5',
    batch_id: 'batch-1',
    candidate_kind: 'diagnostic',
    status: 'rejected',
    payload: { message: 'skip generic term' },
    evidence_text: 'skip generic term',
    created_at: '2026-07-09T10:00:05',
  },
]

describe('GraphCandidatesView', () => {
  beforeEach(() => {
    vi.mocked(api.listGraphCandidateBatches).mockResolvedValue(batches)
    vi.mocked(api.listGraphCandidateItems).mockResolvedValue(candidates)
    vi.mocked(api.getGraphCandidateQuality).mockResolvedValue({
      ok: true,
      errors: [],
      warnings: [],
      stats: { candidates: 5 },
    })
    vi.mocked(api.reviewGraphCandidates).mockResolvedValue({
      batch_id: 'batch-1',
      updated_candidates: 2,
      batch_status: 'approved',
    })
    vi.mocked(api.applyGraphCandidateBatch).mockResolvedValue({
      batch_id: 'batch-2',
      status: 'applied',
      applied_candidates: 2,
    })
  })

  it('loads batches and readable candidate detail on mount', async () => {
    const wrapper = mount(GraphCandidatesView)
    await flushPromises()

    expect(api.listGraphCandidateBatches).toHaveBeenCalled()
    expect(api.listGraphCandidateItems).toHaveBeenCalledWith('batch-1', undefined)
    expect(wrapper.text()).toContain('新增实体：StampServer（Product）')
    expect(wrapper.text()).toContain('PipelineBuilder 又名 管线发布工具')
    expect(wrapper.text()).toContain('StampServer用户手册_Rocky9.docx --has_section--> 操作系统安装 > 创建虚拟机 > 安装模式')
    expect(wrapper.text()).toContain('StampServer --证据来自--> c74ae810-63c9-495f-921a-ef6116a50d36')
    expect(wrapper.text()).toContain('证据关联')
    expect(wrapper.text()).toContain('skip generic term')
  })

  it('approves selected candidates and approve-all pending', async () => {
    const wrapper = mount(GraphCandidatesView)
    await flushPromises()

    await wrapper.get('[data-test="select-cand-1"]').setValue(true)
    await wrapper.get('[data-test="approve-selected"]').trigger('click')
    await flushPromises()
    expect(api.reviewGraphCandidates).toHaveBeenCalledWith('batch-1', { approve_ids: ['cand-1'] })

    await wrapper.get('[data-test="approve-all-pending"]').trigger('click')
    await flushPromises()
    expect(api.reviewGraphCandidates).toHaveBeenCalledWith('batch-1', { approve_all: true })
  })

  it('enables apply only for approved batches', async () => {
    const wrapper = mount(GraphCandidatesView)
    await flushPromises()

    expect(wrapper.get('[data-test="apply-batch"]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-test="batch-batch-2"]').trigger('click')
    await flushPromises()
    expect(api.listGraphCandidateItems).toHaveBeenLastCalledWith('batch-2', undefined)
    expect(wrapper.get('[data-test="apply-batch"]').attributes('disabled')).toBeUndefined()

    await wrapper.get('[data-test="apply-batch"]').trigger('click')
    await flushPromises()
    expect(api.applyGraphCandidateBatch).toHaveBeenCalledWith('batch-2')
  })
})
