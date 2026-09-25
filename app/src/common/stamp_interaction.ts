/** The canvas action owned by the stamp tool for the current click. */
export enum StampCanvasClickAction {
  /** The tool is waiting for a guide line. */
  PICK_GUIDE = "pick-guide",
  /** Manual placement records one pending mark at the click. */
  PLACE_MANUAL = "place-manual",
  /** An even-spacing preview ignores canvas clicks. */
  IGNORE_PREVIEW = "ignore-preview",
  /** No stamp gesture owns the click. */
  NONE = "none"
}

/**
 * Resolve the stamp tool's canvas-click behavior.
 *
 * Even-spacing previews are intentionally panel-controlled: a click on the
 * image must not accidentally write labels while the user is inspecting or
 * tuning the preview.
 *
 * @param options current stamp interaction state
 * @param options.previewing whether a guide preview is open
 * @param options.evenSpacing whether the preview is panel-controlled
 * @param options.armed whether the tool is waiting for a guide pick
 */
export function getStampCanvasClickAction(options: {
  previewing: boolean
  evenSpacing: boolean
  armed: boolean
}): StampCanvasClickAction {
  if (options.previewing) {
    return options.evenSpacing
      ? StampCanvasClickAction.IGNORE_PREVIEW
      : StampCanvasClickAction.PLACE_MANUAL
  }
  if (options.armed) {
    return StampCanvasClickAction.PICK_GUIDE
  }
  return StampCanvasClickAction.NONE
}
