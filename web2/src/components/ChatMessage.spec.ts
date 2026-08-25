// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ChatMessage from './ChatMessage.vue'

describe('ChatMessage clarification card', () => {
  it('renders one Other control and submits unmatched free text as free_text', async () => {
    const wrapper = mount(ChatMessage, {
      props: {
        role: 'assistant',
        content: '',
        clarification: {
          ask_question: '请选择产品：',
          options: [
            {
              id: 'cand_01',
              label: 'PipelineWebGL',
              filter: { entity_name: 'PipelineWebGL' },
              source: 'backbone',
              canonical_name: 'PipelineWebGL',
              binding_status: 'canonical',
            },
            {
              id: 'other',
              label: '以上都不是',
              filter: {},
              source: 'fixed_other',
              binding_status: 'unresolved',
            },
          ],
        },
      },
      global: {
        stubs: {
          AgentStepStream: true,
          AgentThinkingBlock: true,
          AgentToolTimeline: true,
          EvidencePanel: true,
        },
      },
    })

    expect(wrapper.findAll('.clarification-option-btn')).toHaveLength(2)
    expect(wrapper.findAll('.is-other-option')).toHaveLength(1)

    await wrapper.get('.is-other-option').trigger('click')
    await wrapper.get('.other-input').setValue('部署流水线服务')
    await wrapper.get('.other-submit-btn').trigger('click')

    const emitted = wrapper.emitted('selectClarificationOption')
    expect(emitted).toHaveLength(1)
    expect(emitted?.[0]?.[0]).toEqual({
      option: {
        id: 'other',
        label: '以上都不是',
        filter: {},
        source: 'fixed_other',
        binding_status: 'unresolved',
      },
      kind: 'free_text',
      freeText: '部署流水线服务',
    })
  })
})

describe('ChatMessage execution presentation', () => {
  it('renders Agent execution, final answer, and sources without Pipeline status', () => {
    const wrapper = mount(ChatMessage, {
      props: {
        role: 'assistant',
        mode: 'agent',
        content: '这是经审核的答案。',
        loading: false,
        status: '正在检索知识库…',
        timelineItems: [
          {
            type: 'decision',
            eventKey: 'decision:1',
            step: 1,
            action: 'tool_call',
            tool: 'retrieve_kb',
            reason: '需要检索已审核证据。',
          },
        ],
        sources: [
          {
            content: '来源正文',
            metadata: { source: '部署手册.md', citation_id: 1, title: '部署手册' },
          },
        ],
      },
      global: {
        stubs: {
          AgentStepStream: true,
          AgentThinkingBlock: true,
          AgentToolTimeline: true,
          EvidencePanel: true,
        },
      },
    })

    expect(wrapper.find('[data-testid="execution-timeline"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="final-answer"]').text()).toContain('这是经审核的答案。')
    expect(wrapper.get('[data-testid="answer-sources"]').text()).toContain('部署手册')
    expect(wrapper.find('[data-testid="pipeline-status"]').exists()).toBe(false)
  })

  it('keeps Linear stage status and ignores Agent timeline presentation', () => {
    const wrapper = mount(ChatMessage, {
      props: {
        role: 'assistant',
        mode: 'linear',
        content: '',
        loading: true,
        status: '正在检索知识库…',
        timelineItems: [
          {
            type: 'decision',
            eventKey: 'decision:1',
            action: 'tool_call',
            reason: '不应出现在 Linear 模式。',
          },
        ],
      },
      global: {
        stubs: {
          AgentStepStream: true,
          AgentThinkingBlock: true,
          AgentToolTimeline: true,
          EvidencePanel: true,
        },
      },
    })

    expect(wrapper.get('[data-testid="pipeline-status"]').text()).toContain('正在检索知识库…')
    expect(wrapper.find('[data-testid="execution-timeline"]').exists()).toBe(false)
  })
})
