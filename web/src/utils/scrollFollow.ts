export const AUTO_FOLLOW_BOTTOM_THRESHOLD_PX = 96

export interface ScrollMetrics {
  scrollTop: number
  scrollHeight: number
  clientHeight: number
}

/**
 * Whether the viewport is close enough to the bottom that streaming output may
 * continue auto-following without taking scroll control away from the user.
 */
export function isNearScrollBottom(
  metrics: ScrollMetrics,
  thresholdPx = AUTO_FOLLOW_BOTTOM_THRESHOLD_PX,
): boolean {
  const distanceToBottom = metrics.scrollHeight - metrics.clientHeight - metrics.scrollTop
  return distanceToBottom <= thresholdPx
}
