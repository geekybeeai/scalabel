/**
 * The public API: correct a Scalabel annotation document.
 *
 * One call applies both fixes to a parsed export document and hands back a
 * corrected copy plus a report. There is no file I/O beyond reading the images
 * and no global state here, so the same function serves a batch of 221 frames
 * and a single frame.
 *
 * Stage order is deliberate: CLAMP FIRST, THEN CONNECT.
 *
 * Out-of-ROI vertices are frequently line ENDPOINTS sitting in the black padding,
 * and endpoints are exactly what auto-connect reasons about. The measured median
 * spill (8 px) is the same order of magnitude as the connect tolerance (15 px), so
 * connecting before clamping would evaluate junctions at positions that are about
 * to move — and clamping afterwards could pull an already-merged junction apart.
 * Clamping first means every connection decision is made on final geometry.
 */

import * as fs from "fs-extra"
import * as path from "path"

import {
  DEFAULT_MIN_ANGLE,
  DEFAULT_TOLERANCE,
  connectLabels
} from "./autoconnect"
import {
  DEFAULT_FLAG_DISTANCE,
  DEFAULT_INSET,
  clampLabels,
  correctionToJson,
  flagged
} from "./clamp"
import { DEFAULT_THRESHOLD, loadRoiMask } from "./mask"
import { DocumentLike, FrameLike, LabelLike, Options } from "./types"

export { DEFAULT_THRESHOLD } from "./mask"
export { DEFAULT_TOLERANCE, DEFAULT_MIN_ANGLE } from "./autoconnect"
export { DEFAULT_INSET, DEFAULT_FLAG_DISTANCE } from "./clamp"

/**
 * Fill in the defaults for any option the caller left out.
 *
 * @param partial the options the caller supplied
 */
export function makeOptions(partial: Partial<Options> = {}): Options {
  return {
    clamp: partial.clamp ?? true,
    connect: partial.connect ?? true,
    threshold: partial.threshold ?? DEFAULT_THRESHOLD,
    tolerance: partial.tolerance ?? DEFAULT_TOLERANCE,
    minAngle: partial.minAngle ?? DEFAULT_MIN_ANGLE,
    inset: partial.inset ?? DEFAULT_INSET,
    flagDistance: partial.flagDistance ?? DEFAULT_FLAG_DISTANCE,
    imageRoot: partial.imageRoot ?? "",
    cacheDir: partial.cacheDir,
    strict: partial.strict ?? false
  }
}

/**
 * What happened to one frame.
 */
export interface FrameReport {
  /** the frame's name */
  name: string
  /** whether the frame's image could be located */
  imageFound: boolean
  /** fraction of the canvas inside the ROI, or null when no image was read */
  roiCoverage: number | null
  /** every poly2d vertex seen */
  totalVertices: number
  /** how many vertices were pulled back inside the ROI */
  clamped: number
  /** corrections large enough to warrant review */
  flagged: Array<{ [key: string]: unknown }>
  /** how many polyline pairs were joined */
  merged: number
  /** label count before correction */
  labelsBefore: number
  /** label count after correction */
  labelsAfter: number
  /** details of each merge */
  connections: Array<{ [key: string]: unknown }>
  /** why this frame's clamp stage failed, when it did */
  error: string | null
}

/**
 * Aggregate outcome across every frame in a document.
 */
export interface Report {
  /** aggregate counts */
  summary: {
    /** how many frames were processed */
    frames: number
    /** vertices moved back inside the ROI */
    totalClamped: number
    /** polyline pairs joined */
    totalMerged: number
    /** corrections large enough to warrant review */
    totalFlagged: number
    /** frames whose image could not be located */
    missingImages: string[]
  }
  /** the per-frame detail */
  frames: FrameReport[]
}

/**
 * Round to a fixed number of decimal places.
 *
 * @param value the number to round
 * @param places how many decimal places to keep
 */
function round(value: number, places: number): number {
  const factor = Math.pow(10, places)
  return Math.round(value * factor) / factor
}

/**
 * Render an unknown thrown value the way Python renders an exception.
 *
 * @param error whatever was thrown
 */
function describe(error: unknown): string {
  if (error instanceof Error) {
    return `${error.name}: ${error.message}`
  }
  return String(error)
}

/**
 * Locate a frame's image.
 *
 * Frame names in these documents are paths relative to the data root (for
 * example `items/.../images/208683_verification.png`), but a document may also
 * carry an absolute path or a bare filename. Tries the obvious interpretations
 * and gives up rather than guessing wildly.
 *
 * @param name the frame's name
 * @param imageRoot prefix for relative paths
 */
export async function resolveImagePath(
  name: string,
  imageRoot: string
): Promise<string | null> {
  if (name === "") {
    return null
  }

  const candidates = [name]
  if (imageRoot !== "") {
    candidates.push(path.join(imageRoot, name))
    candidates.push(path.join(imageRoot, path.basename(name)))
  }

  for (const candidate of candidates) {
    try {
      if ((await fs.stat(candidate)).isFile()) {
        return candidate
      }
    } catch {
      // Not there, or not readable: try the next interpretation.
    }
  }
  return null
}

/**
 * Correct a single frame in place, returning its report.
 *
 * The frame is mutated, so pass a copy if the original matters. Both stages are
 * independently switchable; a frame whose image is missing still gets the
 * connect stage, which needs no pixels.
 *
 * @param frame the frame to correct, mutated in place
 * @param options the run's settings
 */
export async function processFrame(
  frame: FrameLike,
  options: Options
): Promise<FrameReport> {
  const name = String(frame.name ?? "")
  const labels: LabelLike[] = frame.labels ?? []

  const report: FrameReport = {
    name,
    imageFound: true,
    roiCoverage: null,
    totalVertices: 0,
    clamped: 0,
    flagged: [],
    merged: 0,
    labelsBefore: labels.length,
    labelsAfter: labels.length,
    connections: [],
    error: null
  }

  if (options.clamp) {
    const imagePath = await resolveImagePath(name, options.imageRoot)
    if (imagePath === null) {
      report.imageFound = false
      if (options.strict) {
        throw new Error(`image not found for frame: ${name}`)
      }
    } else {
      try {
        const roi = await loadRoiMask(
          imagePath,
          options.threshold,
          options.cacheDir
        )
        report.roiCoverage = round(roi.coverage, 5)
        const clampResult = clampLabels(roi, labels, options.inset)
        report.totalVertices = clampResult.totalVertices
        report.clamped = clampResult.corrections.length
        report.flagged = flagged(clampResult, options.flagDistance).map(
          correctionToJson
        )
      } catch (error) {
        report.error = describe(error)
        if (options.strict) {
          throw error
        }
      }
    }
  }

  if (options.connect && labels.length > 0) {
    const connected = connectLabels(labels, options.tolerance, options.minAngle)
    frame.labels = connected.labels
    report.merged = connected.result.connections.length
    report.labelsAfter = connected.result.labelsAfter
    report.connections = connected.result.connections.map((connection) => {
      const payload: { [key: string]: unknown } = {
        keptId: connection.keptId,
        absorbedId: connection.absorbedId,
        category: connection.category,
        junction: connection.junction.map((v) => round(v, 3)),
        gap: round(connection.gap, 3)
      }
      if (connection.angle !== null) {
        payload.angle = round(connection.angle, 2)
      }
      return payload
    })
  }

  return report
}

/**
 * Correct every frame in a Scalabel export document.
 *
 * Accepts either a full document (`{"frames": [...], "config": {...}}`) or a
 * bare list of frames, and returns the same shape it was given. The input is
 * deep-copied, so the caller's document is never modified.
 *
 * @param document the document or bare frame list to correct
 * @param options the run's settings
 */
export async function processDocument(
  document: DocumentLike | FrameLike[],
  options: Options = makeOptions()
): Promise<{
  /** the corrected document, in the shape it arrived */
  document: DocumentLike | FrameLike[]
  /** what changed */
  report: Report
}> {
  // The documents here are parsed JSON by construction, so a serialise round
  // trip is a faithful deep copy and avoids depending on structuredClone.
  const copy = JSON.parse(JSON.stringify(document)) as
    | DocumentLike
    | FrameLike[]

  const bareList = Array.isArray(copy)
  const frames: FrameLike[] = bareList ? copy : copy.frames ?? []

  const frameReports: FrameReport[] = []
  for (const frame of frames) {
    frameReports.push(await processFrame(frame, options))
  }

  const report: Report = {
    summary: {
      frames: frameReports.length,
      totalClamped: frameReports.reduce((total, f) => total + f.clamped, 0),
      totalMerged: frameReports.reduce((total, f) => total + f.merged, 0),
      totalFlagged: frameReports.reduce(
        (total, f) => total + f.flagged.length,
        0
      ),
      missingImages: frameReports
        .filter((f) => !f.imageFound)
        .map((f) => f.name)
    },
    frames: frameReports
  }

  return { document: copy, report }
}
