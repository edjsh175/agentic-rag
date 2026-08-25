import { describe, expect, it } from 'vitest'
import { isNearScrollBottom } from './scrollFollow'

describe('isNearScrollBottom', () => {
  it('keeps auto-follow when the viewport is near the bottom', () => {
    expect(isNearScrollBottom({ scrollTop: 904, clientHeight: 500, scrollHeight: 1500 })).toBe(true)
  })

  it('releases auto-follow after the user scrolls sufficiently upward', () => {
    expect(isNearScrollBottom({ scrollTop: 700, clientHeight: 500, scrollHeight: 1500 })).toBe(false)
  })

  it('resumes auto-follow when the user scrolls back near the bottom', () => {
    expect(isNearScrollBottom({ scrollTop: 905, clientHeight: 500, scrollHeight: 1500 })).toBe(true)
  })
})
