/** Edge visual style for product-backbone preview: muted relation hues + formal-page line widths. */

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

/** Match formal KnowledgeGraphView edge widths. */
export const FORMAL_EDGE_WIDTH = {
  normal: 0.8,
  highlighted: 2.2,
} as const

export function relationTypeHue(relationType: string | null | undefined): number {
  const key = String(relationType || '').trim()
  return RELATION_TYPE_HUES[key] ?? DEFAULT_RELATION_HUE
}

/** Soft swatch for legends. */
export function relationTypeColor(relationType: string | null | undefined): string {
  return hslToCss(relationTypeHue(relationType), 26, 60, 0.8)
}

export function resolvePreviewEdgeStyle(input: {
  relationType: string
  highlighted?: boolean
  faded?: boolean
}): { color: string; width: number; labelColor: string } {
  const hue = relationTypeHue(input.relationType)
  let saturation = 26
  let lightness = 58
  let alpha = 0.55
  let width: number = FORMAL_EDGE_WIDTH.normal

  if (input.highlighted) {
    width = FORMAL_EDGE_WIDTH.highlighted
    saturation += 8
    lightness -= 4
    alpha = 0.86
  }
  if (input.faded) {
    saturation = Math.max(8, saturation * 0.45)
    lightness += 6
    alpha = 0.16
  }

  const color = hslToCss(hue, saturation, lightness, alpha)
  const labelColor = hslToCss(
    hue,
    Math.min(38, saturation + 5),
    Math.max(40, lightness - 8),
    Math.min(0.88, alpha + 0.16),
  )
  return { color, width, labelColor }
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
