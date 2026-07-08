import {
  forceCenter,
  forceCollide,
  forceX,
  forceY,
  forceLink,
  forceManyBody,
  forceSimulation,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force'

export interface LayoutNode extends SimulationNodeDatum {
  id: string
  type?: string
  x: number
  y: number
  vx: number
  vy: number
  fx?: number | null
  fy?: number | null
}

export interface LayoutEdge {
  source: string
  target: string
}

interface InternalLink extends SimulationLinkDatum<LayoutNode> {
  source: string | LayoutNode
  target: string | LayoutNode
}

export type GraphChangeMode = 'initial' | 'incremental'

export interface GraphLayoutOptions {
  width: number
  height: number
  autoStart?: boolean
  onTick?: () => void
}

export interface GraphLayoutController {
  setGraph(nodes: LayoutNode[], edges: LayoutEdge[], changeMode: GraphChangeMode): void
  beginNodeDrag(nodeId: string): void
  moveNode(nodeId: string, x: number, y: number): void
  endNodeDrag(nodeId: string): void
  restartLayout(): void
  tick(iterations?: number): void
  getAlpha(): number
  getAlphaMin(): number
  destroy(): void
}

const hashAngle = (value: string) => {
  let hash = 0
  for (let i = 0; i < value.length; i += 1) hash = (hash * 31 + value.charCodeAt(i)) | 0
  return ((Math.abs(hash) % 360) / 180) * Math.PI
}

const isFinitePosition = (node: LayoutNode) => Number.isFinite(node.x) && Number.isFinite(node.y)

export const createGraphLayout = (options: GraphLayoutOptions): GraphLayoutController => {
  const idleAlpha = 0.004
  const autoStart = options.autoStart !== false
  const seenNodeIds = new Set<string>()
  const activeNodeIds = new Set<string>()
  const anchorX = new Map<string, number>()
  const anchorY = new Map<string, number>()
  let nodes: LayoutNode[] = []
  let edges: LayoutEdge[] = []
  let activeEdgeSignature = ''
  let destroyed = false

  const linkForce = forceLink<LayoutNode, InternalLink>()
    .id(node => node.id)
    .distance(135)
    .strength(0.12)

  const simulation: Simulation<LayoutNode, InternalLink> = forceSimulation<LayoutNode>()
    .alphaMin(0.0001)
    .alphaDecay(0.022)
    .alphaTarget(idleAlpha)
    .velocityDecay(0.5)
    .force('charge', forceManyBody<LayoutNode>().strength(-260).distanceMax(420))
    .force('link', linkForce)
    .force('collision', forceCollide<LayoutNode>(30).strength(0.9).iterations(2))
    .force('center', forceCenter(options.width / 2, options.height / 2).strength(0.025))
    .force('anchor-x', forceX<LayoutNode>(node => anchorX.get(node.id) ?? node.x).strength(node => anchorX.has(node.id) ? 0.025 : 0))
    .force('anchor-y', forceY<LayoutNode>(node => anchorY.get(node.id) ?? node.y).strength(node => anchorY.has(node.id) ? 0.025 : 0))
    .on('tick', () => options.onTick?.())

  simulation.stop()

  const neighborsOf = (ids: Set<string>) => {
    const result = new Set(ids)
    for (const edge of edges) {
      if (ids.has(edge.source)) result.add(edge.target)
      if (ids.has(edge.target)) result.add(edge.source)
    }
    return result
  }

  const seedNode = (node: LayoutNode, index: number) => {
    const relatedIds = edges.flatMap(edge => {
      if (edge.source === node.id) return [edge.target]
      if (edge.target === node.id) return [edge.source]
      return []
    })
    const related = relatedIds
      .map(id => nodes.find(candidate => candidate.id === id))
      .find(candidate => candidate && isFinitePosition(candidate))
    const angle = hashAngle(node.id)

    if (related) {
      node.x = related.x + Math.cos(angle) * 90
      node.y = related.y + Math.sin(angle) * 90
    } else {
      const radius = Math.max(180, Math.min(options.width, options.height) * 0.42)
      const fallbackAngle = angle + (index / Math.max(nodes.length, 1)) * Math.PI * 2
      node.x = options.width / 2 + Math.cos(fallbackAngle) * radius
      node.y = options.height / 2 + Math.sin(fallbackAngle) * radius
    }
    node.vx = 0
    node.vy = 0
  }

  const start = (alpha: number) => {
    simulation.alphaTarget(idleAlpha)
    simulation.alpha(alpha)
    if (autoStart) simulation.restart()
  }

  const edgeSignature = (items: LayoutEdge[]) => items
    .map(edge => `${edge.source}\u0000${edge.target}`)
    .sort()
    .join('\u0001')

  const setGraph = (nextNodes: LayoutNode[], nextEdges: LayoutEdge[], changeMode: GraphChangeMode) => {
    if (destroyed) return
    if (changeMode === 'initial') {
      seenNodeIds.clear()
      activeNodeIds.clear()
      activeEdgeSignature = ''
    }
    const nextNodeIds = new Set(nextNodes.map(node => node.id))
    const addedIds = new Set([...nextNodeIds].filter(id => !activeNodeIds.has(id)))
    const removedIds = new Set([...activeNodeIds].filter(id => !nextNodeIds.has(id)))
    const firstSeenIds = new Set([...nextNodeIds].filter(id => !seenNodeIds.has(id)))
    const nextEdgeSignature = edgeSignature(nextEdges)
    const relationshipsChanged = changeMode === 'incremental' && nextEdgeSignature !== activeEdgeSignature
    const relationshipsOnlyChanged = relationshipsChanged && addedIds.size === 0 && removedIds.size === 0
    nodes = nextNodes
    edges = nextEdges

    const bulkExpansionThreshold = Math.max(12, Math.ceil(nodes.length * 0.2))
    const isBulkExpansion = changeMode === 'incremental' && addedIds.size >= bulkExpansionThreshold
    const bulkRemovalThreshold = Math.max(12, Math.ceil(Math.max(activeNodeIds.size, 1) * 0.2))
    const isBulkRemoval = removedIds.size >= bulkRemovalThreshold
    const shouldReleaseWholeGraph = isBulkExpansion || removedIds.size > 0 || relationshipsOnlyChanged

    nodes.forEach((node, index) => {
      if (!isFinitePosition(node) || (changeMode === 'incremental' && firstSeenIds.has(node.id))) {
        seedNode(node, index)
      }
      node.vx = Number.isFinite(node.vx) ? node.vx : 0
      node.vy = Number.isFinite(node.vy) ? node.vy : 0
    })

    simulation.nodes(nodes)
    linkForce.links(edges.map(edge => ({ source: edge.source, target: edge.target })))

    if (changeMode === 'incremental') {
      const activeIds = neighborsOf(addedIds)
      nodes.forEach(node => {
        if (shouldReleaseWholeGraph) {
          if (!addedIds.has(node.id)) {
            anchorX.set(node.id, node.x)
            anchorY.set(node.id, node.y)
          }
          node.fx = null
          node.fy = null
        } else if (activeIds.has(node.id)) {
          node.fx = null
          node.fy = null
        } else {
          node.fx = node.x
          node.fy = node.y
        }
      })
      start(
        isBulkExpansion || isBulkRemoval
          ? 0.85
          : (removedIds.size > 0 || relationshipsOnlyChanged ? 0.32 : (addedIds.size > 0 ? 0.38 : 0.12)),
      )
    } else {
      anchorX.clear()
      anchorY.clear()
      nodes.forEach(node => {
        node.fx = null
        node.fy = null
      })
      start(0.8)
    }

    nodes.forEach(node => seenNodeIds.add(node.id))
    activeNodeIds.clear()
    nodes.forEach(node => activeNodeIds.add(node.id))
    activeEdgeSignature = nextEdgeSignature
    options.onTick?.()
  }

  const beginNodeDrag = (nodeId: string) => {
    const activeIds = neighborsOf(new Set([nodeId]))
    nodes.forEach(node => {
      if (node.id === nodeId) {
        node.fx = node.x
        node.fy = node.y
      } else if (activeIds.has(node.id)) {
        node.fx = null
        node.fy = null
      } else {
        node.fx = node.x
        node.fy = node.y
      }
    })
    start(0.22)
  }

  const moveNode = (nodeId: string, x: number, y: number) => {
    const node = nodes.find(candidate => candidate.id === nodeId)
    if (!node) return
    node.fx = x
    node.fy = y
    node.x = x
    node.y = y
    options.onTick?.()
  }

  const endNodeDrag = (nodeId: string) => {
    const node = nodes.find(candidate => candidate.id === nodeId)
    if (!node) return
    node.fx = null
    node.fy = null
    start(0.14)
  }

  const restartLayout = () => {
    anchorX.clear()
    anchorY.clear()
    nodes.forEach(node => {
      node.fx = null
      node.fy = null
      node.vx = 0
      node.vy = 0
    })
    start(0.9)
  }

  return {
    setGraph,
    beginNodeDrag,
    moveNode,
    endNodeDrag,
    restartLayout,
    tick(iterations = 1) {
      simulation.tick(iterations)
      options.onTick?.()
    },
    getAlpha: () => simulation.alpha(),
    getAlphaMin: () => simulation.alphaMin(),
    destroy() {
      destroyed = true
      simulation.stop()
      simulation.on('tick', null)
      nodes = []
      edges = []
    },
  }
}
