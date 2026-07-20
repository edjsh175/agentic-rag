import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import KnowledgeGraphView from './KnowledgeGraphView.vue'
import * as api from '../api'

const routeState = vi.hoisted(() => ({
  query: {} as Record<string, string>,
}))

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
}))

vi.mock('../api', () => ({
  getGraphData: vi.fn(),
  getProductBackbonePreview: vi.fn(),
  createProductBackboneEntity: vi.fn(),
  updateProductBackboneEntity: vi.fn(),
  deleteProductBackboneEntity: vi.fn(),
  createProductBackboneRelation: vi.fn(),
  deleteProductBackboneRelation: vi.fn(),
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
    setPhysicsEnabled: vi.fn(),
    isPhysicsEnabled: vi.fn(() => true),
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
    routeState.query = {}
    vi.mocked(api.getGraphData).mockResolvedValue(graphData)
    vi.mocked(api.getProductBackbonePreview).mockResolvedValue({
      nodes: [
        {
          id: 'preview-root',
          label: 'StampGIS三维产品',
          type: 'Product',
          review_status: 'pending',
          properties_json: JSON.stringify({ subtype: 'ProductFamily' }),
        },
        {
          id: 'preview-layer',
          label: '客户端与渲染层',
          type: 'Module',
          review_status: 'pending',
          properties_json: JSON.stringify({ subtype: 'CoreLayer' }),
        },
        {
          id: 'preview-activex',
          label: 'ActiveX',
          type: 'Module',
          review_status: 'pending',
          properties_json: JSON.stringify({ subtype: 'RenderingSystem', layer: '客户端与渲染层' }),
        },
        {
          id: 'preview-ue',
          label: 'UEModelBuilder',
          type: 'Tool',
          review_status: 'pending',
          properties_json: JSON.stringify({ subtype: 'MainTool' }),
        },
        {
          id: 'preview-service-library',
          label: 'se_port.so',
          type: 'Service',
          review_status: 'pending',
          properties_json: JSON.stringify({ subtype: 'ServiceLibrary' }),
        },
        {
          id: 'preview-format',
          label: '3DTiles',
          type: 'Format',
          review_status: 'pending',
        },
      ],
      edges: [
        { id: 'preview-edge', source: 'preview-ue', target: 'preview-activex', label: 'belongs_to' },
      ],
    })
    vi.mocked(api.createProductBackboneEntity).mockResolvedValue({ id: 'preview-created' })
    vi.mocked(api.updateProductBackboneEntity).mockResolvedValue({ id: 'preview-updated' })
    vi.mocked(api.deleteProductBackboneEntity).mockResolvedValue({ success: true })
    vi.mocked(api.createProductBackboneRelation).mockResolvedValue({ id: 'preview-relation-created' })
    vi.mocked(api.deleteProductBackboneRelation).mockResolvedValue({ success: true })
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
      setLineDash: vi.fn((...args: unknown[]) => canvasOps.push({ op: 'setLineDash', args })),
      stroke: vi.fn(() => canvasOps.push({ op: 'stroke', args: [] })),
      translate: vi.fn((...args: unknown[]) => canvasOps.push({ op: 'translate', args })),
      set fillStyle(_: string) {},
      set font(_: string) {},
      set globalAlpha(_: number) {},
      set lineWidth(_: number) {},
      set shadowBlur(_: number) {},
      set shadowColor(_: string) {},
      set strokeStyle(_: string) {},
      set textAlign(_: CanvasTextAlign) {},
      set textBaseline(_: CanvasTextBaseline) {},
    } as unknown as CanvasRenderingContext2D
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  it('loads product backbone preview from query source and enables preview edit actions', async () => {
    routeState.query = { source: 'product_backbone_preview' }

    const wrapper = mount(KnowledgeGraphView, { attachTo: document.body })
    await flushPromises()

    expect(api.getProductBackbonePreview).toHaveBeenCalled()
    expect(api.getGraphData).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('产品主干预览')
    expect(wrapper.text()).toContain('ActiveX')
    expect(wrapper.find('[data-test="open-create-entity"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="open-create-relation"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="toggle-link-mode"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="edge-legend"]').exists()).toBe(true)
  })

  it('draws product backbone hierarchy nodes with subtype-based radii', async () => {
    routeState.query = { source: 'product_backbone_preview' }

    const wrapper = mount(KnowledgeGraphView, { attachTo: document.body })
    await flushPromises()

    await wrapper.get('.entity-li').trigger('click')

    const radii = canvasOps
      .filter(op => op.op === 'arc')
      .map(op => op.args[2])

    expect(radii).toContain(34)
    expect(radii).toContain(30)
    expect(radii).toContain(25)
    expect(radii).toContain(20)
  })

  it('saves product backbone preview entities through preview API', async () => {
    routeState.query = { source: 'product_backbone_preview' }

    const wrapper = mount(KnowledgeGraphView, { attachTo: document.body })
    await flushPromises()

    await wrapper.get('[data-test="open-create-entity"]').trigger('click')
    await wrapper.get('[data-test="entity-name"]').setValue('New Product Node')
    await wrapper.get('[data-test="entity-type"]').setValue('Module')
    await wrapper.get('[data-test="entity-layer"]').setValue('业务应用层')
    await wrapper.get('[data-test="entity-subtype"]').setValue('BusinessApplication')
    await wrapper.get('[data-test="save-entity"]').trigger('click')
    await flushPromises()

    expect(api.createProductBackboneEntity).toHaveBeenCalledWith(expect.objectContaining({
      name: 'New Product Node',
      graph_type: 'Module',
      layer: '业务应用层',
      subtype: 'BusinessApplication',
    }))
    expect(api.createGraphEntity).not.toHaveBeenCalled()
  })

  it('saves product backbone preview relations through preview API', async () => {
    routeState.query = { source: 'product_backbone_preview' }

    const wrapper = mount(KnowledgeGraphView, { attachTo: document.body })
    await flushPromises()

    await wrapper.get('[data-test="open-create-relation"]').trigger('click')
    await wrapper.get('[data-test="relation-source"]').setValue('preview-root')
    await wrapper.get('[data-test="relation-target"]').setValue('preview-activex')
    await wrapper.get('[data-test="save-relation"]').trigger('click')
    await flushPromises()

    expect(api.createProductBackboneRelation).toHaveBeenCalledWith(expect.objectContaining({
      source_id: 'preview-root',
      target_id: 'preview-activex',
      relation_type: 'belongs_to',
    }))
    expect(api.createGraphRelation).not.toHaveBeenCalled()
  })

  it('shows link-mode hint and updates it while dragging from source to target', async () => {
    routeState.query = { source: 'product_backbone_preview' }
    vi.spyOn(Math, 'random').mockReturnValue(0)
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get: () => 800,
    })
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
      configurable: true,
      get: () => 600,
    })

    const wrapper = mount(KnowledgeGraphView, { attachTo: document.body })
    await flushPromises()

    expect(wrapper.find('[data-test="link-mode-hint"]').exists()).toBe(false)

    await wrapper.get('[data-test="toggle-link-mode"]').trigger('click')
    expect(wrapper.find('.canvas-container').classes()).toContain('link-mode')
    const hint = wrapper.get('[data-test="link-mode-hint"]')
    expect(hint.text()).toContain('从源实体拉到目标实体')

    const canvas = wrapper.get('canvas')
    vi.spyOn(canvas.element, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      bottom: 600,
      right: 800,
      width: 800,
      height: 600,
      toJSON: () => ({}),
    })

    // With Math.random=0 and canvas 800x600, first node sits at (580, 300)
    canvas.element.dispatchEvent(new MouseEvent('mousedown', {
      clientX: 580,
      clientY: 300,
      bubbles: true,
    }))
    await flushPromises()
    expect(hint.text()).toContain('源：StampGIS三维产品')
    expect(hint.text()).toContain('移动到目标实体')

    // Drag away from the source center so the draft edge is actually drawn
    canvas.element.dispatchEvent(new MouseEvent('mousemove', {
      clientX: 620,
      clientY: 340,
      bubbles: true,
    }))
    await flushPromises()
    expect(canvasOps.some(op => op.op === 'setLineDash')).toBe(true)

    // Second node (CoreLayer) sits at angle π/3 → about (490, 455.88)
    canvas.element.dispatchEvent(new MouseEvent('mousemove', {
      clientX: 490,
      clientY: 456,
      bubbles: true,
    }))
    await flushPromises()
    expect(hint.text()).toContain('源：StampGIS三维产品')
    expect(hint.text()).toContain('目标：客户端与渲染层')
  })

  it('toggles dynamic and static physics layout modes', async () => {
    const wrapper = mount(KnowledgeGraphView, { attachTo: document.body })
    await flushPromises()

    const toggle = wrapper.get('[data-test="toggle-physics-mode"]')
    expect(toggle.text()).toBe('动态布局')
    expect(toggle.classes()).toContain('active')

    await toggle.trigger('click')
    expect(toggle.text()).toBe('静态布局')
    expect(toggle.classes()).not.toContain('active')
    expect(wrapper.get('button[title="重新计算整张图的布局（仅动态模式下生效）"]').attributes('disabled')).toBeDefined()

    await toggle.trigger('click')
    expect(toggle.text()).toBe('动态布局')
    expect(toggle.classes()).toContain('active')
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

    const labelPositions = labelPositionsFor(['依赖', 'depends_on'])

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

    const labelPositions = labelPositionsFor(['依赖', '使用配置'])

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

    expect(labelPositionsFor(['依赖'])).toHaveLength(1)
    expect(canvasOps.some(op => op.op === 'quadraticCurveTo')).toBe(false)
  })
})
