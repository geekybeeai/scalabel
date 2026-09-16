/**
 * Runs the annotation-fix corrections at project creation, when the
 * "Auto-correct annotations" box is ticked.
 *
 * ROI clamp: the orthomosaic images pad a narrow captured footprint into a
 * large rectangle with black. Vertices that drift into the padding are pulled
 * back to the nearest valid pixel.
 *
 * Auto-connect: polyline endpoints of compatible categories that nearly touch
 * are spliced into one label, the batch equivalent of the editor's
 * drag-endpoint-onto-endpoint gesture.
 *
 * The engine lives in ./annotation_fix and is compiled by webpack into
 * app/dist/annotation_fix_worker.js. It runs as a SHORT-LIVED CHILD PROCESS
 * of the same Node binary for the duration of one task rather than as a
 * standing service: nothing to start by hand, no port to collide with, no
 * stale daemon holding old code, and a crash or OOM in the child cannot take
 * the server down. The request goes in on stdin and the corrected document
 * comes back on stdout.
 *
 * Correction is best-effort. If the worker script is missing, sharp fails to
 * load, or the child fails for any reason, the ORIGINAL annotations are
 * returned and the reason is logged. Failing an entire import because a
 * helper broke would be the wrong trade.
 */

import { spawn } from "child_process"
import * as path from "path"

import { ItemExport } from "../types/export"
import Logger from "./logger"

/** How long the child gets before it is killed. */
const DEFAULT_TIMEOUT_MS = 1800000

/** Stdout cap. Corrected documents for a large batch run to tens of MB. */
const MAX_OUTPUT_BYTES = 1024 * 1024 * 1024

/**
 * Endpoint gap, in image pixels, within which two polylines are joined.
 *
 * The engine's own default (15 px) mirrors the editor's snap radius, but on
 * real batches many genuine continuations sit 15-40 px apart, so the server
 * asks for more. SCALABEL_ANNOTATION_FIX_TOLERANCE overrides it.
 */
export const DEFAULT_CONNECT_TOLERANCE = 40

/**
 * Minimum angle, in degrees, for a production auto-connect candidate.
 *
 * 180 is perfectly straight; 150 permits a 30 degree continuation while
 * rejecting forks and offset parallel lines. Set the environment override to
 * 0 to retain the engine's legacy distance-only behavior.
 */
export const DEFAULT_CONNECT_MIN_ANGLE = 150

/** Log hint appended to every skip. */
const DIAGNOSE_HINT =
  "Annotation auto-correct SKIPPED — the project was created with UNCORRECTED annotations. Diagnose with: node app/dist/annotation_fix_worker.js --preflight"

/**
 * Summary of what the corrections changed, for the server log.
 */
export interface AnnotationFixSummary {
  /** number of frames processed */
  frames: number
  /** vertices pulled back inside the region of interest */
  totalClamped: number
  /** polyline pairs joined into one label */
  totalMerged: number
  /** corrections large enough to warrant a human look */
  totalFlagged: number
  /** frames whose image could not be found */
  missingImages: string[]
}

/**
 * Options for one correction run.
 */
export interface AnnotationFixOptions {
  /** path to the compiled worker script */
  workerScript?: string
  /** prefix used to resolve the relative image paths in frame names */
  imageRoot?: string
  /** how long to allow before killing the child */
  timeoutMs?: number
  /** endpoint gap within which polylines are joined, in image pixels */
  tolerance?: number
  /** minimum continuation angle in degrees; 0 disables the geometry guard */
  minAngle?: number
}

/**
 * Connect tolerance to request, from SCALABEL_ANNOTATION_FIX_TOLERANCE when
 * it holds a positive number and DEFAULT_CONNECT_TOLERANCE otherwise.
 */
export function getConnectTolerance(): number {
  const configured = process.env.SCALABEL_ANNOTATION_FIX_TOLERANCE
  if (configured !== undefined && configured !== "") {
    const value = Number(configured)
    if (Number.isFinite(value) && value > 0) {
      return value
    }
    Logger.warning(
      `Annotation auto-correct: ignoring SCALABEL_ANNOTATION_FIX_TOLERANCE=` +
        `"${configured}" (not a positive number); using ${DEFAULT_CONNECT_TOLERANCE}`
    )
  }
  return DEFAULT_CONNECT_TOLERANCE
}

/**
 * Minimum continuation angle to request, from
 * SCALABEL_ANNOTATION_FIX_MIN_ANGLE when it is in the inclusive range 0..180,
 * and DEFAULT_CONNECT_MIN_ANGLE otherwise.
 */
export function getConnectMinAngle(): number {
  const configured = process.env.SCALABEL_ANNOTATION_FIX_MIN_ANGLE
  if (configured !== undefined && configured !== "") {
    const value = Number(configured)
    if (Number.isFinite(value) && value >= 0 && value <= 180) {
      return value
    }
    Logger.warning(
      `Annotation auto-correct: ignoring SCALABEL_ANNOTATION_FIX_MIN_ANGLE=` +
        `"${configured}" (not a number from 0 through 180); using ${DEFAULT_CONNECT_MIN_ANGLE}`
    )
  }
  return DEFAULT_CONNECT_MIN_ANGLE
}

/**
 * The compiled worker script. Webpack emits it beside main.js, so it is found
 * relative to this bundle; SCALABEL_ANNOTATION_FIX_WORKER overrides that for
 * unusual layouts.
 */
export function getWorkerScript(): string {
  const configured = process.env.SCALABEL_ANNOTATION_FIX_WORKER
  if (configured !== undefined && configured !== "") {
    return path.resolve(configured)
  }
  return path.join(__dirname, "annotation_fix_worker.js")
}

/**
 * Root the corrections use to resolve relative image paths.
 *
 * Frame names are stored relative to the data root, so this must be an
 * absolute path: a relative one would resolve against the child's working
 * directory and every image would come back not-found, silently skipping the
 * clamp stage while auto-connect still ran.
 */
export function getImageRoot(): string {
  const configured = process.env.SCALABEL_ANNOTATION_FIX_IMAGE_ROOT
  if (configured !== undefined && configured !== "") {
    return path.resolve(configured)
  }
  return path.join(process.cwd(), "local-data")
}

/**
 * Response shape from the worker child.
 */
interface FixResponse {
  /** whether the correction succeeded */
  ok?: boolean
  /** corrected frames */
  document?: Array<Partial<ItemExport>>
  /** what changed */
  report?: {
    /** aggregate counts */
    summary?: AnnotationFixSummary
  }
  /** why it failed */
  error?: string
}

/**
 * Spawn the corrector, feed it the request, and collect its response.
 *
 * @param request the payload to send on stdin
 * @param options worker script and timeout
 */
async function runCorrector(
  request: string,
  options: AnnotationFixOptions
): Promise<FixResponse> {
  const workerScript = options.workerScript ?? getWorkerScript()
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS

  return await new Promise<FixResponse>((resolve, reject) => {
    // Same binary as the server, so whatever ran main.js can run the worker.
    const child = spawn(process.execPath, [workerScript], {
      env: process.env,
      windowsHide: true
    })

    const stdout: Buffer[] = []
    let stdoutBytes = 0
    let stderr = ""
    let settled = false

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true
        child.kill("SIGKILL")
        reject(new Error(`timed out after ${timeoutMs}ms`))
      }
    }, timeoutMs)

    child.stdout.on("data", (chunk: Buffer) => {
      stdoutBytes += chunk.length
      if (stdoutBytes > MAX_OUTPUT_BYTES) {
        if (!settled) {
          settled = true
          clearTimeout(timer)
          child.kill("SIGKILL")
          reject(new Error("corrected document exceeded the size limit"))
        }
        return
      }
      stdout.push(chunk)
    })

    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString()
    })

    child.on("error", (error: Error) => {
      if (!settled) {
        settled = true
        clearTimeout(timer)
        reject(error)
      }
    })

    child.on("close", (code: number | null) => {
      if (settled) {
        return
      }
      settled = true
      clearTimeout(timer)

      const raw = Buffer.concat(stdout).toString()
      if (raw === "") {
        const detail = stderr.trim().split("\n").slice(-3).join("; ")
        reject(
          new Error(
            `no output (exit ${String(code)})${
              detail !== "" ? `: ${detail}` : ""
            }`
          )
        )
        return
      }

      try {
        resolve(JSON.parse(raw) as FixResponse)
      } catch {
        reject(new Error("could not parse the corrector's response"))
      }
    })

    child.stdin.on("error", () => {
      // Broken pipe: the close handler reports the real reason.
    })
    child.stdin.end(request)
  })
}

/**
 * Correct imported annotations, returning the corrected frames.
 *
 * Returns the ORIGINAL items unchanged if anything goes wrong, including a
 * response whose frame count does not match what was sent. Never throws: a
 * correction failure must not fail project creation.
 *
 * @param items parsed frames from the uploaded item file
 * @param options worker script, image root, connect geometry and timeout
 */
export async function correctAnnotations(
  items: Array<Partial<ItemExport>>,
  options: AnnotationFixOptions = {}
): Promise<Array<Partial<ItemExport>>> {
  if (items.length === 0) {
    return items
  }

  const request = JSON.stringify({
    document: items,
    image_root: options.imageRoot ?? getImageRoot(),
    clamp: true,
    connect: true,
    tolerance: options.tolerance ?? getConnectTolerance(),
    min_angle: options.minAngle ?? getConnectMinAngle()
  })

  try {
    const response = await runCorrector(request, options)

    if (response.ok !== true) {
      const reason = response.error ?? "unknown error"
      Logger.warning(`Annotation auto-correct skipped: ${reason}`)
      Logger.warning(DIAGNOSE_HINT)
      return items
    }

    const corrected = response.document
    if (!Array.isArray(corrected) || corrected.length !== items.length) {
      Logger.warning(
        "Annotation auto-correct skipped: unexpected response from the corrector"
      )
      Logger.warning(DIAGNOSE_HINT)
      return items
    }

    const summary = response.report?.summary
    if (summary !== undefined) {
      Logger.info(
        `Annotation auto-correct: ${summary.totalClamped} vertices clamped, ` +
          `${summary.totalMerged} lines merged across ${summary.frames} frames`
      )
      if (summary.totalFlagged > 0) {
        Logger.info(
          `Annotation auto-correct: ${summary.totalFlagged} correction(s) ` +
            "exceeded the review threshold"
        )
      }
      if (summary.missingImages.length > 0) {
        const all = summary.missingImages.length === items.length
        const message =
          `Annotation auto-correct: ${summary.missingImages.length} image(s) ` +
          "not found; those frames were not clamped"
        if (all) {
          // Not one image resolved, so the ROI clamp did nothing while
          // auto-connect still ran — a half-correction that looks like success.
          Logger.warning(
            `${message} (NO image resolved — check the image root)`
          )
        } else {
          Logger.info(message)
        }
      }
    }

    return corrected
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    Logger.warning(`Annotation auto-correct skipped: ${reason}`)
    Logger.warning(DIAGNOSE_HINT)
    return items
  }
}
