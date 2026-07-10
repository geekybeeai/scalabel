import { markLabels } from "../../common/multi_delete_state"
import { getState } from "../../common/session"
import { LabelTypeName } from "../../const/common"
import { getShapes } from "../../functional/state_util"
import { PathPoint2DType } from "../../types/state"
import { findLassoHits, LassoLine, Pt } from "./freeform_select_geometry"

/** Visibility filters the lasso scan must respect (mirrors CutVisibilityFilter). */
export interface FreeformVisibilityFilter {
  /** all labels hidden */
  hideLabels: boolean
  /** hidden label type names */
  hiddenLabelTypes: string[]
  /** hidden category indices */
  hiddenCategories: number[]
}

/**
 * Mark every visible polyline/polygon in the current item that is completely
 * enclosed by the lasso, unioning them into the batch-delete set. Lines that
 * merely cross the lasso boundary are NOT marked. Returns the
 * number of lines marked. A lasso of fewer than 3 points, a tracking task, or
 * an all-hidden view is a no-op.
 *
 * @param lasso the lasso vertices in the image frame
 * @param visibility optional viewer-config visibility filter
 */
export function runFreeformSelect(
  lasso: Pt[],
  visibility?: FreeformVisibilityFilter
): number {
  if (lasso.length < 3) {
    return 0
  }
  const state = getState()
  if (state.task.config.tracking) {
    return 0
  }
  if (visibility?.hideLabels === true) {
    return 0
  }
  const itemIndex = state.user.select.item
  const item = state.task.items[itemIndex]
  if (item === undefined) {
    return 0
  }
  const lines: LassoLine[] = []
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
    if (stored.length === 0) {
      continue
    }
    lines.push({
      id: labelId,
      pts: stored.map((p) => ({ x: p.x, y: p.y })),
      closed:
        label.type === LabelTypeName.POLYGON_2D || label.closed === true
    })
  }
  const hits = findLassoHits(lines, lasso)
  if (hits.length > 0) {
    markLabels(hits)
  }
  return hits.length
}
