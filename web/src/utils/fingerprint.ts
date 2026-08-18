/**
 * 浏览器指纹工具 —— 生成稳定标识用于服务端对话持久化
 *
 * 首次访问时用浏览器稳定特征算一个哈希，存入 localStorage。
 * 后续直接读取，不清数据就不变。
 */
const STORAGE_KEY = 'rag-device-fingerprint'

function simpleHash(input: string): string {
  let hash = 0
  for (let i = 0; i < input.length; i++) {
    const char = input.charCodeAt(i)
    hash = ((hash << 5) - hash) + char
    hash = hash & hash
  }
  return Math.abs(hash).toString(16).padStart(8, '0')
}

function generate(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID().replace(/-/g, '').slice(0, 16)
  }
  const parts = [
    navigator.userAgent,
    navigator.language,
    Intl.DateTimeFormat().resolvedOptions().timeZone,
    Date.now().toString(),
    Math.random().toString(),
  ]
  return simpleHash(parts.join('||'))
}

export function getFingerprint(): string {
  let fp = localStorage.getItem(STORAGE_KEY)
  if (!fp) {
    fp = generate()
    localStorage.setItem(STORAGE_KEY, fp)
  }
  return fp
}
