// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AgentStepStream from './AgentStepStream.vue'
import type { AssistantBlock } from '../types'

describe('AgentStepStream Block Stream', () => {
  it('renders reasoning, tool, and system_event blocks with single-line rows', async () => {
    const blocks: AssistantBlock[] = [
      {
        id: 'r1',
        kind: 'reasoning',
        type: 'reasoning',
        sequence: 1,
        callId: 'call_1',
        stage: 'agent_controller',
        role: 'main',
        text: '第一行思考\n第二行思考：分析用户需求',
        status: 'completed',
        isStreaming: false,
        duration: '1.2s',
      },
      {
        id: 't1',
        kind: 'tool',
        type: 'tool',
        sequence: 2,
        toolCallKey: 'tool:1:retrieve_kb',
        tool: 'retrieve_kb',
        toolName: 'retrieve_kb',
        label: '知识库检索',
        description: '检索流水线部署',
        input: { query: '流水线部署' },
        output: { summary: '已命中 3 条已审核切片' },
        status: 'completed',
        isStreaming: false,
        elapsedMs: 250,
      },
      {
        id: 's1',
        kind: 'system_event',
        type: 'system_event',
        sequence: 3,
        event: 'review_revise',
        level: 'warning',
        text: '候选回答未通过证据审查，正在重新组织…',
        status: 'completed',
      },
    ]

    const wrapper = mount(AgentStepStream, { props: { blocks } })
    const text = wrapper.text()

    expect(text).toContain('Main · Controller')
    expect(text).toContain('知识库检索')
    expect(text).toContain('候选回答未通过证据审查，正在重新组织…')

    // 找到所有可展开行
    const rows = wrapper.findAll('.disclosure-row')
    expect(rows).toHaveLength(2)

    // 点击第一行展开 reasoning
    await rows[0].trigger('click')
    expect(wrapper.find('.think-body').exists()).toBe(true)
    expect(wrapper.find('.think-body').text()).toContain('第一行思考')

    // 点击第二行展开 tool IN/OUT 详情
    await rows[1].trigger('click')
    expect(wrapper.find('.tool-body-wrap').exists()).toBe(true)
    expect(wrapper.find('.tool-body-wrap').text()).toContain('流水线部署')
  })

  it('renders running tool with shimmer state', () => {
    const blocks: AssistantBlock[] = [
      {
        id: 't2',
        kind: 'tool',
        type: 'tool',
        sequence: 1,
        toolCallKey: 'tool:2:retrieve_kb',
        tool: 'retrieve_kb',
        label: '知识库检索',
        status: 'running',
        isStreaming: true,
      },
    ]

    const wrapper = mount(AgentStepStream, { props: { blocks } })
    expect(wrapper.find('.disclosure-root[data-state="running"]').exists()).toBe(true)
    expect(wrapper.find('.state-dot.running').exists()).toBe(true)
  })
  it('renders activity blocks across running, completed, warning, and failed states', () => {
    const blocks: AssistantBlock[] = [
      {
        id: 'act-1',
        kind: 'activity',
        type: 'activity',
        sequence: 1,
        activity: 'grounding_review',
        reviewCount: 1,
        status: 'running',
        text: '正在核对回答与证据…',
        startedAt: Date.now() - 5000,
      },
      {
        id: 'act-2',
        kind: 'activity',
        type: 'activity',
        sequence: 2,
        activity: 'grounding_review',
        reviewCount: 1,
        status: 'completed',
        text: '证据核对通过',
        elapsedMs: 12400,
      },
      {
        id: 'act-3',
        kind: 'activity',
        type: 'activity',
        sequence: 3,
        activity: 'grounding_review',
        reviewCount: 1,
        status: 'warning',
        text: '发现部分内容需要修正',
        elapsedMs: 11800,
      },
      {
        id: 'act-4',
        kind: 'activity',
        type: 'activity',
        sequence: 4,
        activity: 'grounding_review',
        reviewCount: 2,
        status: 'failed',
        text: '证据核对失败',
        elapsedMs: 5000,
      },
    ]

    const wrapper = mount(AgentStepStream, { props: { blocks } })
    const text = wrapper.text()

    expect(text).toContain('正在核对回答与证据…')
    expect(text).toContain('证据核对通过')
    expect(text).toContain('12.4s')
    expect(text).toContain('发现部分内容需要修正')
    expect(text).toContain('11.8s')
    expect(text).toContain('证据核对失败')
    expect(text).toContain('5.0s')

    const activityRoots = wrapper.findAll('.activity-root')
    expect(activityRoots).toHaveLength(4)
    expect(wrapper.find('.activity-icon.completed').text()).toBe('✓')
    expect(wrapper.find('.activity-icon.warning').text()).toBe('!')
    expect(wrapper.find('.activity-icon.failed').text()).toBe('✕')
  })
})
