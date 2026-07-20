import { describe, expect, it } from 'vitest'

import { createGraphLayout, type LayoutEdge, type LayoutNode } from './graphLayout'

const node = (id: string, x: number, y: number, type = 'Tool'): LayoutNode => ({
  id,
  x,
  y,
  vx: 0,
  vy: 0,
  type,
})

const edge = (source: string, target: string): LayoutEdge => ({ source, target })

describe('graphLayout', () => {
  it('settles into a low non-zero idle energy instead of stopping', () => {
    const nodes = [node('a', 0, 0), node('b', 200, 0)]
    const layout = createGraphLayout({ width: 800, height: 600, autoStart: false })

    layout.setGraph(nodes, [edge('a', 'b')], 'initial')
    const initialAlpha = layout.getAlpha()
    layout.tick(1)

    expect(layout.getAlpha()).toBeLessThan(initialAlpha)

    layout.tick(1000)
    expect(layout.getAlpha()).toBeGreaterThan(layout.getAlphaMin())
    expect(layout.getAlpha()).toBeLessThan(0.01)
    layout.destroy()
  })

  it('soft-releases the existing graph when a bulk expansion adds many nodes', () => {
    const anchor = node('anchor', 300, 300)
    const remote = node('remote', 700, 500)
    const layout = createGraphLayout({ width: 800, height: 600, autoStart: false })
    layout.setGraph([anchor, remote], [], 'initial')
    layout.tick(1000)

    const sections = Array.from({ length: 30 }, (_, index) => (
      node(`section-${index}`, Number.NaN, Number.NaN, 'Section')
    ))
    layout.setGraph(
      [anchor, remote, ...sections],
      sections.map(section => edge(anchor.id, section.id)),
      'incremental',
    )

    expect(remote.fx).toBeNull()
    expect(remote.fy).toBeNull()
    layout.tick(1000)
    expect(sections.every(section => Number.isFinite(section.x) && Number.isFinite(section.y))).toBe(true)
    layout.destroy()
  })

  it('re-solves every add-remove-add visibility cycle', () => {
    const nodes = [
      node('a', 100, 100),
      node('b', 220, 100),
      node('c', 340, 100),
    ]
    const layout = createGraphLayout({ width: 800, height: 600, autoStart: false })
    layout.setGraph(nodes, [edge('a', 'b'), edge('b', 'c')], 'initial')
    layout.tick(1000)
    const hiddenPosition = { x: nodes[1].x, y: nodes[1].y }

    layout.setGraph([nodes[0], nodes[2]], [], 'incremental')

    expect(nodes[0].fx).toBeNull()
    expect(nodes[0].fy).toBeNull()
    expect(nodes[2].fx).toBeNull()
    expect(nodes[2].fy).toBeNull()
    expect(layout.getAlpha()).toBeGreaterThan(0.2)

    layout.tick(1000)
    layout.setGraph(nodes, [edge('a', 'b'), edge('b', 'c')], 'incremental')

    expect(nodes.every(item => item.fx === null && item.fy === null)).toBe(true)
    expect(nodes[1].x).toBe(hiddenPosition.x)
    expect(nodes[1].y).toBe(hiddenPosition.y)
    expect(layout.getAlpha()).toBeGreaterThan(0.2)
    layout.destroy()
  })

  it('re-solves when relationships change without a node change', () => {
    const nodes = [node('a', 100, 100), node('b', 400, 100)]
    const layout = createGraphLayout({ width: 800, height: 600, autoStart: false })
    layout.setGraph(nodes, [], 'initial')
    layout.tick(1000)

    layout.setGraph(nodes, [edge('a', 'b')], 'incremental')

    expect(nodes.every(item => item.fx === null && item.fy === null)).toBe(true)
    expect(layout.getAlpha()).toBeGreaterThan(0.2)
    layout.destroy()
  })

  it('places a new Section beside its relation and preserves the remote layout', () => {
    const a = node('a', 100, 100)
    const b = node('b', 220, 100)
    const remote = node('remote', 700, 500)
    const layout = createGraphLayout({ width: 800, height: 600, autoStart: false })

    layout.setGraph([a, b, remote], [edge('a', 'b')], 'initial')
    layout.tick(1000)
    const remotePosition = { x: remote.x, y: remote.y }
    const section = node('section', Number.NaN, Number.NaN, 'Section')

    layout.setGraph(
      [a, b, remote, section],
      [edge('a', 'b'), edge('a', 'section')],
      'incremental',
    )

    expect(Math.hypot(section.x - a.x, section.y - a.y)).toBeLessThan(180)
    expect(remote.fx).toBe(remotePosition.x)
    expect(remote.fy).toBe(remotePosition.y)

    layout.tick(1000)
    expect(remote.x).toBe(remotePosition.x)
    expect(remote.y).toBe(remotePosition.y)
    layout.destroy()
  })

  it('releases only the dragged node one-hop neighborhood', () => {
    const nodes = [node('a', 100, 100), node('b', 220, 100), node('remote', 700, 500)]
    const layout = createGraphLayout({ width: 800, height: 600, autoStart: false })
    layout.setGraph(nodes, [edge('a', 'b')], 'initial')
    layout.tick(1000)

    layout.beginNodeDrag('a')
    layout.moveNode('a', 150, 160)

    expect(nodes[0].fx).toBe(150)
    expect(nodes[0].fy).toBe(160)
    expect(nodes[1].fx).toBeNull()
    expect(nodes[1].fy).toBeNull()
    expect(nodes[2].fx).toBe(nodes[2].x)
    expect(nodes[2].fy).toBe(nodes[2].y)

    layout.endNodeDrag('a')
    expect(nodes[0].fx).toBeNull()
    expect(nodes[0].fy).toBeNull()
    layout.destroy()
  })

  it('handles empty and isolated graphs safely', () => {
    const layout = createGraphLayout({ width: 800, height: 600, autoStart: false })

    expect(() => layout.setGraph([], [], 'initial')).not.toThrow()
    expect(() => layout.setGraph([node('solo', Number.NaN, Number.NaN)], [], 'incremental')).not.toThrow()
    expect(() => layout.tick(10)).not.toThrow()
    layout.destroy()
  })

  it('freezes nodes in static mode and resumes motion when re-enabled', () => {
    const a = node('a', 100, 100)
    const b = node('b', 300, 100)
    const layout = createGraphLayout({ width: 800, height: 600, autoStart: false })
    layout.setGraph([a, b], [edge('a', 'b')], 'initial')
    layout.tick(20)

    layout.setPhysicsEnabled(false)
    expect(layout.isPhysicsEnabled()).toBe(false)
    expect(layout.getAlpha()).toBe(0)
    expect(a.fx).toBe(a.x)
    expect(a.fy).toBe(a.y)
    expect(b.fx).toBe(b.x)
    expect(b.fy).toBe(b.y)

    const frozenX = a.x
    const frozenY = a.y
    layout.tick(50)
    expect(a.x).toBe(frozenX)
    expect(a.y).toBe(frozenY)

    layout.setPhysicsEnabled(true)
    expect(layout.isPhysicsEnabled()).toBe(true)
    expect(a.fx).toBeNull()
    expect(a.fy).toBeNull()
    expect(layout.getAlpha()).toBeGreaterThan(0)
    layout.destroy()
  })

  it('keeps manual drag positions when physics is static', () => {
    const a = node('a', 120, 140)
    const layout = createGraphLayout({ width: 800, height: 600, autoStart: false })
    layout.setGraph([a], [], 'initial')
    layout.setPhysicsEnabled(false)

    layout.beginNodeDrag('a')
    layout.moveNode('a', 220, 260)
    layout.endNodeDrag('a')

    expect(a.x).toBe(220)
    expect(a.y).toBe(260)
    expect(a.fx).toBe(220)
    expect(a.fy).toBe(260)
    expect(layout.getAlpha()).toBe(0)
    layout.destroy()
  })
})
