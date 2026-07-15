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
  let canvasOps: Array<{ op: string; args: unknown[] }>

  const labelPositionsFor = (labels: string[]) => {
    return canvasOps.reduce<Array<{ label: string; x: unknown; y: unknown }>>((acc, op, index) => {
      if (op.op !== 'fillText' || !labels.includes(String(op.args[0]))) return acc
      let translate: { op: string; args: unknown[] } | undefined
      for (let i = index - 1; i >= 0; i -= 1) {
        if (canvasOps[i].op === 'translate') {
          translate = canvasOps[i]
          break
        }
      }
      acc.push({ label: op.args[0] as string, x: translate?.args[0], y: translate?.args[1] })
      return acc
    }, [])
  }

  beforeEach(() => {
    vi.mocked(api.getGraphData).mockResolvedValue(graphData)
    vi.mocked(api.getEntityChunks).mockResolvedValue([])
    vi.mocked(api.listEntityAliases).mockResolvedValue([])
    canvasOps = []
    const context = {
      arc: vi.fn((...args: unknown[]) => canvasOps.push({ op: 'arc', args })),
      beginPath: vi.fn(() => canvasOps.push({ op: 'beginPath', args: [] })),
      clearRect: vi.fn((...args: unknown[]) => canvasOps.push({ op: 'clearRect', args })),
      closePath: vi.fn(() => canvasOps.push({ op: 'closePath', args: [] })),
      fill: vi.fn(() => canvasOps.push({ op: 'fill', args: [] })),
      fillText: vi.fn((...args: unknown[]) => canvasOps.push({ op: 'fillText', args })),
      lineTo: vi.fn((...args: unknown[]) => canvasOps.push({ op: 'lineTo', args })),
      moveTo: vi.fn((...args: unknown[]) => canvasOps.push({ op: 'moveTo', args })),
      quadraticCurveTo: vi.fn((...args: unknown[]) => canvasOps.push({ op: 'quadraticCurveTo', args })),
      restore: vi.fn(() => canvasOps.push({ op: 'restore', args: [] })),
      rotate: vi.fn((...args: unknown[]) => canvasOps.push({ op: 'rotate', args })),
      save: vi.fn(() => canvasOps.push({ op: 'save', args: [] })),
      scale: vi.fn((...args: unknown[]) => canvasOps.push({ op: 'scale', args })),
      stroke: vi.fn(() => canvasOps.push({ op: 'stroke', args: [] })),
      translate: vi.fn((...args: unknown[]) => canvasOps.push({ op: 'translate', args })),
      set fillStyle(_: string) {},
      set font(_: string) {},
      set lineWidth(_: number) {},
      set strokeStyle(_: string) {},
      set textAlign(_: CanvasTextAlign) {},
      set textBaseline(_: CanvasTextBaseline) {},
    } as unknown as CanvasRenderingContext2D
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context)
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
  it('draws reciprocal relationship labels at separate canvas positions', async () => {
    vi.mocked(api.getGraphData).mockResolvedValue({
      nodes: [
        { id: 'a', label: 'Entity A', type: 'Tool', review_status: 'approved' },
        { id: 'b', label: 'Entity B', type: 'Tool', review_status: 'approved' },
      ],
      edges: [
        { id: 'edge-a-b', source: 'a', target: 'b', label: 'requires', review_status: 'approved' },
        { id: 'edge-b-a', source: 'b', target: 'a', label: 'depends_on', review_status: 'approved' },
      ],
    })
    const wrapper = mount(KnowledgeGraphView, { attachTo: document.body })
    await flushPromises()

    await wrapper.get('.entity-li').trigger('click')

    const labelPositions = labelPositionsFor(['requires', 'depends_on'])

    expect(labelPositions).toHaveLength(2)
    expect(new Set(labelPositions.map(item => `${item.x},${item.y}`)).size).toBe(2)
  })

  it('draws same-direction relationship labels at separate canvas positions', async () => {
    vi.mocked(api.getGraphData).mockResolvedValue({
      nodes: [
        { id: 'a', label: 'Entity A', type: 'Tool', review_status: 'approved' },
        { id: 'b', label: 'Entity B', type: 'Tool', review_status: 'approved' },
      ],
      edges: [
        { id: 'edge-a-b-1', source: 'a', target: 'b', label: 'requires', review_status: 'approved' },
        { id: 'edge-a-b-2', source: 'a', target: 'b', label: 'uses_config', review_status: 'approved' },
      ],
    })
    const wrapper = mount(KnowledgeGraphView, { attachTo: document.body })
    await flushPromises()

    await wrapper.get('.entity-li').trigger('click')

    const labelPositions = labelPositionsFor(['requires', 'uses_config'])

    expect(labelPositions).toHaveLength(2)
    expect(new Set(labelPositions.map(item => `${item.x},${item.y}`)).size).toBe(2)
  })

  it('keeps a single relationship on a straight line', async () => {
    vi.mocked(api.getGraphData).mockResolvedValue({
      nodes: [
        { id: 'a', label: 'Entity A', type: 'Tool', review_status: 'approved' },
        { id: 'b', label: 'Entity B', type: 'Tool', review_status: 'approved' },
      ],
      edges: [
        { id: 'edge-a-b', source: 'a', target: 'b', label: 'requires', review_status: 'approved' },
      ],
    })
    const wrapper = mount(KnowledgeGraphView, { attachTo: document.body })
    await flushPromises()

    await wrapper.get('.entity-li').trigger('click')

    expect(labelPositionsFor(['requires'])).toHaveLength(1)
    expect(canvasOps.some(op => op.op === 'quadraticCurveTo')).toBe(false)
  })
})
