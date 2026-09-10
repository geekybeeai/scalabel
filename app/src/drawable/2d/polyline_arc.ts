/**
 * Executes the arc tool: place an open circular arc as an ordinary polyline.
 *
 * The arc is written as a normal POLYLINE_2D whose points are LCCLCCL..., not
 * as a new label type. That is deliberate: because it is just a curved
 * polyline, it exports to the existing JSON format, re-imports, cuts, and —
 * the point of the exercise — endpoint-merges with other lines using the same
 * drag gesture as everything else. Nothing downstream needs to know arcs exist.
 *
 * Arcs are always open, so a semicircle and an almost-full ring differ only in
 * sweep. Recorded as one undo step.
 */

import _ from "lodash"

import { addLabel, makeSequential } from "../../action/common"
import { drawHistory, LineSnapshot } from "../../common/draw_history"
import { dispatch, getState } from "../../common/session"
import { uid } from "../../common/uid"
import { LabelTypeName } from "../../const/common"
import { getShapes } from "../../functional/state_util"
import { makeLabel, makePathPoint2D } from "../../functional/states"
import { IdType, LabelType } from "../../types/state"
import { ArcPoint, curveThroughPoints } from "./polyline_arc_geometry"

/** Outcome of an arc placement, mapped to user feedback by the caller. */
export interface ArcOutcome {
  /** whether an arc was placed */
  ok: boolean
  /** the new label's id, when one was created */
  labelId?: IdType
  /** why the arc was refused, when it was */
  reason?: "degenerate" | "missing"
}

/**
 * Place a curve through any number of clicked points.
 *
 * Three clicks give the exact circular arc, which is what a ring-shaped
 * marking is; two give a straight span, and four or more give a smooth spline
 * through every point, since points past the third rarely share one circle.
 *
 * All coordinates are in ORIGINAL-IMAGE pixels.
 *
 * @param picks the clicked points, in order along the curve
 * @param category optional category; falls back to the sidebar selection
 */
export function performArc(
  picks: readonly ArcPoint[],
  category?: number
): ArcOutcome {
  const state = getState()
  const itemIndex = state.user.select.item
  if (state.task.items[itemIndex] === undefined) {
    return { ok: false, reason: "missing" }
  }

  // Coincident clicks, or a single point, define no curve; refusing is better
  // than emitting a degenerate segment.
  const points = curveThroughPoints(picks)
  if (points === null) {
    return { ok: false, reason: "degenerate" }
  }

  const labelId = uid()
  const shapes = points.map((p) =>
    makePathPoint2D({
      x: p.x,
      y: p.y,
      pointType: p.pointType,
      label: [labelId]
    })
  )
  const label: LabelType = makeLabel({
    id: labelId,
    type: LabelTypeName.POLYLINE_2D,
    item: itemIndex,
    category: [category ?? state.user.select.category],
    shapes: shapes.map((s) => s.id),
    manual: true
  })

  dispatch(makeSequential([addLabel(itemIndex, label, shapes)], true))

  const committed = getState()
  const created = committed.task.items[itemIndex].labels[labelId]
  if (created !== undefined) {
    const snapshot: LineSnapshot = {
      label: _.cloneDeep(created),
      shapes: _.cloneDeep(getShapes(committed, itemIndex, labelId))
    }
    drawHistory.recordStamp(itemIndex, [snapshot])
  }
  return { ok: true, labelId }
}
