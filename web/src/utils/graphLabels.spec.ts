import { describe, expect, it } from 'vitest'

import {
  entitySubtypeLabel,
  entityTypeBadge,
  entityTypeLabel,
  linkTypeLabel,
  relationTypeLabel,
} from './graphLabels'

describe('graphLabels', () => {
  it('localizes known entity and relation types', () => {
    expect(entityTypeLabel('Product')).toBe('产品')
    expect(entityTypeLabel('DataTable')).toBe('数据表')
    expect(entityTypeBadge('Document')).toBe('文档')
    expect(relationTypeLabel('belongs_to')).toBe('属于')
    expect(relationTypeLabel('has_section')).toBe('包含章节')
    expect(entitySubtypeLabel('ProductFamily')).toBe('产品族')
    expect(linkTypeLabel('evidence')).toBe('证据')
  })

  it('falls back to the original token for unknown values', () => {
    expect(entityTypeLabel('CustomType')).toBe('CustomType')
    expect(relationTypeLabel('custom_rel')).toBe('custom_rel')
    expect(entityTypeLabel('')).toBe('-')
  })
})
