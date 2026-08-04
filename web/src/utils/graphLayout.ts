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
  type?: string
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

  // ── Dagre-specific options ──────────────────────────────────────────────
  /**
   * Edge relation types that drive layer (rank) assignment in Dagre.
   * Only edges whose label/type matches one of these strings are added to
   * the Dagre graph; all other edges are drawn as visual-only lines.
   * Default: ['belongs_to', 'defined_in']
   */
  hierarchyEdgeTypes?: string[]

  /**
   * Explicit root node ID. When provided, this node is unconditionally
   * placed at depth 0. Takes priority over rootNodeMatcher.
   */
  rootNodeId?: string

  /**
   * Predicate to identify the root node when rootNodeId is not given.
   * Receives each LayoutNode and should return true for the desired root.
   * Keeps business-specific logic out of the generic layout layer.
   */
  rootNodeMatcher?: (node: LayoutNode) => boolean

  /**
   * Override the horizontal gap between same-rank nodes fed to Dagre.
   * When omitted, a value is derived from the node count.
   */
  dagreNodeSep?: number

  /**
   * Override the vertical gap between ranks fed to Dagre.
   * When omitted, a value is derived from the node count.
   */
  dagreRankSep?: number
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
    tick: (iterations) => { if (physicsEnabled) simulation.tick(iterations) },
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

import dagre from '@dagrejs/dagre'

export const createDagreLayout = (options: GraphLayoutOptions): GraphLayoutController => {
  let nodes: LayoutNode[] = []
  let edges: LayoutEdge[] = []

  /** Edge types that drive Dagre rank assignment (configurable, stable default). */
  const HIERARCHY_EDGE_TYPES = options.hierarchyEdgeTypes ?? ['belongs_to', 'defined_in']

  const relationTypeOf = (edge: LayoutEdge) =>
    ((edge as { label?: string; type?: string }).label
      ?? (edge as { label?: string; type?: string }).type
      ?? '')

  const nodeLabel = (node: LayoutNode | undefined) =>
    String((node as { label?: string } | undefined)?.label || '')

  const nodeDocCategory = (node: LayoutNode | undefined) =>
    String((node as { doc_category?: string } | undefined)?.doc_category || '')

  const nodeEntityType = (node: LayoutNode | undefined) =>
    String((node as { type?: string } | undefined)?.type || '')

  /** Generic root selection: priority chain that contains zero business keywords. */
  const pickRootId = (nodeById: Map<string, LayoutNode>, parentsByChild: Map<string, string[]>): string | null => {
    // 1. Caller supplied an explicit ID
    if (options.rootNodeId && nodeById.has(options.rootNodeId)) return options.rootNodeId

    // 2. Caller supplied a matcher predicate
    if (options.rootNodeMatcher) {
      const found = [...nodeById.values()].find(options.rootNodeMatcher)
      if (found) return found.id
    }

    // 3. Among parentless Product nodes, pick the one with the most children
    const childCountOf = (id: string) => {
      let count = 0
      parentsByChild.forEach((parents) => { if (parents.includes(id)) count++ })
      return count
    }
    const parentlessProducts = [...nodeById.values()].filter(
      n => n.type === 'Product' && !(parentsByChild.get(n.id)?.length),
    )
    if (parentlessProducts.length > 0) {
      parentlessProducts.sort((a, b) => childCountOf(b.id) - childCountOf(a.id))
      return parentlessProducts[0].id
    }

    // 4. All products – highest in-degree (most children point to it)
    const allProducts = [...nodeById.values()].filter(n => n.type === 'Product')
    if (allProducts.length > 0) {
      allProducts.sort((a, b) => childCountOf(b.id) - childCountOf(a.id))
      return allProducts[0].id
    }

    // 5. Any node that is a hierarchy target (others point to it via hierarchy edges)
    const hierarchyTargets = new Set<string>()
    parentsByChild.forEach(parents => parents.forEach(p => hierarchyTargets.add(p)))
    const isChild = new Set([...parentsByChild.keys()])
    const roots = [...hierarchyTargets].filter(id => !isChild.has(id))
    if (roots.length > 0) {
      roots.sort((a, b) => childCountOf(b) - childCountOf(a))
      return roots[0]
    }

    return null
  }

  return {
    setGraph(newNodes: LayoutNode[], newEdges: LayoutEdge[], _changeMode: GraphChangeMode) {
      nodes = newNodes
      edges = newEdges

      const nodeCount = nodes.length

      // ── Adaptive spacing (overridden by caller options) ─────────────────
      // nodesep / ranksep fed into Dagre (controls Module/Layer/Service nodes)
      const nodesep = options.dagreNodeSep
        ?? Math.round(Math.min(100, Math.max(40, 40 + Math.sqrt(nodeCount) * 3)))
      const ranksep = options.dagreRankSep
        ?? Math.round(Math.min(180, Math.max(80, 80 + Math.sqrt(nodeCount) * 5)))

      // productGap / rankGap used when we manually place Product-band rows
      const productGap = Math.round(Math.min(320, Math.max(140, 140 + Math.sqrt(nodeCount) * 6)))
      const rankGap    = Math.round(Math.min(280, Math.max(120, 120 + Math.sqrt(nodeCount) * 4)))

      const g = new dagre.graphlib.Graph()
      g.setGraph({ rankdir: 'TB', nodesep, edgesep: 20, ranksep })
      g.setDefaultEdgeLabel(() => ({}))

      nodes.forEach(node => {
        g.setNode(node.id, { width: 80, height: 80, originalNode: node })
      })

      // ── Build belongs-to maps using the configurable hierarchy edge types ─
      const hierarchyEdges = edges.filter(e => HIERARCHY_EDGE_TYPES.includes(relationTypeOf(e)))
      const nodeById = new Map(nodes.map(n => [n.id, n]))
      const parentsByChild = new Map<string, string[]>()
      const childrenByParent = new Map<string, string[]>()

      hierarchyEdges.forEach(edge => {
        const childId  = edge.source   // "X belongs_to Y"  →  X is child, Y is parent
        const parentId = edge.target
        if (!nodeById.has(childId) || !nodeById.has(parentId)) return
        const parents = parentsByChild.get(childId) ?? []
        parents.push(parentId)
        parentsByChild.set(childId, parents)
        const children = childrenByParent.get(parentId) ?? []
        children.push(childId)
        childrenByParent.set(parentId, children)
      })

      // ── Root selection: generic priority chain (no business keywords) ────
      const rootId = pickRootId(nodeById, parentsByChild)

      // ── BFS to assign depth and primary parent (single-parent tree) ──────
      const depth = new Map<string, number>()
      const primaryParent = new Map<string, string>()
      const queue: string[] = []

      if (rootId) {
        depth.set(rootId, 0)
        queue.push(rootId)
      }

      while (queue.length > 0) {
        const parentId   = queue.shift()!
        const parentDepth = depth.get(parentId) ?? 0
        const children   = [...(childrenByParent.get(parentId) ?? [])].sort()

        for (const childId of children) {
          const nextDepth    = parentDepth + 1
          const prevDepth    = depth.get(childId)
          const currentParentId = primaryParent.get(childId)

          // Prefer a Product parent over a non-Product parent at the same depth
          const preferProductParent =
            prevDepth === nextDepth &&
            nodeById.get(parentId)?.type === 'Product' &&
            nodeById.get(currentParentId ?? '')?.type !== 'Product'

          if (prevDepth === undefined || nextDepth < prevDepth || preferProductParent) {
            depth.set(childId, nextDepth)
            primaryParent.set(childId, parentId)
            if (prevDepth === undefined || nextDepth < prevDepth) queue.push(childId)
          }
        }
      }

      // ── Feed single-parent hierarchy edges into Dagre ────────────────────
      let addedHierarchyEdge = false
      primaryParent.forEach((parentId, childId) => {
        g.setEdge(parentId, childId)
        addedHierarchyEdge = true
      })

      // Fallback: no root reachable – fall back to raw hierarchy edges
      if (!addedHierarchyEdge) {
        hierarchyEdges.forEach(edge => {
          // belongs_to is bottom-up: source belongs_to target → parent=target
          g.setEdge(edge.target, edge.source)
        })
      }

      // Non-hierarchy edges are visual-only; do NOT add to Dagre

      try {
        dagre.layout(g)
      } catch (e) {
        console.warn('Dagre layout error:', e)
      }

      const gNodeById = new Map<string, any>()
      g.nodes().forEach(v => gNodeById.set(v, g.node(v)))

      const labelOf = (id: string) => nodeLabel(nodeById.get(id)) || id
      // Shorter, lighter wave improves edge readability without destroying layers.
      const waveAmplitude = Math.max(4, Math.min(18, rankGap * 0.12))
      const waveCycles = 2
      const waveOffsetY = (index: number, total: number) => {
        if (total <= 1) return 0
        const phase = (index / Math.max(total - 1, 1)) * Math.PI * 2 * waveCycles
        return Math.sin(phase) * waveAmplitude
      }

      // ── Compact Product nodes by branch (avoid forced pull to global center) ─
      if (rootId && primaryParent.size > 0) {
        const rootGNode = gNodeById.get(rootId)
        if (rootGNode) {
          const productsByDepth = new Map<number, Map<string, string[]>>()
          depth.forEach((d, id) => {
            if (d <= 0 || nodeById.get(id)?.type !== 'Product') return
            const parentId = primaryParent.get(id) ?? rootId
            const byParent = productsByDepth.get(d) ?? new Map<string, string[]>()
            const list = byParent.get(parentId) ?? []
            list.push(id)
            byParent.set(parentId, list)
            productsByDepth.set(d, byParent)
          })

          ;[...productsByDepth.keys()].sort((a, b) => a - b).forEach(d => {
            const byParent = productsByDepth.get(d)!
            const groupEntries = [...byParent.entries()].map(([parentId, ids]) => {
              const sortedIds = [...ids].sort((a, b) => labelOf(a).localeCompare(labelOf(b)))
              const parentGn = gNodeById.get(parentId)
              const centerX = Number.isFinite(parentGn?.x) ? parentGn.x : rootGNode.x
              return { parentId, ids: sortedIds, centerX }
            })

            // Keep sibling groups ordered by current parent x, then push apart to avoid overlap.
            groupEntries.sort((a, b) => {
              if (a.centerX !== b.centerX) return a.centerX - b.centerX
              return labelOf(a.parentId).localeCompare(labelOf(b.parentId))
            })
            const minGroupGap = productGap * 1.1
            for (let i = 1; i < groupEntries.length; i += 1) {
              if (groupEntries[i].centerX - groupEntries[i - 1].centerX < minGroupGap) {
                groupEntries[i].centerX = groupEntries[i - 1].centerX + minGroupGap
              }
            }

            // Mirror Product hierarchy above the root axis:
            // depth-1/2/3 products are placed upward around StampGIS root.
            const y = rootGNode.y - d * rankGap
            groupEntries.forEach(group => {
              // Do not cap the gap here; productGap is already clamped/adaptive.
              // Capping it reintroduces "too tight" product bands.
              const gap = productGap
              const startX = group.centerX - ((group.ids.length - 1) * gap) / 2
              group.ids.forEach((id, index) => {
                const gn = gNodeById.get(id)
                if (!gn) return
                gn.x = startX + index * gap
                gn.y = y + waveOffsetY(index, group.ids.length)
              })
            })
          })
        }
      }

      // Apply the same short-wave Y offset to ALL non-Product nodes in the main tree ranks.
      // This keeps "层" readability for different entity types, not only products.
      if (rootId) {
        const nodesByDepth = new Map<number, string[]>()
        depth.forEach((d, id) => {
          if (d <= 0) return
          if (nodeById.get(id)?.type === 'Product') return
          const list = nodesByDepth.get(d) ?? []
          list.push(id)
          nodesByDepth.set(d, list)
        })

        nodesByDepth.forEach(ids => {
          ids.sort((a, b) => (gNodeById.get(a)?.x ?? 0) - (gNodeById.get(b)?.x ?? 0))
          ids.forEach((id, index) => {
            const gn = gNodeById.get(id)
            if (!gn) return
            gn.y += waveOffsetY(index, ids.length)
          })
        })
      }

      // ── Orphan nodes: group by entity_type (row), sort by doc_category ───
      //
      // Nodes not reachable from the root are placed in bands below the main
      // tree, one horizontal band per entity_type, sorted by doc_category
      // within each band.  This keeps semantically similar orphans together
      // regardless of business-specific category names.
      const orphanIds = nodes.map(n => n.id).filter(id => id !== rootId && !depth.has(id))

      if (orphanIds.length > 0) {
        // Determine the lowest Y coordinate in the main tree
        let maxTreeY = Number.NEGATIVE_INFINITY
        depth.forEach((_, id) => {
          const gn = gNodeById.get(id)
          if (gn && Number.isFinite(gn.y)) maxTreeY = Math.max(maxTreeY, gn.y)
        })
        if (!Number.isFinite(maxTreeY)) {
          maxTreeY = (rootId ? gNodeById.get(rootId)?.y : null) ?? 0
        }

        const orphanSet = new Set(orphanIds)

        // Build orphan-internal belongs_to relationships (for subtree placement)
        const orphanChildren = new Map<string, string[]>()
        const orphanHasParent = new Set<string>()
        hierarchyEdges.forEach(edge => {
          if (!orphanSet.has(edge.source)) return
          // edge.source belongs_to edge.target → parent=target
          if (orphanSet.has(edge.target) || depth.has(edge.target) || edge.target === rootId) {
            const list = orphanChildren.get(edge.target) ?? []
            list.push(edge.source)
            orphanChildren.set(edge.target, list)
            orphanHasParent.add(edge.source)
          }
        })

        // Top-level orphans: no parent inside orphan set or main tree
        const orphanRoots = orphanIds
          .filter(id => !orphanHasParent.has(id))
          .sort((a, b) => {
            // Primary sort: entity_type; secondary: doc_category; tertiary: label
            const ta = nodeEntityType(nodeById.get(a))
            const tb = nodeEntityType(nodeById.get(b))
            if (ta !== tb) return ta.localeCompare(tb)
            const ca = nodeDocCategory(nodeById.get(a))
            const cb = nodeDocCategory(nodeById.get(b))
            if (ca !== cb) return ca.localeCompare(cb)
            return labelOf(a).localeCompare(labelOf(b))
          })

        // Group top-level orphan roots by entity_type → each type gets its own Y row
        const typeOrder = [...new Set(orphanRoots.map(id => nodeEntityType(nodeById.get(id))))]
        const centerX = gNodeById.get(rootId ?? '')?.x ?? options.width / 2
        let orphanBandY = maxTreeY + rankGap * 1.8

        typeOrder.forEach(entityType => {
          const group = orphanRoots.filter(id => nodeEntityType(nodeById.get(id)) === entityType)
          // Within group: already sorted by doc_category then label (from above)
          const startX = centerX - ((group.length - 1) * productGap) / 2
          group.forEach((id, i) => {
            const gn = gNodeById.get(id)
            if (!gn) return
            gn.x = startX + i * productGap
            gn.y = orphanBandY + waveOffsetY(i, group.length)
          })
          orphanBandY += rankGap * 1.4   // next entity_type band goes lower
        })

        // Recursively place orphan subtrees under their orphan root
        const placeOrphanSubtree = (parentId: string, parentY: number) => {
          const parentGn  = gNodeById.get(parentId)
          const children  = [...(orphanChildren.get(parentId) ?? [])]
            .sort((a, b) => {
              const ca = nodeDocCategory(nodeById.get(a))
              const cb = nodeDocCategory(nodeById.get(b))
              if (ca !== cb) return ca.localeCompare(cb)
              return labelOf(a).localeCompare(labelOf(b))
            })
          if (!parentGn || children.length === 0) return
          const childStart = parentGn.x - ((children.length - 1) * productGap) / 2
          const childY = parentY + rankGap
          children.forEach((childId, i) => {
            const cn = gNodeById.get(childId)
            if (!cn) return
            cn.x = childStart + i * productGap
            cn.y = childY + waveOffsetY(i, children.length)
            placeOrphanSubtree(childId, childY)
          })
        }
        orphanRoots.forEach(id => {
          const gn = gNodeById.get(id)
          placeOrphanSubtree(id, gn?.y ?? maxTreeY)
        })

        // Orphans whose primary parent IS inside the main tree: tuck under that parent
        orphanIds.forEach(id => {
          if (orphanRoots.includes(id)) return
          const parents    = parentsByChild.get(id) ?? []
          const treeParent = parents.find(p => depth.has(p) || p === rootId)
          if (!treeParent) return
          const parentGn = gNodeById.get(treeParent)
          const childGn  = gNodeById.get(id)
          if (!parentGn || !childGn) return
          if (orphanHasParent.has(id) && orphanSet.has(primaryParent.get(id) ?? '')) return
          childGn.x = parentGn.x
          childGn.y = parentGn.y + rankGap
        })
      }

      // ── Translate everything so root sits at (cx, targetRootY) ───────────
      let anchorGNode: { x: number; y: number } | null = rootId ? (gNodeById.get(rootId) ?? null) : null

      if (!anchorGNode) {
        // No explicit root: use the topmost node
        let minY = Infinity
        g.nodes().forEach(v => {
          const n = g.node(v)
          if (n && Number.isFinite(n.y) && n.y < minY) { minY = n.y; anchorGNode = n }
        })
      }

      const cx        = options.width / 2
      const targetRootY = 120
      let offsetX = 0
      let offsetY = 0

      if (anchorGNode) {
        offsetX = cx - anchorGNode.x
        offsetY = targetRootY - anchorGNode.y
      } else {
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
        g.nodes().forEach(v => {
          const n = g.node(v)
          if (n && Number.isFinite(n.x) && Number.isFinite(n.y)) {
            minX = Math.min(minX, n.x); minY = Math.min(minY, n.y)
            maxX = Math.max(maxX, n.x); maxY = Math.max(maxY, n.y)
          }
        })
        offsetX = cx - ((minX + maxX) / 2 || 0)
        offsetY = 100 - (minY || 0)
      }

      g.nodes().forEach(v => {
        const n = g.node(v)
        if (n?.originalNode) {
          n.originalNode.x  = n.x + offsetX
          n.originalNode.y  = n.y + offsetY
          n.originalNode.vx = 0
          n.originalNode.vy = 0
        }
      })

      options.onTick?.()
    },

    beginNodeDrag(_nodeId: string) {},
    moveNode(nodeId: string, x: number, y: number) {
      const node = nodes.find(n => n.id === nodeId)
      if (node) { node.x = x; node.y = y; options.onTick?.() }
    },
    endNodeDrag(_nodeId: string) {},
    restartLayout() { this.setGraph(nodes, edges, 'initial') },
    setPhysicsEnabled(_enabled: boolean) {},
    isPhysicsEnabled() { return false },
    tick(_iterations?: number) {},
    getAlpha()    { return 0 },
    getAlphaMin() { return 0 },
    destroy()     {},
  }
}
