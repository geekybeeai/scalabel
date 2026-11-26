import _ from "lodash"

import { addLabel, deleteLabel, makeSequential } from "../../action/common"
import { selectLabels } from "../../action/select"
import {
  ClosedCutPick,
  getClosedCutPick,
  setClosedCutPick
} from "../../common/cut_state"
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
import {
  buildCurveCutHalves,
  CurveCutSite,
  findCurveCutSite,
  linearizeClosedPath
} from "./polyline_curve_cut_geometry"

/** Screen-space search radius for the cut click (display px). */
export const CUT_CLICK_RADIUS_PX = 20
/** Screen-space vertex-snap / endpoint-guard radius (display px). */
export const CUT_SNAP_RADIUS_PX = 8

/** Outcome of a cut attempt, mapped to user feedback by the caller. */
export type CutResult = "cut" | "miss" | "curve" | "near-endpoint" | "closed"

/** Additional outcomes while cutting a closed polyline. */
export type PolylineCutResult =
  | CutResult
  | "first-picked"
  | "wrong-line"
  | "stale"

type Candidate =
  | {
      labelId: IdType
      closed: false
      site: CutSite
      points: SimplePathPoint2DType[]
    }
  | {
      labelId: IdType
      closed: true
      site: CurveCutSite
      points: SimplePathPoint2DType[]
      path: SimplePathPoint2DType[]
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
 * Whether a label is visible to the cut tool.
 *
 * @param label the current label
 * @param visibility the optional viewer filter
 */
function isVisible(
  label: LabelType,
  visibility?: CutVisibilityFilter
): boolean {
  return (
    visibility === undefined ||
    (!visibility.hideLabels &&
      !visibility.hiddenLabelTypes.includes(label.type) &&
      !visibility.hiddenCategories.includes(label.category[0]))
  )
}

/**
 * Check that the pending outline is still the same editable label.
 *
 * @param state the current editor state
 * @param pending the pending cut
 * @param visibility the optional viewer filter
 */
function stillCuttable(
  state: State,
  pending: ClosedCutPick,
  visibility?: CutVisibilityFilter
): boolean {
  const { itemIndex, labelId, points } = pending
  const label = state.task.items[itemIndex]?.labels[labelId]
  return (
    !state.task.config.tracking &&
    state.user.select.item === itemIndex &&
    label?.type === LabelTypeName.POLYLINE_2D &&
    label.closed === true &&
    isVisible(label, visibility) &&
    _.isEqual(points, storedPoints(state, itemIndex, labelId))
  )
}

/**
 * Try to cut the polyline nearest to a click.
 *
 * Open lines split immediately. The first pick on a closed polyline marks
 * where to open it and the next pick splits it. All positions are image pixels.
 *
 * @param click the click position (image frame)
 * @param click.x click x (image px)
 * @param click.y click y (image px)
 * @param radius max click-to-line distance (image px)
 * @param snapRadius vertex snap / endpoint-guard distance (image px)
 * @param visibility optional viewer-config visibility filter; labels the
 * renderer would hide are excluded from the scan
 * @param viewerId the viewer that received the click
 */
export function performCut(
  click: { x: number; y: number },
  radius: number,
  snapRadius: number,
  visibility?: CutVisibilityFilter,
  viewerId?: number
): PolylineCutResult {
  const state = getState()
  const pending = getClosedCutPick()
  if (pending !== null) {
    if (pending.viewerId !== viewerId) {
      return "miss"
    }
    if (!stillCuttable(state, pending, visibility)) {
      setClosedCutPick(null)
      return "stale"
    }
    const result = findCurveCutSite(pending.openedPoints, click, radius, 0)
    if (result.kind !== "site") {
      return result.kind === "miss" ? "wrong-line" : result.kind
    }
    // Snap to anchors on the picked span. A point on the opposite side of
    // a narrow outline can be close on screen and still split the path.
    const { first, second } = buildCurveCutHalves(
      pending.openedPoints,
      result.site,
      snapRadius
    )
    if (first.length < 2 || second.length < 2) {
      return "near-endpoint"
    }
    setClosedCutPick(null)
    return commitCut(pending.itemIndex, pending.labelId, first, second)
  }
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
    if (!isVisible(label, visibility)) {
      continue
    }
    const points = storedPoints(state, itemIndex, labelId)
    if (points.length < 2) {
      continue
    }
    if (label.type === LabelTypeName.POLYLINE_2D && label.closed === true) {
      const path = linearizeClosedPath(points)
      const result = findCurveCutSite(path, click, radius, 0, true)
      if (
        result.kind === "site" &&
        (best === null || result.site.distance < best.site.distance)
      ) {
        best = { labelId, closed: true, site: result.site, points, path }
      }
      continue
    }
    const isPolygon = label.type === LabelTypeName.POLYGON_2D
    const scanPoints = isPolygon ? [...points, points[0]] : points
    const result = findCutSite(scanPoints, click, radius, snapRadius)
    if (result.kind === "miss") {
      continue
    }
    if (isPolygon) {
      const distance =
        result.kind === "site" ? result.site.distance : result.distance
      if (bestRejection === null || distance < bestRejection.distance) {
        bestRejection = { kind: "closed", distance }
      }
      continue
    }
    if (result.kind === "site") {
      if (best === null || result.site.distance < best.site.distance) {
        best = { labelId, closed: false, site: result.site, points }
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
  if (best.closed) {
    const { first, second } = buildCurveCutHalves(
      best.path,
      best.site,
      snapRadius
    )
    setClosedCutPick({
      viewerId,
      itemIndex,
      labelId: best.labelId,
      points: best.points,
      openedPoints: [...second, ...first.slice(1)]
    })
    return "first-picked"
  }
  const { first, second } = buildCutHalves(best.points, best.site)
  return commitCut(itemIndex, best.labelId, first, second)
}

/**
 * Open the outline at its first pick, keeping the full path as one polyline.
 *
 * @param visibility the optional viewer filter
 * @param viewerId the viewer confirming the first pick
 */
export function confirmClosedCut(
  visibility?: CutVisibilityFilter,
  viewerId?: number
): PolylineCutResult {
  const pending = getClosedCutPick()
  if (pending === null || pending.viewerId !== viewerId) {
    return "miss"
  }
  const valid = stillCuttable(getState(), pending, visibility)
  setClosedCutPick(null)
  return valid
    ? commitCut(pending.itemIndex, pending.labelId, pending.openedPoints)
    : "stale"
}

/**
 * Replace the original with one or two open paths in a single undo step.
 *
 * @param itemIndex the item being edited
 * @param labelId the original label
 * @param first points for the original label after the cut
 * @param second optional points for a new label
 */
function commitCut(
  itemIndex: number,
  labelId: IdType,
  first: SimplePathPoint2DType[],
  second?: SimplePathPoint2DType[]
): "cut" {
  const state = getState()
  const label = state.task.items[itemIndex].labels[labelId]
  const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]

  const before: LineSnapshot = {
    label: _.cloneDeep(label),
    shapes: _.cloneDeep(stored)
  }

  const shapesA = first.map((p) =>
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
  if (labelA.closed === true) {
    labelA.closed = false
  }

  const actions = [
    deleteLabel(itemIndex, labelId),
    addLabel(itemIndex, labelA, shapesA)
  ]
  let newLabelId: IdType | undefined
  if (second !== undefined) {
    const secondId = uid()
    newLabelId = secondId
    const shapesB = second.map((p) =>
      makePathPoint2D({
        x: p.x,
        y: p.y,
        pointType: p.pointType,
        label: [secondId]
      })
    )
    const labelB: LabelType = {
      ..._.cloneDeep(labelA),
      id: secondId,
      item: itemIndex,
      track: "",
      parent: "",
      children: [],
      shapes: shapesB.map((s) => s.id)
    }
    actions.push(addLabel(itemIndex, labelB, shapesB))
  }

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

  dispatch(makeSequential(actions))

  const committed = getState()
  const after: LineSnapshot = {
    label: _.cloneDeep(committed.task.items[itemIndex].labels[labelId]),
    shapes: _.cloneDeep(getShapes(committed, itemIndex, labelId))
  }
  if (newLabelId === undefined) {
    drawHistory.recordEdit(itemIndex, labelId, before, after)
  } else {
    drawHistory.recordCut(itemIndex, labelId, before, after, {
      label: _.cloneDeep(committed.task.items[itemIndex].labels[newLabelId]),
      shapes: _.cloneDeep(getShapes(committed, itemIndex, newLabelId))
    })
  }
  return "cut"
}
