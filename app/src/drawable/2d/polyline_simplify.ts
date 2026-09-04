/**
 * Executes the simplify tool: merge a line's nearly-collinear straight spans.
 *
 * Targets the line nearest the click, like the other one-shot tools, and
 * replaces its geometry with the simplified point list. Curves are preserved —
 * the geometry refuses to remove any anchor that bounds a bezier.
 *
 * One label in, one label out, so this records a plain `recordEdit`: a single
 * undo step restores the dropped vertices.
 */

import _ from "lodash"

import { addLabel, deleteLabel, makeSequential } from "../../action/common"
import { selectLabels } from "../../action/select"
import { drawHistory, LineSnapshot } from "../../common/draw_history"
import Session, { dispatch, getState } from "../../common/session"
import { LabelTypeName } from "../../const/common"
import { getShapes } from "../../functional/state_util"
import { makePathPoint2D } from "../../functional/states"
import {
  IdType,
  LabelType,
  PathPoint2DType,
  SimplePathPoint2DType,
  State
} from "../../types/state"
import { CutVisibilityFilter } from "./polyline_cut"
import {
  DEFAULT_MAX_TURN_DEGREES,
  simplifyPolyline
} from "./polyline_simplify_geometry"

/** Outcome of a simplify attempt, mapped to user feedback by the caller. */
export type SimplifyOutcome = "simplified" | "nothing-to-do" | "miss"

/**
 * Read a label's stored path points as plain (id-less) points.
 *
 * @param state the current state
 * @param itemIndex the item index
 * @param labelId the label id
 */
function storedPoints(
  state: State,
  itemIndex: number,
  labelId: IdType
): SimplePathPoint2DType[] {
  const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]
  return stored.map((p) => ({ x: p.x, y: p.y, pointType: p.pointType }))
}

/**
 * Shortest distance from a point to any span of a polyline.
 *
 * @param points the polyline's stored vertices
 * @param query the point to measure from
 * @param query.x query x (image px)
 * @param query.y query y (image px)
 */
function distanceToPolyline(
  points: readonly SimplePathPoint2DType[],
  query: { x: number; y: number }
): number {
  let best = Infinity
  for (let i = 0; i < points.length - 1; i++) {
    const ax = points[i].x
    const ay = points[i].y
    const dx = points[i + 1].x - ax
    const dy = points[i + 1].y - ay
    const lenSq = dx * dx + dy * dy
    let t = 0
    if (lenSq > 0) {
      t = ((query.x - ax) * dx + (query.y - ay) * dy) / lenSq
      t = Math.max(0, Math.min(1, t))
    }
    const dist = Math.hypot(query.x - (ax + t * dx), query.y - (ay + t * dy))
    if (dist < best) {
      best = dist
    }
  }
  return best
}

/**
 * Simplify the polyline nearest to a click.
 *
 * Reports "nothing-to-do" when the nearest line has no removable vertices, so
 * the caller can say so rather than leaving the click looking ignored. All
 * parameters are in ORIGINAL-IMAGE pixels.
 *
 * @param click the click position (image frame)
 * @param click.x click x (image px)
 * @param click.y click y (image px)
 * @param radius max click-to-line distance (image px)
 * @param maxTurnDegrees how much a vertex may turn and still count as redundant
 * @param visibility optional viewer-config visibility filter; labels the
 * renderer would hide are excluded from the scan
 */
export function performSimplify(
  click: { x: number; y: number },
  radius: number,
  maxTurnDegrees: number = DEFAULT_MAX_TURN_DEGREES,
  visibility?: CutVisibilityFilter
): SimplifyOutcome {
  const state = getState()
  if (state.task.config.tracking) {
    return "miss"
  }
  if (visibility?.hideLabels === true) {
    return "miss"
  }
  const itemIndex = state.user.select.item
  const item = state.task.items[itemIndex]
  if (item === undefined) {
    return "miss"
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
    if (visibility?.hiddenLabelTypes.includes(label.type) === true) {
      continue
    }
    if (visibility?.hiddenCategories.includes(label.category[0]) === true) {
      continue
    }
    const points = storedPoints(state, itemIndex, labelId)
    if (points.length < 3) {
      continue
    }
    const distance = distanceToPolyline(points, click)
    if (distance < bestDistance) {
      bestDistance = distance
      bestId = labelId
    }
  }

  if (bestId === null) {
    return "miss"
  }
  return commitSimplify(itemIndex, bestId, maxTurnDegrees)
}

/**
 * Replace one label's geometry with its simplified form.
 *
 * @param itemIndex the item being edited
 * @param labelId the label to simplify
 * @param maxTurnDegrees how much a vertex may turn and still count as redundant
 */
function commitSimplify(
  itemIndex: number,
  labelId: IdType,
  maxTurnDegrees: number
): SimplifyOutcome {
  const state = getState()
  const label = state.task.items[itemIndex].labels[labelId]
  if (label === undefined) {
    return "miss"
  }
  const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]
  const closed =
    label.type === LabelTypeName.POLYGON_2D || label.closed === true

  const result = simplifyPolyline(
    stored.map((p) => ({ x: p.x, y: p.y, pointType: p.pointType })),
    maxTurnDegrees,
    closed
  )
  if (result.removed === 0) {
    return "nothing-to-do"
  }

  const before: LineSnapshot = {
    label: _.cloneDeep(label),
    shapes: _.cloneDeep(stored)
  }

  const shapes = result.points.map((p) =>
    makePathPoint2D({
      x: p.x,
      y: p.y,
      pointType: p.pointType,
      label: [labelId]
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
  return "simplified"
}
