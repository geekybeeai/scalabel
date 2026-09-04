/**
 * Transient, non-Redux state for the grab-move gesture (W) and its
 * copy variant (E).
 *
 * Pressing W over a line picks it up: the line then follows the cursor with no
 * button held, and a click drops it. E does the same but leaves the original
 * where it was and moves a copy. Escape cancels, restoring the original
 * position and discarding any copy.
 *
 * Deliberately not a press-and-drag gesture: a drag would collide with the
 * existing pan/reshape handling on mouse-down, and press-move-click works on a
 * trackpad, where holding a button while moving is awkward.
 */

import { IdType } from "../types/state"

/** What the gesture does on drop. */
export enum GrabMode {
  /** move the original line */
  MOVE = "move",
  /** leave the original and move a copy */
  COPY = "copy"
}

/** A line currently being carried by the cursor. */
export interface GrabState {
  /** the label being moved (the original, for both modes) */
  labelId: IdType
  /** the item the label belongs to */
  itemIndex: number
  /** whether the drop moves the original or leaves a copy */
  mode: GrabMode
  /** cursor position when the line was picked up, in image coords */
  originX: number
  /** cursor position when the line was picked up, in image coords */
  originY: number
  /** current cursor offset from the pick-up point, in image px */
  offsetX: number
  /** current cursor offset from the pick-up point, in image px */
  offsetY: number
}

let grab: GrabState | null = null

const listeners = new Set<() => void>()

/** The line currently being carried, or null. */
export function getGrab(): GrabState | null {
  return grab
}

/** Whether a line is currently being carried. */
export function isGrabbing(): boolean {
  return grab !== null
}

/**
 * Pick up a line.
 *
 * @param labelId the label to carry
 * @param itemIndex the item it belongs to
 * @param mode whether to move the original or a copy
 * @param x cursor x at pick-up, in image coords
 * @param y cursor y at pick-up, in image coords
 */
export function startGrab(
  labelId: IdType,
  itemIndex: number,
  mode: GrabMode,
  x: number,
  y: number
): void {
  grab = {
    labelId,
    itemIndex,
    mode,
    originX: x,
    originY: y,
    offsetX: 0,
    offsetY: 0
  }
  listeners.forEach((listener) => listener())
}

/**
 * Update the carried line's offset as the cursor moves.
 *
 * @param x current cursor x, in image coords
 * @param y current cursor y, in image coords
 */
export function updateGrab(x: number, y: number): void {
  if (grab === null) {
    return
  }
  grab.offsetX = x - grab.originX
  grab.offsetY = y - grab.originY
  listeners.forEach((listener) => listener())
}

/** Drop the carried line, returning its final state (null if none). */
export function endGrab(): GrabState | null {
  const finished = grab
  grab = null
  if (finished !== null) {
    listeners.forEach((listener) => listener())
  }
  return finished
}

/**
 * Subscribe to grab changes. Used to repaint the preview as the cursor moves.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onGrabChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
