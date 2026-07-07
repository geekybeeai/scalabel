/**
 * Transient, non-Redux state for the one-shot polyline cut (scissor) tool.
 *
 * Armed from the Viewer2D toolbar button or the Label2dCanvas context menu;
 * consumed by Label2dCanvas, which performs the cut on the next left-click.
 * Mirrors the pointer_pan_state.ts pattern, plus a minimal listener list so
 * the toolbar button can re-render when the mode is cleared from elsewhere
 * (Escape, a successful one-shot cut, item navigation).
 */
let cutMode = false

const listeners = new Set<() => void>()

/** Whether the cut tool is currently armed. */
export function isCutMode(): boolean {
  return cutMode
}

/**
 * Arm or disarm the cut tool. Notifies listeners only on an actual change.
 *
 * @param on whether the tool should be armed
 */
export function setCutMode(on: boolean): void {
  if (cutMode === on) {
    return
  }
  cutMode = on
  listeners.forEach((listener) => listener())
}

/**
 * Subscribe to cut-mode changes.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onCutModeChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
