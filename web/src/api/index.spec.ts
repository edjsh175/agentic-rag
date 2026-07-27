import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ post: vi.fn() }))

vi.mock('axios', () => ({
  default: {
    create: () => ({
      interceptors: { response: { use: vi.fn() } },
      post: mocks.post,
    }),
  },
}))

import { queryClarify, uploadDocument } from './index'

describe('uploadDocument', () => {
  beforeEach(() => {
    mocks.post.mockReset()
    mocks.post.mockResolvedValue({ data: { file_name: 'manual.md' } })
  })

  it('sends the uploader-selected document profile', async () => {
    const file = new File(['content'], 'manual.md', { type: 'text/markdown' })

    await uploadDocument(file, '文章附件', 'procedure')

    const form = mocks.post.mock.calls[0][1] as FormData
    expect(form.get('document_profile')).toBe('procedure')
  })
})

describe('queryClarify', () => {
  beforeEach(() => {
    mocks.post.mockReset()
  })

  it('posts clarification request with parameters and returns response data', async () => {
    const mockData = {
      needs_clarification: true,
      ask_question: '您提到的「管线发布」指哪个工具？',
      trigger: '管线发布',
      reason: 'entity_ambiguity',
      options: [
        { id: 'a', label: 'PipelineBuilder', filter: { doc_category: 'StampTools' } },
        { id: 'b', label: '管线发布服务', filter: { doc_category: 'StampServer' } },
      ],
    }
    mocks.post.mockResolvedValue({ data: mockData })

    const res = await queryClarify('管线发布怎么配置？', '全部', '全部知识库')

    expect(mocks.post).toHaveBeenCalledWith(
      '/query/clarify',
      {
        question: '管线发布怎么配置？',
        doc_category: undefined,
        kb_name: undefined,
      },
      { signal: undefined },
    )
    expect(res).toEqual(mockData)
  })
})
