/** Undo/redo target while the stamp panel is open. */
export enum StampHistoryTarget {
  /** The marks are still pending in the manual placement stack. */
  POSITIONS = "positions",
  /** The marks have been applied and are recorded in draw history. */
  HISTORY = "history"
}

/**
 * Choose the correct undo/redo stack for the current stamp state.
 *
 * @param options current stamp preview state
 * @param options.previewing whether a guide preview is open
 * @param options.evenSpacing whether placement is panel-controlled
 * @param options.hasApplied whether this preview has already committed marks
 */
export function getStampHistoryTarget(options: {
  previewing: boolean
  evenSpacing: boolean
  hasApplied: boolean
}): StampHistoryTarget {
  if (options.previewing && !options.evenSpacing && !options.hasApplied) {
    return StampHistoryTarget.POSITIONS
  }
  return StampHistoryTarget.HISTORY
}

/**
 * Run a history action and close an applied stamp preview when that action
 * changed the document.
 *
 * The history stack removes the committed labels correctly, but the stamp
 * preview is transient UI state and must be cleared separately.
 *
 * @param historyAction the undo or redo operation
 * @param hasAppliedStamp whether the open preview has committed labels
 * @param clearStampPreview callback that clears the transient preview
 */
export function runStampHistoryAction(
  historyAction: () => boolean,
  hasAppliedStamp: boolean,
  clearStampPreview: () => void
): boolean {
  const changed = historyAction()
  if (changed && hasAppliedStamp) {
    clearStampPreview()
  }
  return changed
}
