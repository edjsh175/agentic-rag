<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import {
  getGraphData,
  getProductBackbonePreview,
  getProductBackboneComplexPreview,
  createProductBackboneEntity,
  updateProductBackboneEntity,
  deleteProductBackboneEntity,
  createProductBackboneRelation,
  deleteProductBackboneRelation,
  getEntityChunks,
  deleteEntity,
  deleteRelation,
  deleteEntityChunkLink,
  createGraphEntity,
  updateGraphEntity,
  createGraphRelation,
  linkEntityChunk,
  listEntityAliases,
  createEntityAlias,
  deleteEntityAlias,
} from '../api'
import type { GraphNode, GraphEdge, EntityChunkDetail, GraphAliasItem } from '../types'
import { DOC_CATEGORIES } from '../types'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import {
  createGraphLayout,
  type GraphLayoutController,
  type LayoutNode,
} from '../utils/graphLayout'
import {
  entitySubtypeLabel,
  entityTypeBadge,
  entityTypeLabel,
  filterTypeLabel,
  linkTypeLabel,
  orderEntityTypes,
  FORMAL_ENTITY_TYPES,
  relationTypeLabel,
} from '../utils/graphLabels'
import { resolvePreviewEdgeStyle } from '../utils/graphEdgeStyle'

const route = useRoute()
const isProductBackbonePreview = computed(() => route.query.source === 'product_backbone_preview')
const isProductBackboneComplexPreview = computed(() => route.query.source === 'product_backbone_preview_complex')
const isProductBackbonePreviewAny = computed(() => isProductBackbonePreview.value || isProductBackboneComplexPreview.value)

// 颜色映射系统
const colors: Record<string, string> = {
  Product: '#a855f7',      // 紫色
  Tool: '#3b82f6',         // 蓝色
  Service: '#10b981',      // 绿色
  Module: '#14b8a6',       // 青色
  DataTable: '#f59e0b',    // 橙黄色
  Field: '#06b6d4',        // 浅蓝色
  ConfigItem: '#64748b',   // 灰色
  Format: '#ec4899',       // 粉色
  Document: '#059669',     // 深绿色
  Section: '#6366f1',      // 靛蓝色
  Procedure: '#f97316',    // 橘橙色
  Step: '#fb923c',         // 浅橙色
  Error: '#ef4444',        // 红色
  Solution: '#22c55e',     // 翠绿色
  ManagementModule: '#0f766e',
  RenderingSystem: '#4f46e5',
  MainTool: '#2563eb',
  StampServerService: '#059669',
  ServiceLibrary: '#475569',
  EnvironmentComponent: '#64748b',
  Command: '#f97316',
  Default: '#94a3b8'
}

/** 正式版默认勾选；Field/Section 默认关闭避免刷屏 */
const DEFAULT_TYPE_SELECTION: Record<string, boolean> = Object.fromEntries(
  FORMAL_ENTITY_TYPES.map(type => [type, type !== 'Field' && type !== 'Section']),
)

const FORM_ENTITY_TYPES = [...FORMAL_ENTITY_TYPES]

// 物理节点接口扩展
interface VisualNode extends GraphNode {
  x: number
  y: number
  vx: number
  vy: number
  fx?: number | null
  fy?: number | null
}

// 可视化边接口
interface NodeStyle {
  radius: number
  fill: string
  stroke: string
  labelFont: string
  badgeText: string
}

interface VisualEdge extends GraphEdge {}
interface DrawableEdge extends VisualEdge {
  parallelOffset: number
  parallelTotal: number
  parallelIndex: number
}

// UI 状态
const loading = ref(false)
const errorMsg = ref('')
const graphData = ref<{ nodes: GraphNode[]; edges: GraphEdge[] }>({ nodes: [], edges: [] })

// 过滤状态
const searchQuery = ref('')
const selectedCategory = ref<string>('all')
const selectedTypes = ref<Record<string, boolean>>({ ...DEFAULT_TYPE_SELECTION })

// 可选的实体类型：正式版固定枚举；主干预览=正式类型顺序 ∩ 数据中出现的类型 + 扩展类型
const availableTypes = computed(() => {
  if (!isProductBackbonePreviewAny.value) {
    return FORM_ENTITY_TYPES
  }
  return Object.keys(selectedTypes.value)
})

const nodeFilterType = (node: GraphNode): string => {
  if (isProductBackbonePreviewAny.value) {
    const props = parseNodeProperties(node) as Record<string, any>
    return (props.layer || node.doc_category || '其他') as string
  }
  return node.type
}

const syncPreviewFilterTypes = (nodes: GraphNode[]) => {
  if (!isProductBackbonePreviewAny.value) {
    selectedTypes.value = { ...DEFAULT_TYPE_SELECTION }
    return
  }
  const previous = selectedTypes.value
  const present = nodes.map(node => {
    const props = parseNodeProperties(node) as Record<string, any>
    return (props.layer || node.doc_category || '其他') as string
  }).filter(Boolean)
  const uniqueLayers = Array.from(new Set(present)).sort((a, b) => a.localeCompare(b))
  const next: Record<string, boolean> = {}
  for (const key of uniqueLayers) {
    next[key] = key in previous ? previous[key] !== false : true
  }
  selectedTypes.value = next
}

const filterTypeLabelLocal = (type: string) => {
  if (isProductBackbonePreviewAny.value) return type
  return filterTypeLabel(type)
}

const filterTypeDotColor = (type: string) => {
  if (isProductBackbonePreviewAny.value) {
    const layerColors: Record<string, string> = {
      '产品体系层': '#a855f7',
      '总体分层框架': '#10b981',
      '客户端与渲染层': '#3b82f6',
      '系统服务层': '#14b8a6',
      '工具与数据处理层': '#f59e0b',
      '标准与安全横向维度': '#ef4444',
      '其他': '#94a3b8'
    }
    return layerColors[type] || '#94a3b8'
  }
  return colors[type] || colors.Default
}

const nodeListBadge = (node: GraphNode) => ({
  text: entityTypeBadge(node.type),
  color: colors[node.type] || colors.Default,
})
const relationTypes = [
  'belongs_to', 'has_table', 'has_field', 'defined_in', 'different_from',
  'uses_config', 'supports_format', 'produces', 'consumes', 'requires',
  'has_step', 'causes', 'solved_by', 'has_section', 'has_chunk',
]
const linkTypes = ['primary', 'mention', 'evidence', 'table_source']

// 画布引用与视口状态
const canvasRef = ref<HTMLCanvasElement | null>(null)
const canvasWidth = ref(800)
const canvasHeight = ref(600)
const panX = ref(0)
const panY = ref(0)
const scale = ref(1.0)

// 图谱布局状态
const visualNodes = ref<VisualNode[]>([])
const visualEdges = ref<VisualEdge[]>([])
let graphLayout: GraphLayoutController | null = null
const parallelEdgeGap = 26
/** 全量加载期间禁止过滤 watch 触发 incremental，否则会钉死节点导致无法散开 */
let suppressFilterLayoutWatch = false

const parseNodeProperties = (node: GraphNode) => {
  if (!node.properties_json) return {}
  try {
    return JSON.parse(node.properties_json) as Record<string, unknown>
  } catch {
    return {}
  }
}

/** Extra node radius from connection count (sqrt so hubs grow without exploding). */
const degreeRadiusBoost = (degree: number) => {
  if (degree <= 0) return 0
  return Math.min(18, Math.sqrt(degree) * 3.5)
}

const nodeDegreeMap = computed(() => {
  const degrees = new Map<string, number>()
  visualEdges.value.forEach(edge => {
    degrees.set(edge.source, (degrees.get(edge.source) || 0) + 1)
    degrees.set(edge.target, (degrees.get(edge.target) || 0) + 1)
  })
  return degrees
})

const getNodeStyle = (node: GraphNode): NodeStyle => {
  const properties = parseNodeProperties(node)
  const subtype = typeof properties.subtype === 'string' ? properties.subtype : ''
  const layer = typeof properties.layer === 'string' ? properties.layer : ''
  const baseFill = colors[node.type] || colors.Default
  let style: NodeStyle = {
    radius: 22,
    fill: baseFill,
    stroke: '#ffffff',
    labelFont: '10px sans-serif',
    badgeText: entityTypeBadge(node.type),
  }

  if (node.type === 'Format') {
    style = { ...style, radius: 20 }
  }

  if (subtype === 'ServiceLibrary') {
    style = { ...style, radius: 20, fill: '#475569', labelFont: '9px sans-serif', badgeText: '库' }
  } else if (subtype === 'ProductFamily') {
    style = { ...style, radius: 34, fill: '#7c3aed', labelFont: 'bold 12px sans-serif', badgeText: '产品' }
  } else if (subtype === 'CoreLayer') {
    style = { ...style, radius: 30, fill: '#0891b2', labelFont: 'bold 11px sans-serif', badgeText: '层' }
  } else if (subtype === 'SupportLayer') {
    style = { ...style, radius: 30, fill: '#0f766e', labelFont: 'bold 11px sans-serif', badgeText: '层' }
  } else if (subtype === 'CrossCuttingDimension') {
    style = { ...style, radius: 30, fill: '#0e7490', labelFont: 'bold 11px sans-serif', badgeText: '横切' }
  } else if (subtype === 'Product' || subtype === 'ManagementProduct') {
    style = { ...style, radius: 27, fill: '#9333ea', labelFont: 'bold 11px sans-serif', badgeText: '产品' }
  } else if (subtype === 'MainTool') {
    style = { ...style, radius: 25, fill: '#2563eb', labelFont: 'bold 10px sans-serif', badgeText: '工具' }
  } else if (subtype === 'RenderingSystem') {
    style = { ...style, radius: 25, fill: '#4f46e5', labelFont: 'bold 10px sans-serif', badgeText: '渲染' }
  } else if (subtype === 'StampServerService') {
    style = { ...style, radius: 25, fill: '#059669', labelFont: 'bold 10px sans-serif', badgeText: '服务' }
  } else if (layer === '客户端与渲染层') {
    style = { ...style, fill: '#6366f1' }
  }

  const degree = nodeDegreeMap.value.get(node.id) || 0
  style.radius += degreeRadiusBoost(degree)
  return style
}

// 选中与交互状态
const selectedNodeId = ref<string | null>(null)
const hoveredNodeId = ref<string | null>(null)
const draggedNodeId = ref<string | null>(null)
const isRightSidebarOpen = ref(false)
const detailsTab = ref<'relations' | 'chunks'>('relations')

// 选中实体详情
const selectedNode = computed(() => {
  return visualNodes.value.find(n => n.id === selectedNodeId.value) || null
})

const selectedNodeProperties = computed<Record<string, any>>(() => {
  if (!selectedNode.value?.properties_json) return {}
  try {
    return JSON.parse(selectedNode.value.properties_json)
  } catch {
    return {}
  }
})

const aliasCandidatesText = (value: unknown) => {
  return Array.isArray(value) ? value.join('\n') : String(value || '')
}

const parseAliasCandidates = (value: string) => {
  return value
    .split(/[\n,，]/)
    .map(item => item.trim())
    .filter(Boolean)
}

// 节点 ID 到节点的快速映射表，用于快速根据 ID 寻找真实实体名称
const nodeMap = computed(() => {
  return new Map(visualNodes.value.map(n => [n.id, n]))
})

// 清除当前图谱和面板的选中高亮状态
const clearSelection = () => {
  selectedNodeId.value = null
  isRightSidebarOpen.value = false
  evidenceChunks.value = []
  expandedChunkId.value = null
  loadingChunks.value = false
}

// 一跳邻居节点集合
const neighborNodeIds = computed(() => {
  if (!selectedNodeId.value) return new Set<string>()
  const neighbors = new Set<string>()
  visualEdges.value.forEach(edge => {
    if (edge.source === selectedNodeId.value) neighbors.add(edge.target)
    if (edge.target === selectedNodeId.value) neighbors.add(edge.source)
  })
  return neighbors
})

// 当前节点的所有关系连接
const selectedNodeRelations = computed(() => {
  if (!selectedNodeId.value) return []
  return visualEdges.value.filter(
    edge => edge.source === selectedNodeId.value || edge.target === selectedNodeId.value
  )
})

// 原文证据 Chunks 状态与加载
const evidenceChunks = ref<EntityChunkDetail[]>([])
const loadingChunks = ref(false)
const expandedChunkId = ref<string | null>(null)

// 删除确认模态框状态
const isDeleteEntityModalOpen = ref(false)
const deleteConfirmationInput = ref('')
const isDeleting = ref(false)

const aliases = ref<GraphAliasItem[]>([])
const aliasesLoading = ref(false)
const aliasInput = ref('')
const aliasSaving = ref(false)

const isEntityModalOpen = ref(false)
const entityModalMode = ref<'create' | 'edit'>('create')
const entityForm = ref({
  name: '',
  entity_type: 'Tool',
  doc_category: '',
  canonical_name: '',
  description: '',
  confidence: '1',
  review_status: 'approved',
  layer: '',
  subtype: '',
  source: '',
  status: '',
  alias_candidates: '',
})
const entitySaving = ref(false)

const isRelationModalOpen = ref(false)
const relationForm = ref({
  source_id: '',
  target_id: '',
  relation_type: 'belongs_to',
  confidence: '1',
  evidence_text: '',
})
const relationSaving = ref(false)
const isLinkMode = ref(false)
const linkStartNodeId = ref<string | null>(null)
const linkHoverNodeId = ref<string | null>(null)
const linkCursorPos = ref<{ x: number; y: number } | null>(null)
const isPhysicsEnabled = ref(true)

const clearLinkDraft = () => {
  linkStartNodeId.value = null
  linkHoverNodeId.value = null
  linkCursorPos.value = null
}

const linkModeHint = computed(() => {
  if (!isLinkMode.value) return ''
  if (!linkStartNodeId.value) {
    return '拖动：从源实体拉到目标实体；松开后填写关系类型'
  }
  const source = nodeMap.value.get(linkStartNodeId.value)
  const sourceLabel = source?.label || '源实体'
  if (linkHoverNodeId.value) {
    const target = nodeMap.value.get(linkHoverNodeId.value)
    return `源：${sourceLabel} → 目标：${target?.label || '目标实体'}`
  }
  return `源：${sourceLabel} → 目标：移动到目标实体`
})

const chunkLinkInput = ref('')
const chunkLinkSaving = ref(false)

// 过滤后的节点（左侧列表展示）
const filteredNodesList = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  const directlyMatchedIds = new Set<string>()
  if (query) {
    graphData.value.nodes.forEach(node => {
      const matchSearch = node.label.toLowerCase().includes(query) ||
                          (node.canonical_name || '').toLowerCase().includes(query)
      if (matchSearch) directlyMatchedIds.add(node.id)
    })
  }
  const searchNeighborIds = new Set<string>(directlyMatchedIds)
  if (query) {
    graphData.value.edges.forEach(edge => {
      if (directlyMatchedIds.has(edge.source)) searchNeighborIds.add(edge.target)
      if (directlyMatchedIds.has(edge.target)) searchNeighborIds.add(edge.source)
    })
  }

  return visualNodes.value.filter(node => {
    const matchSearch = !query || searchNeighborIds.has(node.id)
    const matchCategory = selectedCategory.value === 'all' || node.doc_category === selectedCategory.value
    const matchType = query ? matchSearch : selectedTypes.value[nodeFilterType(node)] !== false
    return matchSearch && matchCategory && matchType
  })
})

// 加载图谱数据
const fetchGraph = async () => {
  loading.value = true
  errorMsg.value = ''
  suppressFilterLayoutWatch = true
  try {
    const data = isProductBackboneComplexPreview.value
      ? await getProductBackboneComplexPreview()
      : isProductBackbonePreview.value
      ? await getProductBackbonePreview()
      : await getGraphData(selectedCategory.value)
    graphData.value = data
    syncPreviewFilterTypes(data.nodes)

    // 初始化物理仿真节点（已有节点保留坐标，仅新节点播种）
    const existingNodeMap = new Map(visualNodes.value.map(n => [n.id, n]))
    const cx = canvasWidth.value / 2
    const cy = canvasHeight.value / 2

    visualNodes.value = data.nodes.map((node, idx) => {
      const existing = existingNodeMap.get(node.id)
      if (existing) {
        return {
          ...node,
          x: existing.x,
          y: existing.y,
          vx: existing.vx,
          vy: existing.vy,
        }
      }
      // 圆形发散初始位置
      const angle = (idx / (data.nodes.length || 1)) * Math.PI * 2
      const radius = 180 + Math.random() * 250
      return {
        ...node,
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
      }
    })

    // 根据过滤显示节点确定需要可视化的边，避免孤立边
    updateVisualSubGraph()
    graphLayout?.setGraph(filteredNodesList.value as LayoutNode[], visualEdges.value, 'initial')
  } catch (err: any) {
    errorMsg.value = err.message || '加载图谱数据失败'
  } finally {
    loading.value = false
    await nextTick()
    suppressFilterLayoutWatch = false
  }
}

// 依据用户过滤条件筛选出可见的子图节点和边
const updateVisualSubGraph = () => {
  const activeNodeIds = new Set(filteredNodesList.value.map(n => n.id))
  
  // 筛选可见的边，两端节点必须都在可见集合中
  visualEdges.value = graphData.value.edges.filter(edge => {
    return activeNodeIds.has(edge.source) && activeNodeIds.has(edge.target)
  })
}

// 类型与搜索过滤只增量调整当前子图，避免扰动原有布局
watch([selectedTypes, searchQuery], () => {
  if (suppressFilterLayoutWatch) return
  updateVisualSubGraph()
  graphLayout?.setGraph(filteredNodesList.value as LayoutNode[], visualEdges.value, 'incremental')
}, { deep: true })

watch(selectedCategory, () => {
  if (isProductBackbonePreviewAny.value) return
  fetchGraph()
})

watch(isProductBackbonePreviewAny, () => {
  selectedCategory.value = 'all'
  isLinkMode.value = false
  clearLinkDraft()
  clearSelection()
  fetchGraph()
})

watch(isLinkMode, (enabled) => {
  if (!enabled) clearLinkDraft()
})

// 监听过滤后的节点列表，如果当前选中的实体被隐藏，则自动清除选中状态，防止图谱异常灰化
watch(filteredNodesList, (nodes) => {
  if (!selectedNodeId.value) return
  const stillVisible = nodes.some(node => node.id === selectedNodeId.value)
  if (!stillVisible) {
    clearSelection()
  }
})

// Canvas 渲染绘图逻辑
const drawGraph = () => {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  
  // 确保 backing store 的大小与高清屏的像素比（DPI）相匹配
  const ratio = window.devicePixelRatio || 1
  const container = containerRef.value
  if (container) {
    const expectedWidth = Math.round(container.clientWidth * ratio)
    const expectedHeight = Math.round(container.clientHeight * ratio)
    if (canvas.width !== expectedWidth || canvas.height !== expectedHeight) {
      canvas.width = expectedWidth
      canvas.height = expectedHeight
      canvasWidth.value = container.clientWidth
      canvasHeight.value = container.clientHeight
    }
  }
  
  // 清空画布
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  
  ctx.save()
  // 应用高清 DPI 缩放
  ctx.scale(ratio, ratio)
  
  // 应用平移和缩放变换
  ctx.translate(panX.value, panY.value)
  ctx.scale(scale.value, scale.value)
  
  const nodes = filteredNodesList.value
  const nodesById = new Map(nodes.map(n => [n.id, n]))
  
  // 是否有高亮节点
  const hasSelection = selectedNodeId.value !== null
  const selectedId = selectedNodeId.value
  const neighbors = neighborNodeIds.value
  const isLinking = isLinkMode.value && !!linkStartNodeId.value
  const linkSourceId = linkStartNodeId.value
  const linkTargetId = linkHoverNodeId.value
  
  // --- 1. 绘制关系连线 ---
  buildDrawableEdges(visualEdges.value).forEach(edge => {
    const n1 = nodesById.get(edge.source)
    const n2 = nodesById.get(edge.target)
    if (!n1 || !n2) return
    
    let isHighlighted = false
    let isFaded = false
    
    if (isLinking) {
      isFaded = true
    } else if (hasSelection) {
      if (edge.source === selectedId || edge.target === selectedId) {
        isHighlighted = true
      } else {
        isFaded = true
      }
    } else if (hoveredNodeId.value && (edge.source === hoveredNodeId.value || edge.target === hoveredNodeId.value)) {
      isHighlighted = true
    }
    
    const isSectionEdge = n1.type === 'Section' || n2.type === 'Section'
    drawEdge(ctx, n1, n2, edge, isHighlighted, isFaded, isSectionEdge)
  })
  
  // --- 2. 绘制实体节点 ---
  nodes.forEach(node => {
    let isSelected = false
    let isNeighbor = false
    let isLinkTarget = false
    let isFaded = false
    
    if (isLinking) {
      if (node.id === linkSourceId) {
        isSelected = true
      } else if (linkTargetId && node.id === linkTargetId) {
        isLinkTarget = true
      } else {
        isFaded = true
      }
    } else if (hasSelection) {
      if (node.id === selectedId) {
        isSelected = true
      } else if (neighbors.has(node.id)) {
        isNeighbor = true
      } else {
        isFaded = true
      }
    } else if (hoveredNodeId.value === node.id) {
      isNeighbor = true
    }
    
    drawNode(ctx, node, isSelected, isNeighbor, isFaded, isLinkTarget)
  })

  // --- 3. 连线模式草稿边 ---
  if (isLinking && linkSourceId) {
    const source = nodesById.get(linkSourceId)
    if (source) {
      const target = linkTargetId ? nodesById.get(linkTargetId) : null
      const end = target
        ? { x: target.x, y: target.y, radius: getNodeStyle(target).radius }
        : linkCursorPos.value
          ? { x: linkCursorPos.value.x, y: linkCursorPos.value.y, radius: 0 }
          : null
      if (end) drawLinkDraft(ctx, source, end)
    }
  }
  
  ctx.restore()
}

// 计算同一对实体之间的多重边偏移，避免线条和标签重叠
const edgePairKey = (edge: Pick<VisualEdge, 'source' | 'target'>) => {
  return [edge.source, edge.target].sort().join('\u0000')
}

const buildDrawableEdges = (edges: VisualEdge[]): DrawableEdge[] => {
  const groups = new Map<string, VisualEdge[]>()
  edges.forEach(edge => {
    const key = edgePairKey(edge)
    groups.set(key, [...(groups.get(key) || []), edge])
  })

  const metadata = new Map<string, Pick<DrawableEdge, 'parallelOffset' | 'parallelTotal' | 'parallelIndex'>>()
  groups.forEach(group => {
    const sorted = [...group].sort((a, b) => a.id.localeCompare(b.id))
    const total = sorted.length
    sorted.forEach((edge, index) => {
      metadata.set(edge.id, {
        parallelOffset: (index - (total - 1) / 2) * parallelEdgeGap,
        parallelTotal: total,
        parallelIndex: index,
      })
    })
  })

  return edges.map(edge => ({
    ...edge,
    ...(metadata.get(edge.id) || { parallelOffset: 0, parallelTotal: 1, parallelIndex: 0 }),
  }))
}

// 绘制单条边
const drawEdge = (
  ctx: CanvasRenderingContext2D,
  n1: VisualNode,
  n2: VisualNode,
  edge: DrawableEdge,
  isHighlighted: boolean,
  isFaded: boolean,
  isSectionEdge: boolean
) => {
  const sourceRadius = getNodeStyle(n1).radius
  const targetRadius = getNodeStyle(n2).radius
  const arrowSize = 6
  
  const dx = n2.x - n1.x
  const dy = n2.y - n1.y
  const dist = Math.sqrt(dx * dx + dy * dy)
  if (dist < 10) return
  
  const ux = dx / dist
  const uy = dy / dist
  const normalX = -uy
  const normalY = ux
  const canonicalDirection = edge.source <= edge.target ? 1 : -1
  const curveOffset = edge.parallelTotal > 1 ? edge.parallelOffset * canonicalDirection : 0
  
  // 连线端点偏置到节点边缘，避开内部重合
  const sourceBorderX = n1.x + ux * sourceRadius
  const sourceBorderY = n1.y + uy * sourceRadius
  const targetBorderX = n2.x - ux * targetRadius
  const targetBorderY = n2.y - uy * targetRadius
  const controlX = (sourceBorderX + targetBorderX) / 2 + normalX * curveOffset
  const controlY = (sourceBorderY + targetBorderY) / 2 + normalY * curveOffset

  const previewStyle = isProductBackbonePreviewAny.value
    ? resolvePreviewEdgeStyle({
        relationType: edge.label,
        highlighted: isHighlighted,
        faded: isFaded,
      })
    : null
  
  ctx.beginPath()
  ctx.moveTo(sourceBorderX, sourceBorderY)
  if (curveOffset) {
    ctx.quadraticCurveTo(controlX, controlY, targetBorderX, targetBorderY)
  } else {
    ctx.lineTo(targetBorderX, targetBorderY)
  }
  ctx.strokeStyle = previewStyle
    ? previewStyle.color
    : (isHighlighted
      ? '#a855f7'
      : (isFaded ? 'rgba(148, 163, 184, 0.15)' : (isSectionEdge ? 'rgba(99, 102, 241, 0.07)' : '#cbd5e1')))
  ctx.lineWidth = previewStyle
    ? previewStyle.width
    : (isHighlighted ? 2.2 : (isSectionEdge ? 0.55 : 0.8))
  ctx.stroke()
  
  const tangentX = curveOffset ? targetBorderX - controlX : ux
  const tangentY = curveOffset ? targetBorderY - controlY : uy
  const tangentDist = Math.sqrt(tangentX * tangentX + tangentY * tangentY) || 1
  const arrowUx = tangentX / tangentDist
  const arrowUy = tangentY / tangentDist
  
  // 绘制箭头
  ctx.beginPath()
  ctx.moveTo(targetBorderX, targetBorderY)
  ctx.lineTo(
    targetBorderX - arrowUx * arrowSize + arrowUy * (arrowSize / 1.4),
    targetBorderY - arrowUy * arrowSize - arrowUx * (arrowSize / 1.4)
  )
  ctx.lineTo(
    targetBorderX - arrowUx * arrowSize - arrowUy * (arrowSize / 1.4),
    targetBorderY - arrowUy * arrowSize + arrowUx * (arrowSize / 1.4)
  )
  ctx.closePath()
  ctx.fillStyle = previewStyle
    ? previewStyle.color
    : (isHighlighted
      ? '#a855f7'
      : (isFaded ? 'rgba(148, 163, 184, 0.15)' : (isSectionEdge ? 'rgba(99, 102, 241, 0.1)' : '#94a3b8')))
  ctx.fill()
  
  // 选中节点的一跳关系边永远显示关系标签，其余普通边保持隐藏，避免噪点
  const shouldDrawLabel = isHighlighted
  if (shouldDrawLabel) {
    const midX = curveOffset
      ? (sourceBorderX + 2 * controlX + targetBorderX) / 4
      : (sourceBorderX + targetBorderX) / 2
    const midY = curveOffset
      ? (sourceBorderY + 2 * controlY + targetBorderY) / 4
      : (sourceBorderY + targetBorderY) / 2
    
    ctx.save()
    ctx.translate(midX, midY)
    
    let angle = Math.atan2(dy, dx)
    // 旋转文本，避免倒挂
    if (angle > Math.PI / 2 || angle < -Math.PI / 2) {
      angle += Math.PI
    }
    ctx.rotate(angle)
    
    ctx.font = isHighlighted ? 'bold 9px sans-serif' : '9px sans-serif'
    ctx.fillStyle = previewStyle
      ? previewStyle.labelColor
      : (isHighlighted ? '#7e22ce' : (isFaded ? 'rgba(148, 163, 184, 0.3)' : '#64748b'))
    ctx.textAlign = 'center'
    ctx.textBaseline = 'bottom'
    ctx.fillText(relationTypeLabel(edge.label), 0, -2)
    ctx.restore()
  }
}

// 绘制单个节点
const drawNode = (
  ctx: CanvasRenderingContext2D,
  node: VisualNode,
  isSelected: boolean,
  isNeighbor: boolean,
  isFaded: boolean,
  isLinkTarget = false
) => {
  const nodeStyle = getNodeStyle(node)
  const nodeRadius = nodeStyle.radius
  
  ctx.save()
  // 如果当前属于被遮罩淡出状态，则整体应用低透明度，彻底降噪
  if (isFaded) {
    ctx.globalAlpha = 0.45
  }
  
  ctx.beginPath()
  ctx.arc(node.x, node.y, nodeRadius, 0, Math.PI * 2)
  ctx.fillStyle = nodeStyle.fill
  ctx.fill()
  
  // 描边画圆
  ctx.beginPath()
  ctx.arc(node.x, node.y, nodeRadius, 0, Math.PI * 2)
  ctx.strokeStyle = isSelected
    ? '#a855f7'
    : (isLinkTarget ? '#0d9488' : (isNeighbor ? '#818cf8' : nodeStyle.stroke))
  ctx.lineWidth = isSelected || isLinkTarget ? 3.5 : (isNeighbor ? 2.5 : 1.5)
  ctx.stroke()
  
  if ((isSelected || isLinkTarget) && !isFaded) {
    ctx.shadowColor = isLinkTarget ? 'rgba(13, 148, 136, 0.45)' : 'rgba(168, 85, 247, 0.45)'
    ctx.shadowBlur = 10
  }
  
  // 写实体类型首字母缩写
  ctx.font = nodeRadius >= 30 ? 'bold 10px monospace' : 'bold 9px monospace'
  ctx.fillStyle = '#ffffff'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(nodeStyle.badgeText, node.x, node.y)
  
  // 节点名称文字
  ctx.font = (isSelected || isLinkTarget) ? 'bold 11px sans-serif' : nodeStyle.labelFont
  ctx.fillStyle = isSelected || isLinkTarget ? '#1e293b' : (isFaded ? '#94a3b8' : '#334155')
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  
  let labelText = node.label
  if (labelText.length > 10) {
    labelText = labelText.substring(0, 8) + '...'
  }
  ctx.fillText(labelText, node.x, node.y + nodeRadius + 5)
  
  ctx.restore()
}

const drawLinkDraft = (
  ctx: CanvasRenderingContext2D,
  source: VisualNode,
  end: { x: number; y: number; radius: number }
) => {
  const sourceRadius = getNodeStyle(source).radius
  const dx = end.x - source.x
  const dy = end.y - source.y
  const dist = Math.sqrt(dx * dx + dy * dy)
  if (dist < 4) return

  const ux = dx / dist
  const uy = dy / dist
  const startX = source.x + ux * sourceRadius
  const startY = source.y + uy * sourceRadius
  const endX = end.x - ux * end.radius
  const endY = end.y - uy * end.radius
  const color = end.radius > 0 ? '#0d9488' : '#14b8a6'
  const arrowSize = 8

  ctx.save()
  ctx.setLineDash([8, 6])
  ctx.beginPath()
  ctx.moveTo(startX, startY)
  ctx.lineTo(endX, endY)
  ctx.strokeStyle = color
  ctx.lineWidth = 2
  ctx.stroke()
  ctx.setLineDash([])

  ctx.beginPath()
  ctx.moveTo(endX, endY)
  ctx.lineTo(
    endX - ux * arrowSize + uy * (arrowSize / 1.4),
    endY - uy * arrowSize - ux * (arrowSize / 1.4)
  )
  ctx.lineTo(
    endX - ux * arrowSize - uy * (arrowSize / 1.4),
    endY - uy * arrowSize + ux * (arrowSize / 1.4)
  )
  ctx.closePath()
  ctx.fillStyle = color
  ctx.fill()
  ctx.restore()
}

// 转换物理鼠标位置到图谱坐标位置
const getGraphCoords = (e: MouseEvent) => {
  const canvas = canvasRef.value
  if (!canvas) return { x: 0, y: 0 }
  const rect = canvas.getBoundingClientRect()
  const mouseX = e.clientX - rect.left
  const mouseY = e.clientY - rect.top
  return {
    x: (mouseX - panX.value) / scale.value,
    y: (mouseY - panY.value) / scale.value
  }
}

// 鼠标按下：拖拽画布或选中/拽移节点
let isPanning = false
let startPanX = 0
let startPanY = 0

const handleMouseDown = (e: MouseEvent) => {
  const coords = getGraphCoords(e)
  const clickedNode = findNodeAt(coords.x, coords.y)

  if (isLinkMode.value) {
    if (clickedNode) {
      linkStartNodeId.value = clickedNode.id
      linkHoverNodeId.value = null
      linkCursorPos.value = { x: coords.x, y: coords.y }
      selectedNodeId.value = clickedNode.id
      isRightSidebarOpen.value = true
      detailsTab.value = 'relations'
    } else {
      clearLinkDraft()
    }
    drawGraph()
    return
  }
  
  if (clickedNode) {
    draggedNodeId.value = clickedNode.id
    graphLayout?.beginNodeDrag(clickedNode.id)
    selectedNodeId.value = clickedNode.id
    isRightSidebarOpen.value = true
    detailsTab.value = 'relations'
    expandedChunkId.value = null
    evidenceChunks.value = []
  } else {
    clearSelection()
    isPanning = true
    startPanX = e.clientX - panX.value
    startPanY = e.clientY - panY.value
  }
}

// 鼠标移动
const handleMouseMove = (e: MouseEvent) => {
  const coords = getGraphCoords(e)

  if (isLinkMode.value && linkStartNodeId.value) {
    linkCursorPos.value = { x: coords.x, y: coords.y }
    const hoverNode = findNodeAt(coords.x, coords.y)
    linkHoverNodeId.value = hoverNode && hoverNode.id !== linkStartNodeId.value
      ? hoverNode.id
      : null
    hoveredNodeId.value = hoverNode ? hoverNode.id : null
    drawGraph()
    return
  }
  
  if (draggedNodeId.value) {
    const node = visualNodes.value.find(n => n.id === draggedNodeId.value)
    if (node) {
      graphLayout?.moveNode(node.id, coords.x, coords.y)
    }
  } else if (isPanning) {
    panX.value = e.clientX - startPanX
    panY.value = e.clientY - startPanY
  } else {
    const hoverNode = findNodeAt(coords.x, coords.y)
    hoveredNodeId.value = hoverNode ? hoverNode.id : null
  }
  drawGraph()
}

// 鼠标放开
const handleMouseUp = (e?: MouseEvent) => {
  if (isLinkMode.value && linkStartNodeId.value) {
    const sourceId = linkStartNodeId.value
    const targetNode = e ? findNodeAt(getGraphCoords(e).x, getGraphCoords(e).y) : null
    clearLinkDraft()
    if (targetNode && targetNode.id !== sourceId) {
      relationForm.value = {
        source_id: sourceId,
        target_id: targetNode.id,
        relation_type: 'belongs_to',
        confidence: '1',
        evidence_text: '',
      }
      isRelationModalOpen.value = true
    }
    drawGraph()
    return
  }
  if (draggedNodeId.value) graphLayout?.endNodeDrag(draggedNodeId.value)
  draggedNodeId.value = null
  isPanning = false
  drawGraph()
}

// 滚轮缩放
const handleWheel = (e: WheelEvent) => {
  e.preventDefault()
  const canvas = canvasRef.value
  if (!canvas) return
  
  const rect = canvas.getBoundingClientRect()
  const mouseX = e.clientX - rect.left
  const mouseY = e.clientY - rect.top
  
  const graphX = (mouseX - panX.value) / scale.value
  const graphY = (mouseY - panY.value) / scale.value
  
  const zoomIntensity = 0.08
  const nextScale = e.deltaY < 0 
    ? scale.value * (1 + zoomIntensity)
    : scale.value / (1 + zoomIntensity)
    
  scale.value = Math.max(0.12, Math.min(3.5, nextScale))
  panX.value = mouseX - graphX * scale.value
  panY.value = mouseY - graphY * scale.value
  drawGraph()
}

// 寻找特定坐标下的节点
const findNodeAt = (x: number, y: number) => {
  const nodes = filteredNodesList.value
  return nodes.find(n => {
    const nodeRadius = getNodeStyle(n).radius
    const dx = n.x - x
    const dy = n.y - y
    return dx * dx + dy * dy < nodeRadius * nodeRadius
  })
}

// 左侧节点列表点击定位
const selectAndFocusNode = (nodeId: string) => {
  selectedNodeId.value = nodeId
  isRightSidebarOpen.value = true
  detailsTab.value = 'relations'
  expandedChunkId.value = null
  evidenceChunks.value = []
  
  const node = visualNodes.value.find(n => n.id === nodeId)
  if (node && canvasRef.value) {
    // 平滑移动使节点置于画布中央
    panX.value = canvasWidth.value / 2 - node.x * scale.value
    panY.value = canvasHeight.value / 2 - node.y * scale.value
    drawGraph()
  }
}

// 重置视口缩放平移
const resetView = () => {
  scale.value = 1.0
  panX.value = 0
  panY.value = 0
  drawGraph()
}

const restartLayout = () => graphLayout?.restartLayout()

const togglePhysicsMode = () => {
  isPhysicsEnabled.value = !isPhysicsEnabled.value
  graphLayout?.setPhysicsEnabled(isPhysicsEnabled.value)
  drawGraph()
}

// 拉取某个实体的证据 Chunks (懒加载)，增加请求归属检查防串
const loadEvidenceChunks = async (entityId: string) => {
  if (isProductBackbonePreviewAny.value) {
    evidenceChunks.value = []
    loadingChunks.value = false
    return
  }
  loadingChunks.value = true
  try {
    const chunks = await getEntityChunks(entityId)
    if (selectedNodeId.value === entityId) {
      evidenceChunks.value = chunks
    }
  } catch (err: any) {
    if (selectedNodeId.value === entityId) {
      errorMsg.value = err.message || '加载证据关联失败'
    }
  } finally {
    if (selectedNodeId.value === entityId) {
      loadingChunks.value = false
    }
  }
}

const loadAliases = async (entityId: string) => {
  if (isProductBackbonePreviewAny.value) {
    aliases.value = []
    aliasesLoading.value = false
    return
  }
  aliasesLoading.value = true
  try {
    const items = await listEntityAliases(entityId)
    if (selectedNodeId.value === entityId) {
      aliases.value = items
    }
  } catch (err: any) {
    if (selectedNodeId.value === entityId) {
      errorMsg.value = err.message || '加载 aliases 失败'
    }
  } finally {
    if (selectedNodeId.value === entityId) {
      aliasesLoading.value = false
    }
  }
}

const openCreateEntityModal = () => {
  entityModalMode.value = 'create'
  entityForm.value = {
    name: '',
    entity_type: 'Tool',
    doc_category: selectedCategory.value === 'all' ? '' : selectedCategory.value,
    canonical_name: '',
    description: '',
    confidence: '1',
    review_status: 'approved',
    layer: isProductBackbonePreviewAny.value ? '' : '',
    subtype: '',
    source: '',
    status: isProductBackbonePreviewAny.value ? '待确认' : '',
    alias_candidates: '',
  }
  isEntityModalOpen.value = true
}

const openEditEntityModal = () => {
  if (!selectedNode.value) return
  const properties = selectedNodeProperties.value
  entityModalMode.value = 'edit'
  entityForm.value = {
    name: selectedNode.value.label,
    entity_type: selectedNode.value.type,
    doc_category: selectedNode.value.doc_category || '',
    canonical_name: selectedNode.value.canonical_name || '',
    description: selectedNode.value.description || '',
    confidence: String(selectedNode.value.confidence ?? 1),
    review_status: selectedNode.value.review_status || 'approved',
    layer: String(properties.layer || selectedNode.value.doc_category || ''),
    subtype: String(properties.subtype || ''),
    source: String(properties.source || ''),
    status: String(properties.status || ''),
    alias_candidates: aliasCandidatesText(properties.alias_candidates),
  }
  isEntityModalOpen.value = true
}

const saveEntity = async () => {
  entitySaving.value = true
  try {
    if (isProductBackbonePreviewAny.value) {
      const payload = {
        name: entityForm.value.name,
        graph_type: entityForm.value.entity_type,
        layer: entityForm.value.layer || null,
        subtype: entityForm.value.subtype || null,
        description: entityForm.value.description || null,
        source: entityForm.value.source || null,
        status: entityForm.value.status || null,
        alias_candidates: parseAliasCandidates(entityForm.value.alias_candidates),
      }
      const saved = entityModalMode.value === 'create'
        ? await createProductBackboneEntity(payload)
        : selectedNodeId.value
          ? await updateProductBackboneEntity(selectedNodeId.value, payload)
          : null
      if (saved?.id) {
        selectedNodeId.value = saved.id
        isRightSidebarOpen.value = true
      }
      isEntityModalOpen.value = false
      await fetchGraph()
      return
    }

    const payload = {
      name: entityForm.value.name,
      entity_type: entityForm.value.entity_type,
      doc_category: entityForm.value.doc_category || null,
      canonical_name: entityForm.value.canonical_name || null,
      description: entityForm.value.description || null,
      confidence: Number(entityForm.value.confidence || 1),
      review_status: entityForm.value.review_status || null,
    }
    if (entityModalMode.value === 'create') {
      const created = await createGraphEntity(payload)
      selectedNodeId.value = created.id
      isRightSidebarOpen.value = true
    } else if (selectedNodeId.value) {
      await updateGraphEntity(selectedNodeId.value, payload)
    }
    isEntityModalOpen.value = false
    await fetchGraph()
    if (selectedNodeId.value) {
      await loadAliases(selectedNodeId.value)
    }
  } catch (err: any) {
    alert('保存实体失败：' + err.message)
  } finally {
    entitySaving.value = false
  }
}

const openRelationModal = () => {
  relationForm.value = {
    source_id: selectedNodeId.value || '',
    target_id: '',
    relation_type: 'belongs_to',
    confidence: '1',
    evidence_text: '',
  }
  isRelationModalOpen.value = true
}

const saveRelation = async () => {
  relationSaving.value = true
  try {
    if (isProductBackbonePreviewAny.value) {
      await createProductBackboneRelation({
        source_id: relationForm.value.source_id,
        target_id: relationForm.value.target_id,
        relation_type: relationForm.value.relation_type,
        evidence_text: relationForm.value.evidence_text || null,
      })
      isRelationModalOpen.value = false
      await fetchGraph()
      return
    }

    await createGraphRelation({
      source_id: relationForm.value.source_id,
      target_id: relationForm.value.target_id,
      relation_type: relationForm.value.relation_type,
      confidence: Number(relationForm.value.confidence || 1),
      evidence_text: relationForm.value.evidence_text || null,
    })
    isRelationModalOpen.value = false
    await fetchGraph()
  } catch (err: any) {
    alert('保存关系失败：' + err.message)
  } finally {
    relationSaving.value = false
  }
}

const addAlias = async () => {
  if (isProductBackbonePreviewAny.value) return
  if (!selectedNodeId.value || !aliasInput.value.trim()) return
  aliasSaving.value = true
  try {
    await createEntityAlias(selectedNodeId.value, {
      alias: aliasInput.value.trim(),
      review_status: 'approved',
    })
    aliasInput.value = ''
    await loadAliases(selectedNodeId.value)
  } catch (err: any) {
    alert('新增 alias 失败：' + err.message)
  } finally {
    aliasSaving.value = false
  }
}

const removeAlias = async (aliasId: string) => {
  if (isProductBackbonePreviewAny.value) return
  if (!confirm('确认删除这个 alias 吗？')) return
  try {
    await deleteEntityAlias(aliasId)
    if (selectedNodeId.value) {
      await loadAliases(selectedNodeId.value)
    }
  } catch (err: any) {
    alert('删除 alias 失败：' + err.message)
  }
}

const addChunkLink = async () => {
  if (isProductBackbonePreviewAny.value) return
  if (!selectedNodeId.value || !chunkLinkInput.value.trim()) return
  chunkLinkSaving.value = true
  try {
    await linkEntityChunk(selectedNodeId.value, chunkLinkInput.value.trim(), linkTypes[0])
    chunkLinkInput.value = ''
    await loadEvidenceChunks(selectedNodeId.value)
  } catch (err: any) {
    alert('关联 Chunk 失败：' + err.message)
  } finally {
    chunkLinkSaving.value = false
  }
}

// 监听详情卡片切换 Tab 并加载数据
watch([selectedNodeId, detailsTab], () => {
  if (selectedNodeId.value && detailsTab.value === 'chunks') {
    loadEvidenceChunks(selectedNodeId.value)
  }
})

watch(selectedNodeId, (entityId) => {
  aliases.value = []
  aliasInput.value = ''
  if (entityId) {
    loadAliases(entityId)
  }
})

// 删除关系连线
const handleDeleteRelation = async (relationId: string) => {
  if (!confirm('确认要删除这条关系连接吗？这将永久从知识图谱中移除该边。')) return
  try {
    if (isProductBackbonePreviewAny.value) {
      await deleteProductBackboneRelation(relationId)
    } else {
      await deleteRelation(relationId)
    }
    // 本地移除边，同步刷新画面
    graphData.value.edges = graphData.value.edges.filter(edge => edge.id !== relationId)
    visualEdges.value = visualEdges.value.filter(edge => edge.id !== relationId)
    updateVisualSubGraph()
  } catch (err: any) {
    alert('删除关系失败：' + err.message)
  }
}

// 解除 Chunks 与实体的证据链接关系
const handleUnlinkChunk = async (chunkId: string) => {
  if (isProductBackbonePreviewAny.value) return
  if (!selectedNodeId.value) return
  if (!confirm('确定要解除该实体与当前原文片段的关联链吗？')) return
  try {
    await deleteEntityChunkLink(selectedNodeId.value, chunkId)
    evidenceChunks.value = evidenceChunks.value.filter(item => item.chunk_id !== chunkId)
  } catch (err: any) {
    alert('解除证据链失败：' + err.message)
  }
}

// 删除实体（强确认逻辑）
const initiateDeleteEntity = () => {
  deleteConfirmationInput.value = ''
  isDeleteEntityModalOpen.value = true
}

const confirmDeleteEntity = async () => {
  if (!selectedNode.value) return
  if (deleteConfirmationInput.value !== selectedNode.value.label) {
    alert('输入的名称不匹配，请重新输入实体名确认！')
    return
  }
  
  isDeleting.value = true
  try {
    if (isProductBackbonePreviewAny.value) {
      await deleteProductBackboneEntity(selectedNode.value.id)
    } else {
      await deleteEntity(selectedNode.value.id)
    }
    
    // 移除本地数据中的实体及所有关联边
    const deletedId = selectedNode.value.id
    graphData.value.nodes = graphData.value.nodes.filter(n => n.id !== deletedId)
    graphData.value.edges = graphData.value.edges.filter(e => e.source !== deletedId && e.target !== deletedId)
    
    visualNodes.value = visualNodes.value.filter(n => n.id !== deletedId)
    
    clearSelection()
    isDeleteEntityModalOpen.value = false
    
    // 重置缩放和平移，使视口回归中心
    panX.value = 0
    panY.value = 0
    scale.value = 1.0
    
    updateVisualSubGraph()
    graphLayout?.setGraph(filteredNodesList.value as LayoutNode[], visualEdges.value, 'incremental')
    alert('实体删除成功')
  } catch (err: any) {
    alert('删除实体失败：' + err.message)
  } finally {
    isDeleting.value = false
  }
}

// Markdown 原文证据展示转换渲染
const renderMarkdown = (content: string) => {
  const raw = marked.parse(content, { async: false }) as string
  return DOMPurify.sanitize(raw)
}

// 画布尺寸自动适配监听
const containerRef = ref<HTMLDivElement | null>(null)
const handleResize = () => {
  const container = containerRef.value
  if (container) {
    canvasWidth.value = container.clientWidth
    canvasHeight.value = container.clientHeight
    const canvas = canvasRef.value
    if (canvas) {
      const ratio = window.devicePixelRatio || 1
      canvas.width = Math.round(container.clientWidth * ratio)
      canvas.height = Math.round(container.clientHeight * ratio)
    }
  }
}

onMounted(() => {
  handleResize()
  graphLayout = createGraphLayout({
    width: canvasWidth.value,
    height: canvasHeight.value,
    onTick: drawGraph,
    // Product backbone is denser; give edges more room so clusters separate.
    ...(isProductBackbonePreviewAny.value
      ? { linkDistance: 260, chargeStrength: -560, collideRadius: 48 }
      : { linkDistance: 220, chargeStrength: -420, collideRadius: 40 }),
  })
  graphLayout.setPhysicsEnabled(isPhysicsEnabled.value)
  window.addEventListener('resize', handleResize)
  fetchGraph()
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  graphLayout?.destroy()
  graphLayout = null
})
</script>

<template>
  <div class="kg-container">
    <!-- 左侧过滤器和列表 -->
    <aside class="kg-left-panel">
      <div class="panel-header">
        <h3>实体过滤筛选</h3>
      </div>
      
      <div class="panel-body scrollable">
        <!-- 搜索 -->
        <div class="filter-group">
          <label>实体检索</label>
          <input 
            type="text" 
            v-model="searchQuery" 
            placeholder="搜索实体名称..." 
            class="filter-input"
          />
        </div>
        
        <!-- 数据分类 -->
        <div v-if="!isProductBackbonePreviewAny" class="filter-group">
          <label>所属分类 (doc_category)</label>
          <select v-model="selectedCategory" class="filter-select">
            <option value="all">全部分类</option>
            <option v-for="cat in DOC_CATEGORIES" :key="cat" :value="cat">
              {{ cat }}
            </option>
          </select>
        </div>
        
        <!-- 实体类型多选 -->
        <div class="filter-group">
          <label>{{ isProductBackbonePreviewAny ? '展示功能分层' : '展示实体类型' }}</label>
          <div class="checkbox-list">
            <div 
              v-for="type in availableTypes" 
              :key="type" 
              class="checkbox-item"
            >
              <input 
                type="checkbox" 
                :id="`type-${type}`" 
                v-model="selectedTypes[type]"
              />
              <label :for="`type-${type}`" class="checkbox-label">
                <span 
                  class="type-dot" 
                  :style="{ backgroundColor: filterTypeDotColor(type) }"
                ></span>
                {{ filterTypeLabelLocal(type) }}
              </label>
            </div>
          </div>
        </div>

        <!-- 实体列表展示 -->
        <div class="entity-list-section">
          <label>筛选结果 ({{ filteredNodesList.length }} 个实体)</label>
          <ul class="entity-ul">
            <li 
              v-for="node in filteredNodesList" 
              :key="node.id" 
              class="entity-li"
              :class="{ active: selectedNodeId === node.id }"
              @click="selectAndFocusNode(node.id)"
            >
              <span 
                class="type-tag" 
                :style="{ backgroundColor: nodeListBadge(node).color }"
              >
                {{ nodeListBadge(node).text }}
              </span>
              <span class="entity-name">{{ node.label }}</span>
            </li>
            <li v-if="filteredNodesList.length === 0" class="empty-list">
              无匹配的实体
            </li>
          </ul>
        </div>
      </div>
    </aside>

    <!-- 中间可视化工作画布 -->
    <main class="kg-center-panel">
      <!-- 状态与统计栏 -->
      <header class="kg-toolbar">
        <div class="kg-stats">
          <span class="stats-badge">
            <strong>{{ graphData.nodes.length }}</strong> 实体 / <strong>{{ graphData.edges.length }}</strong> 关系
          </span>
          <span v-if="selectedCategory !== 'all'" class="category-badge">
            当前分类: {{ selectedCategory }}
          </span>
          <span v-if="isProductBackboneComplexPreview" class="category-badge">
            产品架构主干预览（复杂明细版）
          </span>
          <span v-else-if="isProductBackbonePreview" class="category-badge">
            产品架构主干预览（精简主干版）
          </span>
          <span v-if="isProductBackbonePreviewAny" class="edge-legend" data-test="edge-legend">
            <span class="edge-legend-item"><i style="background:rgba(120,132,180,0.7)"></i>属于</span>
            <span class="edge-legend-item"><i style="background:rgba(185,150,105,0.7)"></i>依赖</span>
            <span class="edge-legend-item edge-legend-note">颜色区分关系类型</span>
          </span>
        </div>
        
        <div class="kg-controls">
          <button @click="openCreateEntityModal" class="icon-btn" data-test="open-create-entity">
            新建实体
          </button>
          <button @click="openRelationModal" class="icon-btn" data-test="open-create-relation">
            新建关系
          </button>
          <button
            @click="isLinkMode = !isLinkMode"
            class="icon-btn"
            :class="{ active: isLinkMode }"
            data-test="toggle-link-mode"
          >
            {{ isLinkMode ? '退出连线' : '连线模式' }}
          </button>
          <button
            @click="togglePhysicsMode"
            class="icon-btn"
            :class="{ active: isPhysicsEnabled }"
            data-test="toggle-physics-mode"
            :title="isPhysicsEnabled ? '当前为动态：节点互相推挤碰撞；点击切换为静态冻结' : '当前为静态：布局已冻结；点击切换为动态物理'"
          >
            {{ isPhysicsEnabled ? '动态布局' : '静态布局' }}
          </button>
          <button @click="resetView" class="icon-btn" title="复位视图">
            复位布局
          </button>
          <button 
            @click="restartLayout" 
            class="icon-btn"
            title="重新计算整张图的布局（仅动态模式下生效）"
            :disabled="!isPhysicsEnabled"
          >
            重新布局
          </button>
        </div>
      </header>

      <!-- 画布容器 -->
      <div
        class="canvas-container"
        ref="containerRef"
        :class="{ 'link-mode': isLinkMode }"
      >
        <div
          v-if="isLinkMode"
          class="link-mode-hint"
          data-test="link-mode-hint"
        >
          {{ linkModeHint }}
        </div>
        <canvas 
          ref="canvasRef" 
          @mousedown="handleMouseDown"
          @mousemove="handleMouseMove"
          @mouseup="handleMouseUp"
          @mouseleave="handleMouseUp"
          @wheel="handleWheel"
        ></canvas>
        
        <div v-if="loading" class="canvas-overlay">
          <div class="loader"></div>
          <span>加载中...</span>
        </div>
        <div v-if="errorMsg" class="canvas-overlay error">
          <span>⚠️ 加载出错: {{ errorMsg }}</span>
          <button @click="fetchGraph" class="retry-btn">重试</button>
        </div>
      </div>
    </main>

    <!-- 右侧实体详情面板 -->
    <aside 
      class="kg-right-panel" 
      :class="{ open: isRightSidebarOpen && selectedNode }"
    >
      <div v-if="selectedNode" class="panel-layout">
        <header class="right-header">
          <div class="header-main">
            <span 
              class="type-badge" 
              :style="{ backgroundColor: colors[selectedNode.type] || colors.Default }"
            >
              {{ entityTypeLabel(selectedNode.type) }}
            </span>
            <div class="header-actions">
              <button @click="openEditEntityModal" class="mini-btn" data-test="open-edit-entity">编辑实体</button>
              <button @click="clearSelection" class="close-btn">&times;</button>
            </div>
          </div>
          <h2>{{ selectedNode.label }}</h2>
          <p class="category-meta" v-if="selectedNode.doc_category">
            分类: {{ selectedNode.doc_category }}
          </p>
        </header>

        <!-- 详细元数据属性 -->
        <div class="metadata-card" v-if="selectedNode.canonical_name || selectedNode.review_status">
          <div class="meta-row" v-if="selectedNode.canonical_name">
            <span class="meta-label">规范名称:</span>
            <span class="meta-val">{{ selectedNode.canonical_name }}</span>
          </div>
          <div class="meta-row" v-if="selectedNode.review_status">
            <span class="meta-label">审核状态:</span>
            <span 
              class="status-tag"
              :class="selectedNode.review_status"
            >
              {{ selectedNode.review_status === 'approved' ? '已审核' : (selectedNode.review_status === 'rejected' ? '已拒绝' : '待审核') }}
            </span>
          </div>
          <div class="meta-row" v-if="selectedNode.confidence !== undefined && selectedNode.confidence !== null">
            <span class="meta-label">置信度:</span>
            <span class="meta-val">{{ selectedNode.confidence.toFixed(2) }}</span>
          </div>
          <div class="meta-row description" v-if="selectedNode.description">
            <span class="meta-label">实体说明:</span>
            <p class="meta-text">{{ selectedNode.description }}</p>
          </div>
          <div class="meta-row" v-if="selectedNodeProperties.layer">
            <span class="meta-label">功能层:</span>
            <span class="meta-val">{{ selectedNodeProperties.layer }}</span>
          </div>
          <div class="meta-row" v-if="selectedNodeProperties.subtype">
            <span class="meta-label">实体子类型:</span>
            <span class="meta-val">{{ entitySubtypeLabel(selectedNodeProperties.subtype) }}</span>
          </div>
          <div class="meta-row" v-if="selectedNodeProperties.source">
            <span class="meta-label">来源:</span>
            <span class="meta-val">{{ selectedNodeProperties.source }}</span>
          </div>
          <div class="meta-row" v-if="selectedNodeProperties.status">
            <span class="meta-label">整理状态:</span>
            <span class="meta-val">{{ selectedNodeProperties.status }}</span>
          </div>
          <div class="meta-row" v-if="selectedNodeProperties.alias_candidates?.length">
            <span class="meta-label">别名候选:</span>
            <span class="meta-val">{{ selectedNodeProperties.alias_candidates.join('、') }}</span>
          </div>
        </div>

        <div class="metadata-card alias-card">
          <div class="alias-head">
            <span class="meta-label">Aliases</span>
            <span class="meta-val" v-if="aliasesLoading">加载中...</span>
          </div>
          <div v-if="!isProductBackbonePreviewAny" class="alias-create-row">
            <input
              v-model="aliasInput"
              class="filter-input compact-input"
              placeholder="新增 alias"
              data-test="alias-input"
            />
            <button class="mini-btn" :disabled="aliasSaving" @click="addAlias" data-test="add-alias">
              添加
            </button>
          </div>
          <ul class="alias-list">
            <li v-for="alias in aliases" :key="alias.id" class="alias-item">
              <span>{{ alias.alias }}</span>
              <button v-if="!isProductBackbonePreviewAny" class="delete-text-btn" @click="removeAlias(alias.id)">删除</button>
            </li>
            <li v-if="!aliasesLoading && aliases.length === 0" class="empty-inline">暂无 alias</li>
          </ul>
        </div>

        <!-- 详情 Tab 菜单 -->
        <nav class="detail-tabs">
          <button 
            :class="{ active: detailsTab === 'relations' }" 
            @click="detailsTab = 'relations'"
          >
            关系连接 ({{ selectedNodeRelations.length }})
          </button>
          <button 
            :class="{ active: detailsTab === 'chunks' }" 
            @click="detailsTab = 'chunks'"
          >
            原文证据
          </button>
        </nav>

        <!-- Tab 内容容器 -->
        <div class="tab-content scrollable">
          <!-- 关系连线展示 -->
          <div v-if="detailsTab === 'relations'" class="tab-pane">
            <ul class="relation-ul">
              <li 
                v-for="rel in selectedNodeRelations" 
                :key="rel.id" 
                class="relation-li"
              >
                <!-- 指向指示 -->
                <div class="relation-path">
                  <span 
                    class="node-link" 
                    @click="selectAndFocusNode(rel.source)"
                    :class="{ current: rel.source === selectedNode.id }"
                    :title="rel.source === selectedNode.id ? '当前实体' : '源端实体'"
                  >
                    {{ nodeMap.get(rel.source)?.label || rel.source }}
                  </span>
                  <span class="rel-arrow">
                    ── <strong>{{ relationTypeLabel(rel.label) }}</strong> ──&gt;
                  </span>
                  <span 
                    class="node-link" 
                    @click="selectAndFocusNode(rel.target)"
                    :class="{ current: rel.target === selectedNode.id }"
                    :title="rel.target === selectedNode.id ? '当前实体' : '目标实体'"
                  >
                    {{ nodeMap.get(rel.target)?.label || rel.target }}
                  </span>
                </div>
                <!-- 关系的源信息与删除 -->
                <div class="relation-footer">
                  <span class="rel-meta" v-if="rel.evidence_text">
                    证据: "{{ rel.evidence_text.substring(0, 25) }}..."
                  </span>
                  <button
                    @click="handleDeleteRelation(rel.id)" 
                    class="delete-text-btn"
                  >
                    删除边
                  </button>
                </div>
              </li>
              <li v-if="selectedNodeRelations.length === 0" class="empty-tab">
                当前实体无任何关系连接。
              </li>
            </ul>
          </div>

          <!-- 证据 Chunk 展示 -->
          <div v-else class="tab-pane">
            <div v-if="loadingChunks" class="tab-loader">
              <div class="loader-sm"></div>
              <span>加载证据片段...</span>
            </div>
            
            <div v-else class="chunks-container">
              <div v-if="!isProductBackbonePreviewAny" class="chunk-link-form">
                <input
                  v-model="chunkLinkInput"
                  class="filter-input compact-input"
                  placeholder="输入 chunk_id 建立证据关联"
                  data-test="chunk-link-input"
                />
                <button class="mini-btn" :disabled="chunkLinkSaving" @click="addChunkLink" data-test="add-chunk-link">
                  关联
                </button>
              </div>
              <div 
                v-for="chunk in evidenceChunks" 
                :key="chunk.chunk_id" 
                class="chunk-card"
              >
                <header class="chunk-card-header">
                  <div class="chunk-title-sec">
                    <h4>{{ chunk.file_name }}</h4>
                    <p class="chunk-path" v-if="chunk.section_title">
                      章节: {{ chunk.section_title }}
                    </p>
                  </div>
                  <span class="chunk-type">{{ linkTypeLabel(chunk.link_type) }}</span>
                </header>

                <div class="chunk-preview-text">
                  {{ chunk.content_preview }}
                </div>

                <!-- Markdown 详情折叠 -->
                <div 
                  v-if="expandedChunkId === chunk.chunk_id" 
                  class="chunk-markdown markdown-body"
                  v-html="renderMarkdown(chunk.content)"
                ></div>

                <footer class="chunk-card-footer">
                  <button 
                    @click="expandedChunkId = expandedChunkId === chunk.chunk_id ? null : chunk.chunk_id" 
                    class="action-text-btn"
                  >
                    {{ expandedChunkId === chunk.chunk_id ? '收起原文' : '查看完整原文' }}
                  </button>
                  <button
                    v-if="!isProductBackbonePreviewAny"
                    @click="handleUnlinkChunk(chunk.chunk_id)" 
                    class="delete-text-btn"
                  >
                    解除关联
                  </button>
                </footer>
              </div>
              <div v-if="evidenceChunks.length === 0" class="empty-tab">
                当前实体未建立与任何 Chunk 的证据关联。
              </div>
            </div>
          </div>
        </div>

        <!-- 删除实体卡片底座 -->
        <footer class="panel-footer">
          <button @click="initiateDeleteEntity" class="danger-btn">
            删除当前实体
          </button>
        </footer>
      </div>
    </aside>

    <!-- 级联删除实体确认模态弹窗 -->
    <div 
      class="modal-backdrop" 
      v-if="isDeleteEntityModalOpen && selectedNode"
    >
      <div class="modal-card">
        <header class="modal-header">
          <h3>⚠️ 高危操作：级联删除实体</h3>
          <button @click="isDeleteEntityModalOpen = false" class="close-btn">&times;</button>
        </header>
        <div class="modal-body">
          <p class="warning-text">
            删除实体 <strong>“{{ selectedNode.label }}”</strong> 将会连带删除它所关联的<strong>全部关系边（{{ selectedNodeRelations.length }} 条）</strong>以及<strong>证据链关联记录</strong>，此操作为物理删除且<strong>不可恢复</strong>。
          </p>
          <p class="prompt-text">
            请输入实体名称 <strong>{{ selectedNode.label }}</strong> 确认此删除操作：
          </p>
          <input 
            type="text" 
            v-model="deleteConfirmationInput" 
            class="modal-input"
            :placeholder="selectedNode.label"
          />
        </div>
        <footer class="modal-footer">
          <button 
            @click="isDeleteEntityModalOpen = false" 
            class="secondary-btn"
          >
            取消
          </button>
          <button 
            @click="confirmDeleteEntity" 
            class="danger-confirm-btn"
            :disabled="deleteConfirmationInput !== selectedNode.label || isDeleting"
          >
            {{ isDeleting ? '正在删除...' : '确认并级联删除' }}
          </button>
        </footer>
      </div>
    </div>

    <div class="modal-backdrop" v-if="isEntityModalOpen">
      <div class="modal-card">
        <header class="modal-header">
          <h3>{{ entityModalMode === 'create' ? '新建实体' : '编辑实体' }}</h3>
          <button @click="isEntityModalOpen = false" class="close-btn">&times;</button>
        </header>
        <div class="modal-body form-body">
          <label class="form-label">名称</label>
          <input v-model="entityForm.name" class="modal-input" data-test="entity-name" />
          <label class="form-label">类型</label>
          <select v-model="entityForm.entity_type" class="filter-select" data-test="entity-type">
            <option v-for="type in FORM_ENTITY_TYPES" :key="type" :value="type">{{ entityTypeLabel(type) }}</option>
          </select>
          <template v-if="isProductBackbonePreviewAny">
            <label class="form-label">功能层</label>
            <input v-model="entityForm.layer" class="modal-input" data-test="entity-layer" />
            <label class="form-label">实体子类型</label>
            <input v-model="entityForm.subtype" class="modal-input" data-test="entity-subtype" />
            <label class="form-label">来源</label>
            <input v-model="entityForm.source" class="modal-input" data-test="entity-source" />
            <label class="form-label">状态</label>
            <input v-model="entityForm.status" class="modal-input" data-test="entity-status" />
            <label class="form-label">别名候选</label>
            <textarea v-model="entityForm.alias_candidates" class="modal-textarea" data-test="entity-alias-candidates"></textarea>
          </template>
          <template v-else>
            <label class="form-label">分类</label>
            <select v-model="entityForm.doc_category" class="filter-select">
              <option value="">未设置</option>
              <option v-for="cat in DOC_CATEGORIES" :key="cat" :value="cat">{{ cat }}</option>
            </select>
            <label class="form-label">规范名</label>
            <input v-model="entityForm.canonical_name" class="modal-input" />
          </template>
          <label class="form-label">说明</label>
          <textarea v-model="entityForm.description" class="modal-textarea"></textarea>
        </div>
        <footer class="modal-footer">
          <button @click="isEntityModalOpen = false" class="secondary-btn">取消</button>
          <button @click="saveEntity" class="danger-confirm-btn" :disabled="entitySaving" data-test="save-entity">
            {{ entitySaving ? '保存中...' : '保存实体' }}
          </button>
        </footer>
      </div>
    </div>

    <div class="modal-backdrop" v-if="isRelationModalOpen">
      <div class="modal-card">
        <header class="modal-header">
          <h3>新建关系</h3>
          <button @click="isRelationModalOpen = false" class="close-btn">&times;</button>
        </header>
        <div class="modal-body form-body">
          <label class="form-label">源实体</label>
          <select v-model="relationForm.source_id" class="filter-select" data-test="relation-source">
            <option value="">请选择</option>
            <option v-for="node in graphData.nodes" :key="`source-${node.id}`" :value="node.id">{{ node.label }}</option>
          </select>
          <label class="form-label">目标实体</label>
          <select v-model="relationForm.target_id" class="filter-select" data-test="relation-target">
            <option value="">请选择</option>
            <option v-for="node in graphData.nodes" :key="`target-${node.id}`" :value="node.id">{{ node.label }}</option>
          </select>
          <label class="form-label">关系类型</label>
          <select v-model="relationForm.relation_type" class="filter-select">
            <option v-for="type in relationTypes" :key="type" :value="type">{{ relationTypeLabel(type) }}</option>
          </select>
          <label class="form-label">证据说明</label>
          <input v-model="relationForm.evidence_text" class="modal-input" />
        </div>
        <footer class="modal-footer">
          <button @click="isRelationModalOpen = false" class="secondary-btn">取消</button>
          <button @click="saveRelation" class="danger-confirm-btn" :disabled="relationSaving" data-test="save-relation">
            {{ relationSaving ? '保存中...' : '保存关系' }}
          </button>
        </footer>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 全局页面三栏结构 */
.kg-container {
  display: flex;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #f1f5f9;
}

/* 一级滚动栏定义 */
.scrollable {
  overflow-y: auto;
  scrollbar-width: thin;
}

/* 左侧过滤面板 */
.kg-left-panel {
  width: 290px;
  background: #ffffff;
  border-right: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  z-index: 10;
  box-shadow: 1px 0 4px rgba(0, 0, 0, 0.02);
}

.panel-header {
  padding: 16px 20px;
  border-bottom: 1px solid #f1f5f9;
  flex-shrink: 0;
}

.panel-header h3 {
  font-size: 15px;
  color: #1e293b;
  font-weight: 600;
}

.panel-body {
  flex: 1;
  padding: 16px 20px;
}

.filter-group {
  margin-bottom: 20px;
}

.filter-group label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 6px;
}

.filter-input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  font-size: 13px;
  color: #1e293b;
  outline: none;
  transition: border-color 0.15s;
}

.filter-input:focus {
  border-color: #3b82f6;
}

.filter-select {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  background: #ffffff;
  font-size: 13px;
  color: #1e293b;
  outline: none;
  cursor: pointer;
}

.checkbox-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid #f1f5f9;
  padding: 8px;
  border-radius: 6px;
}

.checkbox-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.checkbox-item input {
  cursor: pointer;
}

.checkbox-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: #334155;
  cursor: pointer;
  user-select: none;
}

.type-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
}

.entity-list-section {
  border-top: 1px solid #f1f5f9;
  padding-top: 16px;
}

.entity-list-section label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 10px;
}

.entity-ul {
  list-style: none;
}

.entity-li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.1s;
  margin-bottom: 3px;
}

.entity-li:hover {
  background: #f1f5f9;
}

.entity-li.active {
  background: #eff6ff;
}

.type-tag {
  font-size: 8px;
  font-family: monospace;
  font-weight: bold;
  color: #ffffff;
  padding: 2px 4px;
  border-radius: 4px;
  min-width: 22px;
  text-align: center;
  flex-shrink: 0;
}

.entity-name {
  font-size: 12px;
  color: #1e293b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.empty-list {
  padding: 16px 0;
  text-align: center;
  color: #94a3b8;
  font-size: 12px;
}

/* 中部可视化面板 */
.kg-center-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
  position: relative;
}

.kg-toolbar {
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid #e2e8f0;
  padding: 10px 20px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;
}

.kg-stats {
  display: flex;
  align-items: center;
  gap: 10px;
}

.stats-badge {
  font-size: 12px;
  color: #475569;
  background: #f1f5f9;
  padding: 4px 10px;
  border-radius: 20px;
}

.category-badge {
  font-size: 11px;
  color: #4f46e5;
  background: #e0e7ff;
  padding: 3px 8px;
  border-radius: 4px;
  font-weight: 500;
}

.edge-legend {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  margin-left: 4px;
  font-size: 11px;
  color: #64748b;
}

.edge-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.edge-legend-item i {
  width: 14px;
  height: 3px;
  border-radius: 2px;
  display: inline-block;
}

.edge-legend-note {
  color: #94a3b8;
}

.kg-controls {
  display: flex;
  gap: 8px;
}

.icon-btn {
  padding: 6px 12px;
  font-size: 12px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  color: #334155;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.1s;
}

.icon-btn:hover {
  background: #f8fafc;
  border-color: #94a3b8;
}

.icon-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  background: #f8fafc;
  border-color: #e2e8f0;
  color: #94a3b8;
}

.icon-btn:disabled:hover {
  background: #f8fafc;
  border-color: #e2e8f0;
}

.icon-btn.active {
  background: #eff6ff;
  border-color: #3b82f6;
  color: #1d4ed8;
}

.canvas-container {
  flex: 1;
  width: 100%;
  height: 100%;
  position: relative;
  background: #f8fafc;
}

.canvas-container.link-mode,
.canvas-container.link-mode canvas {
  cursor: crosshair;
}

.link-mode-hint {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 2;
  max-width: calc(100% - 32px);
  padding: 6px 14px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.82);
  color: #f8fafc;
  font-size: 12px;
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.18);
}

.canvas-container canvas {
  width: 100%;
  height: 100%;
  display: block;
}

/* 覆盖层 */
.canvas-overlay {
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.7);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #475569;
  font-size: 13px;
}

.canvas-overlay.error {
  background: rgba(254, 242, 242, 0.9);
  color: #ef4444;
}

.retry-btn {
  padding: 6px 16px;
  background: #ef4444;
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}

.retry-btn:hover {
  background: #dc2626;
}

.loader {
  border: 3px solid #f3f3f3;
  border-top: 3px solid #3b82f6;
  border-radius: 50%;
  width: 24px;
  height: 24px;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

/* 右侧详情面板 */
.kg-right-panel {
  width: 380px;
  background: #ffffff;
  border-left: 1px solid #e2e8f0;
  transform: translateX(100%);
  transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  z-index: 10;
  box-shadow: -1px 0 4px rgba(0, 0, 0, 0.02);
}

.kg-right-panel.open {
  transform: translateX(0);
}

.panel-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.right-header {
  padding: 20px 24px 16px;
  border-bottom: 1px solid #f1f5f9;
  flex-shrink: 0;
}

.header-main {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.type-badge {
  font-size: 10px;
  color: white;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 20px;
}

.close-btn {
  background: none;
  border: none;
  font-size: 22px;
  color: #94a3b8;
  cursor: pointer;
  line-height: 1;
}

.close-btn:hover {
  color: #475569;
}

.mini-btn {
  padding: 6px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #ffffff;
  color: #334155;
  font-size: 12px;
  cursor: pointer;
}

.mini-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.right-header h2 {
  font-size: 18px;
  font-weight: 600;
  color: #1e293b;
  word-break: break-all;
}

.category-meta {
  font-size: 11px;
  color: #64748b;
  margin-top: 4px;
}

/* 元数据卡片 */
.metadata-card {
  margin: 16px 24px 0;
  padding: 12px 16px;
  background: #f8fafc;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.meta-row {
  display: flex;
  margin-bottom: 8px;
  font-size: 12px;
}

.meta-row:last-child {
  margin-bottom: 0;
}

.meta-row.description {
  flex-direction: column;
  margin-top: 8px;
  border-top: 1px dashed #e2e8f0;
  padding-top: 8px;
}

.meta-label {
  width: 70px;
  color: #64748b;
  flex-shrink: 0;
}

.meta-val {
  color: #334155;
  font-weight: 500;
  word-break: break-all;
}

.status-tag {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 4px;
  font-weight: 500;
}

.status-tag.approved {
  background: #d1fae5;
  color: #065f46;
}

.status-tag.pending {
  background: #fef3c7;
  color: #92400e;
}

.status-tag.rejected {
  background: #fee2e2;
  color: #991b1b;
}

.meta-text {
  color: #475569;
  line-height: 1.4;
  margin-top: 4px;
}

.alias-card {
  margin-top: 12px;
}

.alias-head,
.alias-create-row,
.alias-item,
.chunk-link-form {
  display: flex;
  align-items: center;
  gap: 8px;
}

.alias-head,
.chunk-link-form {
  justify-content: space-between;
}

.alias-create-row,
.chunk-link-form {
  margin-top: 10px;
}

.compact-input {
  flex: 1;
}

.alias-list {
  list-style: none;
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.alias-item {
  justify-content: space-between;
  font-size: 12px;
  color: #334155;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 8px 10px;
}

.empty-inline {
  color: #94a3b8;
  font-size: 12px;
}

/* Tab 选项卡 */
.detail-tabs {
  display: flex;
  margin: 16px 24px 0;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.detail-tabs button {
  flex: 1;
  padding: 8px 0;
  background: none;
  border: none;
  font-size: 13px;
  color: #64748b;
  cursor: pointer;
  position: relative;
  font-weight: 500;
}

.detail-tabs button.active {
  color: #3b82f6;
  font-weight: 600;
}

.detail-tabs button.active::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 0;
  right: 0;
  height: 2px;
  background: #3b82f6;
}

/* Tab 内容 */
.tab-content {
  flex: 1;
  padding: 16px 24px;
}

.tab-pane {
  min-height: 100%;
}

.tab-loader {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 30px 0;
  gap: 8px;
  color: #94a3b8;
  font-size: 12px;
}

.loader-sm {
  border: 2px solid #f3f3f3;
  border-top: 2px solid #cbd5e1;
  border-radius: 50%;
  width: 16px;
  height: 16px;
  animation: spin 0.8s linear infinite;
}

.empty-tab {
  text-align: center;
  color: #94a3b8;
  font-size: 12px;
  padding: 30px 0;
}

/* 关系连接列表 */
.relation-ul {
  list-style: none;
}

.relation-li {
  padding: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  margin-bottom: 10px;
  background: #ffffff;
}

.relation-path {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.node-link {
  color: #3b82f6;
  cursor: pointer;
  font-weight: 500;
}

.node-link:hover {
  text-decoration: underline;
}

.node-link.current {
  color: #64748b;
  cursor: default;
  font-weight: normal;
}

.node-link.current:hover {
  text-decoration: none;
}

.rel-arrow {
  color: #94a3b8;
}

.relation-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-top: 1px dashed #f1f5f9;
  padding-top: 6px;
  margin-top: 6px;
}

.rel-meta {
  font-size: 10px;
  color: #94a3b8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 220px;
}

.action-text-btn, .delete-text-btn {
  background: none;
  border: none;
  font-size: 11px;
  color: #3b82f6;
  cursor: pointer;
}

.action-text-btn:hover {
  text-decoration: underline;
}

.delete-text-btn {
  color: #ef4444;
}

.delete-text-btn:hover {
  text-decoration: underline;
}

/* 证据片段卡片 */
.chunks-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chunk-card {
  padding: 14px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #ffffff;
}

.chunk-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 8px;
}

.chunk-title-sec h4 {
  font-size: 13px;
  color: #1e293b;
  font-weight: 600;
  word-break: break-all;
}

.chunk-path {
  font-size: 10px;
  color: #64748b;
  margin-top: 2px;
}

.chunk-type {
  font-size: 9px;
  color: #4f46e5;
  background: #eeebff;
  padding: 2px 6px;
  border-radius: 4px;
}

.chunk-preview-text {
  font-size: 11px;
  color: #475569;
  line-height: 1.5;
  background: #f8fafc;
  padding: 8px 10px;
  border-radius: 4px;
  margin-bottom: 8px;
  border-left: 2px solid #cbd5e1;
}

.chunk-markdown {
  font-size: 12px;
  border-top: 1px dashed #e2e8f0;
  padding-top: 12px;
  margin-top: 12px;
  max-height: 350px;
  overflow-y: auto;
}

.chunk-card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-top: 1px solid #f1f5f9;
  padding-top: 8px;
  margin-top: 8px;
}

/* 面板底部按钮 */
.panel-footer {
  padding: 16px 24px;
  border-top: 1px solid #f1f5f9;
  flex-shrink: 0;
  background: #ffffff;
}

.danger-btn {
  width: 100%;
  padding: 10px 0;
  background: #fef2f2;
  color: #ef4444;
  border: 1px solid #fca5a5;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.1s;
}

.danger-btn:hover {
  background: #fee2e2;
}

/* 确认模态框样式 */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.modal-card {
  width: 440px;
  background: #ffffff;
  border-radius: 12px;
  box-shadow: 0 20px 25px -5px rgba(0,0,0,0.1), 0 10px 10px -5px rgba(0,0,0,0.04);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.modal-header {
  padding: 16px 20px;
  background: #fef2f2;
  border-bottom: 1px solid #fee2e2;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.modal-header h3 {
  font-size: 15px;
  color: #dc2626;
  font-weight: 600;
}

.modal-body {
  padding: 20px;
}

.form-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-label {
  font-size: 12px;
  color: #475569;
  font-weight: 600;
}

.warning-text {
  font-size: 13px;
  color: #dc2626;
  line-height: 1.5;
  margin-bottom: 14px;
}

.prompt-text {
  font-size: 12px;
  color: #475569;
  margin-bottom: 8px;
}

.modal-input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #fca5a5;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
}

.modal-input:focus {
  border-color: #ef4444;
  box-shadow: 0 0 0 1px rgba(239, 68, 68, 0.1);
}

.modal-textarea {
  width: 100%;
  min-height: 90px;
  resize: vertical;
  padding: 8px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
}

.modal-footer {
  padding: 12px 20px;
  background: #f8fafc;
  border-top: 1px solid #e2e8f0;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.secondary-btn {
  padding: 8px 16px;
  background: white;
  border: 1px solid #cbd5e1;
  color: #475569;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}

.secondary-btn:hover {
  background: #f8fafc;
}

.danger-confirm-btn {
  padding: 8px 16px;
  background: #ef4444;
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.danger-confirm-btn:hover:not(:disabled) {
  background: #dc2626;
}

.danger-confirm-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 适配移动端 */
@media (max-width: 1024px) {
  .kg-container {
    flex-direction: column;
  }
  .kg-left-panel {
    width: 100%;
    height: 250px;
    border-right: none;
    border-bottom: 1px solid #e2e8f0;
  }
  .kg-right-panel {
    position: fixed;
    top: 50px;
    right: 0;
    bottom: 0;
    width: 100%;
    max-width: 380px;
  }
}
</style>
