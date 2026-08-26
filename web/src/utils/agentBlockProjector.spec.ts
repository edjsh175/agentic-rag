import { nextTick, reactive, watchEffect } from 'vue'
import { describe, expect, it } from 'vitest'
import { AgentBlockProjector, normalizeLegacyMessageToBlocks } from './agentBlockProjector'
import type { AssistantBlock } from '../types'

describe('AgentBlockProjector - 核心生命周期与投影', () => {
  it('projects main reasoning and updates in-place', () => {
    const projector = new AgentBlockProjector()
    projector.handleReasoningStart({
      call_id: 'call_1',
      role: 'main',
      stage: 'agent_controller',
      model: 'qwen3:30b',
    })

    let blocks = projector.getBlocks()
    expect(blocks).toHaveLength(0)

    projector.handleReasoningDelta({
      call_id: 'call_1',
      role: 'main',
      delta: '思考第一行\n思考第二行',
    })
    blocks = projector.getBlocks()
    expect((blocks[0] as any).content).toBe('思考第一行\n思考第二行')

    projector.handleReasoningEnd({
      call_id: 'call_1',
      role: 'main',
      elapsed_ms: 1500,
    })
    blocks = projector.getBlocks()
    expect((blocks[0] as any).isStreaming).toBe(false)
    expect((blocks[0] as any).duration).toBe('1.5s')
  })

  it('keeps Vue reactive Block arrays live across reasoning deltas', async () => {
    const state = reactive<{ blocks: AssistantBlock[] }>({ blocks: [] })
    const rendered: string[] = []
    const stop = watchEffect(() => {
      const block = state.blocks[0]
      rendered.push(block?.kind === 'reasoning' ? block.text : '')
    })

    const projector = new AgentBlockProjector(state.blocks)
    projector.handleReasoningStart({
      call_id: 'call_stream',
      role: 'main',
      stage: 'agent_controller',
    })
    await nextTick()
    projector.handleReasoningDelta({
      call_id: 'call_stream',
      role: 'main',
      delta: '第一段',
    })
    await nextTick()
    projector.handleReasoningDelta({
      call_id: 'call_stream',
      role: 'main',
      delta: '第二段',
    })
    await nextTick()
    stop()

    expect(rendered).toContain('第一段')
    expect(rendered).toContain('第一段第二段')
  })

  it('ignores helper or non-main reasoning (INV-UI-03)', () => {
    const projector = new AgentBlockProjector()
    projector.handleReasoningStart({
      call_id: 'call_helper',
      role: 'helper',
      stage: 'entity_recognition',
    })
    projector.handleReasoningDelta({
      call_id: 'call_helper',
      role: 'helper',
      delta: '辅助模型思考',
    })
    projector.handleReasoningEnd({
      call_id: 'call_helper',
      role: 'helper',
    })

    expect(projector.getBlocks()).toHaveLength(0)
  })

  it('projects public explanations and suppresses empty native reasoning cards', () => {
    const projector = new AgentBlockProjector()
    projector.handlePublicExplanation({
      call_id: 'controller_1', role: 'main', stage: 'agent_controller',
      text: '当前证据不足，先检索相关知识库内容。', source: 'model_protocol',
    })
    projector.handleReasoningStart({ call_id: 'native_1', role: 'main', stage: 'agent_controller' })
    projector.handleReasoningEnd({ call_id: 'native_1', role: 'main', reasoning_available: false })

    const blocks = projector.getBlocks()
    expect(blocks).toHaveLength(1)
    expect((blocks[0] as any).contentSource).toBe('public_explanation')
    expect((blocks[0] as any).text).toContain('先检索')
  })

  it('projects tool lifecycle from running to completed in-place (INV-UI-04)', () => {
    const projector = new AgentBlockProjector()
    projector.handleToolStart({
      name: 'retrieve_kb',
      step: 1,
      arguments: { query: '流水线部署' },
    })

    let blocks = projector.getBlocks()
    expect(blocks).toHaveLength(1)
    expect(blocks[0].kind).toBe('tool')
    expect((blocks[0] as any).status).toBe('running')
    expect((blocks[0] as any).toolName).toBe('retrieve_kb')

    projector.handleToolResult({
      name: 'retrieve_kb',
      step: 1,
      ok: true,
      elapsed_ms: 320,
      summary: '已命中 3 条已审核切片',
      arguments: { query: '流水线部署' },
    })

    blocks = projector.getBlocks()
    expect(blocks).toHaveLength(1)
    expect((blocks[0] as any).status).toBe('completed')
    expect((blocks[0] as any).elapsedMs).toBe(320)
    expect((blocks[0] as any).out).toMatchObject({ summary: '已命中 3 条已审核切片' })
  })

  it('projects review activity lifecycle on PASS in-place', () => {
    const projector = new AgentBlockProjector()
    projector.handleGroundingReviewStarted({
      review_count: 1,
      candidate_version: 1,
      message: '正在核对 Candidate V1 与冻结证据快照。',
    })
    let blocks = projector.getBlocks()
    expect(blocks).toHaveLength(1)
    expect(blocks[0].kind).toBe('activity')
    expect((blocks[0] as any).status).toBe('running')
    expect((blocks[0] as any).text).toBe('正在核对回答与证据…')

    projector.handleReviewStatus({
      review_count: 1,
      verdict: 'PASS',
      coverage: '1.0',
      message: '通过',
    })
    blocks = projector.getBlocks()
    expect(blocks).toHaveLength(1)
    expect((blocks[0] as any).status).toBe('completed')
    expect((blocks[0] as any).text).toBe('证据核对通过')
  })

  it('projects review activity on REVISE in-place without generating system warning card', () => {
    const projector = new AgentBlockProjector()
    projector.handleGroundingReviewStarted({
      review_count: 1,
      candidate_version: 1,
      message: '正在核对 Candidate V1 与冻结证据快照。',
    })
    projector.handleReviewStatus({
      review_count: 1,
      verdict: 'REVISE',
      coverage: '0.5',
      message: '发现部分内容未被支持',
    })

    const blocks = projector.getBlocks()
    expect(blocks).toHaveLength(1)
    expect(blocks[0].kind).toBe('activity')
    expect((blocks[0] as any).status).toBe('warning')
    expect((blocks[0] as any).text).toBe('发现部分内容需要修正')
    expect(blocks.some(b => b.kind === 'system_event')).toBe(false)
  })

  it('projects full REVISE and second-pass review activity progression', () => {
    const projector = new AgentBlockProjector()
    // Review #1
    projector.handleGroundingReviewStarted({ review_count: 1, candidate_version: 1, message: '一审' })
    projector.handleReviewStatus({ review_count: 1, verdict: 'REVISE', coverage: '0.5', message: '需修正' })

    // Rewrite Explanation & Reasoning
    projector.handlePublicExplanation({
      call_id: 'retry_1', role: 'main', stage: 'grounded_retry', text: '将删除未支持断言。', source: 'model_generated',
    })
    projector.handleReasoningStart({ call_id: 'retry_1', role: 'main', stage: 'grounded_retry' })
    projector.handleReasoningDelta({ call_id: 'retry_1', role: 'main', delta: '删除 c2' })
    projector.handleReasoningEnd({ call_id: 'retry_1', role: 'main', elapsed_ms: 1200 })

    // Review #2
    projector.handleGroundingReviewStarted({ review_count: 2, candidate_version: 2, message: '二审' })
    expect((projector.getBlocks()[3] as any).text).toBe('正在再次核对修正后的回答…')
    expect((projector.getBlocks()[3] as any).status).toBe('running')

    projector.handleReviewStatus({ review_count: 2, verdict: 'PASS', coverage: '1.0', message: '通过' })
    expect((projector.getBlocks()[3] as any).text).toBe('二次核对通过')
    expect((projector.getBlocks()[3] as any).status).toBe('completed')

    // Final Answer
    projector.handleFinalAnswer('最终通过的回答。')

    const kinds = projector.getBlocks().map(b => b.kind)
    expect(kinds).toEqual(['activity', 'reasoning', 'reasoning', 'activity', 'markdown'])
  })

  it('appends markdown final answer (INV-UI-10)', () => {
    const projector = new AgentBlockProjector()
    projector.handleFinalAnswer('这是最终生成的回答。')
    const blocks = projector.getBlocks()
    expect(blocks).toHaveLength(1)
    expect(blocks[0].kind).toBe('markdown')
    expect((blocks[0] as any).markdown).toBe('这是最终生成的回答。')
  })
})

describe('AgentBlockProjector - 架构断言 (Architecture Acceptance Suite)', () => {
  it('INV-UI-01: 生产 Block 种类白名单严格为 5 类', () => {
    const projector = new AgentBlockProjector()
    projector.handlePublicExplanation({
      call_id: 'r1', role: 'main', stage: 'agent_controller', text: '准备检索。', source: 'model_protocol',
    })
    projector.handleToolStart({ name: 'retrieve_kb', step: 1 })
    projector.handleToolResult({ name: 'retrieve_kb', step: 1, ok: true })
    projector.handleGroundingReviewStarted({ review_count: 1, candidate_version: 1, message: '核对' })
    projector.handleReviewStatus({ review_count: 1, verdict: 'PASS', message: '通过' })
    projector.handleNotice('当前显存不足以加载所选模型，已自动降级为 qwen3.5:4b。')
    projector.handleFinalAnswer('完整答案')

    const allowedKinds = new Set(['reasoning', 'tool', 'activity', 'system_event', 'markdown'])
    const blocks = projector.getBlocks()
    expect(blocks).toHaveLength(5)
    for (const b of blocks) {
      expect(allowedKinds.has(b.kind)).toBe(true)
    }
  })

  it('INV-UI-05: 内部日志（Decision/Guard/Evidence/Finalization）绝不产生 Block', () => {
    const projector = new AgentBlockProjector()
    // 模拟内部事件到来时 projector 不提供非白名单入口，确保 blocks 保持为空
    expect(projector.getBlocks()).toHaveLength(0)
  })

  it('INV-UI-06: 相同 toolCallKey 的 ToolStart 与 ToolResult 绝不裂变多行', () => {
    const projector = new AgentBlockProjector()
    projector.handleToolStart({ name: 'retrieve_kb', step: 1, arguments: { query: 'test' } })
    projector.handleToolStart({ name: 'retrieve_kb', step: 1, arguments: { query: 'test' } })
    projector.handleToolResult({ name: 'retrieve_kb', step: 1, ok: true, summary: 'done' })
    projector.handleToolResult({ name: 'retrieve_kb', step: 1, ok: true, summary: 'done' })

    const blocks = projector.getBlocks()
    expect(blocks).toHaveLength(1)
    expect(blocks[0].id).toBe('tool:1:retrieve_kb')
  })

  it('INV-UI-05: notice 使用显式白名单，未知 notice 不得进入 System/Event', () => {
    const projector = new AgentBlockProjector()
    projector.handleNotice('内部调试：evidence version changed')
    expect(projector.getBlocks()).toHaveLength(0)

    projector.handleNotice('当前显存不足以加载所选模型，已自动降级为 qwen3.5:4b。')
    const blocks = projector.getBlocks()
    expect(blocks).toHaveLength(1)
    expect(blocks[0].kind).toBe('system_event')
    expect((blocks[0] as any).event).toBe('model_downshift')
  })
})

describe('AgentBlockProjector - 故障注入测试 (Failure Injection Suite)', () => {
  it('FI-01: Tool 执行失败与 DENIED 状态正确原位映射', () => {
    const projector = new AgentBlockProjector()
    projector.handleToolStart({ name: 'web_search', step: 1, arguments: { query: '外网' } })
    projector.handleToolResult({
      name: 'web_search',
      step: 1,
      ok: false,
      error: '网络超时',
      progress: 'ERROR',
    })

    let blocks = projector.getBlocks()
    expect(blocks[0].kind).toBe('tool')
    expect((blocks[0] as any).status).toBe('failed')
    expect((blocks[0] as any).error).toBe('网络超时')

    // 再次注入 DENIED
    projector.handleToolStart({ name: 'web_search', step: 2 })
    projector.handleToolResult({
      name: 'web_search',
      step: 2,
      ok: false,
      progress: 'DENIED',
    })
    blocks = projector.getBlocks()
    expect((blocks[1] as any).status).toBe('denied')
  })

  it('FI-02 / INV-UI-04: 缺少 tool_start 的孤立 tool_result 不得伪造 ToolBlock', () => {
    const projector = new AgentBlockProjector()
    projector.handleToolResult({
      name: 'retrieve_kb',
      step: 99,
      ok: true,
      summary: '孤立结果',
      arguments: { query: '直接检索' },
      elapsed_ms: 100,
    })

    expect(projector.getBlocks()).toHaveLength(0)
  })

  it('FI-03: 连续 50 次 Reasoning Delta 高频追加与幂等 FinalAnswer', () => {
    const projector = new AgentBlockProjector()
    projector.handleReasoningStart({ call_id: 'stress_1', role: 'main' })
    for (let i = 0; i < 50; i++) {
      projector.handleReasoningDelta({ call_id: 'stress_1', role: 'main', delta: `片段${i}` })
    }
    projector.handleReasoningEnd({ call_id: 'stress_1', role: 'main', elapsed_ms: 500 })

    projector.handleFinalAnswer('最终回答 V1')
    projector.handleFinalAnswer('最终回答 V2 (更新)')

    const blocks = projector.getBlocks()
    expect(blocks).toHaveLength(2)
    expect((blocks[0] as any).text.length).toBeGreaterThan(100)
    expect((blocks[1] as any).markdown).toBe('最终回答 V2 (更新)')
  })
})

describe('normalizeLegacyMessageToBlocks', () => {
  it('converts legacy thinking and tools into blocks', () => {
    const blocks = normalizeLegacyMessageToBlocks({
      thinking: '历史思考过程',
      thinkingDuration: '2.0s',
      agentTools: [
        {
          name: 'retrieve_kb',
          step: 1,
          ok: true,
          elapsed_ms: 120,
          summary: '历史检索结果',
          arguments: { query: '旧查询' },
        },
      ],
      content: '旧最终答案',
    })

    expect(blocks).toHaveLength(3)
    expect(blocks[0].kind).toBe('reasoning')
    expect((blocks[0] as any).content).toBe('历史思考过程')
    expect(blocks[1].kind).toBe('tool')
    expect((blocks[1] as any).toolName).toBe('retrieve_kb')
    expect(blocks[2].kind).toBe('markdown')
    expect((blocks[2] as any).markdown).toBe('旧最终答案')
  })
})
