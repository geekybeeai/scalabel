import _ from "lodash"

import { addLabel, deleteLabel } from "../action/common"
import { LabelTypeName } from "../const/common"
import { getShapes } from "../functional/state_util"
import { LabelType, ShapeType } from "../types/state"
import Session, { dispatch, getState } from "./session"

/** A removed polyline, holding everything needed to recreate it. */
interface RemovedPolyline {
  /** Item the polyline belongs to */
  itemIndex: number
  /** The label definition */
  label: LabelType
  /** The label's shapes */
  shapes: ShapeType[]
}

/**
 * Polyline-level undo/redo for the 2D annotator.
 *
 * Undo/redo dispatch normal ADD_LABELS / DELETE_LABELS actions so the
 * synchronizer keeps the backend in sync. No undo stack is kept: the label to
 * undo is derived from state (highest-order polyline). Only a redo stack of
 * removed polylines is held.
 */
export class DrawHistory {
  /** Removed completed polylines available for redo */
  private _redoStack: RemovedPolyline[]

  /** Constructor */
  constructor() {
    this._redoStack = []
  }

  /**
   * Undo the most recent polyline. An in-progress drawing is cancelled first
   * (not redoable); otherwise the last completed polyline is deleted.
   *
   * @returns whether anything was undone
   */
  public undo(): boolean {
    if (Session.label2dList.isDrawingInProgress()) {
      Session.label2dList.cancelDrawing()
      return true
    }
    const state = getState()
    const itemIndex = state.user.select.item
    const item = state.task.items[itemIndex]
    let target: LabelType | null = null
    for (const labelId of Object.keys(item.labels)) {
      const label = item.labels[labelId]
      if (
        label.type === LabelTypeName.POLYGON_2D ||
        label.type === LabelTypeName.POLYLINE_2D
      ) {
        if (target === null || label.order > target.order) {
          target = label
        }
      }
    }
    if (target === null) {
      return false
    }
    const shapes = getShapes(state, itemIndex, target.id)
    this._redoStack.push({
      itemIndex,
      label: _.cloneDeep(target),
      shapes: _.cloneDeep(shapes)
    })
    dispatch(deleteLabel(itemIndex, target.id))
    return true
  }

  /**
   * Redo the last completed polyline removed by undo.
   *
   * @returns whether anything was redone
   */
  public redo(): boolean {
    const entry = this._redoStack.pop()
    if (entry === undefined) {
      return false
    }
    dispatch(addLabel(entry.itemIndex, entry.label, entry.shapes))
    return true
  }

  /** Clear the redo stack (called when a new polyline is drawn). */
  public clearRedo(): void {
    this._redoStack = []
  }

  /**
   * Handle a keyboard event. Ctrl/Cmd+Z = undo, Ctrl/Cmd+Y or
   * Ctrl/Cmd+Shift+Z = redo.
   *
   * @param e the keyboard event
   * @returns whether the event was acted on
   */
  public handleKeyboard(e: KeyboardEvent): boolean {
    if (!(e.ctrlKey || e.metaKey)) {
      return false
    }
    const key = e.key.toLowerCase()
    if (key === "z" && !e.shiftKey) {
      return this.undo()
    }
    if (key === "y" || (key === "z" && e.shiftKey)) {
      return this.redo()
    }
    return false
  }
}

/** Global singleton instance */
export const drawHistory = new DrawHistory()
