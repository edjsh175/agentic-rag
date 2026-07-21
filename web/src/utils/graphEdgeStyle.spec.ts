import { describe, expect, it } from 'vitest'

import {
  FORMAL_EDGE_WIDTH,
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

  it('uses formal-page edge widths without tier grading', () => {
    const normal = resolvePreviewEdgeStyle({ relationType: 'belongs_to' })
    const highlighted = resolvePreviewEdgeStyle({
      relationType: 'belongs_to',
      highlighted: true,
    })
    const other = resolvePreviewEdgeStyle({ relationType: 'requires' })

    expect(normal.width).toBe(FORMAL_EDGE_WIDTH.normal)
    expect(highlighted.width).toBe(FORMAL_EDGE_WIDTH.highlighted)
    expect(other.width).toBe(FORMAL_EDGE_WIDTH.normal)
    expect(normal.color).not.toBe(other.color)
  })
})
