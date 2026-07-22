import { describe, expect, it } from 'vitest'

import {
  entitySubtypeLabel,
  entityTypeBadge,
  entityTypeLabel,
  filterTypeLabel,
  linkTypeLabel,
  orderEntityTypes,
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
    expect(filterTypeLabel('Module')).toBe('模块')
    expect(filterTypeLabel('EnvironmentComponent')).toBe('环境组件')
    expect(filterTypeLabel('StampServerService')).toBe('StampServer 服务')
    expect(linkTypeLabel('evidence')).toBe('证据')
  })

  it('orders formal entity types first and appends extensions', () => {
    expect(orderEntityTypes(['ServiceLibrary', 'Module', 'Product', 'Field'])).toEqual([
      'Product',
      'Module',
      'Field',
      'ServiceLibrary',
    ])
    expect(orderEntityTypes(['Command', 'Document'])).toEqual(['Document', 'Command'])
  })

  it('falls back to the original token for unknown values', () => {
    expect(entityTypeLabel('CustomType')).toBe('CustomType')
    expect(relationTypeLabel('custom_rel')).toBe('custom_rel')
    expect(entityTypeLabel('')).toBe('-')
  })
})
