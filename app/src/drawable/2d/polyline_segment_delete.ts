import _ from "lodash"

import { addLabel, deleteLabel, makeSequential } from "../../action/common"
import { selectLabels } from "../../action/select"
import { drawHistory, LineSnapshot } from "../../common/draw_history"
import {
  getPickData,
  getPreviewData,
  getSegmentDeletePhase,
  recordFirstPick,
  recordSecondPick,
  resetSegmentDelete
} from "../../common/segment_delete_state"
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
import { CutVisibilityFilter } from "./polyline_cut"
import {
  buildSegmentDeletePieces,
  DeleteSitePick,
  findCutSite,
  normalizeDeletePicks,
  resolvePickPoint
} from "./polyline_cut_geometry"

/** How long the doomed piece is previewed before the delete commits (ms). */
export const SEGMENT_DELETE_PREVIEW_MS = 3000

/** Outcome of a pick click, mapped to user feedback by the caller. */
export type SegmentDeletePickOutcome =
  | "first-picked"
  | "preview-started"
  | "miss"
  | "curve"
  | "closed"
  | "wrong-line"
  | "too-close"
  | "stale"
  | "ignored"

/** Outcome of committing the pending delete. */
export type SegmentDeleteCommitOutcome = "deleted" | "stale" | "ignored"

interface PickCandidate {
  /** the polyline's label id */
  labelId: IdType
  /** the resolved pick */
  pick: DeleteSitePick
  /** click-to-line distance (image px) */
  distance: number
  /** the polyline's stored vertices */
  points: SimplePathPoint2DType[]
}

type ScanOutcome =
  | { kind: "pick"; candidate: PickCandidate }
  | { kind: "curve" | "closed"; distance: number }
  | { kind: "miss" }

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
 * Whether two vertex lists are identical (staleness guard).
 *
 * @param a one list
 * @param b the other list
 */
function pointsEqual(
  a: readonly SimplePathPoint2DType[],
  b: readonly SimplePathPoint2DType[]
): boolean {
  if (a.length !== b.length) {
    return false
  }
  for (let i = 0; i < a.length; i++) {
    if (
      a[i].x !== b[i].x ||
      a[i].y !== b[i].y ||
      a[i].pointType !== b[i].pointType
    ) {
      return false
    }
  }
  return true
}

/**
 * Scan labels for the pick nearest to a click. Mirrors performCut's scan,
 * except a "near-endpoint" result is a VALID end-trim pick here. When
 * `onlyLabelId` is set, only that polyline is scanned.
 *
 * @param state the current state
 * @param itemIndex the item to scan
 * @param click the click position (image frame)
 * @param click.x click x (image px)
 * @param click.y click y (image px)
 * @param radius max click-to-line distance (image px)
 * @param snapRadius vertex snap / endpoint radius (image px)
 * @param visibility optional visibility filter (hidden labels excluded)
 * @param onlyLabelId restrict the scan to this label
 */
function scanForPick(
  state: State,
  itemIndex: number,
  click: { x: number; y: number },
  radius: number,
  snapRadius: number,
  visibility?: CutVisibilityFilter,
  onlyLabelId?: IdType
): ScanOutcome {
  const item = state.task.items[itemIndex]
  if (item === undefined) {
    return { kind: "miss" }
  }
  let best: PickCandidate | null = null
  let bestRejection: { kind: "curve" | "closed"; distance: number } | null =
    null

  const labelIds =
    onlyLabelId !== undefined ? [onlyLabelId] : Object.keys(item.labels)
  for (const labelId of labelIds) {
    const label = item.labels[labelId]
    if (label === undefined) {
      continue
    }
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
    const scanPoints = isOpen ? points : [...points, points[0]]
    const result = findCutSite(scanPoints, click, radius, snapRadius, {
      splitCurves: true
    })
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
    if (result.kind === "curve") {
      if (bestRejection === null || result.distance < bestRejection.distance) {
        bestRejection = { kind: "curve", distance: result.distance }
      }
      continue
    }
    // A site or an end-trim pick — both are candidates for this tool.
    const candidate: PickCandidate =
      result.kind === "site"
        ? {
            labelId,
            pick: { kind: "interior", site: result.site },
            distance: result.site.distance,
            points
          }
        : {
            labelId,
            pick: { kind: "end", endpointIndex: result.endpointIndex },
            distance: result.distance,
            points
          }
    if (best === null || candidate.distance < best.distance) {
      best = candidate
    }
  }

  if (best === null) {
    return bestRejection !== null ? bestRejection : { kind: "miss" }
  }
  if (bestRejection !== null && bestRejection.distance < best.distance) {
    return bestRejection
  }
  return { kind: "pick", candidate: best }
}

/**
 * Handle a canvas click while the delete-segment tool is active: resolve the
 * first or second pick and advance the state machine. All parameters are in
 * ORIGINAL-IMAGE pixels.
 *
 * @param click the click position (image frame)
 * @param click.x click x (image px)
 * @param click.y click y (image px)
 * @param radius max click-to-line distance (image px)
 * @param snapRadius vertex snap / endpoint / pick-separation radius (image px)
 * @param visibility optional visibility filter (hidden labels excluded)
 */
export function handleSegmentDeletePick(
  click: { x: number; y: number },
  radius: number,
  snapRadius: number,
  visibility?: CutVisibilityFilter
): SegmentDeletePickOutcome {
  const phase = getSegmentDeletePhase()
  if (phase !== "awaitFirst" && phase !== "awaitSecond") {
    return "ignored"
  }
  const state = getState()
  if (state.task.config.tracking || visibility?.hideLabels === true) {
    return "miss"
  }
  const itemIndex = state.user.select.item

  if (phase === "awaitFirst") {
    const outcome = scanForPick(
      state,
      itemIndex,
      click,
      radius,
      snapRadius,
      visibility
    )
    if (outcome.kind !== "pick") {
      return outcome.kind
    }
    const { candidate } = outcome
    recordFirstPick({
      itemIndex,
      labelId: candidate.labelId,
      pick1: candidate.pick,
      pick1Point: resolvePickPoint(candidate.points, candidate.pick),
      points: candidate.points
    })
    return "first-picked"
  }

  // awaitSecond
  const data = getPickData()
  if (data === null) {
    return "ignored"
  }
  // The line must be unchanged since pick 1 (an undo/edit invalidates it).
  const current = state.task.items[data.itemIndex]?.labels[data.labelId]
  if (
    data.itemIndex !== itemIndex ||
    current === undefined ||
    !pointsEqual(storedPoints(state, data.itemIndex, data.labelId), data.points)
  ) {
    resetSegmentDelete()
    return "stale"
  }
  const outcome = scanForPick(
    state,
    itemIndex,
    click,
    radius,
    snapRadius,
    visibility,
    data.labelId
  )
  if (outcome.kind !== "pick") {
    if (outcome.kind === "curve") {
      return "curve"
    }
    // Not on pick 1's line — was the click on some OTHER polyline?
    const other = scanForPick(
      state,
      itemIndex,
      click,
      radius,
      snapRadius,
      visibility
    )
    if (other.kind === "pick" && other.candidate.labelId !== data.labelId) {
      return "wrong-line"
    }
    return "miss"
  }
  const normalized = normalizeDeletePicks(
    data.points,
    data.pick1,
    outcome.candidate.pick,
    snapRadius
  )
  if (normalized.kind === "too-close") {
    return "too-close"
  }
  const pieces = buildSegmentDeletePieces(
    data.points,
    normalized.first,
    normalized.second
  )
  recordSecondPick(normalized.first, normalized.second, pieces.doomed)
  return "preview-started"
}

/**
 * Materialize a survivor piece as fresh path-point shapes for a label.
 *
 * @param piece the piece's vertices
 * @param labelId the owning label id
 */
function materialize(
  piece: SimplePathPoint2DType[],
  labelId: IdType
): PathPoint2DType[] {
  return piece.map((p) =>
    makePathPoint2D({
      x: p.x,
      y: p.y,
      pointType: p.pointType,
      label: [labelId]
    })
  )
}

/**
 * Commit the pending segment delete (called when the preview timer fires):
 * re-validate the line, build the survivors, dispatch ONE atomic sequential
 * action, and record undo via the existing command kinds — "cut" for two
 * survivors, "edited" for one, "deleted" for none. Resets the tool.
 */
export function commitPendingSegmentDelete(): SegmentDeleteCommitOutcome {
  const data = getPreviewData()
  if (data === null) {
    return "ignored"
  }
  const state = getState()
  const label = state.task.items[data.itemIndex]?.labels[data.labelId]
  if (
    state.user.select.item !== data.itemIndex ||
    label === undefined ||
    !pointsEqual(storedPoints(state, data.itemIndex, data.labelId), data.points)
  ) {
    resetSegmentDelete()
    return "stale"
  }

  const itemIndex = data.itemIndex
  const labelId = data.labelId
  const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]
  const before: LineSnapshot = {
    label: _.cloneDeep(label),
    shapes: _.cloneDeep(stored)
  }
  const pieces = buildSegmentDeletePieces(data.points, data.first, data.second)

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

  if (pieces.left !== undefined && pieces.right !== undefined) {
    // Two survivors: identical state shape to a cut.
    const shapesA = materialize(pieces.left, labelId)
    const labelA: LabelType = _.cloneDeep(label)
    labelA.shapes = shapesA.map((s) => s.id)
    labelA.manual = true

    const newLabelId = uid()
    const shapesB = materialize(pieces.right, newLabelId)
    const labelB: LabelType = _.cloneDeep(label)
    labelB.id = newLabelId
    labelB.item = itemIndex
    labelB.track = ""
    labelB.parent = ""
    labelB.children = []
    labelB.shapes = shapesB.map((s) => s.id)
    labelB.manual = true

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
  } else if (pieces.left !== undefined || pieces.right !== undefined) {
    // One survivor (end trim): keeps the original id — an edit.
    const piece = pieces.left !== undefined ? pieces.left : pieces.right
    const shapesA = materialize(piece as SimplePathPoint2DType[], labelId)
    const labelA: LabelType = _.cloneDeep(label)
    labelA.shapes = shapesA.map((s) => s.id)
    labelA.manual = true

    dispatch(
      makeSequential([
        deleteLabel(itemIndex, labelId),
        addLabel(itemIndex, labelA, shapesA)
      ])
    )
    const committed = getState()
    const after: LineSnapshot = {
      label: _.cloneDeep(committed.task.items[itemIndex].labels[labelId]),
      shapes: _.cloneDeep(getShapes(committed, itemIndex, labelId))
    }
    drawHistory.recordEdit(itemIndex, labelId, before, after)
  } else {
    // No survivors: a whole-line delete.
    drawHistory.recordDeletedLine(itemIndex, labelId, before)
    dispatch(deleteLabel(itemIndex, labelId))
  }

  resetSegmentDelete()
  return "deleted"
}
