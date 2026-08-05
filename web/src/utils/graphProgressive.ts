/** 图谱画布渐进式展开：从种子出发，按已展开节点合并一跳邻居。 */

export interface ProgressiveEdge {
  source: string
  target: string
}

export interface ProgressiveRevealInput {
  /** 当前筛选候选（通过类型/分类/搜索等） */
  candidateIds: ReadonlySet<string>
  /** 自动种子（如 Product / Document / 搜索直命中） */
  seedIds: ReadonlySet<string>
  /** 用户从列表点选加入的种子 */
  manualSeedIds: ReadonlySet<string>
  /** 已执行「展开邻居」的节点 */
  expandedIds: ReadonlySet<string>
  /** 治理编辑保活节点 */
  pinnedIds?: ReadonlySet<string>
  edges: readonly ProgressiveEdge[]
}

/** 从全量边中取某实体的一跳邻居（不限候选）。 */
export function collectNeighborIds(
  entityId: string,
  edges: readonly ProgressiveEdge[],
): Set<string> {
  const neighbors = new Set<string>()
  for (const edge of edges) {
    if (edge.source === entityId) neighbors.add(edge.target)
    if (edge.target === entityId) neighbors.add(edge.source)
  }
  return neighbors
}

/** 候选集合内、相对当前可见集仍隐藏的邻居数量。 */
export function countHiddenNeighbors(
  entityId: string,
  edges: readonly ProgressiveEdge[],
  candidateIds: ReadonlySet<string>,
  visibleIds: ReadonlySet<string>,
): number {
  let count = 0
  for (const neighborId of collectNeighborIds(entityId, edges)) {
    if (candidateIds.has(neighborId) && !visibleIds.has(neighborId)) {
      count += 1
    }
  }
  return count
}

/**
 * 计算渐进可见节点：
 * seeds ∪ manualSeeds ∪ pinned ∪（每个已展开节点的一跳邻居 ∩ candidates）
 */
export function computeProgressiveVisibleIds(input: ProgressiveRevealInput): Set<string> {
  const {
    candidateIds,
    seedIds,
    manualSeedIds,
    expandedIds,
    pinnedIds,
    edges,
  } = input

  const visible = new Set<string>()

  const addIfCandidate = (id: string) => {
    if (candidateIds.has(id)) visible.add(id)
  }

  for (const id of seedIds) addIfCandidate(id)
  for (const id of manualSeedIds) addIfCandidate(id)
  if (pinnedIds) {
    for (const id of pinnedIds) {
      // 编辑保活允许暂时越过筛选，与左侧列表 pinned 语义一致
      visible.add(id)
    }
  }

  for (const id of expandedIds) {
    if (!visible.has(id)) continue
    for (const neighborId of collectNeighborIds(id, edges)) {
      addIfCandidate(neighborId)
    }
  }

  return visible
}

/** 按实体类型收集种子；无命中时返回空集（由调用方决定是否回退全量）。 */
export function collectSeedIdsByType(
  nodes: readonly { id: string; type: string }[],
  candidateIds: ReadonlySet<string>,
  preferredType: string,
): Set<string> {
  const seeds = new Set<string>()
  for (const node of nodes) {
    if (node.type === preferredType && candidateIds.has(node.id)) {
      seeds.add(node.id)
    }
  }
  return seeds
}
