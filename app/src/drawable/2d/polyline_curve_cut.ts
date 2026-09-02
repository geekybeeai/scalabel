/**
 * Executes the curve divide tool.
 *
 * Despite the scissors icon this does NOT break the polyline into two labels —
 * the line stays whole. It divides the bezier under the click into two curve
 * groups joined by a new LINE anchor, so each side can afterwards be reshaped
 * on its own. The drawn shape does not change: De Casteljau subdivision is
 * exact, so the two groups together trace precisely the original curve.
 *
 * A click on a straight span inserts a plain vertex there, which is the same
 * idea — a new anchor, no change to the drawn shape.
 *
 * Because one label goes in and one comes out, this records a plain
 * `recordEdit`: a single undo step restores the undivided curve.
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
import { CutResult, CutVisibilityFilter } from "./polyline_cut"
import {
  buildDividedCurve,
  CurveCutSite,
  findCurveCutSite
} from "./polyline_curve_cut_geometry"

interface Candidate {
  /** the polyline's label id */
  labelId: IdType
  /** where it would be cut */
  site: CurveCutSite
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
 * Divide the curve on the polyline nearest to a click.
 *
 * Closed shapes are supported: dividing only inserts an anchor, so a ring is
 * as valid a target as an open line. All parameters are in ORIGINAL-IMAGE
 * pixels.
 *
 * @param click the click position (image frame)
 * @param click.x click x (image px)
 * @param click.y click y (image px)
 * @param radius max click-to-line distance (image px)
 * @param endpointGuard reject cuts within this distance of either end
 * @param visibility optional viewer-config visibility filter; labels the
 * renderer would hide are excluded from the scan
 */
export function performCurveCut(
  click: { x: number; y: number },
  radius: number,
  endpointGuard: number,
  visibility?: CutVisibilityFilter
): CutResult {
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
  let bestRejection: { kind: CutResult; distance: number } | null = null

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
    // Closed rings participate fully. Unlike a cut, dividing never breaks the
    // shape in two — it only adds an anchor — so there is nothing about a ring
    // that makes the operation invalid.
    const closed =
      label.type === LabelTypeName.POLYGON_2D || label.closed === true
    const points = storedPoints(state, itemIndex, labelId)
    if (points.length < 2) {
      continue
    }
    const result = findCurveCutSite(
      points,
      click,
      radius,
      endpointGuard,
      closed
    )
    if (result.kind === "miss") {
      continue
    }
    if (result.kind === "site") {
      if (best === null || result.site.distance < best.site.distance) {
        best = { labelId, site: result.site }
      }
    } else {
      if (bestRejection === null || result.distance < bestRejection.distance) {
        bestRejection = { kind: "near-endpoint", distance: result.distance }
      }
    }
  }

  if (best === null) {
    return bestRejection !== null ? bestRejection.kind : "miss"
  }
  if (bestRejection !== null && bestRejection.distance < best.site.distance) {
    return bestRejection.kind
  }
  return commitCurveCut(itemIndex, best)
}

/**
 * Apply a validated divide: rewrite the label with the curve split in two.
 *
 * The label keeps its id and its shapes are replaced wholesale with the
 * divided point list — the same replacement pattern the cut tools use, since
 * adding points means the label is rebuilt rather than edited in place.
 * Recorded as a single `edited` command, so one undo restores the original
 * curve.
 *
 * @param itemIndex the item being edited
 * @param candidate the polyline and site to divide
 */
function commitCurveCut(itemIndex: number, candidate: Candidate): CutResult {
  const state = getState()
  const { labelId, site } = candidate
  const label = state.task.items[itemIndex].labels[labelId]
  const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]

  const before: LineSnapshot = {
    label: _.cloneDeep(label),
    shapes: _.cloneDeep(stored)
  }

  const divided = buildDividedCurve(
    stored.map((p) => ({ x: p.x, y: p.y, pointType: p.pointType })),
    site
  )

  const shapes = divided.map((p) =>
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
  return "cut"
}
