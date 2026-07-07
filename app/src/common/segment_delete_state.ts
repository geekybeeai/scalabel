import { DeleteSitePick } from "../drawable/2d/polyline_cut_geometry"
import { IdType, SimplePathPoint2DType } from "../types/state"
import { isCutMode, onCutModeChange, setCutMode } from "./cut_state"

/**
 * Transient, non-Redux state machine for the delete-segment tool.
 *
 * Phases: inactive -> awaitFirst -> awaitSecond -> preview -> (commit or
 * cancel) -> inactive. Picks and the preview live entirely here; redux is
 * only touched when the pending delete commits, so cancelling at any phase
 * needs no rollback. Mirrors cut_state.ts, with a payload per phase.
 */
export type SegmentDeletePhase =
  | "inactive"
  | "awaitFirst"
  | "awaitSecond"
  | "preview"

/** Data captured at the first pick. */
export interface SegmentDeletePickData {
  /** item the polyline belongs to */
  itemIndex: number
  /** the picked polyline's label id */
  labelId: IdType
  /** the first pick */
  pick1: DeleteSitePick
  /** the first pick's resolved coordinate (image frame), for the halo */
  pick1Point: { x: number; y: number }
  /** the polyline's stored vertices at pick time (staleness guard) */
  points: SimplePathPoint2DType[]
}

/** Data available during the preview countdown. */
export interface SegmentDeletePreviewData extends SegmentDeletePickData {
  /** the earlier pick along the line (normalized order) */
  first: DeleteSitePick
  /** the later pick along the line (normalized order) */
  second: DeleteSitePick
  /** the doomed path, for the marching-ants overlay */
  doomed: SimplePathPoint2DType[]
}

let phase: SegmentDeletePhase = "inactive"
let pickData: SegmentDeletePickData | null = null
let previewData: SegmentDeletePreviewData | null = null

const listeners = new Set<() => void>()

/** Notify all listeners of a state change. */
function notify(): void {
  listeners.forEach((listener) => listener())
}

/** The current phase of the delete-segment tool. */
export function getSegmentDeletePhase(): SegmentDeletePhase {
  return phase
}

/** Whether the tool owns canvas clicks (any phase but inactive). */
export function isSegmentDeleteActive(): boolean {
  return phase !== "inactive"
}

/** Arm the tool (disarms the cut tool — the tools are mutually exclusive). */
export function armSegmentDelete(): void {
  setCutMode(false)
  if (phase === "awaitFirst") {
    return
  }
  phase = "awaitFirst"
  pickData = null
  previewData = null
  notify()
}

/**
 * Record a validated first pick and await the second.
 *
 * @param data the pick and its context
 */
export function recordFirstPick(data: SegmentDeletePickData): void {
  phase = "awaitSecond"
  pickData = data
  previewData = null
  notify()
}

/**
 * Record a validated second pick and start the preview.
 *
 * @param first the earlier pick along the line (normalized order)
 * @param second the later pick along the line (normalized order)
 * @param doomed the removed path, for the overlay
 */
export function recordSecondPick(
  first: DeleteSitePick,
  second: DeleteSitePick,
  doomed: SimplePathPoint2DType[]
): void {
  if (pickData === null) {
    return
  }
  phase = "preview"
  previewData = { ...pickData, first, second, doomed }
  notify()
}

/** The pick-1 context (awaitSecond and preview phases), else null. */
export function getPickData(): SegmentDeletePickData | null {
  return phase === "awaitSecond" || phase === "preview" ? pickData : null
}

/** The preview payload (preview phase only), else null. */
export function getPreviewData(): SegmentDeletePreviewData | null {
  return phase === "preview" ? previewData : null
}

/** Cancel/finish: back to inactive. Safe to call in any phase. */
export function resetSegmentDelete(): void {
  if (phase === "inactive") {
    return
  }
  phase = "inactive"
  pickData = null
  previewData = null
  notify()
}

/**
 * Subscribe to phase changes.
 *
 * @param listener called after every transition
 * @returns an unsubscribe function
 */
export function onSegmentDeleteChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

// Mutual exclusion: arming the cut tool cancels any pending segment delete.
onCutModeChange(() => {
  if (isCutMode()) {
    resetSegmentDelete()
  }
})
