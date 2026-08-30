/**
 * Runs the annotation-fix corrections (tools/annotation_fix) at project
 * creation, when the "Auto-correct annotations" box is ticked.
 *
 * ROI clamp: the orthomosaic images pad a narrow captured footprint into a
 * large rectangle with black. Vertices that drift into the padding are pulled
 * back to the nearest valid pixel.
 *
 * Auto-connect: same-category polyline endpoints that nearly touch are spliced
 * into one label, the batch equivalent of the editor's
 * drag-endpoint-onto-endpoint gesture.
 *
 * Both need image pixels and cross-label geometry, so the work happens in
 * Python. It is spawned as a SHORT-LIVED CHILD PROCESS for the duration of one
 * import rather than run as a standing service: nothing has to be started by
 * hand, there is no port to collide with, and no stale daemon can survive
 * holding old code. The request goes in on stdin and the corrected document
 * comes back on stdout.
 *
 * Correction is best-effort. If Python is missing, a dependency is absent, or
 * the child fails for any reason, the ORIGINAL annotations are returned and the
 * reason is logged. Failing an entire import because a helper broke would be
 * the wrong trade.
 */

import { spawn } from "child_process"
import * as path from "path"

import { ItemExport } from "../types/export"
import Logger from "./logger"

/** Interpreter used to run the corrections. */
const DEFAULT_PYTHON = "python3"

/** How long the child gets before it is killed. */
const DEFAULT_TIMEOUT_MS = 1800000

/** Stdout cap. Corrected documents for a large batch run to tens of MB. */
const MAX_OUTPUT_BYTES = 1024 * 1024 * 1024

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
  /** python interpreter to use */
  python?: string
  /** directory containing the annotation_fix package */
  toolsDir?: string
  /** prefix used to resolve the relative image paths in frame names */
  imageRoot?: string
  /** directory used to memoise ROI masks between runs */
  cacheDir?: string
  /** how long to allow before killing the child */
  timeoutMs?: number
}

/**
 * Interpreter to run, overridable for unusual environments.
 */
export function getPythonExecutable(): string {
  const configured = process.env.SCALABEL_PYTHON
  if (configured !== undefined && configured !== "") {
    return configured
  }
  return DEFAULT_PYTHON
}

/**
 * Directory holding the annotation_fix package.
 *
 * Defaults to `<repo>/tools`, resolved from this file's location so it works
 * from both the source tree and the bundled server.
 */
export function getToolsDir(): string {
  const configured = process.env.SCALABEL_ANNOTATION_FIX_DIR
  if (configured !== undefined && configured !== "") {
    return configured
  }
  return path.join(process.cwd(), "tools")
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
 * Response shape from the Python child.
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
 * @param options interpreter, tools directory and timeout
 */
async function runCorrector(
  request: string,
  options: AnnotationFixOptions
): Promise<FixResponse> {
  const python = options.python ?? getPythonExecutable()
  const toolsDir = options.toolsDir ?? getToolsDir()
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS

  return await new Promise<FixResponse>((resolve, reject) => {
    const child = spawn(python, ["-m", "annotation_fix.stdio"], {
      cwd: toolsDir,
      // The package lives in toolsDir, so make it importable from there.
      env: { ...process.env, PYTHONPATH: toolsDir }
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
 * @param options interpreter, image root, cache directory and timeout
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
    cache_dir: options.cacheDir ?? null,
    clamp: true,
    connect: true
  })

  try {
    const response = await runCorrector(request, options)

    if (response.ok !== true) {
      const reason = response.error ?? "unknown error"
      Logger.info(`Annotation auto-correct skipped: ${reason}`)
      return items
    }

    const corrected = response.document
    if (!Array.isArray(corrected) || corrected.length !== items.length) {
      Logger.info(
        "Annotation auto-correct skipped: unexpected response from the corrector"
      )
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
        Logger.info(
          `Annotation auto-correct: ${summary.missingImages.length} image(s) ` +
            "not found; those frames were not clamped"
        )
      }
    }

    return corrected
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    Logger.info(`Annotation auto-correct skipped: ${reason}`)
    return items
  }
}
