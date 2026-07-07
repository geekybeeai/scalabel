import _ from "lodash"

import { addLabel, deleteLabel } from "../action/common"
import { LabelTypeName } from "../const/common"
import { getShapes } from "../functional/state_util"
import { IdType, LabelType, ShapeType, State } from "../types/state"
import Session, { dispatch, getState } from "./session"

/** A snapshot of a polyline, enough to recreate it exactly. */
export interface LineSnapshot {
  /** The label definition */
  label: LabelType
  /** The label's shapes */
  shapes: ShapeType[]
}

/** Kind of recorded user action on a polyline. */
type CommandKind = "created" | "deleted" | "edited" | "cut"

/**
 * One recorded user action on a polyline.
 *
 * - "created": the user drew a new line. Undo deletes it; redo re-adds it.
 * - "deleted": the user deleted a line. Undo restores it; redo deletes it.
 * - "edited": the user moved/reshaped a line. Undo reverts it to `before`;
 * redo re-applies `after`.
 * - "cut": the user cut a line in two. Undo removes the new half and restores
 * the original; redo re-truncates the original and re-adds the new half.
 */
interface Command {
  /** What the user did */
  kind: CommandKind
  /** Item the polyline belongs to */
  itemIndex: number
  /** The polyline's label id */
  labelId: IdType
  /** "created": geometry captured at undo (for redo). "deleted": geometry to restore. */
  snapshot?: LineSnapshot
  /** "edited": geometry before the edit (undo target). */
  before?: LineSnapshot
  /** "edited": geometry after the edit (redo target). */
  after?: LineSnapshot
  /** "cut": the new second-half polyline created by the cut. */
  newLine?: LineSnapshot
}

/**
 * Polyline-level undo/redo for the 2D annotator.
 *
 * Only polylines the user has touched are tracked: drawing records a "created"
 * command, moving/reshaping records an "edited" command, deleting records a
 * "deleted" command. Inference predictions the user never touches are never
 * recorded, so undo can never affect them. Undo/redo dispatch normal
 * ADD_LABELS / DELETE_LABELS actions so the synchronizer keeps the backend in
 * sync.
 *
 * The history is an explicit stack (not derived from state) precisely so that
 * machine-provided predictions can be distinguished from the user's own work.
 */
export class DrawHistory {
  /** User actions, most recent last (undo target = top) */
  private _undoStack: Command[]
  /** Undone actions available for redo */
  private _redoStack: Command[]

  /** Constructor */
  constructor() {
    this._undoStack = []
    this._redoStack = []
  }

  /**
   * Record that the user drew a new polyline. Any pending redo is invalidated.
   *
   * @param itemIndex the item the polyline belongs to
   * @param labelId the polyline's label id
   */
  public recordUserLine(itemIndex: number, labelId: IdType): void {
    const existing = this._undoStack.findIndex(
      (c) => c.kind === "created" && c.labelId === labelId
    )
    if (existing >= 0) {
      this._undoStack.splice(existing, 1)
    }
    this._undoStack.push({ kind: "created", itemIndex, labelId })
    this._redoStack = []
  }

  /**
   * Record that the user moved/reshaped a polyline. Each edit is its own undo
   * step. Any pending redo is invalidated.
   *
   * @param itemIndex the item the polyline belongs to
   * @param labelId the polyline's label id
   * @param before the geometry before the edit
   * @param after the geometry after the edit
   */
  public recordEdit(
    itemIndex: number,
    labelId: IdType,
    before: LineSnapshot,
    after: LineSnapshot
  ): void {
    this._undoStack.push({ kind: "edited", itemIndex, labelId, before, after })
    this._redoStack = []
  }

  /**
   * Record that the user cut a polyline in two, as ONE atomic undo step.
   * Any pending redo is invalidated.
   *
   * @param itemIndex the item the polyline belongs to
   * @param labelId the original polyline's label id (kept by the first half)
   * @param before the original geometry before the cut (undo target)
   * @param after the truncated original after the cut (redo target)
   * @param newLine the new second-half polyline (id inside its label)
   */
  public recordCut(
    itemIndex: number,
    labelId: IdType,
    before: LineSnapshot,
    after: LineSnapshot,
    newLine: LineSnapshot
  ): void {
    this._undoStack.push({
      kind: "cut",
      itemIndex,
      labelId,
      before,
      after,
      newLine
    })
    this._redoStack = []
  }

  /**
   * Record that one polyline is being removed (deleted by the user, or dropped
   * because an edit made it invalid), so undo can restore it from the given
   * snapshot. Any pending redo is invalidated.
   *
   * @param itemIndex the item the polyline belongs to
   * @param labelId the polyline's label id
   * @param snapshot the line's geometry before removal
   */
  public recordDeletedLine(
    itemIndex: number,
    labelId: IdType,
    snapshot: LineSnapshot
  ): void {
    this._undoStack.push({ kind: "deleted", itemIndex, labelId, snapshot })
    this._redoStack = []
  }

  /**
   * Record that the user is deleting the currently selected polylines. Each one
   * is snapshotted so undo can restore it, and any pending redo is invalidated.
   * Call this immediately BEFORE dispatching the deletion.
   *
   * @param state the current state (before the deletion is applied)
   */
  public recordDeletion(state: State): void {
    const selected = state.user.select.labels
    for (const key of Object.keys(selected)) {
      const itemIndex = Number(key)
      const item = state.task.items[itemIndex]
      if (item === undefined) {
        continue
      }
      for (const labelId of selected[itemIndex]) {
        const label = item.labels[labelId]
        if (
          label !== undefined &&
          (label.type === LabelTypeName.POLYGON_2D ||
            label.type === LabelTypeName.POLYLINE_2D)
        ) {
          this.recordDeletedLine(itemIndex, labelId, {
            label: _.cloneDeep(label),
            shapes: _.cloneDeep(getShapes(state, itemIndex, labelId))
          })
        }
      }
    }
  }

  /**
   * Undo the most recent user action: delete a drawn line, restore a deleted
   * line, or revert a move/reshape. An in-progress drawing is cancelled first.
   * Predictions the user never touched are never affected.
   *
   * @returns whether anything was undone
   */
  public undo(): boolean {
    if (Session.label2dList.isDrawingInProgress()) {
      Session.label2dList.cancelDrawing()
      return true
    }
    while (this._undoStack.length > 0) {
      const command = this._undoStack.pop() as Command
      if (command.kind === "created") {
        // Undo a draw = delete the line (snapshot it first for redo).
        const snapshot = this.snapshotLine(command.itemIndex, command.labelId)
        if (snapshot === null) {
          // Already gone (e.g. deleted by hand); skip.
          continue
        }
        command.snapshot = snapshot
        this.removeLine(command.itemIndex, command.labelId)
        this._redoStack.push(command)
        return true
      } else if (command.kind === "deleted") {
        // Undo a delete = restore the line.
        if (command.snapshot === undefined) {
          continue
        }
        this.setLine(command.itemIndex, command.snapshot)
        this._redoStack.push(command)
        return true
      } else if (command.kind === "cut") {
        // Undo a cut = remove the new second half, restore the original line.
        if (command.before === undefined || command.newLine === undefined) {
          continue
        }
        this.removeLine(command.itemIndex, command.newLine.label.id)
        this.setLine(command.itemIndex, command.before)
        this._redoStack.push(command)
        return true
      } else {
        // Undo an edit = revert to the geometry before the edit.
        if (command.before === undefined) {
          continue
        }
        this.setLine(command.itemIndex, command.before)
        this._redoStack.push(command)
        return true
      }
    }
    return false
  }

  /**
   * Redo the most recently undone action.
   *
   * @returns whether anything was redone
   */
  public redo(): boolean {
    while (this._redoStack.length > 0) {
      const command = this._redoStack.pop() as Command
      if (command.kind === "created") {
        // Redo a draw = add the line back.
        if (command.snapshot === undefined) {
          continue
        }
        this.setLine(command.itemIndex, command.snapshot)
        this._undoStack.push(command)
        return true
      } else if (command.kind === "deleted") {
        // Redo a delete = delete the line again.
        this.removeLine(command.itemIndex, command.labelId)
        this._undoStack.push(command)
        return true
      } else if (command.kind === "cut") {
        // Redo a cut = re-truncate the original, re-add the second half.
        if (command.after === undefined || command.newLine === undefined) {
          continue
        }
        this.setLine(command.itemIndex, command.after)
        this.setLine(command.itemIndex, command.newLine)
        this._undoStack.push(command)
        return true
      } else {
        // Redo an edit = re-apply the geometry after the edit.
        if (command.after === undefined) {
          continue
        }
        this.setLine(command.itemIndex, command.after)
        this._undoStack.push(command)
        return true
      }
    }
    return false
  }

  /** Clear the redo stack. */
  public clearRedo(): void {
    this._redoStack = []
  }

  /** Clear all history (e.g. when a new item / fresh predictions are loaded). */
  public reset(): void {
    this._undoStack = []
    this._redoStack = []
  }

  /**
   * Whether there is anything to undo — a tracked line to delete/restore/revert,
   * or an in-progress drawing to cancel.
   *
   * @returns whether undo would do something
   */
  public canUndo(): boolean {
    if (Session.label2dList.isDrawingInProgress()) {
      return true
    }
    if (this._undoStack.length === 0) {
      return false
    }
    const state = getState()
    // A "created" entry is a no-op once its line is gone; the others always act.
    return this._undoStack.some(
      (c) =>
        c.kind !== "created" ||
        state.task.items[c.itemIndex]?.labels[c.labelId] !== undefined
    )
  }

  /**
   * Whether there is anything to redo.
   *
   * @returns whether redo would do something
   */
  public canRedo(): boolean {
    return this._redoStack.length > 0
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

  /**
   * Snapshot a line from the current state, or null if it does not exist.
   *
   * @param itemIndex the item the polyline belongs to
   * @param labelId the polyline's label id
   */
  private snapshotLine(
    itemIndex: number,
    labelId: IdType
  ): LineSnapshot | null {
    const state = getState()
    const item = state.task.items[itemIndex]
    const label = item?.labels[labelId]
    if (label === undefined) {
      return null
    }
    return {
      label: _.cloneDeep(label),
      shapes: _.cloneDeep(getShapes(state, itemIndex, labelId))
    }
  }

  /**
   * Remove a line if it currently exists.
   *
   * @param itemIndex the item the polyline belongs to
   * @param labelId the polyline's label id
   */
  private removeLine(itemIndex: number, labelId: IdType): void {
    const state = getState()
    if (state.task.items[itemIndex]?.labels[labelId] !== undefined) {
      dispatch(deleteLabel(itemIndex, labelId))
    }
  }

  /**
   * Replace a line with the given snapshot (delete it if present, then re-add).
   * Re-adding reuses the label id, so history entries referencing it stay valid.
   *
   * @param itemIndex the item the polyline belongs to
   * @param snapshot the geometry to set
   */
  private setLine(itemIndex: number, snapshot: LineSnapshot): void {
    this.removeLine(itemIndex, snapshot.label.id)
    dispatch(addLabel(itemIndex, snapshot.label, snapshot.shapes))
  }
}

/** Global singleton instance */
export const drawHistory = new DrawHistory()
