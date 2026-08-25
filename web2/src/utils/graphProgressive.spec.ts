import { describe, expect, it } from 'vitest'

import {
  collectNeighborIds,
  collectSeedIdsByType,
  computeProgressiveVisibleIds,
  countHiddenNeighbors,
} from './graphProgressive'

describe('graphProgressive', () => {
  const edges = [
    { source: 'product', target: 'module-a' },
    { source: 'module-a', target: 'tool-a' },
    { source: 'module-a', target: 'tool-b' },
    { source: 'solo', target: 'orphan' },
  ]

  it('collects one-hop neighbors', () => {
    expect([...collectNeighborIds('module-a', edges)].sort()).toEqual([
      'product',
      'tool-a',
      'tool-b',
    ])
  })

  it('counts hidden neighbors within candidates', () => {
    const candidates = new Set(['product', 'module-a', 'tool-a', 'tool-b'])
    const visible = new Set(['product', 'module-a'])
    expect(countHiddenNeighbors('module-a', edges, candidates, visible)).toBe(2)
    expect(countHiddenNeighbors('product', edges, candidates, visible)).toBe(0)
  })

  it('computes visible set from seeds and expansions', () => {
    const candidates = new Set(['product', 'module-a', 'tool-a', 'tool-b', 'solo'])
    const visible = computeProgressiveVisibleIds({
      candidateIds: candidates,
      seedIds: new Set(['product']),
      manualSeedIds: new Set(),
      expandedIds: new Set(['product']),
      edges,
    })
    expect([...visible].sort()).toEqual(['module-a', 'product'])
  })

  it('keeps manual seeds and expands from them', () => {
    const candidates = new Set(['product', 'module-a', 'tool-a', 'tool-b'])
    const visible = computeProgressiveVisibleIds({
      candidateIds: candidates,
      seedIds: new Set(['product']),
      manualSeedIds: new Set(['module-a']),
      expandedIds: new Set(['module-a']),
      edges,
    })
    expect([...visible].sort()).toEqual(['module-a', 'product', 'tool-a', 'tool-b'])
  })

  it('includes pinned ids even outside candidates', () => {
    const visible = computeProgressiveVisibleIds({
      candidateIds: new Set(['product']),
      seedIds: new Set(['product']),
      manualSeedIds: new Set(),
      expandedIds: new Set(),
      pinnedIds: new Set(['draft-1']),
      edges,
    })
    expect(visible.has('draft-1')).toBe(true)
  })

  it('collects seeds by preferred type', () => {
    const nodes = [
      { id: 'p1', type: 'Product' },
      { id: 't1', type: 'Tool' },
      { id: 'p2', type: 'Product' },
    ]
    const seeds = collectSeedIdsByType(nodes, new Set(['p1', 't1', 'p2']), 'Product')
    expect([...seeds].sort()).toEqual(['p1', 'p2'])
  })
})
