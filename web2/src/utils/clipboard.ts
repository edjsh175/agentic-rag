/**
 * 将文本复制到系统剪贴板（兼容安全上下文与非安全上下文环境）
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (!text) return false

  // 优先尝试现代 Clipboard API
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 若受权限阻拦则回退到 input select 方式
    }
  }

  // 回退方案：创建临时 textarea 元素执行 document.execCommand('copy')
  try {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.top = '-9999px'
    textarea.style.left = '-9999px'
    textarea.style.opacity = '0'
    textarea.setAttribute('readonly', '')
    document.body.appendChild(textarea)
    textarea.focus()
    textarea.select()
    const successful = document.execCommand('copy')
    document.body.removeChild(textarea)
    return successful
  } catch (err) {
    console.error('复制失败:', err)
    return false
  }
}
