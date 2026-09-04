/**
 * Commits the grab-move gesture (W) and its copy variant (E).
 *
 * The line under the cursor is picked up by a keypress, follows the cursor
 * while `grab_move_state` holds the offset, and is written to redux here when
 * the user clicks to drop it.
 *
 * MOVE rewrites the label in place and records a plain `recordEdit` — one undo
 * step puts it back. COPY adds a brand-new label and records it with
 * `recordUserLine`, matching how a pasted line is recorded.
 */

import _ from "lodash"

import { addLabel, deleteLabel, makeSequential } from "../../action/common"
import { selectLabels } from "../../action/select"
import { drawHistory, LineSnapshot } from "../../common/draw_history"
import { GrabMode, GrabState } from "../../common/grab_move_state"
import Session, { dispatch, getState } from "../../common/session"
import { uid } from "../../common/uid"
import { LabelTypeName } from "../../const/common"
import { getShapes } from "../../functional/state_util"
import { makePathPoint2D } from "../../functional/states"
import { IdType, LabelType, PathPoint2DType } from "../../types/state"

/** Outcome of a drop, mapped to user feedback by the caller. */
export type GrabOutcome = "moved" | "copied" | "miss"

/**
 * Find the polyline nearest a point, for picking one up.
 *
 * @param click the cursor position (image frame)
 * @param click.x cursor x (image px)
 * @param click.y cursor y (image px)
 * @param radius max cursor-to-line distance (image px)
 * @returns the label id, or null if nothing is close enough
 */
export function findGrabbableLabel(
  click: { x: number; y: number },
  radius: number
): IdType | null {
  const state = getState()
  if (state.task.config.tracking) {
    return null
  }
  const itemIndex = state.user.select.item
  const item = state.task.items[itemIndex]
  if (item === undefined) {
    return null
  }

  let bestId: IdType | null = null
  let bestDistance = radius

  for (const labelId of Object.keys(item.labels)) {
    const label = item.labels[labelId]
    if (
      label.type !== LabelTypeName.POLYLINE_2D &&
      label.type !== LabelTypeName.POLYGON_2D
    ) {
      continue
    }
    const points = getShapes(state, itemIndex, labelId) as PathPoint2DType[]
    if (points.length < 2) {
      continue
    }
    let best = Infinity
    for (let i = 0; i < points.length - 1; i++) {
      const ax = points[i].x
      const ay = points[i].y
      const dx = points[i + 1].x - ax
      const dy = points[i + 1].y - ay
      const lenSq = dx * dx + dy * dy
      let t = 0
      if (lenSq > 0) {
        t = ((click.x - ax) * dx + (click.y - ay) * dy) / lenSq
        t = Math.max(0, Math.min(1, t))
      }
      const dist = Math.hypot(click.x - (ax + t * dx), click.y - (ay + t * dy))
      if (dist < best) {
        best = dist
      }
    }
    if (best < bestDistance) {
      bestDistance = best
      bestId = labelId
    }
  }

  return bestId
}

/**
 * Write a dropped line to redux.
 *
 * A zero offset is treated as a no-op for MOVE (nothing actually moved, so
 * nothing should enter the undo stack), but still copies for COPY, where
 * stacking a duplicate in place is a legitimate thing to ask for.
 *
 * @param grab the finished gesture state
 */
export function commitGrab(grab: GrabState): GrabOutcome {
  const state = getState()
  const { labelId, itemIndex, mode, offsetX, offsetY } = grab
  const label = state.task.items[itemIndex]?.labels[labelId]
  if (label === undefined) {
    return "miss"
  }
  if (mode === GrabMode.MOVE && offsetX === 0 && offsetY === 0) {
    return "miss"
  }

  const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]
  const targetId = mode === GrabMode.COPY ? uid() : labelId

  const shapes = stored.map((p) =>
    makePathPoint2D({
      x: p.x + offsetX,
      y: p.y + offsetY,
      pointType: p.pointType,
      label: [targetId]
    })
  )

  const newLabel: LabelType = _.cloneDeep(label)
  newLabel.shapes = shapes.map((s) => s.id)
  newLabel.manual = true

  // Deselect so no stale selected drawable survives the rebuild.
  dispatch(
    selectLabels(
      {},
      -1,
      [],
      state.user.select.category,
      state.user.select.attributes
    )
  )
  Session.label2dList.selectedLabels.length = 0

  if (mode === GrabMode.COPY) {
    newLabel.id = targetId
    newLabel.item = itemIndex
    newLabel.track = ""
    newLabel.parent = ""
    newLabel.children = []
    dispatch(addLabel(itemIndex, newLabel, shapes))
    drawHistory.recordUserLine(itemIndex, targetId)
    return "copied"
  }

  const before: LineSnapshot = {
    label: _.cloneDeep(label),
    shapes: _.cloneDeep(stored)
  }
  // Replaced wholesale rather than edited in place, the same pattern the cut
  // and straighten tools use.
  dispatch(
    makeSequential([
      deleteLabel(itemIndex, labelId),
      addLabel(itemIndex, newLabel, shapes)
    ])
  )
  const committed = getState()
  const after: LineSnapshot = {
    label: _.cloneDeep(committed.task.items[itemIndex].labels[labelId]),
    shapes: _.cloneDeep(getShapes(committed, itemIndex, labelId))
  }
  drawHistory.recordEdit(itemIndex, labelId, before, after)
  return "moved"
}
