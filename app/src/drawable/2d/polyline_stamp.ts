/**
 * Executes the stamp-along-path tool.
 *
 * Takes the line under the cursor as a guide, walks it by arc length, and adds
 * one label per generated mark. The guide line itself is left untouched — it is
 * usually a real annotation (the lane divider) that should stay.
 *
 * Marks are added as SEPARATE labels rather than as parts of one multi-part
 * label. Export does support multi-part labels, but only through the
 * parent/child tree (convertStateToExport groups polygons by root id), and
 * every editing tool here (select, delete, straighten, cut) assumes flat
 * labels. Separate labels also match the shape of the existing annotations.
 *
 * The whole run is recorded as one undo step, so a bad stamp is a single
 * Ctrl+Z rather than 150 of them.
 */

import _ from "lodash"

import { addLabel, deleteLabel, makeSequential } from "../../action/common"
import { selectLabels } from "../../action/select"
import { drawHistory, LineSnapshot } from "../../common/draw_history"
import Session, { dispatch, getState } from "../../common/session"
import { uid } from "../../common/uid"
import { LabelTypeName } from "../../const/common"
import { getShapes } from "../../functional/state_util"
import { makeLabel, makePathPoint2D } from "../../functional/states"
import {
  IdType,
  LabelType,
  PathPoint2DType,
  SimplePathPoint2DType
} from "../../types/state"
import { CutVisibilityFilter } from "./polyline_cut"
import {
  captureTemplate,
  CustomTemplate,
  DEFAULT_STAMP_OPTIONS,
  StampOptions,
  stampAlongPath
} from "./polyline_stamp_geometry"

/** Outcome of a stamp attempt, mapped to user feedback by the caller. */
export interface StampResult {
  /** how many marks were added */
  count: number
  /** why nothing was added, when nothing was */
  reason?: "miss" | "too-short"
  /** the labels created, so a re-stamp can replace exactly these */
  labelIds?: IdType[]
}

/**
 * Find the polyline nearest a point, to use as the guide path.
 *
 * @param click the cursor position (image frame)
 * @param click.x cursor x (image px)
 * @param click.y cursor y (image px)
 * @param radius max cursor-to-line distance (image px)
 * @param visibility optional viewer-config visibility filter
 */
export function findGuideLine(
  click: { x: number; y: number },
  radius: number,
  visibility?: CutVisibilityFilter
): IdType | null {
  const state = getState()
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
    if (visibility?.hiddenLabelTypes.includes(label.type) === true) {
      continue
    }
    if (visibility?.hiddenCategories.includes(label.category[0]) === true) {
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
 * Compute the marks a guide line would produce, without touching redux.
 *
 * Used to draw the live preview while the settings are being tuned.
 *
 * @param guideId the guide line's label id
 * @param options stamp settings
 */
export function previewMarks(
  guideId: IdType,
  options: StampOptions = DEFAULT_STAMP_OPTIONS
): SimplePathPoint2DType[][] {
  const state = getState()
  const itemIndex = state.user.select.item
  const label = state.task.items[itemIndex]?.labels[guideId]
  if (label === undefined) {
    return []
  }
  const guide = getShapes(state, itemIndex, guideId) as PathPoint2DType[]
  return stampAlongPath(
    guide.map((p) => ({ x: p.x, y: p.y, pointType: p.pointType })),
    options
  )
}

/**
 * Write a previewed set of marks to redux as new labels.
 *
 * New marks take the category currently selected in the sidebar, so the guide
 * line's own category (often a different one) is not imposed on them. The guide
 * line itself is left untouched.
 *
 * @param guideId the guide line's label id
 * @param options stamp settings
 * @param replace labels from a previous stamp on this guide, removed first so
 * re-applying with new settings replaces the marks instead of duplicating them
 */
export function commitStamp(
  guideId: IdType,
  options: StampOptions = DEFAULT_STAMP_OPTIONS,
  replace: IdType[] = []
): StampResult {
  const state = getState()
  if (state.task.config.tracking) {
    return { count: 0, reason: "miss" }
  }
  const itemIndex = state.user.select.item
  const marks = previewMarks(guideId, options)
  if (marks.length === 0) {
    return { count: 0, reason: "too-short" }
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

  // The panel's class, falling back to the sidebar selection when unset.
  const category = options.category ?? state.user.select.category
  const actions = []
  const newIds: IdType[] = []

  // Drop the previous run's marks first, so tweaking the settings after
  // applying replaces them rather than stacking a second set on top.
  for (const id of replace) {
    if (state.task.items[itemIndex]?.labels[id] !== undefined) {
      actions.push(deleteLabel(itemIndex, id))
    }
  }

  for (const mark of marks) {
    const labelId = uid()
    const shapes = mark.map((p: SimplePathPoint2DType) =>
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
      category: [category],
      shapes: shapes.map((s) => s.id),
      manual: true
    })
    actions.push(addLabel(itemIndex, label, shapes))
    newIds.push(labelId)
  }

  // A single sequential action, so the whole stamp is one redux change.
  dispatch(makeSequential(actions, true))

  // ONE history entry for the whole run: recording each mark separately would
  // mean one Ctrl+Z per mark to take a single action back.
  const committed = getState()
  const snapshots: LineSnapshot[] = []
  for (const id of newIds) {
    const label = committed.task.items[itemIndex].labels[id]
    if (label !== undefined) {
      snapshots.push({
        label: _.cloneDeep(label),
        shapes: _.cloneDeep(getShapes(committed, itemIndex, id))
      })
    }
  }
  drawHistory.recordStamp(itemIndex, snapshots)

  return { count: newIds.length, labelIds: newIds }
}

/** Re-exported so callers can build options without a second import. */
export { DEFAULT_STAMP_OPTIONS }
export type { StampOptions }

/**
 * Capture the mark under the cursor as a reusable template.
 *
 * Any drawn polyline can become a template, which is what makes the tool cover
 * shapes the built-in dash and chevron do not: across this batch 422 marks have
 * three vertices and 40 have four to six.
 *
 * @param click the cursor position (image frame)
 * @param click.x cursor x (image px)
 * @param click.y cursor y (image px)
 * @param radius max cursor-to-line distance (image px)
 * @param name what to call the captured shape
 * @param visibility optional viewer-config visibility filter
 * @returns the captured template, or null when nothing was close enough
 */
export function captureMarkAt(
  click: { x: number; y: number },
  radius: number,
  name: string,
  visibility?: CutVisibilityFilter
): CustomTemplate | null {
  const state = getState()
  const itemIndex = state.user.select.item
  const labelId = findGuideLine(click, radius, visibility)
  if (labelId === null) {
    return null
  }
  const points = getShapes(state, itemIndex, labelId) as PathPoint2DType[]
  return captureTemplate(
    points.map((p) => ({ x: p.x, y: p.y, pointType: p.pointType })),
    name
  )
}

/** Clone the default options, for a caller that wants to tweak a few. */
export function defaultStampOptions(): StampOptions {
  return _.cloneDeep(DEFAULT_STAMP_OPTIONS)
}
