import { describe, it, expect } from 'vitest'
import { archiveClarificationInteraction, bindClarificationTrace, buildChatHistoryPayload } from './chatHistory'
import type { Message } from '../types'

describe('chatHistory.ts payload serialization', () => {
  it('extracts trace_id and clarification interaction facts for cross-turn persistence', () => {
    const messages: Message[] = [
      {
        id: 'm1',
        role: 'user',
        content: '帮我查找构建工具',
      },
      {
        id: 'm2',
        role: 'assistant',
        content: '请选择具体产品：',
        trace_id: 'trace_turn_1',
        clarification: {
          ask_question: '请选择具体产品：',
          clarification_snapshot_id: 'snap_001',
          selectedId: 'opt_1',
          published_trace_id: 'trace_turn_1_pub',
          response_trace_id: 'trace_turn_1_cb',
          options: [
            {
              id: 'opt_1',
              label: 'PipelineBuilder',
              filter: {},
            },
            {
              id: 'other',
              label: '以上都不是',
              filter: {},
            },
          ],
        },
        sources: [
          {
            content: 'PipelineBuilder 介绍',
            metadata: {
              source: 'pipeline.md',
              chunk_id: 'c1',
              citation_id: 1,
            },
          },
        ],
      },
    ]

    const history = buildChatHistoryPayload(messages)
    expect(history.length).toBe(2)
    expect(history[0]).toEqual({
      role: 'user',
      content: '帮我查找构建工具',
    })

    const assistantTurn = history[1]
    expect(assistantTurn.role).toBe('assistant')
    expect(assistantTurn.trace_id).toBe('trace_turn_1')
    expect(assistantTurn.sources?.length).toBe(1)
    expect(assistantTurn.sources?.[0].file_name).toBe('pipeline.md')
    expect(assistantTurn.clarification).toEqual({
      question: '请选择具体产品：',
      selected: 'PipelineBuilder',
      option_id: 'opt_1',
      snapshot_id: 'snap_001',
      selection_kind: 'option',
      free_text: undefined,
      published_trace_id: 'trace_turn_1_pub',
      response_trace_id: 'trace_turn_1_cb',
    })
  })

  it('keeps provenance correct across Clarify A -> Clarify B on the same assistant message', () => {
    const msg: Message = {
      id: 'm2',
      role: 'assistant',
      content: '',
      trace_id: 'trace_A',
      clarification: {
        ask_question: '第一次请选择产品',
        clarification_snapshot_id: 'snap_A',
        options: [{ id: 'opt_A', label: 'PipelineBuilder', filter: {} }],
        selectedId: 'opt_A',
        selection_kind: 'option',
        published_trace_id: 'trace_A',
      },
    }

    const archived = archiveClarificationInteraction(msg.clarification!)
    msg.clarification = {
      ask_question: '还需要确认版本',
      clarification_snapshot_id: 'snap_B',
      options: [{ id: 'opt_B', label: 'V2', filter: {} }],
      published_trace_id: undefined,
      history: archived,
    }

    bindClarificationTrace(msg, 'trace_B', {
      respondingSnapshotId: 'snap_A',
      clarificationPublishedInThisRequest: true,
    })

    expect(msg.trace_id).toBe('trace_B')
    expect(msg.clarification.published_trace_id).toBe('trace_B')
    expect(msg.clarification.history?.[0].published_trace_id).toBe('trace_A')
    expect(msg.clarification.history?.[0].response_trace_id).toBe('trace_B')

    const history = buildChatHistoryPayload([msg])
    expect(history[0].clarification?.published_trace_id).toBe('trace_B')
    expect(history[0].clarification_history?.[0].published_trace_id).toBe('trace_A')
    expect(history[0].clarification_history?.[0].response_trace_id).toBe('trace_B')
  })
})
