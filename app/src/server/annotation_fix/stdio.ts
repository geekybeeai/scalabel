/**
 * Worker entry: one correction over a pipe. Request JSON on stdin, response
 * JSON on stdout.
 *
 * The server spawns this as a short-lived child per task: nothing to start by
 * hand, no port, no stale daemon holding old code. Stdout carries ONLY the
 * response; everything else goes to stderr.
 *
 * `--preflight` instead checks that sharp loads, decodes a PNG and that the
 * pipeline merges two touching road-edge lines, then exits 0 or 1.
 */

import * as fs from "fs"
import * as os from "os"
import * as path from "path"

import {
  defaultOptions,
  Document,
  Options,
  processDocument,
  ReportDict,
  reportToDict
} from "./core"
import { computeRoiMask } from "./mask"

/** What the server sends. */
export interface StdioRequest {
  /** frames or a full document */
  document?: unknown
  /** root for relative image paths */
  image_root?: string
  /** accepted for protocol compatibility; the JS engine has no mask cache */
  cache_dir?: string | null
  /** run the clamp stage */
  clamp?: boolean
  /** run the connect stage */
  connect?: boolean
  /** black-padding threshold */
  threshold?: number
  /** connect radius */
  tolerance?: number
  /** junction angle guard */
  min_angle?: number
  /** inward nudge */
  inset?: number
  /** review threshold */
  flag_distance?: number
  /** download http(s) frame urls when the image is not on disk */
  fetch_remote?: boolean
}

/** What the worker answers. */
export type StdioResponse =
  | {
      /** success */
      ok: true
      /** corrected document */
      document: Document
      /** run report */
      report: ReportDict
    }
  | {
      /** failure */
      ok: false
      /** why */
      error: string
    }

/**
 * Apply the requested corrections to one payload.
 *
 * @param payload the parsed request
 */
export async function run(payload: StdioRequest): Promise<StdioResponse> {
  const document = payload.document
  if (document === undefined || document === null) {
    return { ok: false, error: "request is missing 'document'" }
  }
  const defaults = defaultOptions()
  const options: Options = {
    clamp: payload.clamp ?? defaults.clamp,
    connect: payload.connect ?? defaults.connect,
    threshold: Number(payload.threshold ?? defaults.threshold),
    tolerance: Number(payload.tolerance ?? defaults.tolerance),
    minAngle: Number(payload.min_angle ?? defaults.minAngle),
    inset: Number(payload.inset ?? defaults.inset),
    flagDistance: Number(payload.flag_distance ?? defaults.flagDistance),
    imageRoot: String(payload.image_root ?? ""),
    fetchRemote: payload.fetch_remote ?? defaults.fetchRemote
  }
  try {
    const [corrected, report] = await processDocument(
      document as Document,
      options
    )
    return { ok: true, document: corrected, report: reportToDict(report) }
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof Error
          ? `${error.name}: ${error.message}`
          : String(error)
    }
  }
}

/**
 * Read all of stdin.
 */
async function readStdin(): Promise<string> {
  const chunks: Buffer[] = []
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.from(chunk))
  }
  return Buffer.concat(chunks).toString("utf8")
}

/**
 * Check the pieces the correction depends on and report each.
 */
export async function preflight(): Promise<boolean> {
  let ok = true
  const line = (good: boolean, msg: string): void => {
    ok = ok && good
    process.stdout.write(`  ${good ? "OK  " : "FAIL"}  ${msg}\n`)
  }
  process.stdout.write("Scalabel annotation auto-correct preflight (JS)\n\n")

  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const sharp = require("sharp") as typeof import("sharp").default
    line(true, `sharp ${sharp.versions.sharp} (libvips ${sharp.versions.vips})`)
    const dir = fs.mkdtempSync(
      path.join(os.tmpdir(), "annotation-fix-preflight-")
    )
    const file = path.join(dir, "probe.png")
    await sharp({
      create: {
        width: 8,
        height: 8,
        channels: 3,
        background: { r: 0, g: 0, b: 0 }
      }
    })
      .composite([
        {
          input: {
            create: {
              width: 4,
              height: 4,
              channels: 3,
              background: { r: 200, g: 200, b: 200 }
            }
          },
          left: 2,
          top: 2
        }
      ])
      .png()
      .toFile(file)
    const roi = await computeRoiMask(file)
    fs.rmSync(dir, { recursive: true, force: true })
    line(
      Math.abs(roi.coverage - 0.25) < 1e-9,
      "decode a PNG and build its ROI mask"
    )
  } catch (error) {
    line(
      false,
      `sharp unavailable: ${
        error instanceof Error ? error.message : String(error)
      }`
    )
  }

  const response = await run({
    document: [
      {
        name: "preflight.png",
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
    ],
    clamp: false
  })
  const merged =
    response.ok &&
    (response.document as Array<{ labels: unknown[] }>)[0].labels.length === 1
  line(merged, "end-to-end: two touching road-edge lines merged into one")

  process.stdout.write(
    ok
      ? "\nRESULT: auto-correction will work on this machine.\n"
      : "\nRESULT: auto-correction will SILENTLY SKIP on this machine.\n" +
          "Projects will still be created — with UNCORRECTED annotations.\n"
  )
  return ok
}

/**
 * Entry point.
 */
async function main(): Promise<number> {
  if (process.argv.includes("--preflight")) {
    return (await preflight()) ? 0 : 1
  }

  let payload: StdioRequest
  try {
    payload = JSON.parse(await readStdin()) as StdioRequest
  } catch (error) {
    process.stdout.write(
      JSON.stringify({
        ok: false,
        error: `invalid request JSON: ${
          error instanceof Error ? error.message : String(error)
        }`
      })
    )
    return 1
  }

  const response = await run(payload)
  process.stdout.write(JSON.stringify(response))
  return response.ok ? 0 : 1
}

// webpack rewrites `require.main`, so guard on the Jest env instead: the
// tests import `run` from this module and must not start reading stdin.
if (process.env.JEST_WORKER_ID === undefined) {
  main().then(
    (code) => {
      // Let the pipe drain before exiting.
      process.stdout.write("", () => process.exit(code))
    },
    (error) => {
      process.stderr.write(`${String(error)}\n`)
      process.exit(1)
    }
  )
}
