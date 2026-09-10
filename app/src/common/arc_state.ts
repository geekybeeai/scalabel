/**
 * Transient, non-Redux state for the arc tool.
 *
 * Drawing an arc takes three clicks — start, a point along the arc, then the
 * end — so unlike the one-shot cut tools the tool has to remember the clicks
 * collected so far. That partial gesture is what lives here.
 *
 * The middle click is what makes one gesture cover every case the user asked
 * for: it decides which way round the arc goes, and therefore whether the
 * result is a semicircle, a three-quarter arc or an almost-full ring.
 *
 * Deliberately not persisted: an interrupted gesture should never survive a
 * reload as a half-placed arc.
 */

import { ArcPoint } from "../drawable/2d/polyline_arc_geometry"

let arcMode = false
let picks: ArcPoint[] = []

const listeners = new Set<() => void>()

/** Whether the arc tool is armed. */
export function isArcMode(): boolean {
  return arcMode
}

/** The clicks collected so far, in order. */
export function getArcPicks(): readonly ArcPoint[] {
  return picks
}

/**
 * Arm or disarm the arc tool. Disarming always discards a partial gesture.
 *
 * @param on whether the tool should be armed
 */
export function setArcMode(on: boolean): void {
  if (arcMode === on && picks.length === 0) {
    return
  }
  arcMode = on
  picks = []
  listeners.forEach((listener) => listener())
}

/**
 * Record one click of the arc gesture.
 *
 * @param point the click position, in image px
 * @returns the clicks collected so far, including this one
 */
export function addArcPick(point: ArcPoint): readonly ArcPoint[] {
  picks = [...picks, point]
  listeners.forEach((listener) => listener())
  return picks
}

/** Discard a partial gesture but leave the tool armed for another arc. */
export function clearArcPicks(): void {
  if (picks.length === 0) {
    return
  }
  picks = []
  listeners.forEach((listener) => listener())
}

/**
 * Subscribe to arc-tool changes.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onArcChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
