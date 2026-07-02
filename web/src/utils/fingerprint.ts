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
  const parts = [
    navigator.userAgent,
    navigator.language,
    screen.width,
    screen.height,
    Intl.DateTimeFormat().resolvedOptions().timeZone,
  ]
  const raw = parts.join('||')
  return simpleHash(raw)
}

export function getFingerprint(): string {
  let fp = localStorage.getItem(STORAGE_KEY)
  if (!fp) {
    fp = generate()
    localStorage.setItem(STORAGE_KEY, fp)
  }
  return fp
}
