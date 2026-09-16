import express from "express"
import request from "supertest"

import {
  clearProjectStatuses,
  CorrectionState,
  setTaskStatus
} from "../../src/server/correction_status"
import { correctionGateHandler } from "../../src/server/correction_gate"

const PROJECT = "ANGLE 3"
const TASK_ID = "000004"

/** Build a minimal app with the real correction gate and a fake label page. */
function makeApp(): express.Application {
  const app = express()
  app.get(["/label", "/label.html"], correctionGateHandler, (_req, res) => {
    res.status(200).send("label page")
  })
  return app
}

afterEach(() => {
  clearProjectStatuses(PROJECT)
})

test.each([CorrectionState.PENDING, CorrectionState.RUNNING])(
  "redirects direct label access while correction is %s",
  async (state) => {
    setTaskStatus(PROJECT, TASK_ID, { state })

    const response = await request(makeApp()).get(
      "/label?project_name=ANGLE%203&task_index=4"
    )

    expect(response.status).toBe(303)
    expect(response.headers.location).toBe(
      "/dashboard?project_name=ANGLE%203&correction_pending=1"
    )
  }
)

test("also blocks the explicit label.html path", async () => {
  setTaskStatus(PROJECT, TASK_ID, { state: CorrectionState.RUNNING })

  const response = await request(makeApp()).get(
    "/label.html?project_name=ANGLE%203&task_index=4"
  )

  expect(response.status).toBe(303)
  expect(response.headers.location).toBe(
    "/dashboard?project_name=ANGLE%203&correction_pending=1"
  )
})

test.each([CorrectionState.READY, CorrectionState.FAILED, undefined])(
  "allows direct label access when correction is %s",
  async (state) => {
    if (state !== undefined) {
      setTaskStatus(PROJECT, TASK_ID, { state })
    }

    const response = await request(makeApp()).get(
      "/label?project_name=ANGLE%203&task_index=4"
    )

    expect(response.status).toBe(200)
    expect(response.text).toBe("label page")
  }
)

test("does not block label routes without a valid task query", async () => {
  setTaskStatus(PROJECT, TASK_ID, { state: CorrectionState.RUNNING })

  const response = await request(makeApp()).get(
    "/label?project_name=ANGLE%203&task_index=not-a-number"
  )

  expect(response.status).toBe(200)
})
