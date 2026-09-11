/**
 * Runs the annotation-fix corrections at project creation, when the
 * "Auto-correct annotations" box is ticked.
 *
 * ROI clamp: the orthomosaic images pad a narrow captured footprint into a
 * large rectangle with black. Vertices that drift into the padding are pulled
 * back to the nearest valid pixel.
 *
 * Auto-connect: same-category polyline endpoints that nearly touch are spliced
 * into one label, the batch equivalent of the editor's
 * drag-endpoint-onto-endpoint gesture.
 *
 * This was a Python child process (tools/annotation_fix, via
 * `python -m annotation_fix.stdio`) and is now TypeScript running in a worker
 * thread. The Python package is still in the tree and still works as a CLI, but
 * nothing in the server calls it.
 *
 * The worker thread is not an implementation detail. The corrections are
 * CPU-bound for minutes at a time, and the child process used to keep that off
 * the event loop for free; doing the work inline would freeze the dashboard
 * polling that this very feature depends on. The thread preserves that
 * property, and keeps a runaway job killable.
 *
 * Correction is best-effort. If anything fails, the ORIGINAL annotations are
 * returned and the reason is logged. Failing an entire import because a helper
 * broke would be the wrong trade.
 */

import * as fs from "fs-extra"
import * as path from "path"
import { Worker } from "worker_threads"

import { ItemExport } from "../../types/export"
import Logger from "../logger"
import { makeOptions, processDocument } from "./core"
import { FrameLike, Options } from "./types"
import { WorkerRequest, WorkerResponse } from "./worker"

export { processDocument, processFrame, makeOptions } from "./core"
export { computeRoiMask, loadRoiMask, RoiMask } from "./mask"
export { clampLabels, clampVertices } from "./clamp"
export { connectLabels } from "./autoconnect"
export * from "./types"

/** How long the worker gets before it is terminated. */
const DEFAULT_TIMEOUT_MS = 1800000

/** File webpack emits for the worker entry, alongside the server bundle. */
const WORKER_BUNDLE = "annotation_fix_worker.js"

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
  /** prefix used to resolve the relative image paths in frame names */
  imageRoot?: string
  /** directory used to memoise ROI masks between runs */
  cacheDir?: string
  /** how long to allow before terminating the worker */
  timeoutMs?: number
  /** run on this thread instead of a worker; for tests */
  inProcess?: boolean
}

/**
 * Root the corrections use to resolve relative image paths.
 *
 * Frame names are stored relative to the data root, so this must be an absolute
 * path: a relative one would resolve against the process working directory and
 * every image could come back not-found, silently skipping the clamp stage while
 * auto-connect still ran.
 */
export function getImageRoot(): string {
  const configured = process.env.SCALABEL_ANNOTATION_FIX_IMAGE_ROOT
  if (configured !== undefined && configured !== "") {
    return path.resolve(configured)
  }
  return path.join(process.cwd(), "local-data")
}

/**
 * Directory used to memoise ROI masks, or undefined when caching is off.
 *
 * Masks are keyed by image path, mtime, size and threshold, so a stale entry
 * cannot be served for a changed image.
 */
export function getCacheDir(): string | undefined {
  const configured = process.env.SCALABEL_ANNOTATION_FIX_CACHE_DIR
  if (configured !== undefined && configured !== "") {
    return path.resolve(configured)
  }
  return undefined
}

/**
 * Locate the compiled worker entry, or null when it is not present.
 *
 * Webpack emits it next to the server bundle. Running from ts-jest or ts-node
 * there is no bundle, and the caller falls back to this thread.
 */
function findWorkerScript(): string | null {
  const candidate = path.join(__dirname, WORKER_BUNDLE)
  try {
    if (fs.statSync(candidate).isFile()) {
      return candidate
    }
  } catch {
    // Not built, or not a bundled layout.
  }
  return null
}

/**
 * Run one correction in a worker thread.
 *
 * @param script the worker entry to run
 * @param request the payload to hand it
 * @param timeoutMs how long before the worker is terminated
 */
async function runInWorker(
  script: string,
  request: WorkerRequest,
  timeoutMs: number
): Promise<WorkerResponse> {
  return await new Promise<WorkerResponse>((resolve, reject) => {
    const worker = new Worker(script, { workerData: request })
    let settled = false

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true
        void worker.terminate()
        reject(new Error(`timed out after ${timeoutMs}ms`))
      }
    }, timeoutMs)

    worker.on("message", (response: WorkerResponse) => {
      if (!settled) {
        settled = true
        clearTimeout(timer)
        void worker.terminate()
        resolve(response)
      }
    })

    worker.on("error", (error: Error) => {
      if (!settled) {
        settled = true
        clearTimeout(timer)
        reject(error)
      }
    })

    worker.on("exit", (code: number) => {
      if (!settled) {
        settled = true
        clearTimeout(timer)
        reject(new Error(`worker exited with code ${code}`))
      }
    })
  })
}

/**
 * Log what a completed run changed.
 *
 * @param summary the run's aggregate counts
 * @param frameCount how many frames were sent
 */
function logSummary(summary: AnnotationFixSummary, frameCount: number): void {
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
    const message =
      `Annotation auto-correct: ${summary.missingImages.length} image(s) ` +
      "not found; those frames were not clamped"
    if (summary.missingImages.length === frameCount) {
      // Not one image resolved, so the ROI clamp did nothing while auto-connect
      // still ran — a half-correction that looks like success.
      Logger.warning(`${message} (NO image resolved — check the image root)`)
    } else {
      Logger.info(message)
    }
  }
}

/**
 * Note that a run was abandoned and the originals kept.
 *
 * @param reason what went wrong
 */
function logSkipped(reason: string): void {
  Logger.warning(`Annotation auto-correct skipped: ${reason}`)
  Logger.warning(
    "Annotation auto-correct SKIPPED — the project was created with " +
      "UNCORRECTED annotations. Diagnose with: " +
      "node app/dist/annotation_fix_preflight.js"
  )
}

/**
 * Correct imported annotations, returning the corrected frames.
 *
 * Returns the ORIGINAL items unchanged if anything goes wrong, including a
 * response whose frame count does not match what was sent. Never throws: a
 * correction failure must not fail project creation.
 *
 * @param items parsed frames from the uploaded item file
 * @param options image root, cache directory and timeout
 */
export async function correctAnnotations(
  items: Array<Partial<ItemExport>>,
  options: AnnotationFixOptions = {}
): Promise<Array<Partial<ItemExport>>> {
  if (items.length === 0) {
    return items
  }

  const runOptions: Partial<Options> = {
    clamp: true,
    connect: true,
    imageRoot: options.imageRoot ?? getImageRoot(),
    cacheDir: options.cacheDir ?? getCacheDir()
  }

  try {
    let response: WorkerResponse

    const script = options.inProcess === true ? null : findWorkerScript()
    if (script === null) {
      if (options.inProcess !== true) {
        Logger.warning(
          "Annotation auto-correct: worker bundle not found, correcting on " +
            "the main thread (the server will be unresponsive until it ends)"
        )
      }
      const { document, report } = await processDocument(
        items as unknown as FrameLike[],
        makeOptions(runOptions)
      )
      response = {
        ok: true,
        document: document as FrameLike[],
        report
      }
    } else {
      response = await runInWorker(
        script,
        { document: items as unknown as FrameLike[], options: runOptions },
        options.timeoutMs ?? DEFAULT_TIMEOUT_MS
      )
    }

    if (!response.ok) {
      logSkipped(response.error ?? "unknown error")
      return items
    }

    const corrected = response.document
    if (!Array.isArray(corrected) || corrected.length !== items.length) {
      logSkipped("unexpected response from the corrector")
      return items
    }

    const summary = (
      response.report as {
        /** aggregate counts */ summary?: AnnotationFixSummary
      }
    )?.summary
    if (summary !== undefined) {
      logSummary(summary, items.length)
    }

    return corrected as unknown as Array<Partial<ItemExport>>
  } catch (error) {
    logSkipped(error instanceof Error ? error.message : String(error))
    return items
  }
}
