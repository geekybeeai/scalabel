/**
 * Transient, non-Redux state for the one-shot simplify tool.
 *
 * Mirrors the other tool-state modules: armed from the Viewer2D toolbar,
 * consumed by Label2dCanvas on the next left-click, which simplifies the line
 * under the cursor.
 */
let simplifyMode = false

const listeners = new Set<() => void>()

/** Whether the simplify tool is currently armed. */
export function isSimplifyMode(): boolean {
  return simplifyMode
}

/**
 * Arm or disarm the simplify tool. Notifies only on an actual change.
 *
 * @param on whether the tool should be armed
 */
export function setSimplifyMode(on: boolean): void {
  if (simplifyMode === on) {
    return
  }
  simplifyMode = on
  listeners.forEach((listener) => listener())
}

/**
 * Subscribe to simplify-mode changes.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onSimplifyModeChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
