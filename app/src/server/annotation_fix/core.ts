/**
 * Correct a Scalabel annotation document: the entry point the worker calls.
 *
 * Stage order is deliberate: CLAMP FIRST, THEN CONNECT. Out-of-ROI vertices
 * are frequently line endpoints in the black padding, and endpoints are
 * exactly what auto-connect reasons about. Median spill (8 px) is the same
 * order as the connect tolerance (15 px), so connecting before clamping would
 * evaluate junctions at positions about to move, and clamping afterwards
 * could pull a merged junction apart.
 */

import * as fs from "fs"
import * as http from "http"
import * as https from "https"
import * as os from "os"
import * as path from "path"

import { ItemExport } from "../../types/export"
import {
  connectionToDict,
  connectLabels,
  DEFAULT_MIN_ANGLE,
  DEFAULT_TOLERANCE
} from "./autoconnect"
import {
  clampLabels,
  correctionToDict,
  DEFAULT_FLAG_DISTANCE,
  DEFAULT_INSET,
  flaggedCorrections
} from "./clamp"
import { computeRoiMask, DEFAULT_THRESHOLD } from "./mask"

/** One frame of an export document. */
export type Frame = Partial<ItemExport>

/** A full export document or a bare list of frames. */
export type Document = Frame[] | { frames?: Frame[]; [key: string]: unknown }

/** Knobs for one run. */
export interface Options {
  /** run the ROI clamp stage */
  clamp: boolean
  /** run the auto-connect stage */
  connect: boolean
  /** black-padding threshold */
  threshold: number
  /** connect radius in image pixels */
  tolerance: number
  /** junction angle guard in degrees, 0 disables */
  minAngle: number
  /** inward nudge for clamped vertices */
  inset: number
  /** report corrections beyond this distance */
  flagDistance: number
  /** root for relative image paths */
  imageRoot: string
  /**
   * When a frame's image is not on disk but its `url` is http(s), download
   * it for the clamp stage. Embedded sessions pass images this way.
   */
  fetchRemote: boolean
}

/** The measured-sane defaults. */
export function defaultOptions(): Options {
  return {
    clamp: true,
    connect: true,
    threshold: DEFAULT_THRESHOLD,
    tolerance: DEFAULT_TOLERANCE,
    minAngle: DEFAULT_MIN_ANGLE,
    inset: DEFAULT_INSET,
    flagDistance: DEFAULT_FLAG_DISTANCE,
    imageRoot: "",
    fetchRemote: true
  }
}

/** How long one image download may take. */
export const REMOTE_FETCH_TIMEOUT_MS = 60000

/** Largest image download accepted. */
export const REMOTE_FETCH_MAX_BYTES = 1024 * 1024 * 1024

/** Redirects followed before giving up. */
const REMOTE_FETCH_MAX_REDIRECTS = 5

/**
 * Whether a frame url points at a web resource rather than a file.
 *
 * @param url the frame url
 */
export function isRemoteUrl(url: unknown): url is string {
  return typeof url === "string" && /^https?:\/\//i.test(url)
}

/**
 * Download an http(s) image to a temporary file.
 *
 * Streams to disk with a byte cap and a timeout, following a few redirects.
 * Rejects on any failure; the caller reports it and skips the clamp stage.
 *
 * @param url the image url
 * @param timeoutMs abort after this long
 * @param maxBytes abort beyond this many bytes
 */
export async function fetchImageToTemp(
  url: string,
  timeoutMs: number = REMOTE_FETCH_TIMEOUT_MS,
  maxBytes: number = REMOTE_FETCH_MAX_BYTES
): Promise<string> {
  const dir = await fs.promises.mkdtemp(
    path.join(os.tmpdir(), "annotation-fix-image-")
  )
  const pathExt = path.extname(new URL(url).pathname)
  const ext = pathExt === "" ? ".img" : pathExt
  const file = path.join(dir, `image${ext}`)

  const download = async (target: string, redirects: number): Promise<void> =>
    await new Promise((resolve, reject) => {
      const client = target.startsWith("https") ? https : http
      const request = client.get(target, (response) => {
        const status = response.statusCode ?? 0
        const location = response.headers.location
        if (status >= 300 && status < 400 && location !== undefined) {
          response.resume()
          if (redirects >= REMOTE_FETCH_MAX_REDIRECTS) {
            reject(new Error("too many redirects"))
            return
          }
          download(new URL(location, target).toString(), redirects + 1).then(
            resolve,
            reject
          )
          return
        }
        if (status !== 200) {
          response.resume()
          reject(new Error(`HTTP ${status}`))
          return
        }
        let received = 0
        const out = fs.createWriteStream(file)
        response.on("data", (chunk: Buffer) => {
          received += chunk.length
          if (received > maxBytes) {
            request.destroy(new Error("image exceeds the size limit"))
          }
        })
        response.on("error", reject)
        out.on("error", reject)
        out.on("finish", resolve)
        response.pipe(out)
      })
      request.setTimeout(timeoutMs, () => {
        request.destroy(new Error(`timed out after ${timeoutMs}ms`))
      })
      request.on("error", reject)
    })

  try {
    await download(url, 0)
  } catch (error) {
    await fs.promises.rm(dir, { recursive: true, force: true })
    throw error
  }
  return file
}

/** What happened to one frame. */
export interface FrameReport {
  /** frame name */
  name: string
  /** whether the image resolved */
  imageFound: boolean
  /** fraction of the canvas inside the ROI */
  roiCoverage: number | null
  /** vertices examined */
  totalVertices: number
  /** vertices moved */
  clamped: number
  /** serialised large corrections */
  flagged: Array<{ [key: string]: unknown }>
  /** merges applied */
  merged: number
  /** label count before */
  labelsBefore: number
  /** label count after */
  labelsAfter: number
  /** serialised connections */
  connections: Array<{ [key: string]: unknown }>
  /** clamp-stage error, if any */
  error: string | null
}

/** Aggregate outcome across every frame. */
export interface Report {
  /** per-frame reports */
  frames: FrameReport[]
}

/** Serialised report, matching the Python `Report.to_dict()` layout. */
export interface ReportDict {
  /** aggregate counts */
  summary: {
    /** frames processed */
    frames: number
    /** vertices moved */
    totalClamped: number
    /** merges applied */
    totalMerged: number
    /** corrections beyond the review threshold */
    totalFlagged: number
    /** frames whose image could not be located */
    missingImages: string[]
  }
  /** per-frame reports */
  frames: FrameReport[]
}

/**
 * Serialise the whole report.
 *
 * @param report the report
 */
export function reportToDict(report: Report): ReportDict {
  return {
    summary: {
      frames: report.frames.length,
      totalClamped: report.frames.reduce((n, f) => n + f.clamped, 0),
      totalMerged: report.frames.reduce((n, f) => n + f.merged, 0),
      totalFlagged: report.frames.reduce((n, f) => n + f.flagged.length, 0),
      missingImages: report.frames
        .filter((f) => !f.imageFound)
        .map((f) => f.name)
    },
    frames: report.frames.map((f) => ({
      ...f,
      roiCoverage:
        f.roiCoverage === null ? null : Math.round(f.roiCoverage * 1e5) / 1e5
    }))
  }
}

/**
 * Locate a frame's image: as given, under the root, or by basename under the
 * root.
 *
 * @param name frame name
 * @param imageRoot root for relative names
 */
export function resolveImagePath(
  name: string,
  imageRoot: string
): string | null {
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
      if (fs.statSync(candidate).isFile()) {
        return candidate
      }
    } catch {
      // not there
    }
  }
  return null
}

/**
 * Correct a single frame in place. A frame whose image is missing still gets
 * the connect stage, which needs no pixels.
 *
 * @param frame the frame, mutated
 * @param options run options
 */
export async function processFrame(
  frame: Frame,
  options: Options
): Promise<FrameReport> {
  const name = String(frame.name ?? "")
  const labels = frame.labels ?? []
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
    let imagePath = resolveImagePath(name, options.imageRoot)
    let tempFile: string | null = null
    if (imagePath === null && options.fetchRemote && isRemoteUrl(frame.url)) {
      // Embedded sessions hand over a signed URL instead of a local file.
      try {
        tempFile = await fetchImageToTemp(frame.url)
        imagePath = tempFile
      } catch (error) {
        report.error = `image download failed: ${
          error instanceof Error ? error.message : String(error)
        }`
      }
    }
    if (imagePath === null) {
      report.imageFound = false
    } else {
      try {
        const roi = await computeRoiMask(imagePath, options.threshold)
        report.roiCoverage = roi.coverage
        const clampResult = clampLabels(roi, labels, options.inset)
        report.totalVertices = clampResult.totalVertices
        report.clamped = clampResult.corrections.length
        report.flagged = flaggedCorrections(
          clampResult,
          options.flagDistance
        ).map(correctionToDict)
      } catch (error) {
        report.error =
          error instanceof Error
            ? `${error.name}: ${error.message}`
            : String(error)
      }
    }
    if (tempFile !== null) {
      await fs.promises.rm(path.dirname(tempFile), {
        recursive: true,
        force: true
      })
    }
  }

  if (options.connect && labels.length > 0) {
    const [survivors, connectResult] = connectLabels(
      labels,
      options.tolerance,
      options.minAngle
    )
    frame.labels = survivors
    report.merged = connectResult.connections.length
    report.labelsAfter = connectResult.labelsAfter
    report.connections = connectResult.connections.map(connectionToDict)
  }

  return report
}

/**
 * Correct every frame in a document. Accepts a full document or a bare list
 * of frames and returns the same shape. The input is deep-copied.
 *
 * @param document the document
 * @param options run options
 */
export async function processDocument(
  document: Document,
  options: Options = defaultOptions()
): Promise<[Document, Report]> {
  const copy = JSON.parse(JSON.stringify(document)) as Document
  const frames: Frame[] = Array.isArray(copy) ? copy : copy.frames ?? []
  const report: Report = { frames: [] }
  for (const frame of frames) {
    report.frames.push(await processFrame(frame, options))
  }
  return [copy, report]
}
