/**
 * Executes the disjoint tool: break a joined line back apart at its seam.
 *
 * This is the inverse of the endpoint merge. Dragging one line's endpoint onto
 * another's merges them into a single label, and the batch auto-connect at
 * project creation does the same in bulk; the seam survives as a LINE anchor in
 * the middle of the merged run. Clicking that anchor here separates the run
 * into the two lines that went in.
 *
 * Geometry is preserved exactly. The split happens AT an existing anchor, so no
 * bezier is subdivided and not one coordinate changes — the halves are the
 * original lines, not approximations of them.
 *
 * The first half keeps the original label id; the second becomes a new label
 * inheriting its category. Recorded as one atomic undo step.
 */

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
  SimplePathPoint2DType
} from "../../types/state"
import { CutVisibilityFilter } from "./polyline_cut"
import {
  buildDisjointHalves,
  findDisjointAnchor
} from "./polyline_disjoint_geometry"

/** Outcome of a disjoint attempt, mapped to user feedback by the caller. */
export type DisjointResult = "disjointed" | "miss" | "no-anchor" | "closed"

interface Candidate {
  /** the polyline's label id */
  labelId: IdType
  /** the anchor index to break at */
  index: number
  /** how far the click was from that anchor */
  distance: number
}

/**
 * Break the joined line nearest a click at its nearest interior anchor.
 *
 * All parameters are in ORIGINAL-IMAGE pixels.
 *
 * @param click the click position (image frame)
 * @param click.x click x (image px)
 * @param click.y click y (image px)
 * @param radius the greatest click-to-anchor distance that counts as a hit
 * @param visibility optional viewer-config visibility filter
 */
export function performDisjoint(
  click: { x: number; y: number },
  radius: number,
  visibility?: CutVisibilityFilter
): DisjointResult {
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
  let sawClosed = false

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
    const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]
    const points: SimplePathPoint2DType[] = stored.map((p) => ({
      x: p.x,
      y: p.y,
      pointType: p.pointType
    }))
    const hit = findDisjointAnchor(points, click, radius)
    if (hit === null) {
      continue
    }
    // A closed ring has no free ends, so breaking it at one anchor would leave
    // a single open line rather than two — a different operation entirely.
    if (label.type === LabelTypeName.POLYGON_2D || label.closed === true) {
      sawClosed = true
      continue
    }
    if (best === null || hit.distance < best.distance) {
      best = { labelId, index: hit.index, distance: hit.distance }
    }
  }

  if (best === null) {
    return sawClosed ? "closed" : "no-anchor"
  }
  return commitDisjoint(itemIndex, best)
}

/**
 * Apply a validated disjoint: first half keeps the id, second becomes new.
 *
 * @param itemIndex the item being edited
 * @param candidate the line and anchor to break at
 */
function commitDisjoint(
  itemIndex: number,
  candidate: Candidate
): DisjointResult {
  const state = getState()
  const { labelId, index } = candidate
  const label = state.task.items[itemIndex].labels[labelId]
  const stored = getShapes(state, itemIndex, labelId) as PathPoint2DType[]

  const before: LineSnapshot = {
    label: _.cloneDeep(label),
    shapes: _.cloneDeep(stored)
  }

  const halves = buildDisjointHalves(
    stored.map((p) => ({ x: p.x, y: p.y, pointType: p.pointType })),
    index
  )
  if (halves === null) {
    return "no-anchor"
  }

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

  // Second half is a new polyline inheriting category and attributes.
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
  // One atomic command, so a disjoint is a single undo step.
  drawHistory.recordCut(itemIndex, labelId, before, after, newLine)
  return "disjointed"
}
