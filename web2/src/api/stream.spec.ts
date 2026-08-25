import { afterEach, describe, expect, it, vi } from 'vitest'

import { queryAdminDebugStream, queryKnowledgeStream } from './index'

function sseResponse(payload: string) {
  const encoder = new TextEncoder()
  return {
    ok: true,
    body: new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(payload))
        controller.close()
      },
    }),
  }
}

function streamCallbacks() {
  return {
    onToken: vi.fn(),
    onSources: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('queryKnowledgeStream', () => {
  it('dispatches tool_result and tool_end exactly once each', async () => {
    const callbacks = {
      ...streamCallbacks(),
      onToolResult: vi.fn(),
      onToolEnd: vi.fn(),
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      sseResponse(
        'data: {"type":"tool_result","data":{"name":"retrieve_kb","ok":true}}\n\n' +
        'data: {"type":"tool_end","data":{"name":"retrieve_kb","ok":true}}\n\n' +
        'data: {"type":"done"}',
      ),
    ))

    await queryKnowledgeStream('pipeline', [], callbacks)

    expect(callbacks.onToolResult).toHaveBeenCalledTimes(1)
    expect(callbacks.onToolEnd).toHaveBeenCalledTimes(1)
  })

  it('treats EOF after answer generation starts as an interrupted stream', async () => {
    const callbacks = streamCallbacks()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      sseResponse('data: {"type":"answer_generation_started"}\n\n'),
    ))

    await expect(queryKnowledgeStream('pipeline', [], callbacks)).rejects.toThrow(
      '流式响应在最终答案完成前中断',
    )
    expect(callbacks.onDone).not.toHaveBeenCalled()
    expect(callbacks.onError).toHaveBeenCalledTimes(1)
  })

  it('only reports completion after receiving the terminal event', async () => {
    const callbacks = streamCallbacks()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      sseResponse(
        'data: {"type":"answer_generation_started"}\n\n' +
        'data: {"type":"token","data":"答案"}\n\n' +
        'data: {"type":"done"}',
      ),
    ))

    await queryKnowledgeStream('pipeline', [], callbacks)

    expect(callbacks.onToken).toHaveBeenCalledWith('答案')
    expect(callbacks.onDone).toHaveBeenCalledTimes(1)
    expect(callbacks.onError).not.toHaveBeenCalled()
  })

  it('posts option id, full candidate metadata, and free-text selection kind', async () => {
    const callbacks = streamCallbacks()
    const fetchMock = vi.fn().mockResolvedValue(sseResponse('data: {"type":"done"}'))
    vi.stubGlobal('fetch', fetchMock)
    const options = [
      {
        id: 'other',
        label: '以上都不是',
        filter: {},
        source: 'fixed_other',
        binding_status: 'unresolved',
      },
    ]

    await queryKnowledgeStream(
      'pipelien',
      [],
      callbacks,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      '请选择方向',
      '部署流水线服务',
      'agent',
      {
        optionId: 'other',
        options,
        selectionKind: 'free_text',
        freeText: '部署流水线服务',
      },
    )

    const request = fetchMock.mock.calls[0][1]
    const body = JSON.parse(String(request.body))
    expect(body.clarification_option_id).toBe('other')
    expect(body.clarification_options).toEqual(options)
    expect(body.clarification_selection_kind).toBe('free_text')
    expect(body.clarification_free_text).toBe('部署流水线服务')
  })
})

describe('queryAdminDebugStream', () => {
  it('surfaces an interrupted answer phase instead of resolving as complete', async () => {
    const onStatus = vi.fn()
    const onError = vi.fn()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      sseResponse(
        'data: {"type":"evidence_snapshot_created"}\n\n' +
        'data: {"type":"answer_generation_started"}\n\n',
      ),
    ))

    await expect(queryAdminDebugStream('pipeline', { onStatus, onError })).rejects.toThrow(
      '调试流在最终答案完成前中断',
    )
    expect(onStatus).toHaveBeenLastCalledWith('正在生成最终答案…')
    expect(onError).toHaveBeenCalledTimes(1)
  })
})
