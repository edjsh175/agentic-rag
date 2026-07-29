/**
 * 轻量级前端 API 数据缓存层 (SWR / Memory Data Cache)
 *
 * 将 API 请求返回的数据在纯 JS 内存中缓存（不依赖重型 Vue DOM / VNode），
 * 避免切页时组件卸载导致重新发起 HTTP 请求等待，同时保证 UI 彻底卸载时不卡死。
 */

interface CacheEntry<T> {
  data: T
  timestamp: number
}

const memoryCache = new Map<string, CacheEntry<any>>()

/**
 * 带数据缓存的 API 请求包装函数
 * @param key 缓存唯一标识符
 * @param fetcher 实际的网络请求 Promise 工厂函数
 * @param ttlMs 缓存有效期 (毫秒)，默认 5 分钟 (300,000 ms)
 * @param forceRefresh 是否强制刷新网络数据
 */
export async function withDataCache<T>(
  key: string,
  fetcher: () => Promise<T>,
  ttlMs = 300000,
  forceRefresh = false,
): Promise<T> {
  if (!forceRefresh && memoryCache.has(key)) {
    const entry = memoryCache.get(key)!
    if (ttlMs === 0 || Date.now() - entry.timestamp < ttlMs) {
      return entry.data
    }
  }
  const freshData = await fetcher()
  memoryCache.set(key, { data: freshData, timestamp: Date.now() })
  return freshData
}

/**
 * 使指定 Key 或匹配指定前缀的 API 数据缓存失效
 * @param keyOrPrefix 缓存 Key 或 Key 前缀 (为空时全量清空)
 */
export function invalidateDataCache(keyOrPrefix?: string): void {
  if (!keyOrPrefix) {
    memoryCache.clear()
    return
  }
  for (const k of memoryCache.keys()) {
    if (k === keyOrPrefix || k.startsWith(keyOrPrefix)) {
      memoryCache.delete(k)
    }
  }
}
