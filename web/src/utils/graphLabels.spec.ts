import { describe, expect, it } from 'vitest'

import {
  entitySubtypeLabel,
  entityTypeBadge,
  entityTypeLabel,
  filterTypeLabel,
  linkTypeLabel,
  orderEntityTypes,
  relationTypeLabel,
  getShortLabel,
  getLabelPrefix,
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

  describe('getShortLabel & getLabelPrefix', () => {
    it('simplifies nested Section names with document prefixes', () => {
      const longName = 'StampTools用户手册::PipelineBuilder > 数据规范 > 管线线表'
      expect(getShortLabel(longName)).toBe('管线线表')
      expect(getLabelPrefix(longName)).toBe('StampTools用户手册 :: PipelineBuilder > 数据规范')
    })

    it('handles simple paths without document prefix', () => {
      const pathName = 'PipelineBuilder > 数据规范 > 管线点表'
      expect(getShortLabel(pathName)).toBe('管线点表')
      expect(getLabelPrefix(pathName)).toBe('PipelineBuilder > 数据规范')
    })

    it('returns unmodified name if no special delimiters are present', () => {
      expect(getShortLabel('管线线表')).toBe('管线线表')
      expect(getLabelPrefix('管线线表')).toBe('')
    })

    it('handles empty input gracefully', () => {
      expect(getShortLabel(null)).toBe('-')
      expect(getLabelPrefix(null)).toBe('')
    })
  })
})
