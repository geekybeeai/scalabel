import { IdType, SimplePathPoint2DType } from "../types/state"

/** Geometry held while a closed polyline awaits a second pick or Enter. */
export interface ClosedCutPick {
  /** the viewer where the first point was picked */
  viewerId?: number
  /** active item at the time of the first pick */
  itemIndex: number
  /** label that owns the closed outline */
  labelId: IdType
  /** original points, for detecting intervening edits */
  points: SimplePathPoint2DType[]
  /** the full outline opened at the first pick */
  openedPoints: SimplePathPoint2DType[]
}

let cutMode = false
let closedCutPick: ClosedCutPick | null = null

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
  if (cutMode === on && (on || closedCutPick === null)) {
    return
  }
  cutMode = on
  closedCutPick = null
  listeners.forEach((listener) => listener())
}

/** The pending first pick on a closed outline. */
export function getClosedCutPick(): ClosedCutPick | null {
  return closedCutPick
}

/**
 * Set or clear a pending pick without changing the tool's active state.
 *
 * @param pick the new pick, or null to clear it
 */
export function setClosedCutPick(pick: ClosedCutPick | null): void {
  closedCutPick = pick
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
