import { describe, expect, it } from 'vitest'

import {
  edgeAppearanceForTier,
  edgeWidthForTiers,
  entityHierarchyTier,
  relationTypeColor,
  relationTypeHue,
  resolvePreviewEdgeStyle,
} from './graphEdgeStyle'

describe('graphEdgeStyle', () => {
  it('keeps relation hues distinct while legend swatches stay muted', () => {
    expect(relationTypeHue('belongs_to')).not.toBe(relationTypeHue('requires'))
    expect(relationTypeColor('belongs_to')).toMatch(/^rgba\(/)
    expect(relationTypeColor('requires')).toMatch(/^rgba\(/)
    expect(relationTypeColor('belongs_to')).not.toBe(relationTypeColor('requires'))
  })

  it('ranks subtypes with ProductFamily highest and leaf types lowest', () => {
    expect(entityHierarchyTier({ subtype: 'ProductFamily' })).toBe(5)
    expect(entityHierarchyTier({ subtype: 'CoreLayer' })).toBe(4)
    expect(entityHierarchyTier({ subtype: 'MainTool' })).toBe(3)
    expect(entityHierarchyTier({ subtype: 'ServiceLibrary' })).toBe(1)
    expect(entityHierarchyTier({ entityType: 'Tool' })).toBe(3)
  })

  it('couples higher tiers to thicker and stronger edges', () => {
    const top = edgeAppearanceForTier(5, 232)
    const leaf = edgeAppearanceForTier(1, 232)
    expect(top.width).toBeGreaterThan(leaf.width)

    const topAlpha = Number(top.color.match(/rgba\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\)/)?.[1] || 0)
    const leafAlpha = Number(leaf.color.match(/rgba\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\)/)?.[1] || 0)
    expect(topAlpha).toBeGreaterThan(leafAlpha)

    expect(edgeWidthForTiers(5, 4)).toBeGreaterThan(edgeWidthForTiers(1, 1))
  })

  it('resolves preview edge style from relation + endpoint tiers', () => {
    const high = resolvePreviewEdgeStyle({
      relationType: 'belongs_to',
      sourceSubtype: 'ProductFamily',
      targetSubtype: 'CoreLayer',
    })
    const low = resolvePreviewEdgeStyle({
      relationType: 'requires',
      sourceSubtype: 'MainTool',
      targetSubtype: 'ServiceLibrary',
    })

    expect(high.width).toBeGreaterThan(low.width)
    expect(high.color).toMatch(/^rgba\(/)
    expect(low.color).toMatch(/^rgba\(/)
    expect(high.color).not.toBe(low.color)
  })
})
