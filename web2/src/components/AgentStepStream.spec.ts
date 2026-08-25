// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AgentStepStream from './AgentStepStream.vue'
import type { AgentTimelineItem } from '../types'

describe('AgentStepStream', () => {
  it('renders evidence gaps, NO_PROGRESS, review details, rewrite failures, and errors', () => {
    const items: AgentTimelineItem[] = [
      {
        type: 'evidence_update',
        new_chunks: 0,
        new_entities: 0,
        new_relations: 0,
        coverage: 'PARTIAL',
        status: 'NO_PROGRESS',
      },
      {
        type: 'evidence_gap',
        coverage: 'PARTIAL',
        missing_facts: ['完整端口清单'],
      },
      {
        type: 'review_status',
        review_count: 1,
        verdict: 'REVISE',
        coverage: 'PARTIAL',
        message: '存在未受支持事实。',
        claim_reviews: [
          { claim_id: 'c2', status: 'unsupported', evidence_ids: [], claim: '端口为 9999。' },
        ],
        rewrite_actions: [
          { claim_id: 'c2', action: 'remove', instruction: '删除未受支持端口。' },
        ],
      },
      {
        type: 'rewrite_status',
        status: 'failed',
        message: '定向重写失败，候选答案不会发布。',
      },
      {
        type: 'error',
        stage: 'grounding_review',
        code: 'reviewer_error',
        message: '审核模型调用失败，当前候选答案不会发布。',
      },
    ]

    const wrapper = mount(AgentStepStream, { props: { items } })
    const text = wrapper.text()

    expect(text).toContain('NO_PROGRESS')
    expect(text).toContain('完整端口清单')
    expect(text).toContain('存在未受支持事实。')
    expect(text).toContain('定向重写失败，候选答案不会发布。')
    expect(text).toContain('审核模型调用失败，当前候选答案不会发布。')
  })

  it('folds historical expandable items and expands only the latest item by default', () => {
    const items: AgentTimelineItem[] = [
      {
        type: 'understanding',
        eventKey: 'understanding:1',
        summary: '识别问题。',
      },
      {
        type: 'decision',
        eventKey: 'decision:1',
        action: 'tool_call',
        tool: 'retrieve_kb',
        reason: '检索证据。',
      },
    ]

    const wrapper = mount(AgentStepStream, { props: { items } })
    const expandableRows = wrapper.findAll('[role="button"]')

    expect(expandableRows).toHaveLength(2)
    expect(expandableRows[0].attributes('aria-expanded')).toBe('false')
    expect(expandableRows[1].attributes('aria-expanded')).toBe('true')
  })
})
