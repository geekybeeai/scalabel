/**
 * Prevent labeling pages from loading annotations that background correction
 * is about to replace.
 */

import { NextFunction, Request, Response } from "express"

import { index2str } from "../common/util"
import { QueryArg } from "../const/common"
import { CorrectionState, getTaskStatus } from "./correction_status"

/**
 * Redirect direct label-page access to the polling dashboard while its task is
 * queued or running. Unknown, ready, and failed tasks remain accessible.
 *
 * @param req label-page request
 * @param res response
 * @param next continue to the static label page when access is safe
 */
export function correctionGateHandler(
  req: Request,
  res: Response,
  next: NextFunction
): void {
  const projectName = req.query[QueryArg.PROJECT_NAME]
  const rawTaskIndex = req.query[QueryArg.TASK_INDEX]
  if (typeof projectName !== "string" || typeof rawTaskIndex !== "string") {
    next()
    return
  }

  const taskIndex = Number(rawTaskIndex)
  if (!Number.isInteger(taskIndex) || taskIndex < 0) {
    next()
    return
  }

  const status = getTaskStatus(projectName, index2str(taskIndex))
  const busy =
    status?.state === CorrectionState.PENDING ||
    status?.state === CorrectionState.RUNNING
  if (!busy) {
    next()
    return
  }

  res.redirect(
    303,
    `/dashboard?${QueryArg.PROJECT_NAME}=${encodeURIComponent(projectName)}`
  )
}
