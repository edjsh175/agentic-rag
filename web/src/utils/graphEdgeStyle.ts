/** Edge visual style for product-backbone preview (muted relation hue + tier-coupled width/saturation). */

/** Distinct but low-chroma relation hues (degrees). */
export const RELATION_TYPE_HUES: Record<string, number> = {
  belongs_to: 232,
  requires: 36,
  produces: 152,
  consumes: 188,
  supports_format: 328,
  uses_config: 262,
  has_table: 198,
  has_field: 172,
  defined_in: 215,
  different_from: 4,
  has_step: 24,
  causes: 12,
  solved_by: 142,
  has_section: 246,
  has_chunk: 210,
}

const DEFAULT_RELATION_HUE = 215

/** Higher rank = higher in hierarchy (thicker + slightly stronger edges). */
export const ENTITY_SUBTYPE_TIER: Record<string, number> = {
  ProductFamily: 5,
  Product: 4,
  ManagementProduct: 4,
  CoreLayer: 4,
  SupportLayer: 4,
  CrossCuttingDimension: 4,
  MainTool: 3,
  RenderingSystem: 3,
  StampServerService: 3,
  BusinessApplication: 3,
  DevelopmentKit: 3,
  StandardFamily: 3,
  SubTool: 2,
  ToolCapability: 2,
  ToolPlugin: 2,
  SystemData: 2,
  SpatialData: 2,
  TerrainData: 2,
  ImageryData: 2,
  ModelData: 2,
  PointCloudData: 2,
  GaussianData: 2,
  PipelineData: 2,
  BusinessData: 2,
  ModelServiceResource: 2,
  '3DDataStandard': 2,
  ServiceResourceType: 2,
  TransportTechnology: 2,
  RenderingInterface: 2,
  GraphicsAPI: 2,
  ClientMode: 2,
  ClientUIComponent: 2,
  ClientLibrary: 2,
  DevelopmentInterface: 2,
  Protocol: 2,
  VideoStandard: 2,
  ManagementModule: 2,
  OperatingSystem: 2,
  Middleware: 2,
  Database: 2,
  LicenseDriver: 2,
  CrossCuttingEntity: 2,
  SecurityCapability: 2,
  ServiceLibrary: 1,
  Format: 1,
}

export const ENTITY_TYPE_TIER: Record<string, number> = {
  Product: 4,
  Module: 4,
  Tool: 3,
  Service: 3,
  DataTable: 2,
  Procedure: 2,
  Document: 2,
  Format: 1,
  Field: 1,
  ConfigItem: 1,
  Section: 1,
  Step: 1,
  Error: 1,
  Solution: 1,
}

const MIN_TIER = 1
const MAX_TIER = 5

export function relationTypeHue(relationType: string | null | undefined): number {
  const key = String(relationType || '').trim()
  return RELATION_TYPE_HUES[key] ?? DEFAULT_RELATION_HUE
}

/** Soft swatch for legends (fixed mid-tier look). */
export function relationTypeColor(relationType: string | null | undefined): string {
  return hslToCss(relationTypeHue(relationType), 26, 60, 0.8)
}

export function entityHierarchyTier(input: {
  subtype?: string | null
  entityType?: string | null
}): number {
  const subtype = String(input.subtype || '').trim()
  if (subtype && ENTITY_SUBTYPE_TIER[subtype] != null) {
    return ENTITY_SUBTYPE_TIER[subtype]
  }
  const entityType = String(input.entityType || '').trim()
  if (entityType && ENTITY_TYPE_TIER[entityType] != null) {
    return ENTITY_TYPE_TIER[entityType]
  }
  return MIN_TIER
}

export function clampTier(...tiers: number[]): number {
  const value = Math.max(...tiers)
  return Math.min(MAX_TIER, Math.max(MIN_TIER, value))
}

/**
 * Tier couples width + saturation + alpha:
 * high tier → thicker, slightly stronger; low tier → thinner, washed out.
 */
export function edgeAppearanceForTier(
  tier: number,
  hue: number,
  options?: { highlighted?: boolean; faded?: boolean },
): { color: string; width: number; labelColor: string } {
  const t = clampTier(tier)
  // Wider tier steps so hierarchy reads clearly:
  // 5→4.4, 4→3.35, 3→2.3, 2→1.25, 1→0.5
  let width = -0.55 + t * 0.99
  // Mid-low chroma: relation hues readable, still not loud
  let saturation = 16 + t * 3.6 // 19.6 … 34
  let lightness = 66 - t * 2.8 // ~52–63
  // Keep current alpha curve unchanged
  let alpha = 0.16 + t * 0.11 // 0.27 … 0.71

  if (options?.highlighted) {
    width += 0.85
    saturation += 8
    lightness -= 4
    alpha = Math.min(0.9, alpha + 0.18)
  }
  if (options?.faded) {
    width = Math.max(0.4, width * 0.6)
    saturation = Math.max(8, saturation * 0.45)
    lightness += 6
    alpha = Math.min(0.16, alpha * 0.3)
  }

  const color = hslToCss(hue, saturation, lightness, alpha)
  const labelColor = hslToCss(hue, Math.min(38, saturation + 5), Math.max(40, lightness - 8), Math.min(0.88, alpha + 0.16))
  return { color, width, labelColor }
}

export function edgeWidthForTiers(
  sourceTier: number,
  targetTier: number,
  options?: { highlighted?: boolean },
): number {
  return edgeAppearanceForTier(
    clampTier(sourceTier, targetTier),
    DEFAULT_RELATION_HUE,
    options,
  ).width
}

export function resolvePreviewEdgeStyle(input: {
  relationType: string
  sourceSubtype?: string | null
  targetSubtype?: string | null
  sourceType?: string | null
  targetType?: string | null
  highlighted?: boolean
  faded?: boolean
}): { color: string; width: number; labelColor: string } {
  const hue = relationTypeHue(input.relationType)
  const tier = clampTier(
    entityHierarchyTier({
      subtype: input.sourceSubtype,
      entityType: input.sourceType,
    }),
    entityHierarchyTier({
      subtype: input.targetSubtype,
      entityType: input.targetType,
    }),
  )
  return edgeAppearanceForTier(tier, hue, {
    highlighted: input.highlighted,
    faded: input.faded,
  })
}

function hslToCss(h: number, s: number, l: number, a: number): string {
  const { r, g, b } = hslToRgb(h, s / 100, l / 100)
  return `rgba(${r}, ${g}, ${b}, ${roundAlpha(a)})`
}

function roundAlpha(alpha: number): number {
  return Math.round(Math.min(1, Math.max(0, alpha)) * 1000) / 1000
}

function hslToRgb(h: number, s: number, l: number): { r: number; g: number; b: number } {
  const hue = ((h % 360) + 360) % 360
  const c = (1 - Math.abs(2 * l - 1)) * s
  const x = c * (1 - Math.abs(((hue / 60) % 2) - 1))
  const m = l - c / 2
  let rp = 0
  let gp = 0
  let bp = 0
  if (hue < 60) [rp, gp, bp] = [c, x, 0]
  else if (hue < 120) [rp, gp, bp] = [x, c, 0]
  else if (hue < 180) [rp, gp, bp] = [0, c, x]
  else if (hue < 240) [rp, gp, bp] = [0, x, c]
  else if (hue < 300) [rp, gp, bp] = [x, 0, c]
  else [rp, gp, bp] = [c, 0, x]
  return {
    r: Math.round((rp + m) * 255),
    g: Math.round((gp + m) * 255),
    b: Math.round((bp + m) * 255),
  }
}
