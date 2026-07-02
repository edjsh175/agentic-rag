import type { SourceDoc } from '../types'

const NO_PAGE_LABELS = new Set(['', '无页码', '未知', 'none', 'null'])

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function documentName(source: SourceDoc): string {
  const raw = source.metadata?.file_name
    || source.metadata?.source
    || source.metadata?.title
    || '未知资料'
  return raw.replace(/\.(?:pdf|docx?|md|markdown|txt)$/i, '')
}

export function citationLabel(source: SourceDoc): string {
  const name = `《${documentName(source)}》`
  const rawPage = String(source.metadata?.page_label ?? '').trim()
  if (!NO_PAGE_LABELS.has(rawPage.toLowerCase())) {
    return `${name}· 第 ${rawPage} 页`
  }

  const section = source.metadata?.section_title || source.metadata?.section_path
  return section ? `${name}· ${section}` : name
}

function citationButton(source: SourceDoc, id: number): string {
  const label = escapeHtml(citationLabel(source))
  return `<button type="button" class="citation-chip" data-citation-id="${id}" title="${label}">${label}</button>`
}

function decorateInline(line: string, sourceMap: Map<number, SourceDoc>): string {
  let result = ''
  let index = 0

  while (index < line.length) {
    if (line[index] === '`') {
      const ticks = line.slice(index).match(/^`+/)?.[0] || '`'
      const end = line.indexOf(ticks, index + ticks.length)
      if (end === -1) return result + line.slice(index)
      result += line.slice(index, end + ticks.length)
      index = end + ticks.length
      continue
    }

    const match = line.slice(index).match(/^\[(\d+)\]/)
    if (match && line[index - 1] !== '\\' && line[index + match[0].length] !== '(') {
      const id = Number(match[1])
      const source = sourceMap.get(id)
      if (source) {
        result += citationButton(source, id)
        index += match[0].length
        continue
      }
    }

    result += line[index]
    index += 1
  }

  return result
}

export function decorateCitations(markdown: string, sources: SourceDoc[] = []): string {
  const sourceMap = new Map<number, SourceDoc>()
  sources.forEach((source, index) => {
    sourceMap.set(source.metadata?.citation_id ?? index + 1, source)
  })
  if (sourceMap.size === 0) return markdown

  let inFence = false
  return markdown.split('\n').map((line) => {
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence
      return line
    }
    return inFence ? line : decorateInline(line, sourceMap)
  }).join('\n')
}
