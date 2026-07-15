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

import { uploadDocument } from './index'

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
