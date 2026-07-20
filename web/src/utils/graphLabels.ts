/** Display labels for graph entity / relation type enums (API values stay English). */

export const ENTITY_TYPE_LABELS: Record<string, string> = {
  Product: '产品',
  Tool: '工具',
  Service: '服务',
  Module: '模块',
  DataTable: '数据表',
  Field: '字段',
  ConfigItem: '配置项',
  Format: '格式',
  Document: '文档',
  Section: '章节',
  Procedure: '流程',
  Step: '步骤',
  Error: '错误',
  Solution: '方案',
}

export const ENTITY_TYPE_BADGES: Record<string, string> = {
  Product: '产品',
  Tool: '工具',
  Service: '服务',
  Module: '模块',
  DataTable: '表',
  Field: '字段',
  ConfigItem: '配置',
  Format: '格式',
  Document: '文档',
  Section: '章节',
  Procedure: '流程',
  Step: '步骤',
  Error: '错误',
  Solution: '方案',
}

export const RELATION_TYPE_LABELS: Record<string, string> = {
  belongs_to: '属于',
  has_table: '包含表',
  has_field: '包含字段',
  defined_in: '定义于',
  different_from: '区别于',
  uses_config: '使用配置',
  supports_format: '支持格式',
  produces: '产出',
  consumes: '消费',
  requires: '依赖',
  has_step: '包含步骤',
  causes: '导致',
  solved_by: '解决于',
  has_section: '包含章节',
  has_chunk: '关联知识块',
}

export const ENTITY_SUBTYPE_LABELS: Record<string, string> = {
  ProductFamily: '产品族',
  Product: '产品',
  ManagementProduct: '管理类产品',
  CoreLayer: '核心层',
  SupportLayer: '支撑层',
  CrossCuttingDimension: '横切维度',
  MainTool: '主工具',
  RenderingSystem: '渲染系统',
  StampServerService: 'StampServer 服务',
  ServiceLibrary: '服务库 (.so)',
  BusinessApplication: '业务应用',
}

export const LINK_TYPE_LABELS: Record<string, string> = {
  primary: '主证据',
  mention: '提及',
  evidence: '证据',
  table_source: '表来源',
}

export function entityTypeLabel(type: string | null | undefined): string {
  const key = String(type || '').trim()
  if (!key) return '-'
  return ENTITY_TYPE_LABELS[key] || key
}

export function entityTypeBadge(type: string | null | undefined): string {
  const key = String(type || '').trim()
  if (!key) return '?'
  return ENTITY_TYPE_BADGES[key] || key.slice(0, 2)
}

export function relationTypeLabel(type: string | null | undefined): string {
  const key = String(type || '').trim()
  if (!key) return '-'
  return RELATION_TYPE_LABELS[key] || key
}

export function entitySubtypeLabel(subtype: string | null | undefined): string {
  const key = String(subtype || '').trim()
  if (!key) return '-'
  return ENTITY_SUBTYPE_LABELS[key] || key
}

export function linkTypeLabel(type: string | null | undefined): string {
  const key = String(type || '').trim()
  if (!key) return '-'
  return LINK_TYPE_LABELS[key] || key
}
