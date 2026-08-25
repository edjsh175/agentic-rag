import test from 'node:test'
import assert from 'node:assert/strict'

import { citationLabel, decorateCitations } from './citations.ts'

const source = (id, metadata = {}) => ({
  content: 'source excerpt',
  metadata: {
    source: 'Linux--如何安装rockyLinux9虚拟机.md',
    citation_id: id,
    ...metadata,
  },
})

test('formats a PDF citation with a cleaned document name and page number', () => {
  assert.equal(
    citationLabel(source(1, { source: '安装手册.pdf', page_label: '12' })),
    '《安装手册》· 第 12 页',
  )
})

test('falls back from missing page number to section and then document name', () => {
  assert.equal(
    citationLabel(source(1, { page_label: '无页码', section_title: '下载镜像' })),
    '《Linux--如何安装rockyLinux9虚拟机》· 下载镜像',
  )
  assert.equal(
    citationLabel(source(1, { page_label: '无页码' })),
    '《Linux--如何安装rockyLinux9虚拟机》',
  )
})

test('decorates adjacent and repeated known citation markers', () => {
  const html = decorateCitations('步骤一[1][2]，再次说明[1]。', [
    source(1),
    source(2, { source: '补充资料.docx' }),
  ])

  assert.equal((html.match(/data-citation-id="1"/g) || []).length, 2)
  assert.equal((html.match(/data-citation-id="2"/g) || []).length, 1)
  assert.match(html, /《补充资料》/)
})

test('leaves unknown markers, inline code, fenced code, and markdown links unchanged', () => {
  const markdown = '未知[9]，`arr[1]`，链接[1](https://example.com)\n```js\nconst value = arr[1]\n```'
  const decorated = decorateCitations(markdown, [source(1)])

  assert.match(decorated, /未知\[9\]/)
  assert.match(decorated, /`arr\[1\]`/)
  assert.match(decorated, /\[1\]\(https:\/\/example\.com\)/)
  assert.match(decorated, /const value = arr\[1\]/)
  assert.doesNotMatch(decorated, /data-citation-id/)
})
