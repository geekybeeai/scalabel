/**
 * Shapes the corrections work on.
 *
 * These are deliberately looser than `ItemExport` and `LabelExport` from
 * ../../types/export. The corrections run on whatever JSON the user uploaded,
 * before Scalabel has normalised any of it, so fields the export types mark as
 * required are routinely absent and vertices arrive as plain number arrays
 * rather than tuples. Treating that input as fully-formed would mean either
 * lying to the compiler with casts at every access or rejecting documents the
 * Python implementation accepted.
 */

/** A polyline vertex: [x, y]. */
export type Vertex = number[]

/**
 * One `poly2d` entry of a label.
 */
export interface PolyLike {
  /** the vertices, in order */
  vertices?: Vertex[]
  /** per-vertex curve flags, one character per vertex */
  types?: string
  /** whether the polyline closes back on itself */
  closed?: boolean
  /** anything else the document carries, preserved untouched */
  [key: string]: unknown
}

/**
 * One label of a frame.
 */
export interface LabelLike {
  /** label id */
  id?: string | number
  /** label category */
  category?: string
  /** polyline shapes, absent for non-polyline labels */
  poly2d?: PolyLike[] | null
  /** anything else the document carries, preserved untouched */
  [key: string]: unknown
}

/**
 * One frame of a document.
 */
export interface FrameLike {
  /** frame name, a path relative to the data root */
  name?: string
  /** the frame's labels */
  labels?: LabelLike[] | null
  /** anything else the document carries, preserved untouched */
  [key: string]: unknown
}

/**
 * A full Scalabel export document.
 */
export interface DocumentLike {
  /** the frames */
  frames?: FrameLike[]
  /** anything else the document carries, preserved untouched */
  [key: string]: unknown
}

/**
 * Knobs for one run. Defaults are the measured-sane values.
 */
export interface Options {
  /** pull out-of-ROI vertices back inside */
  clamp: boolean
  /** join near-touching endpoints */
  connect: boolean
  /** brightness above which a pixel counts as imagery */
  threshold: number
  /** endpoint connect radius, in image pixels */
  tolerance: number
  /** junction angle guard in degrees; 0 disables it */
  minAngle: number
  /** pixels to nudge a clamped vertex inward */
  inset: number
  /** report corrections that moved further than this */
  flagDistance: number
  /** prefix used to resolve the relative image paths in frame names */
  imageRoot: string
  /** where to memoise ROI masks; undefined disables caching */
  cacheDir?: string
  /** fail the run on a missing image instead of skipping the clamp stage */
  strict: boolean
}
