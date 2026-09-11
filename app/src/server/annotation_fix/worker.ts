/**
 * Worker-thread entry point for one correction run.
 *
 * The corrections are CPU-bound and long: decoding a 238.9 megapixel
 * orthomosaic and flood-filling its mask takes several seconds, and a task is
 * fifty frames. Running that on the main thread would block the event loop for
 * the whole job — no dashboard polling, no websockets, no other user able to
 * annotate — which is precisely the isolation the Python child process used to
 * provide for free. A worker thread keeps it.
 *
 * The contract mirrors the old stdio protocol: one request in, one response out,
 * and the thread exits. Failures are reported as a message rather than thrown,
 * so the caller always has something to fall back from.
 */

import { parentPort, workerData } from "worker_threads"

import { makeOptions, processDocument } from "./core"
import { FrameLike, Options } from "./types"

/**
 * What the parent sends to start a run.
 */
export interface WorkerRequest {
  /** the frames to correct */
  document: FrameLike[]
  /** the run's settings */
  options: Partial<Options>
}

/**
 * What the worker sends back.
 */
export interface WorkerResponse {
  /** whether the correction succeeded */
  ok: boolean
  /** the corrected frames, when it did */
  document?: FrameLike[]
  /** what changed, when it did */
  report?: unknown
  /** why it failed, when it did */
  error?: string
}

/**
 * Run the correction described by `workerData` and post the response.
 */
async function main(): Promise<void> {
  if (parentPort === null) {
    return
  }
  try {
    const request = workerData as WorkerRequest
    const { document, report } = await processDocument(
      request.document,
      makeOptions(request.options)
    )
    const response: WorkerResponse = {
      ok: true,
      document: document as FrameLike[],
      report
    }
    parentPort.postMessage(response)
  } catch (error) {
    const response: WorkerResponse = {
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    }
    parentPort.postMessage(response)
  }
}

void main()
