/**
 * Transient, non-Redux state for the disjoint tool.
 *
 * A one-shot armed mode, like the cut tools: arm it, click a seam, and the tool
 * disarms itself so a stray second click cannot break a line unintentionally.
 *
 * Deliberately not persisted, so no session starts with a tool silently armed.
 */

let disjointMode = false

const listeners = new Set<() => void>()

/** Whether the disjoint tool is armed. */
export function isDisjointMode(): boolean {
  return disjointMode
}

/**
 * Arm or disarm the disjoint tool. Notifies only on an actual change.
 *
 * @param on whether the tool should be armed
 */
export function setDisjointMode(on: boolean): void {
  if (disjointMode === on) {
    return
  }
  disjointMode = on
  listeners.forEach((listener) => listener())
}

/**
 * Subscribe to disjoint-tool changes.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onDisjointChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
