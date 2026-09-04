import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  fetchServerSessions: vi.fn(),
  createServerSession: vi.fn(),
  saveServerSession: vi.fn(),
  renameServerSession: vi.fn(),
  setActiveServerSession: vi.fn(),
  deleteServerSession: vi.fn(),
  fetchServerSessionDetail: vi.fn(),
}))

vi.mock('../api', () => ({
  fetchServerSessions: apiMocks.fetchServerSessions,
  createServerSession: apiMocks.createServerSession,
  saveServerSession: apiMocks.saveServerSession,
  renameServerSession: apiMocks.renameServerSession,
  setActiveServerSession: apiMocks.setActiveServerSession,
  deleteServerSession: apiMocks.deleteServerSession,
  fetchServerSessionDetail: apiMocks.fetchServerSessionDetail,
  loadServerChat: vi.fn(),
  saveServerChat: vi.fn(),
  deleteServerChat: vi.fn(),
}))

import {
  loadChatSessions,
  setActiveChatSession,
  createChatSession,
  deleteChatSession,
  loadSessionMessages,
  saveSessionState,
} from './storage'

describe('storage.ts session management', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('uses server as single source of truth and does not revive old localStorage when server returns empty array', async () => {
    // 模拟本地存在旧的残留缓存
    localStorage.setItem(
      'rag-knowledge-sessions',
      JSON.stringify([{ id: 'old_zombie_id', title: '被删除的旧会话', message_count: 5 }]),
    )
    localStorage.setItem('rag-knowledge-active-session-id', 'old_zombie_id')

    // 服务端明确返回无会话
    apiMocks.fetchServerSessions.mockResolvedValue({
      active_session_id: null,
      sessions: [],
    })

    const result = await loadChatSessions()

    expect(result.sessions).toEqual([])
    expect(result.activeSessionId).toBeNull()

    // 验证本地 localStorage 也被同步更新为空，杜绝复活
    const stored = JSON.parse(localStorage.getItem('rag-knowledge-sessions') || '[]')
    expect(stored).toEqual([])
  })

  it('falls back to local cache when server throws a network error', async () => {
    localStorage.setItem(
      'rag-knowledge-sessions',
      JSON.stringify([{ id: 'cached_id', title: '离线缓存会话', message_count: 1 }]),
    )
    localStorage.setItem('rag-knowledge-active-session-id', 'cached_id')

    apiMocks.fetchServerSessions.mockRejectedValue(new Error('Network Error / 502'))

    const result = await loadChatSessions()

    expect(result.sessions.length).toBe(1)
    expect(result.sessions[0].id).toBe('cached_id')
    expect(result.activeSessionId).toBe('cached_id')
  })

  it('does not mask a server response error with stale local cache', async () => {
    localStorage.setItem(
      'rag-knowledge-sessions',
      JSON.stringify([{ id: 'cached_id', title: '离线缓存会话', message_count: 1 }]),
    )
    apiMocks.fetchServerSessions.mockRejectedValue({ isAxiosError: true, response: { status: 500 } })

    await expect(loadChatSessions()).rejects.toMatchObject({ response: { status: 500 } })
  })

  it('setActiveChatSession updates both local and server active session', async () => {
    apiMocks.setActiveServerSession.mockResolvedValue({ message: 'ok' })

    await setActiveChatSession('sess_123')

    expect(localStorage.getItem('rag-knowledge-active-session-id')).toBe('sess_123')
    expect(apiMocks.setActiveServerSession).toHaveBeenCalledWith(expect.any(String), 'sess_123')
  })

  it('does not update local active session when server persistence fails', async () => {
    localStorage.setItem('rag-knowledge-active-session-id', 'previous_session')
    apiMocks.setActiveServerSession.mockRejectedValue(new Error('Network Error'))

    await expect(setActiveChatSession('sess_123')).rejects.toThrow('Network Error')
    expect(localStorage.getItem('rag-knowledge-active-session-id')).toBe('previous_session')
  })

  it('removes a locally cached session when the server reports it no longer exists', async () => {
    localStorage.setItem(
      'rag-knowledge-sessions',
      JSON.stringify([{ id: 'deleted_session', title: '已删除会话' }]),
    )
    localStorage.setItem('rag-knowledge-active-session-id', 'deleted_session')
    localStorage.setItem('rag-knowledge-msgs:deleted_session', JSON.stringify([{ id: 'stale' }]))
    apiMocks.setActiveServerSession.mockRejectedValue({ response: { status: 404 } })

    await expect(setActiveChatSession('deleted_session')).rejects.toMatchObject({ response: { status: 404 } })

    expect(localStorage.getItem('rag-knowledge-active-session-id')).toBeNull()
    expect(localStorage.getItem('rag-knowledge-msgs:deleted_session')).toBeNull()
    expect(JSON.parse(localStorage.getItem('rag-knowledge-sessions') || '[]')).toEqual([])
  })

  it('does not restore local messages when the server confirms a session was deleted', async () => {
    localStorage.setItem(
      'rag-knowledge-msgs:deleted_session',
      JSON.stringify([{ id: 'stale', role: 'user', content: '过期消息', hasImage: false }]),
    )
    apiMocks.fetchServerSessionDetail.mockResolvedValue(null)

    await expect(loadSessionMessages('deleted_session')).resolves.toEqual([])
  })

  it('createChatSession calls server and stores session locally', async () => {
    apiMocks.createServerSession.mockResolvedValue({ id: 'sess_new', title: '新会话' })

    const created = await createChatSession('新会话', 'sess_new')

    expect(created.id).toBe('sess_new')
    expect(apiMocks.createServerSession).toHaveBeenCalledWith(expect.any(String), '新会话', 'sess_new')

    const stored = JSON.parse(localStorage.getItem('rag-knowledge-sessions') || '[]')
    expect(stored.length).toBe(1)
    expect(stored[0].id).toBe('sess_new')
  })

  it('recreates the same session id and retries when save reports 404', async () => {
    apiMocks.saveServerSession
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockResolvedValueOnce({ message: 'ok' })
    apiMocks.createServerSession.mockResolvedValue({ id: 'sess_missing', title: '恢复会话' })

    await saveSessionState(
      'sess_missing',
      [{ id: 'msg_1', role: 'user', content: '继续提问' } as any],
      '恢复会话',
    )

    expect(apiMocks.createServerSession).toHaveBeenCalledWith(
      expect.any(String),
      '恢复会话',
      'sess_missing',
    )
    expect(apiMocks.saveServerSession).toHaveBeenCalledTimes(2)
    expect(apiMocks.saveServerSession.mock.calls[1][1]).toBe('sess_missing')
  })

  it('deleteChatSession calls server delete and cleans local cache', async () => {
    localStorage.setItem(
      'rag-knowledge-sessions',
      JSON.stringify([
        { id: 'sess_1', title: '会话1' },
        { id: 'sess_2', title: '会话2' },
      ]),
    )
    localStorage.setItem('rag-knowledge-active-session-id', 'sess_1')
    apiMocks.deleteServerSession.mockResolvedValue({ message: '已删除' })

    await deleteChatSession('sess_1')

    expect(apiMocks.deleteServerSession).toHaveBeenCalledWith(expect.any(String), 'sess_1')
    const stored = JSON.parse(localStorage.getItem('rag-knowledge-sessions') || '[]')
    expect(stored.length).toBe(1)
    expect(stored[0].id).toBe('sess_2')
  })

  it('normalizes object trace_id when loading session messages', async () => {
    apiMocks.fetchServerSessionDetail.mockResolvedValue({
      id: 'sess_trace',
      title: 'Trace 会话',
      messages: [
        {
          id: 'msg_1',
          role: 'assistant',
          content: '回答内容',
          trace_id: { trace_id: 'trace_abc_123' },
        },
        {
          id: 'msg_2',
          role: 'assistant',
          content: '普通回答',
          trace_id: 'trace_xyz_789',
        },
      ],
    })

    const msgs = await loadSessionMessages('sess_trace')
    expect(msgs).toHaveLength(2)
    expect(msgs[0].trace_id).toBe('trace_abc_123')
    expect(typeof msgs[0].trace_id).toBe('string')
    expect(msgs[1].trace_id).toBe('trace_xyz_789')
  })
})
