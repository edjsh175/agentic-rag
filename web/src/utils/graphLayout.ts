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
  /** Ideal edge length for forceLink (default 220). */
  linkDistance?: number
  /** Many-body repulsion strength, negative = push apart (default -420). */
  chargeStrength?: number
  /** Collision radius around nodes (default 40). */
  collideRadius?: number
}

export interface GraphLayoutController {
  setGraph(nodes: LayoutNode[], edges: LayoutEdge[], changeMode: GraphChangeMode): void
  beginNodeDrag(nodeId: string): void
  moveNode(nodeId: string, x: number, y: number): void
  endNodeDrag(nodeId: string): void
  restartLayout(): void
  setPhysicsEnabled(enabled: boolean): void
  isPhysicsEnabled(): boolean
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
  const linkDistance = options.linkDistance ?? 180
  const chargeStrength = options.chargeStrength ?? -550
  const collideRadius = options.collideRadius ?? 42
  const seenNodeIds = new Set<string>()
  const activeNodeIds = new Set<string>()
  const anchorX = new Map<string, number>()
  const anchorY = new Map<string, number>()
  let nodes: LayoutNode[] = []
  let edges: LayoutEdge[] = []
  let activeEdgeSignature = ''
  let destroyed = false
  let physicsEnabled = true

  const linkForce = forceLink<LayoutNode, InternalLink>()
    .id(node => node.id)
    .distance(linkDistance)
    .strength(0.28)

  const simulation: Simulation<LayoutNode, InternalLink> = forceSimulation<LayoutNode>()
    .alphaMin(0.0001)
    .alphaDecay(0.022)
    .alphaTarget(idleAlpha)
    .velocityDecay(0.40)
    .force('charge', forceManyBody<LayoutNode>().strength(chargeStrength).distanceMax(2200))
    .force('link', linkForce)
    .force('collision', forceCollide<LayoutNode>(collideRadius).strength(0.95).iterations(3))
    .force('center', forceCenter(options.width / 2, options.height / 2).strength(0.012))
    .force('anchor-x', forceX<LayoutNode>(node => anchorX.get(node.id) ?? node.x).strength(node => anchorX.has(node.id) ? 0.02 : 0))
    .force('anchor-y', forceY<LayoutNode>(node => anchorY.get(node.id) ?? node.y).strength(node => anchorY.has(node.id) ? 0.02 : 0))
    .on('tick', () => {
      // 速度截断 (Velocity Clamping)，防止突变力导致节点暴冲飞出画布
      const maxSpeed = 25
      for (const node of nodes) {
        if (Number.isFinite(node.vx) && Number.isFinite(node.vy)) {
          const speed = Math.hypot(node.vx, node.vy)
          if (speed > maxSpeed) {
            const scale = maxSpeed / speed
            node.vx *= scale
            node.vy *= scale
          }
        }
      }
      options.onTick?.()
    })

  simulation.stop()

  const neighborsOf = (ids: Set<string>) => {
    const result = new Set(ids)
    for (const edge of edges) {
      if (ids.has(edge.source)) result.add(edge.target)
      if (ids.has(edge.target)) result.add(edge.source)
    }
    return result
  }

  // 基于连通分量与拓扑度的四向均匀播种 (O(V+E) 高性能算法)
  const seedNodesTopology = (targetNodes: LayoutNode[]) => {
    if (targetNodes.length === 0) return
    const nodeMap = new Map<string, LayoutNode>()
    const adj = new Map<string, Set<string>>()
    targetNodes.forEach(n => {
      nodeMap.set(n.id, n)
      adj.set(n.id, new Set())
    })
    edges.forEach(e => {
      if (adj.has(e.source) && adj.has(e.target)) {
        adj.get(e.source)!.add(e.target)
        adj.get(e.target)!.add(e.source)
      }
    })

    const visited = new Set<string>()
    const components: LayoutNode[][] = []

    targetNodes.forEach(node => {
      if (visited.has(node.id)) return
      const comp: LayoutNode[] = []
      const queue = [node.id]
      visited.add(node.id)
      while (queue.length > 0) {
        const currId = queue.shift()!
        const currNode = nodeMap.get(currId)
        if (currNode) comp.push(currNode)
        adj.get(currId)?.forEach(neighborId => {
          if (!visited.has(neighborId)) {
            visited.add(neighborId)
            queue.push(neighborId)
          }
        })
      }
      components.push(comp)
    })

    // 按组件大小降序排列，主连通分量居中，小组件均匀环绕四周
    components.sort((a, b) => b.length - a.length)
    const cx = options.width / 2
    const cy = options.height / 2
    const numComp = components.length

    components.forEach((comp, compIdx) => {
      let compCx = cx
      let compCy = cy
      if (compIdx > 0) {
        const compAngle = (compIdx / Math.max(numComp - 1, 1)) * Math.PI * 2
        const compDist = Math.max(260, Math.min(options.width, options.height) * 0.32)
        compCx = cx + Math.cos(compAngle) * compDist
        compCy = cy + Math.sin(compAngle) * compDist
      }

      // 组件内部按度数降序，高度数中心节点靠近分量中央
      comp.sort((a, b) => (adj.get(b.id)?.size || 0) - (adj.get(a.id)?.size || 0))
      const count = comp.length
      comp.forEach((node, nodeIdx) => {
        if (nodeIdx === 0 && count > 1) {
          node.x = compCx
          node.y = compCy
        } else {
          const angle = (nodeIdx / Math.max(count, 1)) * Math.PI * 2 + hashAngle(node.id) * 0.5
          const radius = count === 1 ? 0 : 70 + (nodeIdx / count) * linkDistance * 1.4
          node.x = compCx + Math.cos(angle) * radius
          node.y = compCy + Math.sin(angle) * radius
        }
        node.vx = 0
        node.vy = 0
      })
    })
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
    const seedGap = Math.max(140, linkDistance * 0.7)

    if (related) {
      node.x = related.x + Math.cos(angle) * seedGap
      node.y = related.y + Math.sin(angle) * seedGap
    } else {
      const radius = Math.max(linkDistance * 1.4, Math.min(options.width, options.height) * 0.42)
      const fallbackAngle = angle + (index / Math.max(nodes.length, 1)) * Math.PI * 2
      node.x = options.width / 2 + Math.cos(fallbackAngle) * radius
      node.y = options.height / 2 + Math.sin(fallbackAngle) * radius
    }
    node.vx = 0
    node.vy = 0
  }

  const pinAllNodes = () => {
    nodes.forEach(node => {
      node.fx = node.x
      node.fy = node.y
      node.vx = 0
      node.vy = 0
    })
  }

  const freezeSimulation = () => {
    pinAllNodes()
    simulation.alphaTarget(0)
    simulation.alpha(0)
    simulation.stop()
    options.onTick?.()
  }

  const start = (alpha: number) => {
    if (!physicsEnabled) {
      freezeSimulation()
      return
    }
    simulation.alphaTarget(idleAlpha)
    simulation.alpha(alpha)
    if (autoStart) simulation.restart()
  }

  const setPhysicsEnabled = (enabled: boolean) => {
    if (destroyed) return
    physicsEnabled = enabled
    if (!enabled) {
      freezeSimulation()
      return
    }
    nodes.forEach(node => {
      node.fx = null
      node.fy = null
    })
    start(0.35)
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

    if (changeMode === 'initial') {
      seedNodesTopology(nodes)
    } else {
      nodes.forEach((node, index) => {
        if (!isFinitePosition(node)) {
          seedNode(node, index)
        }
        node.vx = 0
        node.vy = 0
      })
    }

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
      start(0.6)
    }

    nodes.forEach(node => seenNodeIds.add(node.id))
    activeNodeIds.clear()
    nodes.forEach(node => activeNodeIds.add(node.id))
    activeEdgeSignature = nextEdgeSignature
    options.onTick?.()
  }

  const beginNodeDrag = (nodeId: string) => {
    if (!physicsEnabled) {
      const node = nodes.find(candidate => candidate.id === nodeId)
      if (!node) return
      node.fx = node.x
      node.fy = node.y
      return
    }
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
    if (!physicsEnabled) {
      node.fx = node.x
      node.fy = node.y
      node.vx = 0
      node.vy = 0
      options.onTick?.()
      return
    }
    // 关键修正：拖拽结束时解除全图非显式固定的临时锁定坐标，防止连线或碰撞产生突变反弹推飞
    nodes.forEach(n => {
      n.fx = null
      n.fy = null
    })
    start(0.14)
  }

  const restartLayout = () => {
    if (!physicsEnabled) return
    anchorX.clear()
    anchorY.clear()
    nodes.forEach(node => {
      node.fx = null
      node.fy = null
      node.vx = 0
      node.vy = 0
    })
    seedNodesTopology(nodes)
    simulation.nodes(nodes)
    start(0.65)
  }

  return {
    setGraph,
    beginNodeDrag,
    moveNode,
    endNodeDrag,
    restartLayout,
    setPhysicsEnabled,
    isPhysicsEnabled: () => physicsEnabled,
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
