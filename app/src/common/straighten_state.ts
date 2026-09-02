/**
 * Transient, non-Redux state for the one-shot straighten tool.
 *
 * Mirrors `cut_state.ts` and `curve_cut_state.ts`: armed from the Viewer2D
 * toolbar, consumed by Label2dCanvas on the next left-click.
 *
 * A dedicated tool rather than a key chord on purpose. The old C-key
 * curve-to-straight toggle was removed because it fired on stale key state and
 * destroyed curves during ordinary adjust clicks; an explicitly armed tool
 * cannot misfire that way.
 */
let straightenMode = false

const listeners = new Set<() => void>()

/** Whether the straighten tool is currently armed. */
export function isStraightenMode(): boolean {
  return straightenMode
}

/**
 * Arm or disarm the straighten tool. Notifies only on an actual change.
 *
 * @param on whether the tool should be armed
 */
export function setStraightenMode(on: boolean): void {
  if (straightenMode === on) {
    return
  }
  straightenMode = on
  listeners.forEach((listener) => listener())
}

/**
 * Subscribe to straighten-mode changes.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onStraightenModeChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
