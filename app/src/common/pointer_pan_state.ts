/**
 * Transient, non-Redux coordination between Label2dCanvas (which owns the
 * hit-test) and Viewer2D (which owns panning) so they agree on whether the
 * current left-button gesture is a draw/edit or a map-like pan.
 *
 * - Label2dCanvas arms an "empty drag" on mouse-down over empty canvas and
 *   defers the draw; Viewer2D pans once movement passes the threshold and marks
 *   panned; Label2dCanvas, on mouse-up, draws only if no pan happened.
 * - A double-click opens a short pan window during which any drag pans.
 */
let armed = false
let panned = false
let downX = 0
let downY = 0
let panWindowUntilMs = 0

/** Pixels of movement that turn a click into a drag/pan. */
export const PAN_THRESHOLD = 5
/** How long after a double-click a drag is treated as pan (ms). */
export const PAN_WINDOW_MS = 400

/** Arm a deferred empty-space gesture at the given container-relative point. */
export function armEmptyDrag(x: number, y: number): void {
  armed = true
  panned = false
  downX = x
  downY = y
}

export function isArmed(): boolean {
  return armed
}

export function downPos(): { x: number; y: number } {
  return { x: downX, y: downY }
}

/** True once the pointer has moved past PAN_THRESHOLD from the down point. */
export function exceededThreshold(x: number, y: number): boolean {
  return Math.abs(x - downX) > PAN_THRESHOLD || Math.abs(y - downY) > PAN_THRESHOLD
}

export function markPanned(): void {
  panned = true
}

export function didPan(): boolean {
  return panned
}

export function reset(): void {
  armed = false
  panned = false
}

/** Open the post-double-click pan window. Pass Date.now(). */
export function openPanWindow(nowMs: number): void {
  panWindowUntilMs = nowMs + PAN_WINDOW_MS
}

/** Whether we are still in the double-click pan window. Pass Date.now(). */
export function inPanWindow(nowMs: number): boolean {
  return nowMs < panWindowUntilMs
}
