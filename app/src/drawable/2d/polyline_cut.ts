import _ from "lodash"

import { addLabel, deleteLabel, makeSequential } from "../../action/common"
import { selectLabels } from "../../action/select"
import { drawHistory, LineSnapshot } from "../../common/draw_history"
import Session, { dispatch, getState } from "../../common/session"
import { uid } from "../../common/uid"
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
import { buildCutHalves, CutSite, findCutSite } from "./polyline_cut_geometry"

/** Screen-space search radius for the cut click (display px). */
export const CUT_CLICK_RADIUS_PX = 20
/** Screen-space vertex-snap / endpoint-guard radius (display px). */
export const CUT_SNAP_RADIUS_PX = 8

/** Outcome of a cut attempt, mapped to user feedback by the caller. */
export type CutResult = "cut" | "miss" | "curve" | "near-endpoint" | "closed"

interface Candidate {
  /** the polyline's label id */
  labelId: IdType
  /** where it would be cut */
  site: CutSite
}

/** Visibility filters the cut scan must respect (mirrors Label2DList.redraw). */
export interface CutVisibilityFilter {
  /** all labels hidden */
  hideLabels: boolean
  /** hidden label type names */
  hiddenLabelTypes: string[]
  /** hidden category indices */
  hiddenCategories: number[]
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
 * Try to cut the open polyline nearest to a click.
 *
 * Scans EVERY polyline/polygon in the current item from redux (no dependency
 * on the control-canvas hit-test, so thin lines are easy to hit), finds the
 * globally nearest cut site within `radius`, and applies it. Closed shapes
 * are scanned only so a click nearest to one reports "closed" instead of
 * silently missing. All parameters are in ORIGINAL-IMAGE pixels.
 *
 * @param click the click position (image frame)
 * @param click.x click x (image px)
 * @param click.y click y (image px)
 * @param radius max click-to-line distance (image px)
 * @param snapRadius vertex snap / endpoint-guard distance (image px)
 * @param visibility optional viewer-config visibility filter; labels the
 * renderer would hide are excluded from the scan
 */
export function performCut(
  click: { x: number; y: number },
  radius: number,
  snapRadius: number,
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
    const isOpen =
      label.type === LabelTypeName.POLYLINE_2D && label.closed !== true
    const points = storedPoints(state, itemIndex, labelId)
    if (points.length < 2) {
      continue
    }
    // Closed shapes participate only for the "closed" rejection message;
    // include their closing edge so clicks on it are attributed to them.
    const scanPoints = isOpen ? points : [...points, points[0]]
    const result = findCutSite(scanPoints, click, radius, snapRadius)
    if (result.kind === "miss") {
      continue
    }
    if (!isOpen) {
      const distance =
        result.kind === "site" ? result.site.distance : result.distance
      if (bestRejection === null || distance < bestRejection.distance) {
        bestRejection = { kind: "closed", distance }
      }
      continue
    }
    if (result.kind === "site") {
      if (best === null || result.site.distance < best.site.distance) {
        best = { labelId, site: result.site }
      }
    } else {
      if (bestRejection === null || result.distance < bestRejection.distance) {
        bestRejection = { kind: result.kind, distance: result.distance }
      }
    }
  }

  if (best === null) {
    return bestRejection !== null ? bestRejection.kind : "miss"
  }
  // If a rejection is strictly nearer than the best cuttable site, the user
  // most likely clicked the rejected thing — report it instead of cutting.
  if (bestRejection !== null && bestRejection.distance < best.site.distance) {
    return bestRejection.kind
  }
  return commitCut(itemIndex, best)
}

/**
 * Apply a validated cut: replace the original with its first half (same
 * label id — the drawHistory.setLine wholesale-replacement pattern), add the
 * second half as a new label (the pasteLabel pattern), and record ONE atomic
 * undo command.
 *
 * @param itemIndex the item being edited
 * @param candidate the polyline and site to cut
 */
function commitCut(itemIndex: number, candidate: Candidate): CutResult {
  const state = getState()
  const { labelId, site } = candidate
  const label = state.task.items[itemIndex].labels[labelId]
  const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]

  const before: LineSnapshot = {
    label: _.cloneDeep(label),
    shapes: _.cloneDeep(stored)
  }

  const halves = buildCutHalves(
    stored.map((p) => ({ x: p.x, y: p.y, pointType: p.pointType })),
    site
  )

  // First half keeps the original label id.
  const shapesA = halves.first.map((p) =>
    makePathPoint2D({
      x: p.x,
      y: p.y,
      pointType: p.pointType,
      label: [labelId]
    })
  )
  const labelA: LabelType = _.cloneDeep(label)
  labelA.shapes = shapesA.map((s) => s.id)
  labelA.manual = true

  // Second half is a brand-new polyline inheriting category/attributes.
  const newLabelId = uid()
  const shapesB = halves.second.map((p) =>
    makePathPoint2D({
      x: p.x,
      y: p.y,
      pointType: p.pointType,
      label: [newLabelId]
    })
  )
  const labelB: LabelType = _.cloneDeep(label)
  labelB.id = newLabelId
  labelB.item = itemIndex
  labelB.track = ""
  labelB.parent = ""
  labelB.children = []
  labelB.shapes = shapesB.map((s) => s.id)
  labelB.manual = true

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
      addLabel(itemIndex, labelA, shapesA),
      addLabel(itemIndex, labelB, shapesB)
    ])
  )

  const committed = getState()
  const after: LineSnapshot = {
    label: _.cloneDeep(committed.task.items[itemIndex].labels[labelId]),
    shapes: _.cloneDeep(getShapes(committed, itemIndex, labelId))
  }
  const newLine: LineSnapshot = {
    label: _.cloneDeep(committed.task.items[itemIndex].labels[newLabelId]),
    shapes: _.cloneDeep(getShapes(committed, itemIndex, newLabelId))
  }
  drawHistory.recordCut(itemIndex, labelId, before, after, newLine)
  return "cut"
}
