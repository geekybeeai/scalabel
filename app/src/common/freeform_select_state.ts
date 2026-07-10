import { Pt } from "../drawable/2d/freeform_select_geometry"
import { isCutMode, onCutModeChange, setCutMode } from "./cut_state"
import {
  isSegmentDeleteActive,
  onSegmentDeleteChange,
  resetSegmentDelete
} from "./segment_delete_state"

/**
 * Transient, non-Redux state for the freeform (lasso) select tool. Mirrors
 * cut_state.ts / segment_delete_state.ts: plain module state plus a listener
 * list so the canvas repaints on change.
 *
 * `armed` is the sticky toolbar mode; `drawing` is true only between lasso
 * mouse-down and mouse-up; `path` holds the in-progress lasso vertices in the
 * image frame. Mutually exclusive with the cut and delete-segment tools.
 */

/** Squared image-px distance below which a new lasso point is merged away. */
const MERGE_DIST_SQ = 1

let armed = false
let drawing = false
let path: Pt[] = []

const listeners = new Set<() => void>()

/** Notify all listeners of a change. */
function notify(): void {
  listeners.forEach((listener) => listener())
}

/** Whether the sticky toolbar mode is on (drives the button color). */
export function isFreeformArmed(): boolean {
  return armed
}

/** Whether the tool owns the gesture/cursor (armed or mid-lasso). */
export function isFreeformActive(): boolean {
  return armed || drawing
}

/** Whether a lasso drag is currently in progress. */
export function isFreeformDrawing(): boolean {
  return drawing
}

/** Arm the sticky mode. Disarms the cut and delete-segment tools. */
export function armFreeform(): void {
  setCutMode(false)
  resetSegmentDelete()
  if (armed) {
    return
  }
  armed = true
  notify()
}

/** Disarm and clear any in-progress lasso. Safe to call when already off. */
export function resetFreeform(): void {
  if (!armed && !drawing && path.length === 0) {
    return
  }
  armed = false
  drawing = false
  path = []
  notify()
}

/**
 * Begin a lasso at the given image-frame point.
 *
 * @param pt the first lasso point (image frame)
 */
export function beginFreeformPath(pt: Pt): void {
  drawing = true
  path = [{ x: pt.x, y: pt.y }]
  notify()
}

/**
 * Append a point to the in-progress lasso, merging points closer than
 * MERGE_DIST_SQ to the last one to avoid zero-length segments.
 *
 * @param pt the next lasso point (image frame)
 */
export function addFreeformPoint(pt: Pt): void {
  if (!drawing) {
    return
  }
  const last = path[path.length - 1]
  if (last !== undefined) {
    const dx = pt.x - last.x
    const dy = pt.y - last.y
    if (dx * dx + dy * dy < MERGE_DIST_SQ) {
      return
    }
  }
  path.push({ x: pt.x, y: pt.y })
  notify()
}

/** The in-progress lasso vertices (image frame). */
export function getFreeformPath(): Pt[] {
  return path
}

/**
 * Finish the lasso: return its vertices and clear the in-progress path.
 * Leaves `armed` unchanged (sticky mode survives one lasso).
 */
export function endFreeformPath(): Pt[] {
  const finished = path
  drawing = false
  path = []
  notify()
  return finished
}

/**
 * Subscribe to freeform-tool changes.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onFreeformChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

// Mutual exclusion: arming the cut or delete-segment tool cancels freeform.
onCutModeChange(() => {
  if (isCutMode()) {
    resetFreeform()
  }
})
onSegmentDeleteChange(() => {
  if (isSegmentDeleteActive()) {
    resetFreeform()
  }
})
