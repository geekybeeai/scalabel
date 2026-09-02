/**
 * Transient, non-Redux state for the one-shot curve-aware cut tool.
 *
 * Mirrors `cut_state.ts` exactly, but arms a separate tool: the plain scissors
 * refuse curved spans on purpose (so a curve cannot be cut by accident), while
 * this one cuts whatever it lands on, subdividing a bezier when that is what
 * was hit. Two independent flags rather than a mode on one flag, so the two
 * toolbar buttons can be armed and cleared without interfering.
 */
let curveCutMode = false

const listeners = new Set<() => void>()

/** Whether the curve-aware cut tool is currently armed. */
export function isCurveCutMode(): boolean {
  return curveCutMode
}

/**
 * Arm or disarm the curve-aware cut tool. Notifies only on an actual change.
 *
 * @param on whether the tool should be armed
 */
export function setCurveCutMode(on: boolean): void {
  if (curveCutMode === on) {
    return
  }
  curveCutMode = on
  listeners.forEach((listener) => listener())
}

/**
 * Subscribe to curve-cut-mode changes.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onCurveCutModeChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
