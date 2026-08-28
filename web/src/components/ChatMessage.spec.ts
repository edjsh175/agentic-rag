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
        provide: {
          [Symbol.for('vue-router')]: { push: () => {} },
        },
        stubs: {
          AgentStepStream: true,
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

  it('emits selectClarificationOption when a canonical option button is clicked', async () => {
    const wrapper = mount(ChatMessage, {
      props: {
        role: 'assistant',
        content: '',
        clarification: {
          ask_question: '请选择产品：',
          clarification_snapshot_id: 'clar_snap_test_001',
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
        provide: {
          [Symbol.for('vue-router')]: { push: () => {} },
        },
        stubs: {
          AgentStepStream: true,
          EvidencePanel: true,
        },
      },
    })

    const buttons = wrapper.findAll('.clarification-option-btn')
    expect(buttons.length).toBe(2)
    await buttons[0].trigger('click')

    const emitted = wrapper.emitted('selectClarificationOption')
    expect(emitted).toHaveLength(1)
    expect(emitted?.[0]?.[0]).toEqual({
      option: {
        id: 'cand_01',
        label: 'PipelineWebGL',
        filter: { entity_name: 'PipelineWebGL' },
        source: 'backbone',
        canonical_name: 'PipelineWebGL',
        binding_status: 'canonical',
      },
      kind: 'option',
    })
  })
})

describe('ChatMessage execution presentation', () => {
  it('renders Agent blocks stream, final answer, and sources without Pipeline status', () => {
    const wrapper = mount(ChatMessage, {
      props: {
        role: 'assistant',
        mode: 'agent',
        content: '这是经审核的答案。',
        loading: false,
        status: '正在检索知识库…',
        blocks: [
          {
            id: 'block_1',
            kind: 'tool',
            type: 'tool',
            sequence: 1,
            toolCallKey: 'tool:1:retrieve_kb',
            tool: 'retrieve_kb',
            toolName: 'retrieve_kb',
            label: '知识库检索',
            description: '检索流水线部署',
            status: 'completed',
            isStreaming: false,
          },
          {
            id: 'markdown_2',
            kind: 'markdown',
            type: 'markdown',
            sequence: 2,
            text: '这是经审核的答案。',
            markdown: '这是经审核的答案。',
            status: 'final',
          },
        ],
        sources: [
          {
            content: '来源正文',
            metadata: { source: '部署手册.md', citation_id: 1, title: '部署手册' },
          },
        ],
        traceId: 'trace_123',
      },
      global: {
        stubs: {
          EvidencePanel: true,
        },
      },
    })

    expect(wrapper.findComponent({ name: 'AgentStepStream' }).exists()).toBe(true)
    expect(wrapper.get('[data-testid="final-answer"]').text()).toContain('这是经审核的答案。')
    expect(wrapper.get('[data-testid="answer-sources"]').text()).toContain('部署手册')
    expect(wrapper.find('[data-testid="pipeline-status"]').exists()).toBe(false)
    expect(wrapper.find('.trace-detail-btn').exists()).toBe(true)
  })

  it('keeps Linear stage status and ignores Agent timeline presentation', () => {
    const wrapper = mount(ChatMessage, {
      props: {
        role: 'assistant',
        mode: 'linear',
        content: '',
        loading: true,
        status: '正在检索知识库…',
      },
      global: {
        stubs: {
          EvidencePanel: true,
        },
      },
    })

    expect(wrapper.get('[data-testid="pipeline-status"]').text()).toContain('正在检索知识库…')
    expect(wrapper.findComponent({ name: 'AgentStepStream' }).exists()).toBe(false)
  })

  it('reveals streamed provider thinking in Linear mode', async () => {
    const wrapper = mount(ChatMessage, {
      props: {
        role: 'assistant',
        mode: 'linear',
        content: '',
        loading: true,
        thinking: '先核对知识库证据。',
      },
      global: {
        stubs: {
          EvidencePanel: true,
        },
      },
    })

    expect(wrapper.get('.thinking-toggle').text()).toContain('正在思考')
    expect(wrapper.get('.thinking-content').isVisible()).toBe(false)
    await wrapper.get('.thinking-toggle').trigger('click')
    expect(wrapper.get('.thinking-content').text()).toContain('先核对知识库证据。')
  })
})
