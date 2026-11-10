export interface ViewportRect { left: number; top: number; width: number; height: number }

/** Stage-local rectangle below the actual floating toolbar, with room for sockets. */
export function visibleViewport(stage: ViewportRect, toolbar?: ViewportRect | null, padding = 24) {
  const intersects = toolbar && toolbar.width > 0 && toolbar.height > 0
    && toolbar.left < stage.left + stage.width && toolbar.left + toolbar.width > stage.left
    && toolbar.top < stage.top + stage.height && toolbar.top + toolbar.height > stage.top
  const coveredTop = intersects ? Math.max(0, toolbar.top + toolbar.height - stage.top) : 0
  const x = Math.min(padding, stage.width / 2)
  const y = Math.min(coveredTop + padding, stage.height)
  return { x, y, width: Math.max(0, stage.width - x - padding), height: Math.max(0, stage.height - y - padding) }
}
