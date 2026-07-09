import { describe, expect, it } from 'vitest'

import { routes } from './index'

describe('application routes', () => {
  it('exposes stable URLs for chat, blog and chunk review', () => {
    expect(routes.map(route => route.path)).toEqual([
      '/',
      '/blog',
      '/admin/chunks',
      '/admin/graph-candidates',
      '/admin/graph',
    ])
  })
})
