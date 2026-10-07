/**
 * 把元素滚到所在面板的可见范围内，只滚动最近的可滚动面板，不带动整个页面。
 * 手机宽度下面板不限高度，此时不滚动，避免识别完成后页面突然跳走。
 */
export function revealInPane(element: Element | null | undefined, margin = 16) {
  if (!(element instanceof HTMLElement)) return
  let pane = element.parentElement
  while (pane && pane !== document.body && pane !== document.documentElement) {
    const { overflowY } = getComputedStyle(pane)
    if ((overflowY === 'auto' || overflowY === 'scroll') && pane.scrollHeight > pane.clientHeight + 1) break
    pane = pane.parentElement
  }
  if (!pane || pane === document.body || pane === document.documentElement) return
  const box = element.getBoundingClientRect()
  const frame = pane.getBoundingClientRect()
  const behavior: ScrollBehavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
  if (box.top < frame.top + margin) pane.scrollBy({ top: box.top - frame.top - margin, behavior })
  else if (box.bottom > frame.bottom - margin) pane.scrollBy({ top: Math.min(box.bottom - frame.bottom + margin, box.top - frame.top - margin), behavior })
}
