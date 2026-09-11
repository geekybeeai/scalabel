/**
 * Diagnose why auto-correction is not running on this machine.
 *
 * Correction is deliberately non-fatal: if a path resolves wrongly or an image
 * cannot be read, project creation logs a line and keeps the ORIGINAL
 * annotations. That is the right trade for imports, but it means a
 * misconfigured machine looks identical to a working one — the project is
 * created, just uncorrected.
 *
 * Far less can go wrong than when this was Python: there is no interpreter to
 * find and no pip packages to install, so the interpreter and dependency checks
 * the old preflight ran are simply gone. What remains is paths, the worker
 * bundle, and an end-to-end run.
 *
 * Run from the repo root with:
 * `node app/dist/annotation_fix_preflight.js`
 */

import * as fs from "fs-extra"
import * as path from "path"

import { correctAnnotations, getCacheDir, getImageRoot } from "./index"
import { computeRoiMask } from "./mask"
import { FrameLike } from "./types"

/**
 * Report a passing check.
 *
 * @param message what passed
 */
function ok(message: string): void {
  console.log(`  OK    ${message}`)
}

/**
 * Report a failing check.
 *
 * @param message what failed
 */
function fail(message: string): void {
  console.log(`  FAIL  ${message}`)
}

/**
 * Check the image root, which is where relative frame names resolve.
 *
 * @param skipImageRoot skip the data directory, still unmounted during a build
 */
async function checkPaths(skipImageRoot: boolean): Promise<boolean> {
  if (skipImageRoot) {
    console.log("  SKIP  data directory is mounted at runtime (--build)")
    return true
  }

  const imageRoot = getImageRoot()
  if (await fs.pathExists(imageRoot)) {
    ok(`image root ${imageRoot}`)
  } else {
    // Clamping silently no-ops when images cannot be found; auto-connect still
    // runs, so this looks like a half-working correction.
    fail(`image root not found at ${imageRoot}`)
    console.log("        without it every image is 'missing' and the ROI clamp")
    console.log("        is skipped while auto-connect still runs")
    console.log("        set SCALABEL_ANNOTATION_FIX_IMAGE_ROOT")
    return false
  }

  const cacheDir = getCacheDir()
  if (cacheDir === undefined) {
    console.log(
      "  SKIP  ROI mask cache disabled (set " +
        "SCALABEL_ANNOTATION_FIX_CACHE_DIR to enable)"
    )
  } else {
    ok(`ROI mask cache ${cacheDir}`)
  }
  return true
}

/**
 * Check that the worker bundle webpack emits is present.
 *
 * Without it the corrections still run, but on the main thread, which makes the
 * server unresponsive for the duration of a job.
 */
function checkWorker(): boolean {
  const script = path.join(__dirname, "annotation_fix_worker.js")
  if (fs.existsSync(script)) {
    ok(`worker bundle ${script}`)
    return true
  }
  fail(`worker bundle not found at ${script}`)
  console.log("        corrections would run on the main thread and block the")
  console.log("        server; rebuild with: npm run build")
  return false
}

/**
 * Run the real correction path end to end, with no images involved.
 *
 * Two road-edge lines whose ends touch must come back as a single label. That
 * exercises option handling, the connect stage and the worker round trip.
 */
async function checkRoundTrip(): Promise<boolean> {
  const frames: FrameLike[] = [
    {
      name: "preflight.png",
      url: "preflight.png",
      labels: [
        {
          id: "a",
          category: "curb_road_edge",
          poly2d: [
            {
              vertices: [
                [0, 0],
                [100, 0]
              ],
              types: "LL",
              closed: false
            }
          ]
        },
        {
          id: "b",
          category: "without_curb_road_edge",
          poly2d: [
            {
              vertices: [
                [101, 0],
                [200, 0]
              ],
              types: "LL",
              closed: false
            }
          ]
        }
      ]
    }
  ]

  const corrected = await correctAnnotations(
    frames as never,
    // A root nothing resolves against, so the clamp stage is skipped and the
    // check needs no image on disk.
    { imageRoot: path.join(path.sep, "nonexistent") }
  )
  const labels = (corrected[0] as FrameLike).labels ?? []
  if (labels.length === 1) {
    ok("end-to-end: two touching road-edge lines merged into one")
    return true
  }
  fail(`end-to-end: expected 1 merged label, got ${labels.length}`)
  return false
}

/**
 * Decode one real image from the image root, to prove the PNG path works.
 */
async function checkImage(): Promise<boolean> {
  const imageRoot = getImageRoot()

  /**
   * Find the first PNG under a directory, without walking the whole tree.
   *
   * @param dir where to look
   * @param depth how much further to descend
   */
  const findPng = async (
    dir: string,
    depth: number
  ): Promise<string | null> => {
    if (depth < 0) {
      return null
    }
    let entries: string[]
    try {
      entries = await fs.readdir(dir)
    } catch {
      return null
    }
    const directories: string[] = []
    for (const entry of entries) {
      const full = path.join(dir, entry)
      let isDirectory = false
      try {
        isDirectory = (await fs.stat(full)).isDirectory()
      } catch {
        continue
      }
      if (isDirectory) {
        directories.push(full)
      } else if (entry.toLowerCase().endsWith(".png")) {
        return full
      }
    }
    for (const directory of directories) {
      const found = await findPng(directory, depth - 1)
      if (found !== null) {
        return found
      }
    }
    return null
  }

  const sample = await findPng(imageRoot, 4)
  if (sample === null) {
    console.log("  SKIP  no PNG found under the image root to test decoding")
    return true
  }

  const started = Date.now()
  try {
    const roi = await computeRoiMask(sample)
    const megapixels = (roi.width * roi.height) / 1e6
    ok(
      `decoded ${path.basename(sample)} ${roi.width}x${roi.height} ` +
        `(${megapixels.toFixed(1)} MP) coverage=${roi.coverage.toFixed(4)} ` +
        `in ${((Date.now() - started) / 1000).toFixed(1)}s`
    )
    if (roi.coverage === 0) {
      fail("the ROI is empty — every vertex would count as outside")
      return false
    }
    return true
  } catch (error) {
    fail(
      `could not decode ${sample}: ` +
        (error instanceof Error ? error.message : String(error))
    )
    return false
  }
}

/**
 * Run every check and report what needs fixing.
 *
 * `--build` skips the checks that only make sense at runtime, so a container
 * build can verify the bundle while the data directory is still an unmounted
 * volume.
 */
async function main(): Promise<number> {
  const buildOnly = process.argv.includes("--build")

  console.log("Scalabel annotation auto-correct preflight")
  console.log()
  console.log("worker")
  const okWorker = checkWorker()
  console.log()
  console.log("paths (resolved against the CURRENT working directory)")
  const okPaths = await checkPaths(buildOnly)
  console.log()
  console.log("images")
  const okImage = buildOnly ? true : await checkImage()
  if (buildOnly) {
    console.log("  SKIP  images are mounted at runtime (--build)")
  }
  console.log()
  console.log("end-to-end")
  // Needs no images, so it is meaningful even during a build.
  const okRun = await checkRoundTrip()

  console.log()
  if (okWorker && okPaths && okImage && okRun) {
    console.log("RESULT: auto-correction will work on this machine.")
    return 0
  }
  console.log("RESULT: auto-correction will not work correctly here.")
  console.log("Projects will still be created — with UNCORRECTED annotations.")
  return 1
}

void main().then((code) => {
  process.exitCode = code
})
