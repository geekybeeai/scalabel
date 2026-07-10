/**
 * Tracks the last C-key midpoint->curve conversion per label so the clicks of
 * a single gesture burst don't toggle the conversion back.
 *
 * The C gesture must work identically for mouse and trackpad: hold C, click
 * (or double-click) the point, drag. Every mousedown with C held used to call
 * lineToCurve, so the 2nd/3rd press of a double-click(-drag) straightened the
 * segment again and the drag shaped nothing. A press on an already-converted
 * CURVE point within this window is treated as "continue the gesture" (drag
 * the control point); outside the window it keeps its original meaning
 * (toggle back to straight).
 */

/** How long after a conversion a C+press continues the gesture (ms). */
export const CURVE_BURST_WINDOW_MS = 600

/** Label id of the last midpoint->curve conversion. */
let lastLabelId: string | null = null
/** Timestamp (ms) of the last midpoint->curve conversion. */
let lastAtMs = 0

/**
 * Record a midpoint->curve conversion. Call from the C mouse-down path.
 *
 * @param labelId the converted label's id
 * @param nowMs the current time — pass Date.now()
 */
export function markCurveConversion(labelId: string, nowMs: number): void {
  lastLabelId = labelId
  lastAtMs = nowMs
}

/**
 * Whether this label was converted to a curve within the burst window.
 *
 * @param labelId the label the press landed on
 * @param nowMs the current time — pass Date.now()
 */
export function isRecentCurveConversion(
  labelId: string,
  nowMs: number
): boolean {
  return lastLabelId === labelId && nowMs - lastAtMs < CURVE_BURST_WINDOW_MS
}

/** Clear the burst record. */
export function resetCurveBurst(): void {
  lastLabelId = null
  lastAtMs = 0
}
