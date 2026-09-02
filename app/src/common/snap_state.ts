/**
 * Transient, non-Redux state for the endpoint-snap toggle.
 *
 * Dragging an open line's start/end vertex within 15 screen px of another
 * endpoint normally snaps it there, and releasing merges the two lines when
 * they share a category. That is the desired default, but lane lines often run
 * genuinely close together, and there the snap fights the user: endpoints that
 * must stay distinct get pulled onto each other and silently joined.
 *
 * Turning snapping OFF suppresses the whole behaviour — no snap, no merge, no
 * indicator — so vertices can be placed exactly where they are dragged.
 *
 * Unlike the one-shot cut tools this is a STICKY mode: it stays as set until
 * toggled back, since a user who needs it usually needs it for a whole region.
 * It is deliberately not persisted, so every session starts with snapping on.
 */
let snapEnabled = true

const listeners = new Set<() => void>()

/** Whether endpoint snapping (and therefore merging) is active. */
export function isSnapEnabled(): boolean {
  return snapEnabled
}

/**
 * Enable or disable endpoint snapping. Notifies only on an actual change.
 *
 * @param on whether snapping should be active
 */
export function setSnapEnabled(on: boolean): void {
  if (snapEnabled === on) {
    return
  }
  snapEnabled = on
  listeners.forEach((listener) => listener())
}

/**
 * Subscribe to snap-toggle changes.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onSnapChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
