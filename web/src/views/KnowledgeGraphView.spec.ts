import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import KnowledgeGraphView from './KnowledgeGraphView.vue'
import * as api from '../api'

vi.mock('../api', () => ({
  getGraphData: vi.fn(),
  getEntityChunks: vi.fn(),
  deleteEntity: vi.fn(),
  deleteRelation: vi.fn(),
  deleteEntityChunkLink: vi.fn(),
  createGraphEntity: vi.fn(),
  updateGraphEntity: vi.fn(),
  createGraphRelation: vi.fn(),
  linkEntityChunk: vi.fn(),
  listEntityAliases: vi.fn(),
  createEntityAlias: vi.fn(),
  deleteEntityAlias: vi.fn(),
}))

vi.mock('../utils/graphLayout', () => ({
  createGraphLayout: vi.fn(() => ({
    setGraph: vi.fn(),
    beginNodeDrag: vi.fn(),
    moveNode: vi.fn(),
    endNodeDrag: vi.fn(),
    restartLayout: vi.fn(),
    tick: vi.fn(),
    getAlpha: vi.fn(() => 0),
    getAlphaMin: vi.fn(() => 0),
    destroy: vi.fn(),
  })),
}))

const graphData = {
  nodes: [
    {
      id: 'doc-1',
      label: '2ca727efa70847b49f0f67528544d210.pdf',
      type: 'Document',
      doc_category: '其他',
      review_status: 'approved',
    },
    {
      id: 'tool-1',
      label: 'PipelineBuilder',
      type: 'Tool',
      doc_category: 'StampTools',
      review_status: 'approved',
    },
    {
      id: 'section-1',
      label: '操作系统安装 > 创建虚拟机 > 安装模式',
      type: 'Section',
      doc_category: '其他',
      review_status: 'approved',
    },
  ],
  edges: [
    {
      id: 'edge-1',
      source: 'doc-1',
      target: 'section-1',
      label: 'has_section',
      review_status: 'approved',
    },
  ],
}

describe('KnowledgeGraphView', () => {
  beforeEach(() => {
    vi.mocked(api.getGraphData).mockResolvedValue(graphData)
    vi.mocked(api.getEntityChunks).mockResolvedValue([])
    vi.mocked(api.listEntityAliases).mockResolvedValue([])
  })

  it('shows Document nodes by default and keeps search results visible', async () => {
    const wrapper = mount(KnowledgeGraphView, {
      attachTo: document.body,
    })
    await flushPromises()

    expect(wrapper.text()).toContain('2ca727efa70847b49f0f67528544d210.pdf')

    await wrapper.get('input[placeholder="搜索实体名称..."]').setValue('2ca727')
    await flushPromises()
    expect(wrapper.text()).toContain('2ca727efa70847b49f0f67528544d210.pdf')
    expect(wrapper.text()).toContain('操作系统安装 > 创建虚拟机 > 安装模式')
  })
})
