/**
 * Transient, non-Redux state for the stamp-along-path tool.
 *
 * The tool has two phases. ARMED: waiting for a click to pick the guide line.
 * PREVIEW: a guide line has been chosen and marks are being previewed along it
 * while the settings are tuned. Nothing reaches redux until the preview is
 * committed, so re-tuning replaces the marks instead of stacking duplicates.
 *
 * Settings persist across uses: period, angle and template are usually the same
 * for a whole batch of images, and re-entering them per stamp would defeat the
 * point of the tool.
 */

import {
  CustomTemplate,
  DEFAULT_STAMP_OPTIONS,
  StampOptions
} from "../drawable/2d/polyline_stamp_geometry"
import { IdType } from "../types/state"

let stampMode = false
let options: StampOptions = { ...DEFAULT_STAMP_OPTIONS }
let previewGuideId: IdType | null = null
let appliedIds: IdType[] = []
/** Where captured shapes live between sessions. */
const TEMPLATE_STORAGE_KEY = "scalabel.stampTemplates"

/**
 * Read saved shapes from local storage.
 *
 * Anything malformed is discarded rather than thrown: a corrupt entry should
 * cost the saved shapes, not break the annotator.
 */
function loadTemplates(): CustomTemplate[] {
  try {
    const raw = window.localStorage.getItem(TEMPLATE_STORAGE_KEY)
    if (raw === null) {
      return []
    }
    const parsed = JSON.parse(raw) as CustomTemplate[]
    if (!Array.isArray(parsed)) {
      return []
    }
    return parsed.filter(
      (t) =>
        typeof t?.name === "string" &&
        Array.isArray(t?.points) &&
        t.points.length >= 2
    )
  } catch {
    return []
  }
}

/** Write the saved shapes back to local storage. */
function persistTemplates(): void {
  try {
    window.localStorage.setItem(TEMPLATE_STORAGE_KEY, JSON.stringify(templates))
  } catch {
    // Storage can be unavailable (private browsing, quota). Losing persistence
    // is acceptable; failing the capture is not.
  }
}

let templates: CustomTemplate[] = loadTemplates()
let captureMode = false
let panelOpen = false

/**
 * Undo/redo for manual mark placement.
 *
 * Placed marks live here rather than in redux until they are applied, so
 * DrawHistory cannot see them. Without their own stack a mis-click could only
 * be fixed by clicking the mark again to remove it — workable, but not what
 * Ctrl+Z is expected to do.
 */
let positionUndo: number[][] = []
let positionRedo: number[][] = []

const listeners = new Set<() => void>()

/**
 * Handlers registered by the canvas, which owns the commit logic.
 *
 * The settings panel renders in the viewer (so it stays put while the image
 * pans) but Apply/Done must reach the canvas, so it publishes them here rather
 * than the two components being threaded together through props.
 */
let commitHandler: (() => void) | null = null
let finishHandler: (() => void) | null = null

/**
 * Register the canvas's commit and finish handlers.
 *
 * @param onCommit writes the marks at the current settings
 * @param onFinish closes the preview, keeping what was applied
 */
export function setStampHandlers(
  onCommit: (() => void) | null,
  onFinish: (() => void) | null
): void {
  commitHandler = onCommit
  finishHandler = onFinish
}

/** Ask the canvas to write the marks at the current settings. */
export function requestCommit(): void {
  commitHandler?.()
}

/** Ask the canvas to close the preview, keeping what was applied. */
export function requestFinish(): void {
  finishHandler?.()
}

/** Whether the stamp tool is armed, waiting for a guide line to be picked. */
export function isStampMode(): boolean {
  return stampMode
}

/**
 * Arm or disarm the stamp tool. Notifies only on an actual change.
 *
 * @param on whether the tool should be armed
 */
export function setStampMode(on: boolean): void {
  if (stampMode === on) {
    return
  }
  stampMode = on
  listeners.forEach((listener) => listener())
}

/** The guide line being previewed, or null when no preview is active. */
export function getPreviewGuide(): IdType | null {
  return previewGuideId
}

/** Whether marks are currently being previewed. */
export function isPreviewing(): boolean {
  return previewGuideId !== null
}

/**
 * Begin previewing marks along a guide line.
 *
 * @param labelId the guide line's label id
 */
export function startPreview(labelId: IdType): void {
  previewGuideId = labelId
  appliedIds = []
  positionUndo = []
  positionRedo = []
  // Positions are distances along the PREVIOUS guide, so they mean nothing on
  // a new one. Everything else (shape, angle, class) is deliberately kept:
  // those are usually the same for a whole batch.
  options = { ...options, positions: [] }
  stampMode = false
  panelOpen = true
  listeners.forEach((listener) => listener())
}

/**
 * The labels written by the most recent Apply on the open preview.
 *
 * Empty until Apply is pressed. Re-applying deletes these before adding the new
 * marks, so adjusting the settings after applying replaces the run rather than
 * stacking a second set on top of it.
 */
export function getAppliedIds(): IdType[] {
  return appliedIds
}

/**
 * Record the labels an Apply produced.
 *
 * @param ids the new labels
 */
export function setAppliedIds(ids: IdType[]): void {
  appliedIds = ids
  listeners.forEach((listener) => listener())
}

/** Whether the open preview has already been applied at least once. */
export function hasApplied(): boolean {
  return appliedIds.length > 0
}

/** End the preview, whether it was committed or cancelled. */
export function endPreview(): void {
  if (previewGuideId === null) {
    return
  }
  previewGuideId = null
  appliedIds = []
  positionUndo = []
  positionRedo = []
  listeners.forEach((listener) => listener())
}

/** The current stamp settings. */
export function getStampOptions(): StampOptions {
  return options
}

/**
 * Replace the stamp settings. Notifies so an active preview repaints.
 *
 * @param next the settings to use
 */
export function setStampOptions(next: StampOptions): void {
  options = { ...next }
  listeners.forEach((listener) => listener())
}

/**
 * Replace the placed marks, recording the change for undo.
 *
 * Use this rather than setStampOptions for anything that changes `positions`,
 * so every placement, removal and clear can be taken back.
 *
 * @param positions the new set of arc-length positions
 */
export function setPositions(positions: number[]): void {
  positionUndo = [...positionUndo, options.positions]
  positionRedo = []
  options = { ...options, positions }
  listeners.forEach((listener) => listener())
}

/** Whether a placement can be undone. */
export function canUndoPositions(): boolean {
  return positionUndo.length > 0
}

/** Whether an undone placement can be redone. */
export function canRedoPositions(): boolean {
  return positionRedo.length > 0
}

/**
 * Take back the last placement change.
 *
 * @returns true when something was undone
 */
export function undoPositions(): boolean {
  const previous = positionUndo.pop()
  if (previous === undefined) {
    return false
  }
  positionRedo = [...positionRedo, options.positions]
  options = { ...options, positions: previous }
  listeners.forEach((listener) => listener())
  return true
}

/**
 * Reapply the last undone placement change.
 *
 * @returns true when something was redone
 */
export function redoPositions(): boolean {
  const next = positionRedo.pop()
  if (next === undefined) {
    return false
  }
  positionUndo = [...positionUndo, options.positions]
  options = { ...options, positions: next }
  listeners.forEach((listener) => listener())
  return true
}

/** Restore the measured defaults. */
export function resetStampOptions(): void {
  options = { ...DEFAULT_STAMP_OPTIONS }
  listeners.forEach((listener) => listener())
}

/**
 * Whether the settings panel is open.
 *
 * Independent of whether a stamp is being previewed: the panel is where marks
 * are captured and settings are tuned, and both are worth doing before picking
 * a line rather than only in the middle of a preview.
 */
export function isPanelOpen(): boolean {
  return panelOpen
}

/**
 * Show or hide the settings panel. Notifies only on an actual change.
 *
 * @param on whether the panel should be visible
 */
export function setPanelOpen(on: boolean): void {
  if (panelOpen === on) {
    return
  }
  panelOpen = on
  listeners.forEach((listener) => listener())
}

/** Whether the tool is waiting for a mark to be clicked and captured. */
export function isCaptureMode(): boolean {
  return captureMode
}

/**
 * Arm or disarm capture mode. Notifies only on an actual change.
 *
 * @param on whether the tool should be waiting for a mark to capture
 */
export function setCaptureMode(on: boolean): void {
  if (captureMode === on) {
    return
  }
  captureMode = on
  listeners.forEach((listener) => listener())
}

/**
 * Custom mark shapes the user has captured.
 *
 * Persisted in local storage, so a shape captured once is available in every
 * later session — recapturing the same marking on every reload would make the
 * feature more trouble than drawing by hand.
 */
export function getTemplates(): CustomTemplate[] {
  return templates
}

/**
 * Add a captured shape to the picker and select it.
 *
 * @param template the captured shape
 */
export function addTemplate(template: CustomTemplate): void {
  templates = [...templates, template]
  persistTemplates()
  listeners.forEach((listener) => listener())
}

/**
 * Remove a captured shape.
 *
 * @param name the template's name
 */
export function removeTemplate(name: string): void {
  templates = templates.filter((t) => t.name !== name)
  persistTemplates()
  listeners.forEach((listener) => listener())
}

/**
 * Rename a saved shape.
 *
 * Captured shapes are named "Mark 1", "Mark 2" and so on, which says nothing
 * about what they are; a name like "arrow" or "double tick" is what makes a
 * library of them usable.
 *
 * @param from the current name
 * @param to the new name
 */
export function renameTemplate(from: string, to: string): void {
  const trimmed = to.trim()
  if (trimmed === "" || templates.some((t) => t.name === trimmed)) {
    return
  }
  templates = templates.map((t) =>
    t.name === from ? { ...t, name: trimmed } : t
  )
  persistTemplates()
  listeners.forEach((listener) => listener())
}

/**
 * Subscribe to stamp state changes.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onStampChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
