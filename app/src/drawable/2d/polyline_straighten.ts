/**
 * Executes the straighten tool: turn one clicked curve back into a straight
 * span, discarding its control points.
 *
 * Scans every visible polyline in the item for the curve nearest the click,
 * exactly as the cut tools scan for a cut site, so thin lines are easy to hit
 * without depending on the control-canvas hit test.
 *
 * Unlike a cut this produces ONE label rather than two, so it records a plain
 * `recordEdit` — a single undo step that restores the curve.
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
  buildStraightened,
  findStraightenSite,
  StraightenSite
} from "./polyline_straighten_geometry"

/** Outcome of a straighten attempt, mapped to user feedback by the caller. */
export type StraightenOutcome = "straightened" | "miss"

interface Candidate {
  /** the polyline's label id */
  labelId: IdType
  /** which curve to straighten */
  site: StraightenSite
  /** whether the shape is closed */
  closed: boolean
}

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
 * Straighten the curve nearest to a click.
 *
 * Clicking anywhere that is not within `radius` of a curve is a miss — there
 * is nothing to straighten on a span that is already straight. All parameters
 * are in ORIGINAL-IMAGE pixels.
 *
 * @param click the click position (image frame)
 * @param click.x click x (image px)
 * @param click.y click y (image px)
 * @param radius max click-to-curve distance (image px)
 * @param visibility optional viewer-config visibility filter; labels the
 * renderer would hide are excluded from the scan
 */
export function performStraighten(
  click: { x: number; y: number },
  radius: number,
  visibility?: CutVisibilityFilter
): StraightenOutcome {
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

  let best: Candidate | null = null

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
    if (points.length < 4) {
      continue
    }
    const closed =
      label.type === LabelTypeName.POLYGON_2D || label.closed === true
    const result = findStraightenSite(points, click, radius, closed)
    if (result.kind !== "site") {
      continue
    }
    if (best === null || result.site.distance < best.site.distance) {
      best = { labelId, site: result.site, closed }
    }
  }

  if (best === null) {
    return "miss"
  }
  return commitStraighten(itemIndex, best)
}

/**
 * Apply a validated straighten.
 *
 * The label keeps its id: its shapes are replaced wholesale with the
 * curve-free point list. Recorded as a single `edited` command, so one undo
 * brings the curve back.
 *
 * @param itemIndex the item being edited
 * @param candidate the polyline and curve to straighten
 */
function commitStraighten(
  itemIndex: number,
  candidate: Candidate
): StraightenOutcome {
  const state = getState()
  const { labelId, site } = candidate
  const label = state.task.items[itemIndex].labels[labelId]
  const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]

  const before: LineSnapshot = {
    label: _.cloneDeep(label),
    shapes: _.cloneDeep(stored)
  }

  const straightened = buildStraightened(
    stored.map((p) => ({ x: p.x, y: p.y, pointType: p.pointType })),
    site
  )

  const shapes = straightened.map((p) =>
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

  // Removing shapes means the label is rebuilt rather than edited in place,
  // the same wholesale-replacement pattern the cut tools use.
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
  return "straightened"
}
