import _ from "lodash"

import { deleteLabels } from "../../action/common"
import { drawHistory, LineSnapshot } from "../../common/draw_history"
import {
  clearMarked,
  getMarked,
  markedCount
} from "../../common/multi_delete_state"
import Session, { dispatch, getState } from "../../common/session"
import { LabelTypeName } from "../../const/common"
import { getShapes } from "../../functional/state_util"
import { PathPoint2DType } from "../../types/state"

/** Outcome of a batch-delete attempt. */
export type MarkedDeleteOutcome = "deleted" | "ignored"

/**
 * Delete every line currently marked for deletion in one atomic action.
 *
 * Only polylines and polygons in the current item are removed. Each is
 * snapshotted first via the existing per-line undo command, so undo restores
 * them one at a time (matching the annotator's existing multi-label delete).
 * The marked set is always cleared afterward. Returns "ignored" (and clears)
 * when nothing valid is marked, so callers can fall back to normal deletion.
 */
export function commitMarkedDelete(): MarkedDeleteOutcome {
  if (markedCount() === 0) {
    return "ignored"
  }
  const state = getState()
  const itemIndex = state.user.select.item
  const item = state.task.items[itemIndex]
  if (item === undefined) {
    clearMarked()
    return "ignored"
  }
  const deletable = getMarked().filter((id) => {
    const label = item.labels[id]
    return (
      label !== undefined &&
      (label.type === LabelTypeName.POLYLINE_2D ||
        label.type === LabelTypeName.POLYGON_2D)
    )
  })
  if (deletable.length === 0) {
    clearMarked()
    return "ignored"
  }
  // Snapshot each line before deletion so undo can restore it.
  for (const id of deletable) {
    const before: LineSnapshot = {
      label: _.cloneDeep(item.labels[id]),
      shapes: _.cloneDeep(getShapes(state, itemIndex, id) as PathPoint2DType[])
    }
    drawHistory.recordDeletedLine(itemIndex, id, before)
  }
  // Drop any stale selected drawable before the rebuild (mirrors segment delete).
  Session.label2dList.selectedLabels.length = 0
  dispatch(deleteLabels([itemIndex], [deletable]))
  clearMarked()
  return "deleted"
}
