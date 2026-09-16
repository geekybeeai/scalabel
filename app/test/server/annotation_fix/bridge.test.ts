/** @jest-environment node */
import * as path from "path"

import { correctAnnotations } from "../../../src/server/annotation_fix"

const workerScript = path.join(
  __dirname,
  "fixtures",
  "capture_request_worker.js"
)

test("correctAnnotations sends an explicit continuity guard to the worker", async () => {
  const corrected = await correctAnnotations(
    [{ name: "source-frame", labels: [] }],
    { workerScript, minAngle: 123, timeoutMs: 5000 }
  )

  expect(corrected[0].name).toBe("min-angle:123")
})
